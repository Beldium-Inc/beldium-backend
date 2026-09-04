from datetime import timedelta

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import User
from organisations.models import JoinRequest, Organisation, OrganisationInvitation, OrganisationMembership


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class OrganisationAuthorizationTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner@example.com", "password")
        self.admin = User.objects.create_user("admin@example.com", "password")
        self.member = User.objects.create_user("member@example.com", "password")
        self.outsider = User.objects.create_user("outsider@example.com", "password")
        self.organisation = Organisation.objects.create(
            name="Nasarawa Lithium",
            organisation_type="mining_company",
            registration_number="RC-100",
        )
        OrganisationMembership.objects.create(organisation=self.organisation, user=self.owner, role="owner")
        OrganisationMembership.objects.create(organisation=self.organisation, user=self.admin, role="admin")
        OrganisationMembership.objects.create(organisation=self.organisation, user=self.member, role="member")

    def test_unauthenticated_user_cannot_list_organisations(self):
        response = self.client.get(reverse("organisation-list"))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_outsider_cannot_discover_private_organisation_by_id(self):
        self.client.force_authenticate(self.outsider)
        response = self.client.get(reverse("organisation-detail", args=[self.organisation.id]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_member_cannot_update_organisation_but_admin_can(self):
        url = reverse("organisation-detail", args=[self.organisation.id])
        self.client.force_authenticate(self.member)
        denied = self.client.patch(url, {"name": "Changed by member"})
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(self.admin)
        allowed = self.client.patch(url, {"name": "Changed by admin"})
        self.assertEqual(allowed.status_code, status.HTTP_200_OK)
        self.organisation.refresh_from_db()
        self.assertEqual(self.organisation.name, "Changed by admin")

    def test_only_owner_can_delete_organisation(self):
        url = reverse("organisation-detail", args=[self.organisation.id])
        self.client.force_authenticate(self.admin)
        denied = self.client.delete(url)
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(self.owner)
        allowed = self.client.delete(url)
        self.assertEqual(allowed.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Organisation.objects.filter(id=self.organisation.id).exists())

    def test_directory_returns_only_verified_organisations(self):
        self.organisation.verification_status = "verified"
        self.organisation.save(update_fields=["verification_status"])
        Organisation.objects.create(name="Draft Organisation", organisation_type="regulator")
        self.client.force_authenticate(self.outsider)

        response = self.client.get(reverse("organisation-directory"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], str(self.organisation.id))

    def test_registration_number_is_unique_within_country(self):
        self.client.force_authenticate(self.outsider)
        response = self.client.post(reverse("organisation-list"), {
            "name": "Duplicate",
            "organisation_type": "mining_company",
            "registration_number": "RC-100",
            "country": "Nigeria",
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["error"]["code"], "validation_error")


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class InvitationAndJoinRequestTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner@example.com", "password")
        self.member = User.objects.create_user("member@example.com", "password")
        self.outsider = User.objects.create_user("outsider@example.com", "password")
        self.organisation = Organisation.objects.create(name="Beldium Compliance", organisation_type="compliance_partner")
        OrganisationMembership.objects.create(organisation=self.organisation, user=self.owner, role="owner")
        OrganisationMembership.objects.create(organisation=self.organisation, user=self.member, role="member")

    def create_invitation(self, **overrides):
        values = {
            "organisation": self.organisation,
            "email": self.outsider.email,
            "role": "reviewer",
            "invited_by": self.owner,
            "expires_at": timezone.now() + timedelta(days=1),
        }
        values.update(overrides)
        return OrganisationInvitation.objects.create(**values)

    def test_owner_can_create_invitation_and_token_is_server_generated(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(reverse("organisation-invitations", args=[self.organisation.id]), {
            "email": self.outsider.email,
            "role": "reviewer",
            "token": "attacker-controlled-token",
            "expires_at": (timezone.now() + timedelta(days=1)).isoformat(),
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertNotEqual(response.data["token"], "attacker-controlled-token")

    def test_invitation_expiry_must_be_in_future(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(reverse("organisation-invitations", args=[self.organisation.id]), {
            "email": self.outsider.email,
            "role": "reviewer",
            "expires_at": (timezone.now() - timedelta(seconds=1)).isoformat(),
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("expires_at", response.data["error"]["details"])

    def test_wrong_user_cannot_accept_invitation(self):
        invitation = self.create_invitation()
        self.client.force_authenticate(self.member)
        response = self.client.post(reverse("accept-invitation"), {"token": invitation.token})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(OrganisationMembership.objects.filter(organisation=self.organisation, user=self.outsider).exists())

    def test_expired_revoked_and_used_invitations_are_rejected(self):
        cases = [
            {"expires_at": timezone.now() - timedelta(seconds=1)},
            {"revoked_at": timezone.now()},
            {"accepted_at": timezone.now()},
        ]
        self.client.force_authenticate(self.outsider)
        for values in cases:
            invitation = self.create_invitation(**values)
            response = self.client.post(reverse("accept-invitation"), {"token": invitation.token})
            self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
            self.assertEqual(response.data["error"]["code"], "invitation_invalid")

    def test_member_cannot_decide_join_request(self):
        join_request = JoinRequest.objects.create(organisation=self.organisation, requester=self.outsider)
        self.client.force_authenticate(self.member)
        response = self.client.post(reverse("join-request-decide", args=[join_request.id]), {"decision": "approved"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        join_request.refresh_from_db()
        self.assertEqual(join_request.status, "pending")

    def test_rejection_records_decision_without_creating_membership(self):
        join_request = JoinRequest.objects.create(organisation=self.organisation, requester=self.outsider)
        self.client.force_authenticate(self.owner)
        response = self.client.post(reverse("join-request-decide", args=[join_request.id]), {
            "decision": "rejected",
            "notes": "Credentials could not be confirmed.",
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        join_request.refresh_from_db()
        self.assertEqual(join_request.status, "rejected")
        self.assertEqual(join_request.decided_by, self.owner)
        self.assertFalse(OrganisationMembership.objects.filter(organisation=self.organisation, user=self.outsider).exists())

    def test_existing_member_cannot_submit_join_request(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(reverse("join-request-list"), {
            "organisation": self.organisation.id,
            "requested_role": "member",
        })
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_user_only_lists_own_join_requests(self):
        mine = JoinRequest.objects.create(organisation=self.organisation, requester=self.outsider)
        JoinRequest.objects.create(
            organisation=Organisation.objects.create(name="Regulator", organisation_type="regulator"),
            requester=self.owner,
        )
        self.client.force_authenticate(self.outsider)
        response = self.client.get(reverse("join-request-list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], str(mine.id))
