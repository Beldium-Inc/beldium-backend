"""Section 2 rights table on the organisation endpoints (BE-O4, BE-O7)."""
from datetime import timedelta

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import User
from organisations.models import (
    JoinRequest,
    MembershipRole,
    Organisation,
    OrganisationInvitation,
    OrganisationMembership,
)


def make_user(email, **extra):
    return User.objects.create_user(email, "password", **extra)


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class RoleTestCase(APITestCase):
    def setUp(self):
        cache.clear()
        self.org = Organisation.objects.create(name="Jos Processing Ltd", organisation_type="processing_company")
        self.owner = make_user("owner@jos.test", first_name="Ada", last_name="Obi")
        self.admin = make_user("admin@jos.test")
        self.staff = make_user("staff@jos.test", first_name="Bola")
        self.owner_m = OrganisationMembership.objects.create(organisation=self.org, user=self.owner, role=MembershipRole.OWNER)
        self.admin_m = OrganisationMembership.objects.create(organisation=self.org, user=self.admin, role=MembershipRole.ADMIN)
        self.staff_m = OrganisationMembership.objects.create(organisation=self.org, user=self.staff, role=MembershipRole.MEMBER)
        self.applicant = make_user("applicant@jos.test")
        self.join_request = JoinRequest.objects.create(organisation=self.org, requester=self.applicant)
        self.other_org = Organisation.objects.create(name="Kano Processing Ltd", organisation_type="processing_company")
        self.outsider = make_user("outsider@kano.test")
        OrganisationMembership.objects.create(organisation=self.other_org, user=self.outsider, role=MembershipRole.OWNER)

    def url(self, name, *args):
        return reverse(name, args=[self.org.id, *args])


class JoinRequestCancelTests(RoleTestCase):
    def cancel(self, user, join_request=None):
        self.client.force_authenticate(user)
        return self.client.post(reverse("join-request-cancel", args=[(join_request or self.join_request).id]))

    def test_requester_cancels_a_pending_request(self):
        response = self.cancel(self.applicant)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "cancelled")
        self.join_request.refresh_from_db()
        self.assertEqual(self.join_request.status, "cancelled")

    def test_only_a_pending_request_can_be_cancelled(self):
        self.cancel(self.applicant)
        self.assertEqual(self.cancel(self.applicant).status_code, 409)

    def test_another_user_cannot_cancel(self):
        for user in (self.owner, self.outsider):
            self.assertEqual(self.cancel(user).status_code, 404)
        self.join_request.refresh_from_db()
        self.assertEqual(self.join_request.status, "pending")

    def test_a_cancelled_request_cannot_be_approved(self):
        self.cancel(self.applicant)
        self.client.force_authenticate(self.owner)
        response = self.client.post(reverse("join-request-decide", args=[self.join_request.id]), {"decision": "approved"})
        self.assertEqual(response.status_code, 409)
        self.assertFalse(OrganisationMembership.objects.filter(user=self.applicant).exists())


