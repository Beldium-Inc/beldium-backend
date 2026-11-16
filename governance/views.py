from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import AccountAuditEvent
from common.files import serve_stored_file
from governance import copilot, intelligence, reports, services
from governance import models as m
from governance import permissions as perms
from governance import serializers as s
from governance.permissions import IsGovernanceMember


class AtomicMixin:
    """Each request is one transaction: a change and its audit entry commit together."""

    @transaction.atomic
    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        if response.status_code >= 400:
            transaction.set_rollback(True)
        return response

    def get_object(self):
        obj = super().get_object()
        if self.request.method not in {"GET", "HEAD", "OPTIONS"}:
            obj = type(obj).objects.select_for_update().get(pk=obj.pk)
        return obj


class RecordViewSet(AtomicMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """Read for every member; create and edit for authors. No delete path."""

    permission_classes = [IsAuthenticated, IsGovernanceMember]
    http_method_names = ["get", "post", "patch", "head", "options"]
    audit_name = ""

    def perform_create(self, serializer):
        perms.require(self.request.user, "author")
        obj = serializer.save(**self.create_extra())
        services.audit(self.request, f"{self.audit_name}_created", object_id=obj.pk)

    def perform_update(self, serializer):
        perms.require(self.request.user, "author")
        obj = serializer.save()
        services.audit(self.request, f"{self.audit_name}_updated", object_id=obj.pk, fields=",".join(sorted(serializer.validated_data)))

    def create_extra(self):
        return {}


class PolicyViewSet(RecordViewSet):
    queryset = m.Policy.objects.select_related("owner", "created_by", "approved_by")
    search_fields = ["reference", "title", "domain"]
    filterset_fields = ["status", "sector"]
    ordering_fields = ["updated_at", "review_date", "reference", "title"]
    audit_name = "policy"

    def get_serializer_class(self):
        return s.PolicyDetailSerializer if self.action == "retrieve" else s.PolicySerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action == "retrieve":
            queryset = queryset.prefetch_related("events__actor")
        return queryset

    def perform_create(self, serializer):
        perms.require(self.request.user, "author")
        policy = serializer.save(created_by=self.request.user)
        services.record_policy_created(self.request, policy)

    def perform_update(self, serializer):
        perms.require(self.request.user, "author")
        services.assert_policy_editable(serializer.instance)
        policy = serializer.save()
        services.audit(self.request, "policy_updated", policy_id=policy.pk, fields=",".join(sorted(serializer.validated_data)))

    @extend_schema(request=s.TransitionSerializer, responses=s.PolicyDetailSerializer)
    @action(detail=True, methods=["post"])
    def transition(self, request, pk=None):
        policy = self.get_object()
        payload = s.TransitionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        services.transition_policy(request, policy, payload.validated_data["action"], payload.validated_data.get("comment", ""))
        policy = self.get_queryset().prefetch_related("events__actor").get(pk=policy.pk)
        return Response(s.PolicyDetailSerializer(policy, context=self.get_serializer_context()).data)


class RiskViewSet(RecordViewSet):
    queryset = m.Risk.objects.select_related("owner")
    serializer_class = s.RiskSerializer
    search_fields = ["reference", "title", "description"]
    filterset_fields = ["status", "category", "sector", "control_effectiveness", "likelihood", "impact"]
    ordering_fields = ["updated_at", "due_date", "reference"]
    audit_name = "risk"

    def create_extra(self):
        return {"created_by": self.request.user}

    def perform_update(self, serializer):
        if serializer.instance.status == m.RiskStatus.CLOSED:
            raise ValidationError({"status": "Reopen a closed risk before editing it."})
        super().perform_update(serializer)

    @extend_schema(request=s.CommentSerializer, responses=s.RiskSerializer)
    @action(detail=True, methods=["post"])
    def escalate(self, request, pk=None):
        risk = self.get_object()
        payload = s.CommentSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        services.escalate_risk(request, risk, payload.validated_data.get("comment", ""))
        return Response(self.get_serializer(risk).data)

    @extend_schema(request=s.RiskStatusSerializer, responses=s.RiskSerializer)
    @action(detail=True, methods=["post"], url_path="set-status")
    def set_status(self, request, pk=None):
        risk = self.get_object()
        payload = s.RiskStatusSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        services.set_risk_status(request, risk, payload.validated_data["status"], payload.validated_data.get("comment", ""))
        return Response(self.get_serializer(risk).data)


class FindingViewSet(RecordViewSet):
    queryset = m.RegulatoryFinding.objects.select_related("recorded_by", "verified_by")
    serializer_class = s.FindingSerializer
    search_fields = ["reference", "requirement", "authority", "instrument", "gap"]
    filterset_fields = ["status", "sector"]
    audit_name = "finding"

    def create_extra(self):
        return {"recorded_by": self.request.user}

    def perform_update(self, serializer):
        if serializer.instance.status != m.FindingStatus.PENDING:
            raise ValidationError({"status": "A finding can only be edited before it is verified."})
        super().perform_update(serializer)

    @extend_schema(request=s.CommentSerializer, responses=s.FindingSerializer)
    @action(detail=True, methods=["post"])
    def verify(self, request, pk=None):
        finding = self.get_object()
        payload = s.CommentSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        services.verify_finding(request, finding, payload.validated_data.get("comment", ""))
        return Response(self.get_serializer(finding).data)

    @extend_schema(request=s.CommentSerializer, responses=s.FindingSerializer)
    @action(detail=True, methods=["post"])
    def escalate(self, request, pk=None):
        finding = self.get_object()
        payload = s.CommentSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        services.escalate_finding(request, finding, payload.validated_data.get("comment", ""))
        return Response(self.get_serializer(finding).data)

    @extend_schema(request=s.ResolutionSerializer, responses=s.FindingSerializer)
    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        finding = self.get_object()
        payload = s.ResolutionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        services.close_finding(request, finding, payload.validated_data["resolution"])
        return Response(self.get_serializer(finding).data)


class StakeholderViewSet(RecordViewSet):
    queryset = m.Stakeholder.objects.select_related("owner")
    serializer_class = s.StakeholderSerializer
    search_fields = ["name"]
    filterset_fields = ["category", "is_active"]
    audit_name = "stakeholder"


class MilestoneViewSet(RecordViewSet):
    queryset = m.Milestone.objects.select_related("owner", "policy")
    serializer_class = s.MilestoneSerializer
    filterset_fields = ["status", "phase", "policy"]
    audit_name = "milestone"


class AssessmentViewSet(RecordViewSet):
    queryset = m.ImpactAssessment.objects.select_related("created_by", "policy").prefetch_related("scenarios")
    serializer_class = s.AssessmentSerializer
    search_fields = ["reference", "title"]
    audit_name = "assessment"

    def create_extra(self):
        return {"created_by": self.request.user}


class ScenarioViewSet(RecordViewSet):
    queryset = m.ImpactScenario.objects.all()
    serializer_class = s.ScenarioSerializer
    filterset_fields = ["assessment"]
    audit_name = "scenario"

    def perform_update(self, serializer):
        if "assessment" in serializer.validated_data and serializer.validated_data["assessment"] != serializer.instance.assessment:
            raise ValidationError({"assessment": "A scenario cannot be moved to another assessment."})
        super().perform_update(serializer)


class DocumentViewSet(AtomicMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet):
    """Documents are filed once and never edited; a correction is a new upload."""

    permission_classes = [IsAuthenticated, IsGovernanceMember]
    serializer_class = s.DocumentSerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    search_fields = ["name", "original_filename"]
    filterset_fields = ["document_type", "classification", "policy", "risk", "finding"]

    def get_queryset(self):
        # A document above the caller's classification does not exist for them.
        return m.GovernanceDocument.objects.filter(
            classification__in=perms.classifications(self.request.user)
        ).select_related("uploaded_by")

    def perform_create(self, serializer):
        perms.require(self.request.user, "author")
        upload = serializer.validated_data["file"]
        document = serializer.save(
            uploaded_by=self.request.user,
            original_filename=upload.name[:255],
            size=upload.size,
            content_type=upload.content_type or "",
        )
        services.audit(self.request, "document_uploaded", document_id=document.pk, classification=document.classification)

    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        document = self.get_object()
        services.audit(request, "document_downloaded", document_id=document.pk, classification=document.classification)
        return serve_stored_file(document.file, document.original_filename)


class PeopleView(APIView):
    """Active members, for owner and assignee pickers."""

    permission_classes = [IsAuthenticated, IsGovernanceMember]

    def get(self, request):
        members = m.GovernanceMember.objects.filter(status=m.MemberStatus.ACTIVE, user__is_active=True).select_related("user")
        return Response(
            [
                {"id": str(member.user_id), "name": services.display_name(member.user), "email": member.user.email, "role": member.role, "role_label": member.get_role_display()}
                for member in members
            ]
        )


# Workspace summaries --------------------------------------------------------


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        record = m.GovernanceMember.objects.filter(user=request.user).first()
        current = perms.role(request.user)
        return Response(
            {
                "member": s.MemberSerializer(record).data if record else None,
                "status": record.status if record else ("active" if current else "none"),
                "role": current,
                "role_label": m.GovernanceRole(current).label if current else None,
                "capabilities": perms.capabilities(request.user),
                "classifications": sorted(perms.classifications(request.user)),
                "roles": [{"id": value, "label": label} for value, label in m.GovernanceRole.choices],
            }
        )


def _month_starts(count):
    today = timezone.localdate().replace(day=1)
    months = []
    for _ in range(count):
        months.append(today)
        today = (today - timedelta(days=1)).replace(day=1)
    return list(reversed(months))


class DashboardView(APIView):
    permission_classes = [IsAuthenticated, IsGovernanceMember]

    def get(self, request):
        sector = request.query_params.get("sector") or ""
        if sector and sector not in m.Sector.values:
            raise ValidationError({"sector": "Unknown sector."})
        policies = m.Policy.objects.all()
        risks = m.Risk.objects.all()
        findings = m.RegulatoryFinding.objects.all()
        milestones = m.Milestone.objects.all()
        if sector and sector != m.Sector.ALL:
            # A sector view includes the records that apply to every sector.
            scope = Q(sector=sector) | Q(sector=m.Sector.ALL)
            policies, risks, findings = policies.filter(scope), risks.filter(scope), findings.filter(scope)
            milestones = milestones.filter(Q(policy__sector=sector) | Q(policy__sector=m.Sector.ALL))

        open_risks = risks.exclude(status=m.RiskStatus.CLOSED).select_related("owner")
        top = sorted(open_risks, key=lambda r: r.residual_score, reverse=True)[:5]
        pending = policies.filter(status__in=[m.PolicyStatus.SUBMITTED, m.PolicyStatus.UNDER_REVIEW, m.PolicyStatus.RECOMMENDED, m.PolicyStatus.APPROVED]).order_by("updated_at")[:8]

        trend = []
        months = _month_starts(6)
        for index, start in enumerate(months):
            end = months[index + 1] if index + 1 < len(months) else None
            created = risks.filter(created_at__date__lt=end) if end else risks
            still_open = created.filter(Q(closed_at__isnull=True) | Q(closed_at__date__gte=end) if end else Q(closed_at__isnull=True))
            escalated = risks.filter(escalated_at__date__gte=start, **({"escalated_at__date__lt": end} if end else {}))
            trend.append({"month": start.strftime("%b %Y"), "open": still_open.count(), "escalated": escalated.count()})

        sectors = intelligence.sector_summary()
        payload = {
            "sector": sector or m.Sector.ALL,
            "counts": {
                "active_policies": policies.filter(status__in=[m.PolicyStatus.APPROVED, m.PolicyStatus.PUBLISHED]).count(),
                "awaiting_review": policies.filter(status__in=[m.PolicyStatus.SUBMITTED, m.PolicyStatus.UNDER_REVIEW, m.PolicyStatus.RECOMMENDED]).count(),
                "open_risks": open_risks.count(),
                "escalated_risks": risks.filter(status=m.RiskStatus.ESCALATED).count(),
                "findings_pending": findings.filter(status=m.FindingStatus.PENDING).count(),
                "delayed_milestones": milestones.filter(status=m.MilestoneStatus.DELAYED).count(),
                "sector_open_findings": sum(row["open_findings"] or 0 for row in sectors),
            },
            "top_risks": s.RiskSerializer(top, many=True, context={"request": request}).data,
            "pending_policies": s.PolicySerializer(pending, many=True, context={"request": request}).data,
            "risk_trend": trend,
            "sectors": sectors,
            "recent_activity": _audit_rows(request, 8) if perms.has(request.user, "view_audit") else [],
        }
        return Response(payload)


class ComplianceView(APIView):
    permission_classes = [IsAuthenticated, IsGovernanceMember]

    def get(self, request):
        rows = intelligence.sector_summary()
        return Response({"window_days": intelligence.EXPIRY_WINDOW_DAYS, "sectors": rows, "alerts": intelligence.alerts(rows)})


class CopilotView(APIView):
    permission_classes = [IsAuthenticated, IsGovernanceMember]

    @extend_schema(request=s.CopilotSerializer)
    def post(self, request):
        payload = s.CopilotSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        result = copilot.answer(request.user, payload.validated_data["question"])
        services.audit(request, "copilot_query", citations=len(result["citations"]))
        return Response(result)


class ReportView(APIView):
    permission_classes = [IsAuthenticated, IsGovernanceMember]

    def get(self, request, kind):
        perms.require(request.user, "report")
        if kind not in reports.KINDS:
            raise NotFound("Unknown report.")
        # Not "format": DRF reserves that query parameter for content negotiation.
        fmt = request.query_params.get("output", "pdf")
        if fmt not in reports.FORMATS:
            raise ValidationError({"output": "Use pdf or csv."})
        tables = reports.build(kind)
        stamp = timezone.now().strftime("%Y%m%d-%H%M")
        filename = f"beldium-governance-{kind}-{stamp}.{fmt}"
        if fmt == "csv":
            body, content_type = reports.render_csv(kind, tables), "text/csv"
        else:
            body, content_type = reports.render_pdf(kind, tables, services.display_name(request.user)), "application/pdf"
        services.audit(request, "report_downloaded", kind=kind, format=fmt)
        response = HttpResponse(body, content_type=content_type)
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class ReportListView(APIView):
    permission_classes = [IsAuthenticated, IsGovernanceMember]

    def get(self, request):
        return Response([{"kind": key, "label": label, "formats": sorted(reports.FORMATS)} for key, label in reports.KINDS.items()])


def _audit_rows(request, limit):
    events = AccountAuditEvent.objects.filter(event_type__startswith="governance.").select_related("actor")[:limit]
    return [
        {
            "id": str(event.id),
            "event": event.event_type.removeprefix("governance."),
            "actor": services.display_name(event.actor) if event.actor else "System",
            "metadata": event.metadata,
            "created_at": event.created_at,
        }
        for event in events
    ]


class AuditView(APIView):
    permission_classes = [IsAuthenticated, IsGovernanceMember]

    def get(self, request):
        perms.require(request.user, "view_audit")
        try:
            limit = max(1, min(int(request.query_params.get("limit", 100)), 500))
        except ValueError:
            raise ValidationError({"limit": "Use a whole number."})
        return Response(_audit_rows(request, limit))


# Access management ----------------------------------------------------------


class MemberViewSet(AtomicMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = s.MemberSerializer
    filterset_fields = ["status", "role"]
    search_fields = ["user__email", "user__first_name", "user__last_name", "organisation"]

    def get_queryset(self):
        perms.require(self.request.user, "manage_access")
        return m.GovernanceMember.objects.select_related("user", "decided_by")

    @extend_schema(request=s.MemberDecisionSerializer, responses=s.MemberSerializer)
    @action(detail=True, methods=["post"])
    def decide(self, request, pk=None):
        member = self.get_object()
        payload = s.MemberDecisionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        services.decide_member(
            request,
            member,
            status=payload.validated_data["status"],
            role=payload.validated_data.get("role"),
            notes=payload.validated_data.get("notes", ""),
        )
        return Response(self.get_serializer(member).data)


class InvitationViewSet(AtomicMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = s.InvitationSerializer
    filterset_fields = ["status", "role"]
    search_fields = ["email", "name", "organisation"]

    def get_queryset(self):
        if self.action in {"lookup", "accept"}:
            return m.GovernanceInvitation.objects.all()
        perms.require(self.request.user, "manage_access")
        return m.GovernanceInvitation.objects.select_related("invited_by")

    def get_permissions(self):
        if self.action == "lookup":
            return [AllowAny()]
        return super().get_permissions()

    @extend_schema(request=s.InvitationCreateSerializer, responses=s.InvitationSerializer)
    def create(self, request):
        payload = s.InvitationCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        invitation, token = services.create_invitation(request, **payload.validated_data)
        # The plain token is returned once, to the inviter, so the link can be
        # shared by hand if email is slow. It is never readable again.
        return Response({**self.get_serializer(invitation).data, "token": token}, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def resend(self, request, pk=None):
        invitation = self.get_object()
        invitation, token = services.resend_invitation(request, invitation)
        return Response({**self.get_serializer(invitation).data, "token": token})

    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        invitation = self.get_object()
        services.revoke_invitation(request, invitation)
        return Response(self.get_serializer(invitation).data)

    @extend_schema(request=s.InvitationTokenSerializer)
    @action(detail=False, methods=["post"])
    def lookup(self, request):
        """What an invitation link offers, read before the person signs in."""
        payload = s.InvitationTokenSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        invitation = services.invitation_by_token(payload.validated_data["token"])
        if invitation is None:
            raise NotFound("This invitation link is not valid.")
        services.refresh_expiry(invitation)
        return Response(
            {
                "email": invitation.email,
                "name": invitation.name,
                "organisation": invitation.organisation,
                "role": invitation.role,
                "role_label": invitation.get_role_display(),
                "status": invitation.status,
                "expires_at": invitation.expires_at,
            }
        )

    @extend_schema(request=s.InvitationTokenSerializer, responses=s.MemberSerializer)
    @action(detail=False, methods=["post"])
    def accept(self, request):
        payload = s.InvitationTokenSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        member = services.accept_invitation(request, payload.validated_data["token"])
        return Response(s.MemberSerializer(member).data)


class AccessRequestView(AtomicMixin, APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=s.AccessRequestSerializer, responses=s.MemberSerializer)
    def post(self, request):
        payload = s.AccessRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        member = services.request_access(
            request,
            organisation=data["organisation"],
            job_title=data["job_title"],
            requested_role=data["requested_role"],
            profile={**data["profile"], "declarations": data["declarations"]},
            scope=data["scope"],
        )
        return Response(s.MemberSerializer(member).data, status=status.HTTP_201_CREATED)
