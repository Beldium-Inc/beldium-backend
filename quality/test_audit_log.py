"""The append only Q&C audit log (task G4)."""
from django.db import IntegrityError, connection, transaction
from django.db.utils import DatabaseError
from django.urls import reverse
from django.utils import timezone

from quality import audit_log
from quality.models import AuditOutcome, QualityAuditEntry, SampleStatus
from quality.test_access import QualityAccessBase, make_user
from quality.test_roles import grant


class AppendOnlyTests(QualityAccessBase):
    def make_entry(self):
        return audit_log.record(actor=self.operator, action="test.write", record_type="sample", record_id="x")

    def test_model_refuses_update_and_delete(self):
        entry = self.make_entry()
        entry.reason = "changed"
        with self.assertRaises(PermissionError):
            entry.save()
        with self.assertRaises(PermissionError):
            entry.delete()
        with self.assertRaises(PermissionError):
            QualityAuditEntry.objects.filter(pk=entry.pk).update(reason="changed")
        with self.assertRaises(PermissionError):
            QualityAuditEntry.objects.all().delete()

    def test_database_refuses_update_and_delete(self):
        entry = self.make_entry()
        table = QualityAuditEntry._meta.db_table
        for sql in (f"UPDATE {table} SET reason = 'x' WHERE id = %s", f"DELETE FROM {table} WHERE id = %s"):
            with self.assertRaises((IntegrityError, DatabaseError)):
                with transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute(sql, [entry.pk.hex if connection.vendor == "sqlite" else entry.pk])
        self.assertTrue(QualityAuditEntry.objects.filter(pk=entry.pk, reason="").exists())

    def test_entry_keeps_a_copy_of_the_actor(self):
        entry = self.make_entry()
        self.assertEqual(entry.actor_email, self.operator.email)
        self.assertEqual(entry.actor_role_code, "QC")


class WritesAreAuditedTests(QualityAccessBase):
    def test_successful_write_is_logged_in_the_same_transaction(self):
        self.request_test(laboratory=self.lab_one)
        entry = QualityAuditEntry.objects.filter(action="sample.test_request").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.outcome, AuditOutcome.SUCCESS)
        self.assertEqual(entry.record_id, str(self.sample.pk))
        self.assertEqual(entry.actor_email, self.operator.email)

    def test_refused_write_is_logged_and_the_change_is_not(self):
        self.as_user(self.miner_a)
        response = self.client.patch(
            reverse("quality-sample-detail", args=[self.sample.pk]), {"status": "rejected"}, format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.sample.refresh_from_db()
        self.assertEqual(self.sample.status, SampleStatus.IN_TRANSIT)
        entry = QualityAuditEntry.objects.get(outcome=AuditOutcome.REFUSED)
        self.assertEqual(entry.actor_email, self.miner_a.email)
        self.assertFalse(QualityAuditEntry.objects.filter(outcome=AuditOutcome.SUCCESS).exists())

    def test_wrong_owner_404_is_logged_as_refused(self):
        self.as_user(self.miner_b)
        response = self.client.get(reverse("quality-sample-detail", args=[self.sample.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(QualityAuditEntry.objects.filter(outcome=AuditOutcome.REFUSED, actor_email=self.miner_b.email).exists())

    def test_role_assignment_change_keeps_before_and_after(self):
        admin = make_user("admin@beldium.test", is_staff=True)
        grant(admin, "ADM")
        qc = make_user("qc@beldium.test", is_staff=True)
        grant(qc, "QC")
        self.as_user(admin)
        created = self.client.post(reverse("quality-role-assignment-list"), {
            "user_id": str(self.lab1.pk), "role_code": "LAB", "organisation_id": str(self.lab_one.pk),
            "valid_from": str(timezone.localdate()),
        }, format="json")
        self.as_user(qc)
        self.client.post(reverse("quality-role-assignment-approve", args=[created.data["id"]]), {"decision": "approve"}, format="json")
        entry = QualityAuditEntry.objects.get(action="users_and_roles.approve")
        self.assertEqual(entry.before["status"], "pending_approval")
        self.assertEqual(entry.after["status"], "active")
        self.assertEqual(entry.actor_role_code, "QC")


class AuditLogReadTests(QualityAccessBase):
    def setUp(self):
        super().setUp()
        audit_log.record(actor=self.operator, action="sample.review", record_type="sample", record_id="abc")
        self.url = reverse("quality-audit-log-list")

    def test_qc_reads_and_the_read_is_logged(self):
        self.as_user(self.operator)
        response = self.client.get(self.url, {"record_id": "abc"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertTrue(QualityAuditEntry.objects.filter(action="audit_log.read").exists())

    def test_admin_is_refused(self):
        admin = make_user("admin@beldium.test", is_staff=True)
        grant(admin, "ADM")
        self.as_user(admin)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error"]["code"], "audit_log_closed")
        self.assertTrue(QualityAuditEntry.objects.filter(outcome=AuditOutcome.REFUSED, actor_email=admin.email).exists())

    def test_legal_reads(self):
        lawyer = make_user("legal@beldium.test")
        grant(lawyer, "LGL")
        self.as_user(lawyer)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_outside_roles_are_refused(self):
        for user in (self.miner_a, self.lab1, self.regulator, self.nobody):
            self.as_user(user)
            self.assertEqual(self.client.get(self.url).status_code, 403, user.email)

    def test_no_write_routes(self):
        self.as_user(self.operator)
        self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 405)
        entry = QualityAuditEntry.objects.first()
        detail = reverse("quality-audit-log-detail", args=[entry.pk])
        self.assertEqual(self.client.delete(detail).status_code, 405)
        self.assertEqual(self.client.patch(detail, {}, format="json").status_code, 405)

    def test_date_range_limit(self):
        self.as_user(self.operator)
        response = self.client.get(self.url, {"from": "2025-01-01", "to": "2026-06-01"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "date_range_too_wide")
