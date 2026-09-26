from django.core.cache import cache
from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema

from accounts.models import AccountAuditEvent
from accounts.services import enqueue_account_email
from organisations.audit import record_event
from organisations.dedupe import apply_dedupe, plan_dedupe
from organisations.models import JoinRequest, MembershipRole, Organisation, OrganisationInvitation, OrganisationMembership
from common.exceptions import ConflictError, ResourceNotFoundError
from organisations.timeline import build_timeline
from organisations.permissions import has_organisation_role
from organisations.serializers import (
    JoinRequestDecisionSerializer,
    JoinRequestSerializer,
    InvitationAcceptanceSerializer,
    MembershipUpdateSerializer,
    OrganisationDecisionSerializer,
    AccountAuditEventSerializer,
    OrganisationInvitationSerializer,
    OrganisationMembershipSerializer,
    OrganisationSerializer,
)


ADMIN_ROLES = [MembershipRole.OWNER, MembershipRole.ADMIN]


class OrganisationViewSet(viewsets.ModelViewSet):
    queryset = Organisation.objects.none()
    serializer_class = OrganisationSerializer
    search_fields = ["name", "registration_number"]
    filterset_fields = ["organisation_type", "verification_status", "country", "state"]
    ordering_fields = ["name", "created_at"]

    def perform_create(self, serializer):
        organisation = serializer.save()
        record_event(self.request, "organisation.created", organisation=organisation)

    @action(detail=False, methods=["get"])
    def directory(self, request):
        # filter_queryset applies the search and filter fields declared above, so
        # the register can be searched by name or registration number.
        organisations = self.filter_queryset(
            Organisation.objects.filter(verification_status="verified").annotate(
                member_count=Count("memberships", distinct=True)
            )
        ).order_by("name")
        page = self.paginate_queryset(organisations)
        serializer = self.get_serializer(page if page is not None else organisations, many=True)
        return self.get_paginated_response(serializer.data) if page is not None else Response(serializer.data)

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        qs = Organisation.objects.annotate(member_count=Count("memberships", distinct=True)).order_by("name")
        if self.request.user.is_staff or self.request.user.is_superuser:
            return qs
        return qs.filter(memberships__user=self.request.user, memberships__is_active=True).distinct()

    def perform_update(self, serializer):
        if not has_organisation_role(self.request.user, serializer.instance.id, ADMIN_ROLES):
            raise PermissionDenied("Only organisation administrators may update this organisation.")
        serializer.save()
        record_event(self.request, "organisation.updated", organisation=serializer.instance)

    def perform_destroy(self, instance):
        if not has_organisation_role(self.request.user, instance.id, [MembershipRole.OWNER]):
            raise PermissionDenied("Only the organisation owner may delete it.")
        instance.delete()

    @action(detail=True, methods=["get"])
    def members(self, request, pk=None):
        organisation = self.get_object()
        memberships = organisation.memberships.select_related("user")
        return Response(OrganisationMembershipSerializer(memberships, many=True).data)

    @action(detail=True, methods=["get"], url_path="my-permissions")
    def my_permissions(self, request, pk=None):
        organisation = self.get_object()
        membership = get_object_or_404(organisation.memberships, user=request.user, is_active=True)
        return Response(OrganisationMembershipSerializer(membership).data)

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
        transaction.on_commit(lambda: enqueue_account_email(
            "send_organisation_invitation_email",
            invitation.email,
            organisation.name,
            request.user.full_name or request.user.email,
            invitation.get_role_display(),
            invitation.token,
        ))
        record_event(request, "membership.invited", organisation=organisation, email=invitation.email, role=invitation.role)
        return Response(OrganisationInvitationSerializer(invitation).data, status=status.HTTP_201_CREATED)

    @extend_schema(parameters=[OpenApiParameter("invitation_id", OpenApiTypes.UUID, OpenApiParameter.PATH)])
    @action(detail=True, methods=["post"], url_path=r"invitations/(?P<invitation_id>[^/.]+)/revoke")
    def revoke_invitation(self, request, pk=None, invitation_id=None):
        organisation = self.get_object()
        if not has_organisation_role(request.user, organisation.id, ADMIN_ROLES):
            raise PermissionDenied("Only organisation administrators may revoke invitations.")
        invitation = get_object_or_404(organisation.invitations, id=invitation_id)
        if invitation.accepted_at or invitation.revoked_at:
            raise ConflictError("Invitation is no longer active.", code="invitation_invalid")
        invitation.revoked_at = timezone.now()
        invitation.save(update_fields=["revoked_at", "updated_at"])
        record_event(request, "membership.invitation_revoked", organisation=organisation, invitation_id=str(invitation.id))
        return Response({"message": "Invitation revoked."})

    @action(detail=True, methods=["get"], url_path="join-requests")
    def join_requests(self, request, pk=None):
        organisation = self.get_object()
        if not has_organisation_role(request.user, organisation.id, ADMIN_ROLES):
            raise PermissionDenied("Only organisation administrators may review join requests.")
        return Response(JoinRequestSerializer(organisation.join_requests.select_related("requester"), many=True).data)

    def _managed_membership(self, request, organisation, membership_id):
        if not has_organisation_role(request.user, organisation.id, ADMIN_ROLES):
            raise PermissionDenied("Only organisation administrators may manage members.")
        membership = get_object_or_404(organisation.memberships.select_related("user"), id=membership_id)
        if membership.role == MembershipRole.OWNER:
            raise PermissionDenied("The organisation owner cannot be modified through member management.")
        return membership

    @extend_schema(parameters=[OpenApiParameter("membership_id", OpenApiTypes.UUID, OpenApiParameter.PATH)])
    @action(detail=True, methods=["patch"], url_path=r"members/(?P<membership_id>[^/.]+)")
    def update_member(self, request, pk=None, membership_id=None):
        organisation = self.get_object()
        membership = self._managed_membership(request, organisation, membership_id)
        serializer = MembershipUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if serializer.validated_data.get("role") == MembershipRole.OWNER:
            raise PermissionDenied("Ownership transfer requires a dedicated workflow.")
        for field, value in serializer.validated_data.items():
            setattr(membership, field, value)
        membership.save()
        record_event(request, "membership.updated", organisation=organisation, membership_id=str(membership.id), role=membership.role)
        return Response(OrganisationMembershipSerializer(membership).data)

    @extend_schema(parameters=[OpenApiParameter("membership_id", OpenApiTypes.UUID, OpenApiParameter.PATH)])
    @action(detail=True, methods=["post"], url_path=r"members/(?P<membership_id>[^/.]+)/suspend")
    def suspend_member(self, request, pk=None, membership_id=None):
        organisation = self.get_object()
        membership = self._managed_membership(request, organisation, membership_id)
        membership.is_active = False
        membership.suspended_at = timezone.now()
        membership.save(update_fields=["is_active", "suspended_at", "updated_at"])
        record_event(request, "membership.suspended", organisation=organisation, membership_id=str(membership.id))
        return Response(OrganisationMembershipSerializer(membership).data)

    @extend_schema(parameters=[OpenApiParameter("membership_id", OpenApiTypes.UUID, OpenApiParameter.PATH)])
    @action(detail=True, methods=["post"], url_path=r"members/(?P<membership_id>[^/.]+)/activate")
    def activate_member(self, request, pk=None, membership_id=None):
        organisation = self.get_object()
        if not has_organisation_role(request.user, organisation.id, ADMIN_ROLES):
            raise PermissionDenied("Only organisation administrators may manage members.")
        membership = get_object_or_404(organisation.memberships, id=membership_id)
        membership.is_active = True
        membership.suspended_at = None
        membership.save(update_fields=["is_active", "suspended_at", "updated_at"])
        record_event(request, "membership.activated", organisation=organisation, membership_id=str(membership.id))
        return Response(OrganisationMembershipSerializer(membership).data)

    @extend_schema(parameters=[OpenApiParameter("membership_id", OpenApiTypes.UUID, OpenApiParameter.PATH)])
    @action(detail=True, methods=["delete"], url_path=r"members/(?P<membership_id>[^/.]+)/remove")
    def remove_member(self, request, pk=None, membership_id=None):
        organisation = self.get_object()
        membership = self._managed_membership(request, organisation, membership_id)
        user_id = str(membership.user_id)
        membership.delete()
        record_event(request, "membership.removed", organisation=organisation, user_id=user_id)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        organisation = self.get_object()
        if not has_organisation_role(request.user, organisation.id, ADMIN_ROLES):
            raise PermissionDenied("Only organisation administrators may submit verification applications.")
        if organisation.verification_status not in {"draft", "rejected"}:
            raise ConflictError("This organisation has already been submitted.", code="organisation_already_submitted")
        organisation.verification_status = "under_review"
        organisation.submitted_at = timezone.now()
        organisation.rejection_reason = ""
        organisation.save(update_fields=["verification_status", "submitted_at", "rejection_reason", "updated_at"])
        record_event(request, "organisation.submitted", organisation=organisation)
        return Response(self.get_serializer(organisation).data)

    @action(detail=True, methods=["post"], permission_classes=[IsAdminUser])
    def decide(self, request, pk=None):
        organisation = self.get_object()
        if hasattr(organisation, "compliance_application"):
            raise ConflictError("Use the compliance application decision endpoint.", code="compliance_review_required")
        if organisation.verification_status != "under_review":
            raise ConflictError("Only organisations under review can be decided.", code="organisation_not_under_review")
        serializer = OrganisationDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        decision = serializer.validated_data["decision"]
        organisation.verification_status = decision
        organisation.verified_by = request.user
        organisation.verified_at = timezone.now() if decision == "verified" else None
        organisation.rejection_reason = serializer.validated_data.get("reason", "")
        organisation.save(update_fields=["verification_status", "verified_by", "verified_at", "rejection_reason", "updated_at"])
        record_event(request, f"organisation.{decision}", organisation=organisation)
        return Response(self.get_serializer(organisation).data)

    @action(detail=True, methods=["get"])
    def timeline(self, request, pk=None):
        """Verification stages and a member-safe activity feed. Any active member may read it."""
        organisation = self.get_object()
        return Response(build_timeline(organisation))

    @action(detail=True, methods=["get"])
    def audit(self, request, pk=None):
        organisation = self.get_object()
        if not has_organisation_role(request.user, organisation.id, ADMIN_ROLES):
            raise PermissionDenied("Only organisation administrators may view the audit trail.")
        events = AccountAuditEvent.objects.filter(metadata__organisation_id=str(organisation.id)).select_related("actor")
        page = self.paginate_queryset(events)
        serializer = AccountAuditEventSerializer(page if page is not None else events, many=True)
        return self.get_paginated_response(serializer.data) if page is not None else Response(serializer.data)

    @action(detail=False, methods=["post"], permission_classes=[IsAdminUser])
    def dedupe_duplicates(self, request):
        """Staff-only cleanup for the pre-guard duplicate organisations (same
        name + type created before OrganisationSerializer started rejecting
        that on create). Exists so this can be run from Render without shell
        access. Defaults to a dry run — pass {"apply": true} to actually
        delete; see organisations/dedupe.py for the keeper/loser rule.
        """
        report = plan_dedupe()
        payload = {
            "groups": [
                {
                    "name": g.name,
                    "organisation_type": g.organisation_type,
                    "keeper_id": g.keeper_id,
                    "keeper_beldium_id": g.keeper_beldium_id,
                    "removed": g.losers,
                }
                for g in report.groups
            ],
            "skipped_ambiguous": report.skipped_ambiguous,
            "organisations_removed": report.organisations_removed,
            "applied": False,
        }
        if request.data.get("apply"):
            apply_dedupe(report)
            payload["applied"] = True
            record_event(request, "organisations.deduplicated", organisations_removed=report.organisations_removed)
        return Response(payload)


