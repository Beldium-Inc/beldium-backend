"""Roles, the access grid and role assignments (task G3)."""
from datetime import timedelta

from django.core.management import call_command
from django.core.management.base import CommandError
from django.urls import reverse
from django.utils import timezone

from organisations.models import OrganisationType
from quality import permissions
from quality.models import (
    QualityRole,
    QualityRoleAssignment,
    QualityRoleGrant,
    RoleAssignmentStatus,
)
from quality.test_access import QualityAccessBase, make_org, make_user, member


def grant(user, code, organisation=None, status=RoleAssignmentStatus.ACTIVE, **extra):
    return QualityRoleAssignment.objects.create(
        user=user, role_id=code, organisation=organisation, status=status,
        valid_from=extra.pop("valid_from", timezone.localdate()), **extra,
    )


class GridSeedTests(QualityAccessBase):
    def test_twelve_roles_are_seeded(self):
        self.assertEqual(QualityRole.objects.count(), 12)
        self.assertFalse(QualityRole.objects.get(pk="SUP").second_factor_required)
        self.assertTrue(QualityRole.objects.get(pk="LAB").second_factor_required)

    def test_admin_has_no_audit_log_access(self):
        self.assertFalse(QualityRoleGrant.objects.filter(role_id="ADM", record_type="audit_log").exists())
        self.assertTrue(QualityRoleGrant.objects.filter(role_id="LGL", record_type="audit_log").exists())

    def test_restricted_view_is_marked(self):
        cell = QualityRoleGrant.objects.get(role_id="LAB", record_type="test_request")
        self.assertEqual(cell.letters, "R")
        self.assertTrue(cell.restricted)


class PermissionTests(QualityAccessBase):
    def test_staff_without_assignment_falls_back_to_qc(self):
        self.assertEqual(permissions.role_codes(self.operator), {"QC"})
        self.assertTrue(permissions.can(self.operator, "quality_decision", "approve"))

    def test_admin_assignment_replaces_staff_fallback(self):
        admin = make_user("admin@beldium.test", is_staff=True)
        grant(admin, "ADM")
        self.assertEqual(permissions.role_codes(admin), {"ADM"})
        self.assertFalse(permissions.can(admin, "audit_log", "read"))
        self.assertFalse(permissions.can(admin, "quality_decision", "approve"))
        from quality import services
        self.assertFalse(services.can_decide(admin))
        self.assertEqual(services.derive_role(admin), "regulator")

    def test_ended_assignment_removes_access_at_once(self):
        tl = make_user("tl@beldium.test", is_staff=True)
        row = grant(tl, "TL")
        self.assertTrue(permissions.can(tl, "audit_log", "read"))
        row.status = RoleAssignmentStatus.ENDED
        row.save()
        self.assertEqual(permissions.role_codes(tl), set())
        from quality import services
        self.assertIsNone(services.derive_role(tl))

    def test_future_and_lapsed_assignments_give_nothing(self):
        user = make_user("later@beldium.test")
        grant(user, "CEO", valid_from=timezone.localdate() + timedelta(days=3))
        self.assertEqual(permissions.role_codes(user), set())
        other = make_user("old@beldium.test")
        grant(other, "CEO", valid_from=timezone.localdate() - timedelta(days=30),
              valid_to=timezone.localdate() - timedelta(days=1))
        self.assertEqual(permissions.role_codes(other), set())

    def test_suspended_organisation_takes_the_role_away(self):
        grant(self.lab1, "LAB", organisation=self.lab_one)
        self.assertEqual(permissions.role_codes(self.lab1), {"LAB"})
        self.lab_one.verification_status = "suspended"
        self.lab_one.save()
        self.assertEqual(permissions.role_codes(self.lab1), set())

    def test_assignment_cannot_be_deleted(self):
        row = grant(self.lab1, "LAB", organisation=self.lab_one)
        with self.assertRaises(PermissionError):
            row.delete()


