from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from accounts.models import AccountAuditEvent
from accounts.serializers import UserSerializer
from organisations.role_permissions import permissions_for_role
from organisations.models import (
    JoinRequest,
    MembershipRole,
    Organisation,
    OrganisationInvitation,
    OrganisationMembership,
    normalise_join_code,
)


class OrganisationMembershipSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    permissions = serializers.SerializerMethodField()

    class Meta:
        model = OrganisationMembership
        fields = ["id", "user", "role", "title", "employee_id", "is_active", "suspended_at", "permissions", "created_at"]
        read_only_fields = ["id", "created_at"]

    def get_permissions(self, obj) -> list[str]:
        return permissions_for_role(obj.role)


class OrganisationSerializer(serializers.ModelSerializer):
    my_role = serializers.SerializerMethodField()
    member_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Organisation
        fields = [
            "id", "beldium_id", "name", "organisation_type", "registration_number", "tax_identifier",
            "email", "phone_number", "website", "address", "country", "state",
            "verification_status", "submitted_at", "verified_at", "rejection_reason",
            "my_role", "member_count", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "beldium_id", "verification_status", "submitted_at", "verified_at", "rejection_reason", "my_role", "member_count", "created_at", "updated_at"]

    def get_my_role(self, obj) -> str | None:
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return None
        membership = obj.memberships.filter(user=request.user, is_active=True).only("role").first()
        return membership.role if membership else None

    def validate_organisation_type(self, value):
        # organisation_type drives desk/oversight access elsewhere (see
        # processing.permissions.audience); once set, only the platform may
        # change it, never the organisation's own administrators.
        request = self.context.get("request")
        is_staff = bool(request and request.user.is_authenticated and request.user.is_staff)
        if self.instance and value != self.instance.organisation_type and not is_staff:
            raise serializers.ValidationError(
                "An organisation's type can only be changed by the platform."
            )
        return value

    def validate(self, attrs):
        # The only DB-level duplicate guard is (country, registration_number),
        # and it's skipped entirely when registration_number is blank (see
        # Organisation.Meta.constraints). Without this, onboarding under a new
        # account (or leaving registration number blank) silently mints a
        # fresh Organisation + application chain for the same real company,
        # each sitting at "Under Review" independently with no way to tell
        # they're duplicates. Block it at creation instead of cleaning up after.
        if self.instance is None:
            name = (attrs.get("name") or "").strip()
            organisation_type = attrs.get("organisation_type")
            if name and organisation_type:
                duplicate = Organisation.objects.filter(
                    name__iexact=name,
                    organisation_type=organisation_type,
                ).exclude(verification_status="rejected")
                if duplicate.exists():
                    raise serializers.ValidationError(
                        {
                            "name": (
                                "An organisation named "
                                f"\"{name}\" is already registered or under review. "
                                "Ask an existing member to invite you instead of creating a new one."
                            )
                        }
                    )
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        organisation = super().create(validated_data)
        OrganisationMembership.objects.create(
            organisation=organisation,
            user=self.context["request"].user,
            role=MembershipRole.OWNER,
        )
        return organisation


class OrganisationInvitationSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrganisationInvitation
        fields = ["id", "organisation", "email", "role", "token", "expires_at", "accepted_at", "revoked_at", "created_at"]
        read_only_fields = ["id", "organisation", "token", "accepted_at", "revoked_at", "created_at"]

    def validate_expires_at(self, value):
        if value <= timezone.now():
            raise serializers.ValidationError("Expiry must be in the future.")
        return value

    def validate_role(self, value):
        if value == MembershipRole.OWNER:
            raise serializers.ValidationError("Ownership cannot be granted through an invitation.")
        return value


INVALID_JOIN_CODE_MESSAGE = "That join code is not valid. Check it with the organisation that shared it."


def organisation_for_join_code(value):
    """The organisation a join code belongs to, or None.

    Rejected and suspended organisations cannot take new members, so their
    codes are treated as unknown rather than revealing that they exist.
    """
    code = normalise_join_code(value)
    if not code:
        return None
    return (
        Organisation.objects.filter(join_code=code)
        .exclude(verification_status__in=["rejected", "suspended"])
        .first()
    )


class JoinRequestSerializer(serializers.ModelSerializer):
    requester = UserSerializer(read_only=True)
    organisation_name = serializers.CharField(source="organisation.name", read_only=True)
    # The usual way in: the code or link an organisation administrator shared.
    join_code = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = JoinRequest
        fields = [
            "id", "organisation", "organisation_name", "requester", "requested_role",
            "job_title", "employee_id", "justification", "join_code",
            "status", "decision_notes", "decided_at", "created_at",
        ]
        read_only_fields = [
            "id", "organisation_name", "requester", "status", "decision_notes", "decided_at", "created_at",
        ]
        extra_kwargs = {"organisation": {"required": False}}

    def validate_requested_role(self, value):
        if value in {MembershipRole.OWNER, MembershipRole.ADMIN}:
            raise serializers.ValidationError("Owner and administrator access cannot be requested.")
        return value

    def validate(self, attrs):
        code = attrs.pop("join_code", "")
        if code:
            organisation = organisation_for_join_code(code)
            if organisation is None:
                raise serializers.ValidationError({"join_code": [INVALID_JOIN_CODE_MESSAGE]})
            if attrs.get("organisation") and attrs["organisation"].pk != organisation.pk:
                raise serializers.ValidationError({"join_code": [INVALID_JOIN_CODE_MESSAGE]})
            attrs["organisation"] = organisation
        if not attrs.get("organisation"):
            raise serializers.ValidationError({"join_code": ["Enter the join code your organisation shared with you."]})
        request = self.context.get("request")
        if request and JoinRequest.objects.filter(
            organisation=attrs["organisation"], requester=request.user, status="pending"
        ).exists():
            raise serializers.ValidationError(
                {"organisation": ["You already have a request waiting for this organisation's administrators."]}
            )
        return attrs


class JoinCodeLookupSerializer(serializers.Serializer):
    join_code = serializers.CharField()


class JoinRequestDecisionSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=["approved", "rejected"])
    notes = serializers.CharField(required=False, allow_blank=True)
    role = serializers.ChoiceField(choices=MembershipRole.choices, required=False)


class InvitationAcceptanceSerializer(serializers.Serializer):
    token = serializers.CharField()


class MembershipUpdateSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=MembershipRole.choices, required=False)
    title = serializers.CharField(max_length=150, required=False, allow_blank=True)
    employee_id = serializers.CharField(max_length=100, required=False, allow_blank=True)


class OrganisationDecisionSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=["verified", "rejected"])
    reason = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        if attrs["decision"] == "rejected" and not attrs.get("reason"):
            raise serializers.ValidationError({"reason": ["A rejection reason is required."]})
        return attrs


class AccountAuditEventSerializer(serializers.ModelSerializer):
    actor_email = serializers.EmailField(source="actor.email", read_only=True)

    class Meta:
        model = AccountAuditEvent
        fields = ["id", "event_type", "actor_email", "ip_address", "metadata", "created_at"]
