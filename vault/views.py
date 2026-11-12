from django.db.models import Count, Q
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.exceptions import ResourceNotFoundError
from common.files import serve_stored_file
from compliance.models import Sector
from organisations.models import Organisation
from vault import access, services
from vault import serializers as s
from vault.models import VaultActivity, VaultCategory, VaultDocument


def _sector_or_404(value):
    if value not in access.enabled_sectors():
        raise ResourceNotFoundError("This sector has no document vault yet.")
    return value


class VaultSectorsView(APIView):
    """The vaults the caller can open, with their folders and counts."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        visible = VaultDocument.objects.filter(access.visible_filter(request.user), superseded_by__isnull=True)
        sectors = []
        for sector in access.enabled_sectors():
            found = access.standing(request.user, sector)
            if found is None:
                continue
            in_sector = visible.filter(sector=sector)
            categories = VaultCategory.objects.filter(sector=sector, is_active=True).annotate(
                document_count=Count("documents", filter=Q(documents__in=in_sector)),
            ).order_by("sort_order", "name")
            sectors.append({
                "sector": sector.value,
                "label": Sector(sector).label,
                "audience": found.audience,
                "organisation": found.organisation.name if found.organisation else "Beldium",
                "can_approve": found.is_desk,
                "total": in_sector.count(),
                "awaiting_approval": in_sector.filter(approval__isnull=True).count(),
                "categories": s.VaultCategorySerializer(categories, many=True).data,
            })
        return Response({"sectors": sectors, "max_upload_bytes": s.max_upload_bytes()})


class VaultDocumentViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Vault documents. There is no update or delete route by design: a
    corrected file is added as a new version of the document."""

    permission_classes = [IsAuthenticated]
    serializer_class = s.VaultDocumentSerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    search_fields = ["reference", "title", "original_name", "description"]
    filterset_fields = []
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        qs = VaultDocument.objects.filter(access.visible_filter(self.request.user)).select_related(
            "category", "organisation", "uploaded_by", "approval__approved_by", "superseded_by",
        )
        if self.action != "list":
            return qs
        params = self.request.query_params
        qs = qs.filter(sector=_sector_or_404(params.get("sector", "")), superseded_by__isnull=True)
        if params.get("category"):
            qs = qs.filter(category__code=params["category"])
        if params.get("status") == "approved":
            qs = qs.filter(approval__isnull=False)
        elif params.get("status") == "awaiting_approval":
            qs = qs.filter(approval__isnull=True)
        if params.get("organisation"):
            if params["organisation"] == "beldium":
                qs = qs.filter(organisation__isnull=True)
            else:
                qs = qs.filter(organisation_id=params["organisation"])
        return qs

    def retrieve(self, request, *args, **kwargs):
        document = self.get_object()
        versions = self.get_queryset().filter(family=document.family).order_by("-version")
        data = {
            "document": self.get_serializer(document).data,
            "versions": self.get_serializer(versions, many=True).data,
            "can_approve": access.can_approve(request.user, document) and not hasattr(document, "approval"),
            "activity": [],
        }
        found = access.standing(request.user, document.sector)
        if found and found.is_desk:
            # Partners see the version history; the desk also sees who opened it.
            activity = VaultActivity.objects.filter(document__family=document.family).select_related("actor", "document")[:100]
            data["activity"] = s.VaultActivitySerializer(activity, many=True).data
        return Response(data)

    @extend_schema(request=s.VaultUploadSerializer, responses=s.VaultDocumentSerializer)
    def create(self, request, *args, **kwargs):
        serializer = s.VaultUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        sector = _sector_or_404(data["sector"])
        category = VaultCategory.objects.filter(sector=sector, code=data["category"], is_active=True).first()
        if category is None:
            raise ValidationError({"category": ["Choose one of this vault's categories."]})
        organisation = None
        if data.get("organisation"):
            organisation = Organisation.objects.filter(pk=data["organisation"]).first()
            if organisation is None:
                raise ValidationError({"organisation": ["Organisation not found."]})
        document = services.upload(
            request.user, sector=sector, category=category, title=data["title"],
            description=data["description"], uploaded_file=data["file"], organisation=organisation,
            received_from=data["received_from"], received_date=data["received_date"],
        )
        return Response(self.get_serializer(document).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=s.VaultVersionSerializer, responses=s.VaultDocumentSerializer)
    @action(detail=True, methods=["post"])
    def versions(self, request, pk=None):
        current = self.get_object()
        serializer = s.VaultVersionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        document = services.add_version(
            request.user, current, uploaded_file=serializer.validated_data["file"],
            note=serializer.validated_data["note"],
            received_from=serializer.validated_data["received_from"],
            received_date=serializer.validated_data["received_date"],
        )
        return Response(self.get_serializer(document).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=s.VaultApproveSerializer, responses=s.VaultDocumentSerializer)
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        document = self.get_object()
        serializer = s.VaultApproveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.approve(request.user, document, note=serializer.validated_data["note"])
        document = self.get_queryset().get(pk=document.pk)
        return Response(self.get_serializer(document).data)

    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        document = self.get_object()
        if not document.file:
            raise ResourceNotFoundError("The stored file is missing.")
        if document.scan_status == "infected":
            from common.exceptions import AppError
            raise AppError("This file failed the safety scan and cannot be opened.", code="file_quarantined", status_code=410)
        services.record_download(request.user, document)
        return serve_stored_file(document.file, document.original_name)

    @action(detail=True, methods=["post"])
    def verify(self, request, pk=None):
        document = self.get_object()
        return Response(services.check_integrity(request.user, document))