class TeamVisibilityTests(RoleTestCase):
    def test_admin_sees_member_emails(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get(self.url("organisation-members"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("staff@jos.test", [row["user"]["email"] for row in response.data])

    def test_staff_see_names_and_roles_only(self):
        self.client.force_authenticate(self.staff)
        response = self.client.get(self.url("organisation-members"))
        self.assertEqual(response.status_code, 200)
        for row in response.data:
            self.assertNotIn("email", row["user"])
            self.assertNotIn("phone_number", row["user"])
            self.assertIn("role", row)
        self.assertNotIn("staff@jos.test", str(response.data))

    def test_pending_member_sees_no_company_data(self):
        self.client.force_authenticate(self.applicant)
        self.assertEqual(self.client.get(self.url("organisation-members")).status_code, 404)
        self.assertEqual(self.client.get(self.url("organisation-detail")).status_code, 404)
        self.assertEqual(self.client.get(self.url("organisation-timeline")).status_code, 404)
        response = self.client.get(reverse("organisation-list"))
        self.assertEqual(response.data["count"] if "count" in response.data else len(response.data), 0)

    def test_suspended_member_sees_no_company_data(self):
        self.staff_m.is_active = False
        self.staff_m.save()
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.get(self.url("organisation-members")).status_code, 404)

    def test_other_organisations_member_is_refused(self):
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get(self.url("organisation-members")).status_code, 404)
        self.assertEqual(self.client.get(self.url("organisation-join-code")).status_code, 404)
        response = self.client.post(reverse("join-request-decide", args=[self.join_request.id]), {"decision": "approved"})
        self.assertEqual(response.status_code, 403)


class StaffRightsTests(RoleTestCase):
    """Staff cannot approve, invite, change roles, remove, see the code or submit."""

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.staff)

    def test_staff_cannot_invite(self):
        response = self.client.post(self.url("organisation-invitations"), {
            "email": "new@jos.test", "role": "member", "expires_at": (timezone.now() + timedelta(days=3)).isoformat(),
        })
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get(self.url("organisation-invitations")).status_code, 403)

    def test_staff_cannot_see_or_rotate_the_join_code(self):
        self.assertEqual(self.client.get(self.url("organisation-join-code")).status_code, 403)
        self.assertEqual(self.client.post(self.url("organisation-rotate-join-code")).status_code, 403)

    def test_staff_cannot_review_or_decide_join_requests(self):
        self.assertEqual(self.client.get(self.url("organisation-join-requests")).status_code, 403)
        response = self.client.post(reverse("join-request-decide", args=[self.join_request.id]), {"decision": "approved"})
        self.assertEqual(response.status_code, 403)

    def test_staff_cannot_change_roles_suspend_or_remove(self):
        other = make_user("other@jos.test")
        membership = OrganisationMembership.objects.create(organisation=self.org, user=other, role=MembershipRole.MEMBER)
        self.assertEqual(self.client.patch(self.url("organisation-update-member", membership.id), {"role": "admin"}).status_code, 403)
        self.assertEqual(self.client.post(self.url("organisation-suspend-member", membership.id)).status_code, 403)
        self.assertEqual(self.client.delete(self.url("organisation-remove-member", membership.id)).status_code, 403)
        membership.refresh_from_db()
        self.assertEqual(membership.role, MembershipRole.MEMBER)
        self.assertTrue(membership.is_active)

    def test_staff_cannot_submit_or_edit_the_profile(self):
        self.assertEqual(self.client.post(self.url("organisation-submit")).status_code, 403)
        self.assertEqual(self.client.patch(self.url("organisation-detail"), {"name": "Renamed"}).status_code, 403)
        self.org.refresh_from_db()
        self.assertEqual(self.org.verification_status, "draft")


class LastAdminTests(RoleTestCase):
    """An organisation must always keep one active owner or administrator."""

    def setUp(self):
        super().setUp()
        # An organisation run by administrators only (no owner row), so the
        # owner protection does not already cover it.
        self.owner_m.delete()

    def test_one_of_two_admins_can_be_demoted(self):
        self.client.force_authenticate(self.owner)
        second = make_user("second@jos.test")
        second_m = OrganisationMembership.objects.create(organisation=self.org, user=second, role=MembershipRole.ADMIN)
        self.client.force_authenticate(self.admin)
        response = self.client.patch(self.url("organisation-update-member", second_m.id), {"role": "member"})
        self.assertEqual(response.status_code, 200)

    def test_last_admin_cannot_be_demoted_suspended_or_removed(self):
        self.client.force_authenticate(self.admin)
        response = self.client.patch(self.url("organisation-update-member", self.admin_m.id), {"role": "member"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"] if "code" in response.data else response.data.get("error", {}).get("code"), "last_admin")
        self.assertEqual(self.client.post(self.url("organisation-suspend-member", self.admin_m.id)).status_code, 409)
        self.assertEqual(self.client.delete(self.url("organisation-remove-member", self.admin_m.id)).status_code, 409)
        self.admin_m.refresh_from_db()
        self.assertEqual(self.admin_m.role, MembershipRole.ADMIN)
        self.assertTrue(self.admin_m.is_active)

    def test_admin_can_change_their_own_title(self):
        self.client.force_authenticate(self.admin)
        response = self.client.patch(self.url("organisation-update-member", self.admin_m.id), {"title": "Director"})
        self.assertEqual(response.status_code, 200)


class MembershipShapeTests(RoleTestCase):
    def test_approval_records_who_approved(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(reverse("join-request-decide", args=[self.join_request.id]), {"decision": "approved"})
        self.assertEqual(response.status_code, 200)
        membership = OrganisationMembership.objects.get(user=self.applicant)
        self.assertEqual(membership.role, MembershipRole.MEMBER)
        self.assertEqual(membership.approved_by, self.owner)
        self.assertIsNotNone(membership.approved_at)
        self.assertEqual(membership.status, "active")

    def test_accepting_an_invite_records_who_invited(self):
        invited = make_user("invited@jos.test")
        invitation = OrganisationInvitation.objects.create(
            organisation=self.org, email=invited.email, role=MembershipRole.MEMBER,
            invited_by=self.admin, expires_at=timezone.now() + timedelta(days=3),
        )
        self.client.force_authenticate(invited)
        response = self.client.post(reverse("accept-invitation"), {"token": invitation.token})
        self.assertEqual(response.status_code, 200)
        membership = OrganisationMembership.objects.get(user=invited)
        self.assertTrue(membership.is_active)
        self.assertEqual(membership.invited_by, self.admin)
