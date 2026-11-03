from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import User
from accounts.tasks import (
    send_join_request_decided_email,
    send_join_request_received_email,
    send_join_request_submitted_email,
)
from organisations.models import (
    JOIN_CODE_ALPHABET,
    JOIN_CODE_LENGTH,
    JoinRequest,
    Organisation,
    OrganisationMembership,
)


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    FRONTEND_URL="https://compliance.example.test",
)
class JoinCodeTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user("owner@example.com", "password", first_name="Ada")
        self.admin = User.objects.create_user("admin@example.com", "password")
        self.member = User.objects.create_user("member@example.com", "password")
        self.applicant = User.objects.create_user("applicant@example.com", "password", first_name="Bola")
        self.organisation = Organisation.objects.create(
            name="Jos Assay Partners", organisation_type="compliance_partner", verification_status="verified",
        )
        self.other = Organisation.objects.create(name="Other Partners", organisation_type="compliance_partner")
        OrganisationMembership.objects.create(organisation=self.organisation, user=self.owner, role="owner")
        OrganisationMembership.objects.create(organisation=self.organisation, user=self.admin, role="admin")
        OrganisationMembership.objects.create(organisation=self.organisation, user=self.member, role="member")

    def code_url(self, organisation=None):
        return reverse("organisation-join-code", args=[(organisation or self.organisation).id])

    def rotate_url(self, organisation=None):
        return reverse("organisation-rotate-join-code", args=[(organisation or self.organisation).id])

    # The code itself

    def test_every_organisation_gets_a_readable_code(self):
        code = self.organisation.join_code
        self.assertEqual(len(code), JOIN_CODE_LENGTH)
        self.assertTrue(all(ch in JOIN_CODE_ALPHABET for ch in code))
        self.assertNotEqual(code, self.other.join_code)

    def test_admin_reads_code_and_link(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get(self.code_url())
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["join_code"], self.organisation.join_code)
        self.assertEqual(
            response.data["join_url"],
            f"https://compliance.example.test/onboarding/join?code={self.organisation.join_code}",
        )

    def test_plain_member_cannot_read_code(self):
        self.client.force_authenticate(self.member)
        response = self.client.get(self.code_url())
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_of_another_organisation_cannot_read_code(self):
        # The wrong owner: an admin elsewhere cannot see this organisation at all.
        OrganisationMembership.objects.create(organisation=self.other, user=self.applicant, role="owner")
        self.client.force_authenticate(self.applicant)
        response = self.client.get(self.code_url())
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_rotating_replaces_the_code_and_old_code_stops_working(self):
        old = self.organisation.join_code
        self.client.force_authenticate(self.owner)
        response = self.client.post(self.rotate_url())
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.organisation.refresh_from_db()
        self.assertNotEqual(self.organisation.join_code, old)
        self.assertEqual(response.data["join_code"], self.organisation.join_code)

        self.client.force_authenticate(self.applicant)
        stale = self.client.post(reverse("join-request-lookup"), {"join_code": old})
        self.assertEqual(stale.status_code, status.HTTP_404_NOT_FOUND)

    def test_member_cannot_rotate_code(self):
        old = self.organisation.join_code
        self.client.force_authenticate(self.member)
        response = self.client.post(self.rotate_url())
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.organisation.refresh_from_db()
        self.assertEqual(self.organisation.join_code, old)

    # Lookup

    def test_lookup_accepts_code_as_people_type_it(self):
        code = self.organisation.join_code
        typed = f" {code[:4].lower()}-{code[4:]} "
        self.client.force_authenticate(self.applicant)
        response = self.client.post(reverse("join-request-lookup"), {"join_code": typed})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["organisation_name"], "Jos Assay Partners")
        self.assertFalse(response.data["already_member"])
        self.assertFalse(response.data["pending_request"])

    def test_lookup_works_before_sign_in_and_reveals_only_name_and_type(self):
        response = self.client.post(reverse("join-request-lookup"), {"join_code": self.organisation.join_code})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            set(response.data),
            {"organisation", "organisation_name", "organisation_type", "already_member", "pending_request"},
        )
        self.assertEqual(response.data["organisation_name"], "Jos Assay Partners")
        self.assertFalse(response.data["already_member"])

    def test_lookup_before_sign_in_is_rate_limited(self):
        statuses = {
            self.client.post(reverse("join-request-lookup"), {"join_code": "WRONG123"}).status_code
            for _ in range(35)
        }
        self.assertIn(status.HTTP_429_TOO_MANY_REQUESTS, statuses)

    def test_sending_a_request_still_requires_sign_in(self):
        response = self.client.post(reverse("join-request-list"), {"join_code": self.organisation.join_code})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(JoinRequest.objects.exists())

    def test_lookup_hides_suspended_organisation(self):
        self.organisation.verification_status = "suspended"
        self.organisation.save(update_fields=["verification_status"])
        self.client.force_authenticate(self.applicant)
        response = self.client.post(reverse("join-request-lookup"), {"join_code": self.organisation.join_code})
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_lookup_is_rate_limited(self):
        self.client.force_authenticate(self.applicant)
        statuses = {
            self.client.post(reverse("join-request-lookup"), {"join_code": "WRONG123"}).status_code
            for _ in range(35)
        }
        self.assertIn(status.HTTP_429_TOO_MANY_REQUESTS, statuses)

    # Asking to join with the code

    @patch("organisations.views.enqueue_account_email")
    def test_request_with_code_notifies_admins_and_requester(self, enqueue):
        self.client.force_authenticate(self.applicant)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("join-request-list"), {
                "join_code": self.organisation.join_code,
                "requested_role": "reviewer",
                "justification": "I lead the sampling team.",
            })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        join_request = JoinRequest.objects.get()
        self.assertEqual(join_request.organisation, self.organisation)
        self.assertEqual(join_request.requester, self.applicant)
        self.assertNotIn("join_code", response.data)

        sent = [(c.args[0], c.args[1]) for c in enqueue.call_args_list]
        self.assertIn(("send_join_request_received_email", "owner@example.com"), sent)
        self.assertIn(("send_join_request_received_email", "admin@example.com"), sent)
        self.assertNotIn(("send_join_request_received_email", "member@example.com"), sent)
        self.assertIn(("send_join_request_submitted_email", "applicant@example.com"), sent)

    def test_request_with_wrong_code_is_refused(self):
        self.client.force_authenticate(self.applicant)
        response = self.client.post(reverse("join-request-list"), {"join_code": "AAAAAAAA"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(JoinRequest.objects.exists())

    def test_code_and_organisation_must_match(self):
        # The client cannot pair a valid code with a different organisation id.
        self.client.force_authenticate(self.applicant)
        response = self.client.post(reverse("join-request-list"), {
            "join_code": self.organisation.join_code,
            "organisation": str(self.other.id),
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(JoinRequest.objects.exists())

    def test_request_needs_code_or_organisation(self):
        self.client.force_authenticate(self.applicant)
        response = self.client.post(reverse("join-request-list"), {"justification": "Please let me in."})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("organisations.views.enqueue_account_email")
    def test_second_pending_request_is_refused_cleanly(self, _enqueue):
        self.client.force_authenticate(self.applicant)
        first = self.client.post(reverse("join-request-list"), {"join_code": self.organisation.join_code})
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        second = self.client.post(reverse("join-request-list"), {"join_code": self.organisation.join_code})
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(JoinRequest.objects.count(), 1)

    def test_owner_and_admin_roles_cannot_be_requested_with_code(self):
        self.client.force_authenticate(self.applicant)
        response = self.client.post(reverse("join-request-list"), {
            "join_code": self.organisation.join_code,
            "requested_role": "admin",
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # Deciding

    @patch("organisations.views.enqueue_account_email")
    def test_approval_grants_membership_and_emails_requester(self, enqueue):
        join_request = JoinRequest.objects.create(
            organisation=self.organisation, requester=self.applicant, requested_role="reviewer",
        )
        self.client.force_authenticate(self.admin)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("join-request-decide", args=[join_request.id]),
                {"decision": "approved", "notes": "Welcome aboard."},
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(OrganisationMembership.objects.filter(
            organisation=self.organisation, user=self.applicant, role="reviewer", is_active=True,
        ).exists())
        enqueue.assert_called_once_with(
            "send_join_request_decided_email",
            "applicant@example.com", "Bola", "Jos Assay Partners", True, "Reviewer", "Welcome aboard.",
        )

    @patch("organisations.views.enqueue_account_email")
    def test_decline_emails_requester_and_grants_nothing(self, enqueue):
        join_request = JoinRequest.objects.create(organisation=self.organisation, requester=self.applicant)
        self.client.force_authenticate(self.owner)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("join-request-decide", args=[join_request.id]), {"decision": "rejected"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(OrganisationMembership.objects.filter(user=self.applicant).exists())
        self.assertEqual(enqueue.call_args.args[0], "send_join_request_decided_email")
        self.assertFalse(enqueue.call_args.args[4])

    @patch("organisations.views.enqueue_account_email")
    def test_admin_of_another_organisation_cannot_decide(self, enqueue):
        outsider_admin = User.objects.create_user("elsewhere@example.com", "password")
        OrganisationMembership.objects.create(organisation=self.other, user=outsider_admin, role="owner")
        join_request = JoinRequest.objects.create(organisation=self.organisation, requester=self.applicant)
        self.client.force_authenticate(outsider_admin)
        response = self.client.post(reverse("join-request-decide", args=[join_request.id]), {"decision": "approved"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        join_request.refresh_from_db()
        self.assertEqual(join_request.status, "pending")
        enqueue.assert_not_called()


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    FRONTEND_URL="https://compliance.example.test",
)
class JoinRequestEmailTests(APITestCase):
    def test_admin_email_links_to_review_page(self):
        send_join_request_received_email("owner@example.com", "Jos Assay Partners", "Bola Ade", "bola@example.com", "Reviewer")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Bola Ade", mail.outbox[0].subject)
        self.assertIn("https://compliance.example.test/onboarding/admin", mail.outbox[0].alternatives[0][0])

    def test_requester_confirmation_email(self):
        send_join_request_submitted_email("bola@example.com", "Bola", "Jos Assay Partners", "Reviewer")
        self.assertEqual(mail.outbox[0].to, ["bola@example.com"])
        self.assertIn("Jos Assay Partners", mail.outbox[0].body)

    def test_decision_emails_say_accepted_or_declined(self):
        send_join_request_decided_email("bola@example.com", "Bola", "Jos Assay Partners", True, "Reviewer", "")
        send_join_request_decided_email("bola@example.com", "Bola", "Jos Assay Partners", False, "Reviewer", "Not on our staff list.")
        self.assertIn("now have access", mail.outbox[0].subject)
        self.assertIn("declined", mail.outbox[1].subject)
        self.assertIn("Not on our staff list.", mail.outbox[1].body)
