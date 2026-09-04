from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from rest_framework import generics, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from organisations.models import JoinRequest, MembershipRole, Organisation, OrganisationInvitation, OrganisationMembership
from organisations.permissions import has_organisation_role
from organisations.serializers import (
    JoinRequestDecisionSerializer,
    JoinRequestSerializer,
    InvitationAcceptanceSerializer,
    OrganisationInvitationSerializer,
    OrganisationMembershipSerializer,
    OrganisationSerializer,
)


ADMIN_ROLES = [MembershipRole.OWNER, MembershipRole.ADMIN]


class OrganisationViewSet(viewsets.ModelViewSet):
    serializer_class = OrganisationSerializer
    search_fields = ["name", "registration_number"]
    filterset_fields = ["organisation_type", "verification_status", "country", "state"]
    ordering_fields = ["name", "created_at"]

    @action(detail=False, methods=["get"])
    def directory(self, request):
        organisations = Organisation.objects.filter(verification_status="verified").annotate(
            member_count=Count("memberships", distinct=True)
        ).order_by("name")
        page = self.paginate_queryset(organisations)
        serializer = self.get_serializer(page if page is not None else organisations, many=True)
        return self.get_paginated_response(serializer.data) if page is not None else Response(serializer.data)

    def get_queryset(self):
        qs = Organisation.objects.annotate(member_count=Count("memberships", distinct=True)).order_by("name")
        if self.request.user.is_staff or self.request.user.is_superuser:
            return qs
        return qs.filter(memberships__user=self.request.user, memberships__is_active=True).distinct()

    def perform_update(self, serializer):
        if not has_organisation_role(self.request.user, serializer.instance.id, ADMIN_ROLES):
            raise PermissionDenied("Only organisation administrators may update this organisation.")
        serializer.save()

    def perform_destroy(self, instance):
        if not has_organisation_role(self.request.user, instance.id, [MembershipRole.OWNER]):
            raise PermissionDenied("Only the organisation owner may delete it.")
        instance.delete()

    @action(detail=True, methods=["get"])
    def members(self, request, pk=None):
        organisation = self.get_object()
        memberships = organisation.memberships.select_related("user")
        return Response(OrganisationMembershipSerializer(memberships, many=True).data)

    @action(detail=True, methods=["get", "post"])
    def invitations(self, request, pk=None):
        organisation = self.get_object()
        if not has_organisation_role(request.user, organisation.id, ADMIN_ROLES):
            raise PermissionDenied("Only organisation administrators may manage invitations.")
        if request.method == "GET":
            return Response(OrganisationInvitationSerializer(organisation.invitations.all(), many=True).data)
        serializer = OrganisationInvitationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invitation = serializer.save(organisation=organisation, invited_by=request.user)
        return Response(OrganisationInvitationSerializer(invitation).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], url_path="join-requests")
    def join_requests(self, request, pk=None):
        organisation = self.get_object()
        if not has_organisation_role(request.user, organisation.id, ADMIN_ROLES):
            raise PermissionDenied("Only organisation administrators may review join requests.")
        return Response(JoinRequestSerializer(organisation.join_requests.select_related("requester"), many=True).data)


class JoinRequestViewSet(viewsets.ModelViewSet):
    serializer_class = JoinRequestSerializer
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        return JoinRequest.objects.filter(requester=self.request.user).select_related("requester", "organisation")

    def perform_create(self, serializer):
        organisation = serializer.validated_data["organisation"]
        if OrganisationMembership.objects.filter(organisation=organisation, user=self.request.user, is_active=True).exists():
            raise PermissionDenied("You are already a member of this organisation.")
        serializer.save(requester=self.request.user)

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def decide(self, request, pk=None):
        join_request = JoinRequest.objects.select_for_update().select_related("organisation").get(pk=pk)
        if not has_organisation_role(request.user, join_request.organisation_id, ADMIN_ROLES):
            raise PermissionDenied("Only organisation administrators may decide join requests.")
        if join_request.status != "pending":
            return Response({"detail": "This request has already been decided."}, status=status.HTTP_409_CONFLICT)
        serializer = JoinRequestDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        join_request.status = serializer.validated_data["decision"]
        join_request.decision_notes = serializer.validated_data.get("notes", "")
        join_request.decided_by = request.user
        join_request.decided_at = timezone.now()
        join_request.save(update_fields=["status", "decision_notes", "decided_by", "decided_at", "updated_at"])
        if join_request.status == "approved":
            OrganisationMembership.objects.update_or_create(
                organisation=join_request.organisation,
                user=join_request.requester,
                defaults={"role": join_request.requested_role, "is_active": True},
            )
        return Response(JoinRequestSerializer(join_request).data)


class InvitationAcceptanceView(generics.GenericAPIView):
    serializer_class = InvitationAcceptanceSerializer

    @transaction.atomic
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invitation = OrganisationInvitation.objects.select_for_update().filter(
            token=serializer.validated_data["token"]
        ).first()
        if not invitation:
            return Response({"detail": "Invitation not found."}, status=status.HTTP_404_NOT_FOUND)
        if invitation.accepted_at or invitation.revoked_at or invitation.expires_at <= timezone.now():
            return Response({"detail": "Invitation is no longer valid."}, status=status.HTTP_409_CONFLICT)
        if invitation.email.lower() != request.user.email.lower():
            raise PermissionDenied("This invitation belongs to another email address.")
        OrganisationMembership.objects.update_or_create(
            organisation=invitation.organisation,
            user=request.user,
            defaults={"role": invitation.role, "is_active": True},
        )
        invitation.accepted_at = timezone.now()
        invitation.save(update_fields=["accepted_at", "updated_at"])
        return Response({"organisation_id": invitation.organisation_id, "role": invitation.role})
