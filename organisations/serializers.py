from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from accounts.models import AccountAuditEvent
from accounts.serializers import UserSerializer
from organisations.role_permissions import permissions_for_role
from organisations.models import JoinRequest, MembershipRole, Organisation, OrganisationInvitation, OrganisationMembership


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


class JoinRequestSerializer(serializers.ModelSerializer):
    requester = UserSerializer(read_only=True)
    organisation_name = serializers.CharField(source="organisation.name", read_only=True)

    class Meta:
        model = JoinRequest
        fields = [
            "id", "organisation", "organisation_name", "requester", "requested_role",
            "job_title", "employee_id", "justification",
            "status", "decision_notes", "decided_at", "created_at",
        ]
        read_only_fields = [
            "id", "organisation_name", "requester", "status", "decision_notes", "decided_at", "created_at",
        ]

    def validate_requested_role(self, value):
        if value in {MembershipRole.OWNER, MembershipRole.ADMIN}:
            raise serializers.ValidationError("Owner and administrator access cannot be requested.")
        return value


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