class JoinRequestViewSet(viewsets.ModelViewSet):
    queryset = JoinRequest.objects.none()
    serializer_class = JoinRequestSerializer
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        return JoinRequest.objects.filter(requester=self.request.user).select_related("requester", "organisation")

    def perform_create(self, serializer):
        organisation = serializer.validated_data["organisation"]
        if OrganisationMembership.objects.filter(organisation=organisation, user=self.request.user, is_active=True).exists():
            raise PermissionDenied("You are already a member of this organisation.")
        serializer.save(requester=self.request.user)

    @extend_schema(
        request=JoinRequestDecisionSerializer,
        responses={status.HTTP_200_OK: JoinRequestSerializer},
    )
    @action(detail=True, methods=["post"])
    @transaction.atomic
    def decide(self, request, pk=None):
        join_request = get_object_or_404(
            JoinRequest.objects.select_for_update().select_related("organisation"), pk=pk
        )
        if not has_organisation_role(request.user, join_request.organisation_id, ADMIN_ROLES):
            raise PermissionDenied("Only organisation administrators may decide join requests.")
        if join_request.status != "pending":
            raise ConflictError(
                "This join request has already been decided.",
                code="join_request_already_decided",
            )
        serializer = JoinRequestDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        join_request.status = serializer.validated_data["decision"]
        join_request.decision_notes = serializer.validated_data.get("notes", "")
        join_request.decided_by = request.user
        join_request.decided_at = timezone.now()
        join_request.save(update_fields=["status", "decision_notes", "decided_by", "decided_at", "updated_at"])
        if join_request.status == "approved":
            approved_role = serializer.validated_data.get("role", join_request.requested_role)
            if approved_role == MembershipRole.OWNER:
                raise PermissionDenied("Ownership cannot be granted through a join request.")
            OrganisationMembership.objects.update_or_create(
                organisation=join_request.organisation,
                user=join_request.requester,
                defaults={
                    "role": approved_role,
                    "title": join_request.job_title,
                    "employee_id": join_request.employee_id,
                    "is_active": True,
                },
            )
        record_event(request, f"membership.join_request_{join_request.status}", organisation=join_request.organisation, request_id=str(join_request.id))
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
            raise ResourceNotFoundError("Invitation not found.", code="invitation_not_found")
        if invitation.accepted_at or invitation.revoked_at or invitation.expires_at <= timezone.now():
            raise ConflictError(
                "Invitation is no longer valid.",
                code="invitation_invalid",
            )
        if invitation.email.lower() != request.user.email.lower():
            raise PermissionDenied("This invitation belongs to another email address.")
        OrganisationMembership.objects.update_or_create(
            organisation=invitation.organisation,
            user=request.user,
            defaults={"role": invitation.role, "is_active": True},
        )
        invitation.accepted_at = timezone.now()
        invitation.save(update_fields=["accepted_at", "updated_at"])
        record_event(request, "membership.invitation_accepted", organisation=invitation.organisation)
        return Response({"organisation_id": invitation.organisation_id, "role": invitation.role})


class PlatformStatsView(generics.GenericAPIView):
    """Public headline figures for the sign-in page. Counts only, nothing that
    identifies an organisation, and cached so an anonymous page can't turn
    into a query load."""

    permission_classes = [AllowAny]
    authentication_classes = []
    CACHE_KEY = "platform-stats:v1"
    CACHE_SECONDS = 300

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        stats = cache.get(self.CACHE_KEY)
        if stats is None:
            stats = {"verified_organisations": Organisation.objects.filter(verification_status="verified").count()}
            cache.set(self.CACHE_KEY, stats, self.CACHE_SECONDS)
        return Response(stats)