class MeEndpointTests(QualityAccessBase):
    def test_me_returns_roles_and_permissions(self):
        grant(self.lab1, "LAB", organisation=self.lab_one)
        self.as_user(self.lab1)
        response = self.client.get(reverse("quality-me"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["role_codes"], ["LAB"])
        self.assertEqual(response.data["roles"][0]["organisation_name"], "Lab One")
        self.assertIn("create", response.data["permissions"]["assay_report"])
        self.assertIn("test_request", response.data["restricted_views"])
        self.assertNotIn("audit_log", response.data["permissions"])

    def test_user_with_nothing_gets_empty_rights(self):
        self.as_user(self.nobody)
        response = self.client.get(reverse("quality-me"))
        self.assertEqual(response.data["roles"], [])
        self.assertEqual(response.data["permissions"], {})


class RoleAssignmentApiTests(QualityAccessBase):
    def setUp(self):
        super().setUp()
        self.admin = make_user("admin@beldium.test", is_staff=True)
        grant(self.admin, "ADM")
        self.qc = make_user("qc@beldium.test", is_staff=True)
        grant(self.qc, "QC")
        self.tl = make_user("tl@beldium.test", is_staff=True)
        grant(self.tl, "TL")
        self.list_url = reverse("quality-role-assignment-list")

    def create(self, user, **body):
        self.as_user(user)
        payload = {"user_id": str(self.lab1.pk), "role_code": "LAB", "organisation_id": str(self.lab_one.pk),
                   "valid_from": str(timezone.localdate())}
        payload.update(body)
        return self.client.post(self.list_url, payload, format="json")

    def test_admin_creates_pending_assignment_with_no_access(self):
        response = self.create(self.admin)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["status"], "pending_approval")
        self.assertEqual(permissions.role_codes(self.lab1), {"LAB"})  # legacy fallback only
        self.assertFalse(permissions.uses_stored_roles(self.lab1))

    def test_wrong_roles_cannot_create(self):
        for user in (self.qc, self.tl, self.lab1, self.miner_a, self.nobody):
            self.assertEqual(self.create(user).status_code, 403, user.email)

    def test_operator_fallback_cannot_create(self):
        # A staff user with no stored role falls back to QC, which does not create.
        self.assertEqual(self.create(self.operator).status_code, 403)

    def test_cannot_assign_self(self):
        response = self.create(self.admin, user_id=str(self.admin.pk), role_code="CEO", organisation_id=None)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error"]["code"], "self_assignment")

    def test_organisation_rules(self):
        response = self.create(self.admin, organisation_id=None)
        self.assertEqual(response.data["error"]["code"], "organisation_required")
        # The user is not a member of Lab Two.
        response = self.create(self.admin, organisation_id=str(self.lab_two.pk))
        self.assertEqual(response.data["error"]["code"], "not_a_member")
        pending_lab = make_org("Pending Lab", OrganisationType.LABORATORY, status="under_review")
        person = member(pending_lab, "p@pending.test")
        response = self.create(self.admin, user_id=str(person.pk), organisation_id=str(pending_lab.pk))
        self.assertEqual(response.data["error"]["code"], "organisation_not_verified")

    def test_duplicate_is_refused(self):
        self.assertEqual(self.create(self.admin).status_code, 201)
        response = self.create(self.admin)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "assignment_exists")

    def test_qc_approves_and_access_starts(self):
        row_id = self.create(self.admin).data["id"]
        url = reverse("quality-role-assignment-approve", args=[row_id])
        self.as_user(self.tl)
        self.assertEqual(self.client.post(url, {"decision": "approve"}, format="json").status_code, 403)
        self.as_user(self.qc)
        response = self.client.post(url, {"decision": "approve"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "active")
        self.assertTrue(permissions.uses_stored_roles(self.lab1))
        again = self.client.post(url, {"decision": "approve"}, format="json")
        self.assertEqual(again.data["error"]["code"], "already_decided")

    def test_approver_cannot_be_the_person_given_the_role(self):
        new_qc = make_user("qc2@beldium.test", is_staff=True)
        row = grant(new_qc, "QC", status=RoleAssignmentStatus.PENDING_APPROVAL, granted_by=self.admin)
        grant(new_qc, "QC")  # also already active, so the approve right is there
        self.as_user(new_qc)
        response = self.client.post(reverse("quality-role-assignment-approve", args=[row.pk]), {"decision": "approve"}, format="json")
        self.assertEqual(response.data["error"]["code"], "same_person")

    def test_reject_needs_reason(self):
        row_id = self.create(self.admin).data["id"]
        self.as_user(self.qc)
        url = reverse("quality-role-assignment-approve", args=[row_id])
        self.assertEqual(self.client.post(url, {"decision": "reject"}, format="json").status_code, 400)
        response = self.client.post(url, {"decision": "reject", "reason": "Wrong lab"}, format="json")
        self.assertEqual(response.data["status"], "rejected")

    def test_end_by_tl_and_refused_for_others(self):
        row = grant(self.lab1, "LAB", organisation=self.lab_one)
        url = reverse("quality-role-assignment-end", args=[row.pk])
        for user in (self.qc, self.lab1):
            self.as_user(user)
            self.assertEqual(self.client.post(url, {"reason": "x"}, format="json").status_code, 403)
        self.as_user(self.tl)
        response = self.client.post(url, {"reason": "Contract over"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "ended")
        self.assertEqual(permissions.role_codes(self.lab1), set())
        again = self.client.post(url, {"reason": "again"}, format="json")
        self.assertEqual(again.data["error"]["code"], "already_ended")

    def test_list_and_detail_are_closed_to_outsiders(self):
        row = grant(self.lab1, "LAB", organisation=self.lab_one)
        self.as_user(self.lab1)
        self.assertEqual(self.client.get(self.list_url).status_code, 403)
        self.assertEqual(self.client.get(reverse("quality-role-assignment-detail", args=[row.pk])).status_code, 404)
        self.as_user(self.admin)
        self.assertEqual(self.client.get(self.list_url).status_code, 200)

    def test_roles_list(self):
        self.as_user(self.tl)
        response = self.client.get(reverse("quality-role-list"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 12)
        self.as_user(self.miner_a)
        self.assertEqual(self.client.get(reverse("quality-role-list")).status_code, 403)


class BootstrapCommandTests(QualityAccessBase):
    def test_first_admin_then_refused(self):
        user = make_user("first-admin@beldium.test", is_staff=True)
        call_command("grant_first_quality_role", email=user.email, role="ADM", stdout=open("/dev/null", "w"))
        self.assertEqual(permissions.role_codes(user), {"ADM"})
        with self.assertRaises(CommandError):
            call_command("grant_first_quality_role", email=self.operator.email, role="ADM")
