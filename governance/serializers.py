from pathlib import Path

from rest_framework import serializers

from accounts.models import User
from governance import models as m
from governance import permissions as perms
from governance import services


class PersonSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    name = serializers.SerializerMethodField()
    email = serializers.EmailField(read_only=True)

    def get_name(self, user):
        return services.display_name(user)


def active_member_user(value):
    """Owners and assignees must be active governance members."""
    if not m.GovernanceMember.objects.filter(user=value, status=m.MemberStatus.ACTIVE).exists():
        raise serializers.ValidationError("Choose an active member of the governance workspace.")
    return value


class OwnerField(serializers.PrimaryKeyRelatedField):
    def get_queryset(self):
        return User.objects.filter(is_active=True, governance_member__status=m.MemberStatus.ACTIVE)


# Policies -----------------------------------------------------------------


class PolicyEventSerializer(serializers.ModelSerializer):
    actor = PersonSerializer(read_only=True)

    class Meta:
        model = m.PolicyEvent
        fields = ["id", "action", "from_status", "to_status", "version", "comment", "actor", "created_at"]


class PolicySerializer(serializers.ModelSerializer):
    owner = OwnerField()
    owner_detail = PersonSerializer(source="owner", read_only=True)
    created_by = PersonSerializer(read_only=True)
    approved_by = PersonSerializer(read_only=True)
    version = serializers.CharField(read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    allowed_actions = serializers.SerializerMethodField()

    class Meta:
        model = m.Policy
        fields = [
            "id", "reference", "title", "domain", "sector", "responsible_organisation", "jurisdiction",
            "owner", "owner_detail", "review_date", "status", "status_label", "version",
            "executive_summary", "strategic_context", "objectives", "stakeholders", "regulatory_instruments",
            "expected_outcomes", "created_by", "approved_by", "approved_at", "published_at",
            "allowed_actions", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "reference", "status", "approved_at", "published_at", "created_at", "updated_at"]

    def get_allowed_actions(self, policy):
        request = self.context.get("request")
        return services.allowed_policy_actions(request.user, policy) if request else []


class PolicyDetailSerializer(PolicySerializer):
    events = PolicyEventSerializer(many=True, read_only=True)

    class Meta(PolicySerializer.Meta):
        fields = PolicySerializer.Meta.fields + ["events"]


class TransitionSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=list(services.POLICY_TRANSITIONS))
    comment = serializers.CharField(required=False, allow_blank=True, max_length=4000)


class CommentSerializer(serializers.Serializer):
    comment = serializers.CharField(required=False, allow_blank=True, max_length=4000)


# Risks --------------------------------------------------------------------


class RiskSerializer(serializers.ModelSerializer):
    owner = OwnerField()
    owner_detail = PersonSerializer(source="owner", read_only=True)
    inherent_score = serializers.IntegerField(read_only=True)
    residual_score = serializers.IntegerField(read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = m.Risk
        fields = [
            "id", "reference", "title", "description", "category", "sector", "owner", "owner_detail",
            "likelihood", "impact", "inherent_score", "controls", "control_effectiveness",
            "residual_likelihood", "residual_impact", "residual_score", "treatment_plan", "due_date",
            "status", "status_label", "escalated_at", "closed_at", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "reference", "status", "escalated_at", "closed_at", "created_at", "updated_at"]

    def validate(self, attrs):
        likelihood = attrs.get("likelihood", getattr(self.instance, "likelihood", None))
        impact = attrs.get("impact", getattr(self.instance, "impact", None))
        residual_l = attrs.get("residual_likelihood", getattr(self.instance, "residual_likelihood", None))
        residual_i = attrs.get("residual_impact", getattr(self.instance, "residual_impact", None))
        if None not in (likelihood, impact, residual_l, residual_i) and residual_l * residual_i > likelihood * impact:
            raise serializers.ValidationError(
                {"residual_likelihood": "Residual risk cannot exceed inherent risk. Controls do not make a risk worse."}
            )
        return attrs


class RiskStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=[m.RiskStatus.OPEN, m.RiskStatus.MONITORING, m.RiskStatus.CLOSED])
    comment = serializers.CharField(required=False, allow_blank=True, max_length=4000)


