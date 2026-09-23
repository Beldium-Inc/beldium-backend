from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import User
from organisations.models import JoinRequest, Organisation, OrganisationInvitation, OrganisationMembership


class OrganisationAPITests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner@example.com", "SafePassword-2026!")
        self.member = User.objects.create_user("member@example.com", "SafePassword-2026!")

    def test_creator_becomes_owner_and_other_users_cannot_see_private_org(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(reverse("organisation-list"), {
            "name": "Nasarawa Lithium Minerals Ltd",
            "organisation_type": "mining_company",
            "registration_number": "RC-1428903",
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        organisation = Organisation.objects.get()
        self.assertTrue(OrganisationMembership.objects.filter(
            organisation=organisation, user=self.owner, role="owner"
        ).exists())

        self.client.force_authenticate(self.member)
        response = self.client.get(reverse("organisation-list"))
        self.assertEqual(response.data["count"], 0)

    def test_cannot_create_duplicate_organisation_by_name_and_type(self):
        Organisation.objects.create(name="Cosmaris Industries", organisation_type="compliance_partner")

        self.client.force_authenticate(self.member)
        response = self.client.post(reverse("organisation-list"), {
            "name": "cosmaris industries",  # case-insensitive match
            "organisation_type": "compliance_partner",
            "registration_number": "RC-9988776",
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["error"]["details"]["name"][0], (
            "An organisation named \"cosmaris industries\" is already registered or under review. "
            "Ask an existing member to invite you instead of creating a new one."
        ))
        self.assertEqual(Organisation.objects.filter(name__iexact="Cosmaris Industries").count(), 1)

    def test_duplicate_check_ignores_a_different_organisation_type(self):
        Organisation.objects.create(name="Cosmaris Industries", organisation_type="compliance_partner")

        self.client.force_authenticate(self.member)
        response = self.client.post(reverse("organisation-list"), {
            "name": "Cosmaris Industries",
            "organisation_type": "mining_company",
            "registration_number": "RC-1122334",
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_duplicate_check_ignores_a_previously_rejected_organisation(self):
        Organisation.objects.create(
            name="Cosmaris Industries", organisation_type="compliance_partner", verification_status="rejected"
        )

        self.client.force_authenticate(self.member)
        response = self.client.post(reverse("organisation-list"), {
            "name": "Cosmaris Industries",
            "organisation_type": "compliance_partner",
            "registration_number": "RC-5544332",
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_admin_can_approve_join_request(self):
        organisation = Organisation.objects.create(name="Beldium Compliance", organisation_type="compliance_partner")
        OrganisationMembership.objects.create(organisation=organisation, user=self.owner, role="owner")
        join_request = JoinRequest.objects.create(
            organisation=organisation, requester=self.member, requested_role="reviewer"
        )
        self.client.force_authenticate(self.owner)
        response = self.client.post(reverse("join-request-decide", args=[join_request.id]), {"decision": "approved"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(OrganisationMembership.objects.filter(
            organisation=organisation, user=self.member, role="reviewer", is_active=True
        ).exists())

        response = self.client.post(reverse("join-request-decide", args=[join_request.id]), {"decision": "approved"})
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["error"]["code"], "join_request_already_decided")
        self.assertEqual(response.data["status"], "failed")

    def test_invitation_can_only_be_accepted_by_matching_email(self):
        organisation = Organisation.objects.create(name="Federal Regulator", organisation_type="regulator")
        OrganisationMembership.objects.create(organisation=organisation, user=self.owner, role="owner")
        invitation = OrganisationInvitation.objects.create(
            organisation=organisation,
            email=self.member.email,
            role="inspector",
            invited_by=self.owner,
            expires_at=timezone.now() + timedelta(days=2),
        )
        self.client.force_authenticate(self.member)
        response = self.client.post(reverse("accept-invitation"), {"token": invitation.token})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(OrganisationMembership.objects.filter(
            organisation=organisation, user=self.member, role="inspector"
        ).exists())

    def test_permission_and_not_found_errors_use_standard_envelope(self):
        organisation = Organisation.objects.create(name="Private Miner", organisation_type="mining_company")
        OrganisationMembership.objects.create(organisation=organisation, user=self.member, role="member")
        self.client.force_authenticate(self.member)

        response = self.client.post(reverse("organisation-invitations", args=[organisation.id]), {
            "email": "another@example.com",
            "role": "member",
            "expires_at": (timezone.now() + timedelta(days=1)).isoformat(),
        })
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["error"]["code"], "permission_denied")

        response = self.client.get(reverse("organisation-detail", args=["00000000-0000-0000-0000-000000000000"]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data["error"]["code"], "not_found")


class OrganisationTimelineTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user("tl-owner@example.com", "SafePassword-2026!")
        self.outsider = User.objects.create_user("tl-out@example.com", "SafePassword-2026!")
        self.staff = User.objects.create_user("tl-staff@example.com", "SafePassword-2026!", is_staff=True)
        self.org = Organisation.objects.create(name="Timeline Mining", organisation_type="mining_company")
        OrganisationMembership.objects.create(organisation=self.org, user=self.owner, role="owner")
        self.url = reverse("organisation-timeline", args=[self.org.id])

    def _stages(self):
        return {s["key"]: s["state"] for s in self.client.get(self.url).data["stages"]}

    def test_draft_organisation_has_current_submission_stage(self):
        self.client.force_authenticate(self.owner)
        stages = self._stages()
        self.assertEqual(stages["submitted"], "current")
        self.assertEqual(stages["decision"], "upcoming")

    def test_submit_then_verify_advances_stages_and_feeds_activity(self):
        self.client.force_authenticate(self.owner)
        self.client.post(reverse("organisation-submit", args=[self.org.id]))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        stages = {s["key"]: s["state"] for s in response.data["stages"]}
        self.assertEqual(stages["submitted"], "complete")
        self.assertEqual(stages["document_review"], "current")
        self.assertEqual(response.data["activity"][0]["event_type"], "organisation.submitted")
        self.assertNotIn("ip_address", response.data["activity"][0])

        self.client.force_authenticate(self.staff)
        self.client.post(reverse("organisation-decide", args=[self.org.id]), {"decision": "verified"})
        self.client.force_authenticate(self.owner)
        stages = self._stages()
        self.assertEqual(set(stages.values()), {"complete"})
        self.assertEqual(list(stages), ["submitted", "document_review", "site_verification", "decision"])

    def test_non_member_cannot_read_timeline(self):
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_404_NOT_FOUND)
