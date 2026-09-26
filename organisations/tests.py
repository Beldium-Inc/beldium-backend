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


class OrganisationDedupeTests(APITestCase):
    """Cleanup for duplicate Organisation rows created before the create-time
    guard existed — see organisations/dedupe.py."""

    def setUp(self):
        self.staff = User.objects.create_user("dedupe-staff@example.com", "SafePassword-2026!", is_staff=True)
        self.member = User.objects.create_user("dedupe-member@example.com", "SafePassword-2026!")

    def test_non_staff_cannot_run_dedupe(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(reverse("organisation-dedupe-duplicates"), {})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_dry_run_reports_but_does_not_delete(self):
        first = Organisation.objects.create(name="Cosmaris Industries", organisation_type="compliance_partner")
        second = Organisation.objects.create(name="cosmaris industries", organisation_type="compliance_partner")
        OrganisationMembership.objects.create(organisation=second, user=self.member, role="owner")

        self.client.force_authenticate(self.staff)
        response = self.client.post(reverse("organisation-dedupe-duplicates"), {})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["organisations_removed"], 1)
        self.assertEqual(response.data["groups"][0]["keeper_id"], str(first.id))
        self.assertFalse(response.data["applied"])
        self.assertEqual(Organisation.objects.count(), 2)

    def test_apply_deletes_losers_and_their_orphanable_sites_and_applications(self):
        from mining.models import Application as MiningApplication
        from mining.models import MineSite

        keeper = Organisation.objects.create(
            name="Favvy Miners LTD", organisation_type="mining_company", verification_status="verified"
        )
        loser = Organisation.objects.create(name="Favvy Miners LTD", organisation_type="mining_company")
        MineSite.objects.create(organisation=loser, name="Eton", mineral="Lithium")
        MiningApplication.objects.create(organisation=loser, site_name="Eton")

        self.client.force_authenticate(self.staff)
        response = self.client.post(reverse("organisation-dedupe-duplicates"), {"apply": True})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["applied"])
        self.assertEqual(response.data["organisations_removed"], 1)

        self.assertTrue(Organisation.objects.filter(id=keeper.id).exists())
        self.assertFalse(Organisation.objects.filter(id=loser.id).exists())
        self.assertFalse(MineSite.objects.filter(name="Eton").exists())
        self.assertFalse(MiningApplication.objects.filter(site_name="Eton").exists())

    def test_a_verified_organisation_is_always_the_keeper(self):
        older = Organisation.objects.create(name="Kaduna Minerals", organisation_type="mining_company")
        verified_but_newer = Organisation.objects.create(
            name="Kaduna Minerals", organisation_type="mining_company", verification_status="verified"
        )

        self.client.force_authenticate(self.staff)
        response = self.client.post(reverse("organisation-dedupe-duplicates"), {"apply": True})
        self.assertEqual(response.data["groups"][0]["keeper_id"], str(verified_but_newer.id))
        self.assertFalse(Organisation.objects.filter(id=older.id).exists())
        self.assertTrue(Organisation.objects.filter(id=verified_but_newer.id).exists())

    def test_two_verified_duplicates_are_skipped_not_guessed_at(self):
        Organisation.objects.create(name="Ambiguous Co", organisation_type="mining_company", verification_status="verified")
        Organisation.objects.create(name="Ambiguous Co", organisation_type="mining_company", verification_status="verified")

        self.client.force_authenticate(self.staff)
        response = self.client.post(reverse("organisation-dedupe-duplicates"), {"apply": True})
        self.assertEqual(response.data["groups"], [])
        self.assertEqual(len(response.data["skipped_ambiguous"]), 1)
        self.assertEqual(Organisation.objects.filter(name="Ambiguous Co").count(), 2)


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


class PlatformStatsTests(APITestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()

    def test_anonymous_caller_gets_only_the_verified_count(self):
        Organisation.objects.create(name="A", organisation_type="mining_company", verification_status="verified")
        Organisation.objects.create(name="B", organisation_type="compliance_partner", verification_status="verified")
        Organisation.objects.create(name="C", organisation_type="mining_company", verification_status="draft")

        response = self.client.get(reverse("platform-stats"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json(), {"verified_organisations": 2})

    def test_a_stale_bearer_token_does_not_break_the_public_page(self):
        response = self.client.get(reverse("platform-stats"), HTTP_AUTHORIZATION="Bearer not-a-real-token")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