# Regulatory findings ------------------------------------------------------


class FindingSerializer(serializers.ModelSerializer):
    recorded_by = PersonSerializer(read_only=True)
    verified_by = PersonSerializer(read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = m.RegulatoryFinding
        fields = [
            "id", "reference", "requirement", "instrument", "authority", "sector", "gap", "source",
            "status", "status_label", "recorded_by", "verified_by", "verified_at", "resolution",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "reference", "status", "verified_at", "resolution", "created_at", "updated_at"]


class ResolutionSerializer(serializers.Serializer):
    resolution = serializers.CharField(max_length=4000)


# Stakeholders and milestones ---------------------------------------------


class StakeholderSerializer(serializers.ModelSerializer):
    owner = OwnerField()
    owner_detail = PersonSerializer(source="owner", read_only=True)

    class Meta:
        model = m.Stakeholder
        fields = [
            "id", "name", "category", "influence", "interest", "owner", "owner_detail",
            "engagement_plan", "last_engaged_on", "is_active", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class MilestoneSerializer(serializers.ModelSerializer):
    owner = OwnerField()
    owner_detail = PersonSerializer(source="owner", read_only=True)
    policy_reference = serializers.CharField(source="policy.reference", read_only=True)
    policy_title = serializers.CharField(source="policy.title", read_only=True)

    class Meta:
        model = m.Milestone
        fields = [
            "id", "policy", "policy_reference", "policy_title", "name", "phase", "owner", "owner_detail",
            "start_date", "end_date", "progress", "kpi", "status", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate(self, attrs):
        start = attrs.get("start_date", getattr(self.instance, "start_date", None))
        end = attrs.get("end_date", getattr(self.instance, "end_date", None))
        if start and end and end < start:
            raise serializers.ValidationError({"end_date": "The end date cannot be before the start date."})
        progress = attrs.get("progress", getattr(self.instance, "progress", 0))
        status = attrs.get("status", getattr(self.instance, "status", m.MilestoneStatus.NOT_STARTED))
        if status == m.MilestoneStatus.COMPLETE and progress != 100:
            raise serializers.ValidationError({"progress": "A complete milestone must be at 100 percent."})
        if status == m.MilestoneStatus.NOT_STARTED and progress != 0:
            raise serializers.ValidationError({"progress": "A milestone that has not started is at 0 percent."})
        return attrs


# Impact assessments -------------------------------------------------------


class ScenarioSerializer(serializers.ModelSerializer):
    net = serializers.SerializerMethodField()
    benefit_cost_ratio = serializers.SerializerMethodField()

    class Meta:
        model = m.ImpactScenario
        fields = [
            "id", "assessment", "name", "description", "cost", "benefit", "net", "benefit_cost_ratio",
            "verified", "is_baseline", "scores", "position", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_net(self, scenario):
        return str(scenario.benefit - scenario.cost)

    def get_benefit_cost_ratio(self, scenario):
        return None if not scenario.cost else str(round(scenario.benefit / scenario.cost, 2))

    def validate_scores(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("Scores are a map of category to a 1 to 5 score.")
        unknown = set(value) - set(m.IMPACT_CATEGORIES)
        if unknown:
            raise serializers.ValidationError(f"Unknown categories: {', '.join(sorted(unknown))}.")
        for key, score in value.items():
            if not isinstance(score, int) or isinstance(score, bool) or not 1 <= score <= 5:
                raise serializers.ValidationError(f"The {key} score must be a whole number from 1 to 5.")
        return value


class AssessmentSerializer(serializers.ModelSerializer):
    scenarios = ScenarioSerializer(many=True, read_only=True)
    created_by = PersonSerializer(read_only=True)
    policy_reference = serializers.CharField(source="policy.reference", read_only=True, default=None)
    categories = serializers.SerializerMethodField()

    class Meta:
        model = m.ImpactAssessment
        fields = [
            "id", "reference", "title", "policy", "policy_reference", "currency", "unit", "horizon_years",
            "discount_rate", "assumptions", "scenarios", "categories", "created_by", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "reference", "created_at", "updated_at"]

    def get_categories(self, _assessment):
        return m.IMPACT_CATEGORIES


# Documents ----------------------------------------------------------------

ALLOWED_UPLOADS = {
    ".pdf": {"application/pdf"},
    ".doc": {"application/msword"},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    ".xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
    ".csv": {"text/csv", "application/vnd.ms-excel"},
    ".jpg": {"image/jpeg"},
    ".jpeg": {"image/jpeg"},
    ".png": {"image/png"},
}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


class DocumentSerializer(serializers.ModelSerializer):
    uploaded_by = PersonSerializer(read_only=True)
    file = serializers.FileField(write_only=True)

    class Meta:
        model = m.GovernanceDocument
        fields = [
            "id", "name", "document_type", "classification", "file", "original_filename", "size",
            "content_type", "policy", "risk", "finding", "uploaded_by", "created_at",
        ]
        read_only_fields = ["id", "original_filename", "size", "content_type", "created_at"]

    def validate_file(self, value):
        suffix = Path(value.name).suffix.lower()
        if suffix not in ALLOWED_UPLOADS or value.content_type not in ALLOWED_UPLOADS[suffix]:
            raise serializers.ValidationError("Use PDF, Word, Excel, CSV, JPEG or PNG with the matching content type.")
        if not 0 < value.size <= MAX_UPLOAD_BYTES:
            raise serializers.ValidationError("Files must be non-empty and no larger than 20 MB.")
        return value

    def validate_classification(self, value):
        request = self.context.get("request")
        if request and value not in perms.classifications(request.user):
            raise serializers.ValidationError("You cannot file a document above your own classification level.")
        return value


# Access -------------------------------------------------------------------


class MemberSerializer(serializers.ModelSerializer):
    user = PersonSerializer(read_only=True)
    role_label = serializers.CharField(source="get_role_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    decided_by = PersonSerializer(read_only=True)

    class Meta:
        model = m.GovernanceMember
        fields = [
            "id", "user", "role", "role_label", "status", "status_label", "organisation", "job_title",
            "profile", "scope", "decided_by", "decided_at", "decision_notes", "created_at",
        ]


class MemberDecisionSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=m.MemberStatus.choices)
    role = serializers.ChoiceField(choices=m.GovernanceRole.choices, required=False)
    notes = serializers.CharField(required=False, allow_blank=True, max_length=2000)


class InvitationSerializer(serializers.ModelSerializer):
    role_label = serializers.CharField(source="get_role_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    invited_by = PersonSerializer(read_only=True)

    class Meta:
        model = m.GovernanceInvitation
        fields = [
            "id", "email", "name", "organisation", "role", "role_label", "status", "status_label",
            "expires_at", "sent_count", "last_sent_at", "invited_by", "accepted_at", "created_at",
        ]
        read_only_fields = fields


class InvitationCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    name = serializers.CharField(max_length=200)
    organisation = serializers.CharField(max_length=255)
    role = serializers.ChoiceField(choices=m.GovernanceRole.choices)


class InvitationTokenSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=200)


DECLARATIONS = ["conflict_of_interest", "confidentiality", "code_of_conduct"]


class AccessRequestSerializer(serializers.Serializer):
    organisation = serializers.CharField(max_length=255)
    job_title = serializers.CharField(max_length=200)
    requested_role = serializers.ChoiceField(
        choices=[m.GovernanceRole.ANALYST, m.GovernanceRole.REVIEWER, m.GovernanceRole.VIEWER]
    )
    profile = serializers.DictField(child=serializers.CharField(max_length=2000, allow_blank=True))
    scope = serializers.ListField(child=serializers.CharField(max_length=100), max_length=40)
    declarations = serializers.DictField(child=serializers.BooleanField())

    def validate_profile(self, value):
        if len(value) > 40:
            raise serializers.ValidationError("Too many profile fields.")
        return value

    def validate_declarations(self, value):
        missing = [key for key in DECLARATIONS if not value.get(key)]
        if missing:
            raise serializers.ValidationError("Accept every declaration to request access.")
        return value


class CopilotSerializer(serializers.Serializer):
    question = serializers.CharField(max_length=1000)
