import logging

from django.core import signing
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from careers import tasks
from careers.models import PARTNER_DOCUMENT_REQUIREMENTS, PartnerApplication, PartnerDocument
from careers.serializers import (
    ApplicationSerializer,
    PartnerApplicationCreateSerializer,
    PartnerApplicationStatusSerializer,
    PartnerDocumentUploadSerializer,
)
from careers.throttles import CareersStatusThrottle, CareersSubmitThrottle, CareersUploadThrottle
from common.exceptions import AppError, ResourceNotFoundError

logger = logging.getLogger(__name__)

UPLOAD_TOKEN_SALT = "careers.partner-upload"
UPLOAD_TOKEN_MAX_AGE_SECONDS = 6 * 60 * 60


def notify_staff(request, kind, instance):
    """Email the careers inbox once the submission is committed.

    A failed send (Resend down, Redis unreachable) is logged and dropped: the
    application is already saved, and telling the applicant it failed would
    only make them submit it twice.
    """
    admin_url = request.build_absolute_uri(
        reverse(f"admin:careers_{instance._meta.model_name}_change", args=[instance.pk])
    )

    def send():
        try:
            tasks.send_application_notification.delay(kind, str(instance.pk), admin_url)
        except Exception:
            logger.exception("Unable to send careers notification", extra={"kind": kind, "id": str(instance.pk)})

    transaction.on_commit(send)


class PublicCareersView(APIView):
    """Applicants have no account. A stale bearer token left in the browser by
    one of the portals must not turn a public submission into a 401."""

    authentication_classes = []
    permission_classes = [AllowAny]


class ApplicationCreateView(PublicCareersView):
    serializer_class = ApplicationSerializer
    throttle_classes = [CareersSubmitThrottle]

    @transaction.atomic
    def post(self, request):
        serializer = ApplicationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        application = serializer.save()
        notify_staff(request, "application", application)
        return Response({"reference_id": application.reference_id}, status=status.HTTP_201_CREATED)


class PartnerApplicationCreateView(PublicCareersView):
    """Step one of three. Fourteen documents of up to 10MB each do not fit in
    one request on a slow connection, so the record is opened first and each
    document then travels on its own, authorised by the token returned here."""

    serializer_class = PartnerApplicationCreateSerializer
    throttle_classes = [CareersSubmitThrottle]

    def post(self, request):
        serializer = PartnerApplicationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        application = serializer.save()
        return Response(
            {
                "application_id": application.application_id,
                "sector": application.sector,
                "status": application.status,
                "required_documents": list(PARTNER_DOCUMENT_REQUIREMENTS[application.sector]["required"]),
                "optional_documents": list(PARTNER_DOCUMENT_REQUIREMENTS[application.sector]["optional"]),
                "upload_token": signing.dumps(application.application_id, salt=UPLOAD_TOKEN_SALT),
            },
            status=status.HTTP_201_CREATED,
        )


class PartnerApplicationDraftView(PublicCareersView):
    throttle_classes = [CareersUploadThrottle]

    def get_draft(self, request, application_id):
        try:
            signed_for = signing.loads(
                str(request.data.get("upload_token", "")),
                salt=UPLOAD_TOKEN_SALT,
                max_age=UPLOAD_TOKEN_MAX_AGE_SECONDS,
            )
        except signing.BadSignature:
            signed_for = None
        if signed_for != application_id:
            raise AppError(
                "This upload session has expired. Please submit the form again.",
                code="upload_token_invalid",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        application = PartnerApplication.objects.select_for_update().filter(application_id=application_id).first()
        if not application or application.status != PartnerApplication.Status.DRAFT:
            raise AppError("This application has already been submitted.", code="already_submitted")
        return application


class PartnerDocumentUploadView(PartnerApplicationDraftView):
    serializer_class = PartnerDocumentUploadSerializer

    @transaction.atomic
    def post(self, request, application_id):
        application = self.get_draft(request, application_id)
        serializer = PartnerDocumentUploadSerializer(data=request.data, context={"application": application})
        serializer.is_valid(raise_exception=True)
        key = serializer.validated_data["key"]
        # A retried upload replaces the earlier attempt rather than stacking up.
        for previous in application.documents.filter(key=key):
            previous.file.delete(save=False)
            previous.delete()
        PartnerDocument.objects.create(application=application, key=key, file=serializer.validated_data["file"])
        return Response({"key": key}, status=status.HTTP_201_CREATED)


class PartnerApplicationSubmitView(PartnerApplicationDraftView):
    @transaction.atomic
    def post(self, request, application_id):
        application = self.get_draft(request, application_id)
        uploaded = set(application.documents.values_list("key", flat=True))
        required_documents = PARTNER_DOCUMENT_REQUIREMENTS[application.sector]["required"]
        missing = [key for key in required_documents if key not in uploaded]
        if missing:
            raise AppError(
                "Upload every required document before submitting.",
                code="documents_missing",
                details={"missing": missing},
            )
        application.status = PartnerApplication.Status.SUBMITTED
        application.submitted_at = timezone.now()
        application.save(update_fields=["status", "submitted_at", "updated_at"])
        notify_staff(request, "partner-application", application)
        return Response(PartnerApplicationStatusSerializer(application).data)


class PartnerApplicationStatusView(PublicCareersView):
    serializer_class = PartnerApplicationStatusSerializer
    throttle_classes = [CareersStatusThrottle]

    def get(self, request, application_id):
        application = (
            PartnerApplication.objects.exclude(status=PartnerApplication.Status.DRAFT)
            .filter(application_id=application_id)
            .first()
        )
        if not application:
            raise ResourceNotFoundError("No application was found with that reference.")
        return Response(PartnerApplicationStatusSerializer(application).data)
