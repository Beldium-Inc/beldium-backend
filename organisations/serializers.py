from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from accounts.serializers import UserSerializer
from organisations.models import JoinRequest, MembershipRole, Organisation, OrganisationInvitation, OrganisationMembership


class OrganisationMembershipSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)

    class Meta:
        model = OrganisationMembership
        fields = ["id", "user", "role", "title", "is_active", "created_at"]
        read_only_fields = ["id", "created_at"]


class OrganisationSerializer(serializers.ModelSerializer):
    my_role = serializers.SerializerMethodField()
    member_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Organisation
        fields = [
            "id", "name", "organisation_type", "registration_number", "tax_identifier",
            "email", "phone_number", "website", "address", "country", "state",
            "verification_status", "my_role", "member_count", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "verification_status", "my_role", "member_count", "created_at", "updated_at"]

    def get_my_role(self, obj):
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return None
        membership = obj.memberships.filter(user=request.user, is_active=True).only("role").first()
        return membership.role if membership else None

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


class JoinRequestSerializer(serializers.ModelSerializer):
    requester = UserSerializer(read_only=True)

    class Meta:
        model = JoinRequest
        fields = [
            "id", "organisation", "requester", "requested_role", "justification",
            "status", "decision_notes", "decided_at", "created_at",
        ]
        read_only_fields = ["id", "requester", "status", "decision_notes", "decided_at", "created_at"]


class JoinRequestDecisionSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=["approved", "rejected"])
    notes = serializers.CharField(required=False, allow_blank=True)


class InvitationAcceptanceSerializer(serializers.Serializer):
    token = serializers.CharField()
