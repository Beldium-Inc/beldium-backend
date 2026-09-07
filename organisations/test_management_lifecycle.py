from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import AccountAuditEvent, User
from organisations.models import JoinRequest, Organisation, OrganisationInvitation, OrganisationMembership


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class OrganisationManagementLifecycleTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner@example.com", "password")
        self.admin_user = User.objects.create_user("platform@example.com", "password", is_staff=True)
        self.member_user = User.objects.create_user("member@example.com", "password")
        self.organisation = Organisation.objects.create(name="Compliance Partners", organisation_type="compliance_partner")
        self.owner_membership = OrganisationMembership.objects.create(
            organisation=self.organisation, user=self.owner, role="owner"
        )
        self.membership = OrganisationMembership.objects.create(
            organisation=self.organisation, user=self.member_user, role="member"
        )

    def test_beldium_id_is_generated(self):
        self.assertTrue(self.organisation.beldium_id.startswith("BLD-ORG-"))

    def test_member_role_update_suspend_activate_and_remove(self):
        self.client.force_authenticate(self.owner)
        updated = self.client.patch(
            reverse("organisation-update-member", args=[self.organisation.id, self.membership.id]),
            {"role": "environmental_specialist", "title": "Lead Environmental Specialist"},
        )
        self.assertEqual(updated.status_code, status.HTTP_200_OK)
        self.assertIn("reviews.contribute", updated.data["permissions"])

        suspended = self.client.post(
            reverse("organisation-suspend-member", args=[self.organisation.id, self.membership.id])
        )
        self.assertEqual(suspended.status_code, status.HTTP_200_OK)
        self.assertFalse(suspended.data["is_active"])

        activated = self.client.post(
            reverse("organisation-activate-member", args=[self.organisation.id, self.membership.id])
        )
        self.assertEqual(activated.status_code, status.HTTP_200_OK)
        self.assertTrue(activated.data["is_active"])

        removed = self.client.delete(
            reverse("organisation-remove-member", args=[self.organisation.id, self.membership.id])
        )
        self.assertEqual(removed.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(OrganisationMembership.objects.filter(id=self.membership.id).exists())

    def test_owner_cannot_be_suspended_or_removed(self):
        self.client.force_authenticate(self.owner)
        suspended = self.client.post(
            reverse("organisation-suspend-member", args=[self.organisation.id, self.owner_membership.id])
        )
        removed = self.client.delete(
            reverse("organisation-remove-member", args=[self.organisation.id, self.owner_membership.id])
        )
        self.assertEqual(suspended.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(removed.status_code, status.HTTP_403_FORBIDDEN)

    def test_my_permissions_returns_role_capabilities(self):
        self.client.force_authenticate(self.member_user)
        response = self.client.get(reverse("organisation-my-permissions", args=[self.organisation.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["role"], "member")
        self.assertIn("applications.view", response.data["permissions"])

    @patch("accounts.services.enqueue_account_email")
    def test_invitation_is_emailed_and_can_be_revoked(self, enqueue):
        self.client.force_authenticate(self.owner)
        created = self.client.post(reverse("organisation-invitations", args=[self.organisation.id]), {
            "email": "invitee@example.com",
            "role": "mining_compliance_officer",
            "expires_at": (timezone.now() + timedelta(days=1)).isoformat(),
        })
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)

        revoked = self.client.post(reverse(
            "organisation-revoke-invitation", args=[self.organisation.id, created.data["id"]]
        ))
        self.assertEqual(revoked.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(OrganisationInvitation.objects.get(id=created.data["id"]).revoked_at)

    def test_join_request_role_can_be_modified_before_approval(self):
        requester = User.objects.create_user("requester@example.com", "password")
        join_request = JoinRequest.objects.create(
            organisation=self.organisation,
            requester=requester,
            requested_role="analyst",
        )
        self.client.force_authenticate(self.owner)
        response = self.client.post(reverse("join-request-decide", args=[join_request.id]), {
            "decision": "approved",
            "role": "read_only",
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        membership = OrganisationMembership.objects.get(organisation=self.organisation, user=requester)
        self.assertEqual(membership.role, "read_only")

    def test_organisation_submission_and_platform_decision(self):
        self.client.force_authenticate(self.owner)
        submitted = self.client.post(reverse("organisation-submit", args=[self.organisation.id]))
        self.assertEqual(submitted.status_code, status.HTTP_200_OK)
        self.assertEqual(submitted.data["verification_status"], "under_review")

        self.client.force_authenticate(self.admin_user)
        approved = self.client.post(reverse("organisation-decide", args=[self.organisation.id]), {
            "decision": "verified"
        })
        self.assertEqual(approved.status_code, status.HTTP_200_OK)
        self.assertEqual(approved.data["verification_status"], "verified")
        self.assertIsNotNone(approved.data["verified_at"])

    def test_rejection_requires_reason(self):
        self.organisation.verification_status = "under_review"
        self.organisation.save(update_fields=["verification_status"])
        self.client.force_authenticate(self.admin_user)
        response = self.client.post(reverse("organisation-decide", args=[self.organisation.id]), {
            "decision": "rejected"
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("reason", response.data["error"]["details"])

    def test_administrator_can_read_organisation_audit_events(self):
        AccountAuditEvent.objects.create(
            actor=self.owner,
            event_type="organisation.updated",
            metadata={"organisation_id": str(self.organisation.id)},
        )
        self.client.force_authenticate(self.owner)
        response = self.client.get(reverse("organisation-audit", args=[self.organisation.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
