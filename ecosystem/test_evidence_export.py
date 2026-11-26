import csv
import io

from django.urls import reverse
from rest_framework.test import APITestCase

from accounts.models import AccountAuditEvent, User
from ecosystem.models import Rfq, Transaction, TransactionStageEvent
from mining.models import MineSite
from organisations.models import Organisation, OrganisationMembership


class TransactionEvidenceExportTests(APITestCase):
    def setUp(self):
        self.seller = Organisation.objects.create(name="Seller", organisation_type="mining_company")
        self.buyer = Organisation.objects.create(name="Buyer", organisation_type="market_participant")
        self.other = Organisation.objects.create(name="Other", organisation_type="mining_company")
        self.site = MineSite.objects.create(organisation=self.seller, name="Site", mineral="Lithium")
        self.rfq = Rfq.objects.create(
            buyer_organisation=self.buyer, seller_organisation=self.seller,
            mineral="Lithium", quantity_requested=10,
        )
        self.transaction = Transaction.objects.create(
            rfq=self.rfq, mine_site=self.site, buyer_organisation=self.buyer,
            seller_organisation=self.seller, mineral="Lithium", committed_tonnes=10,
        )
        TransactionStageEvent.objects.create(
            transaction=self.transaction, stage="rfq_received", note="Evidence checked",
        )
        self.admin = User.objects.create_user("admin@example.com", "Password-2026!")
        self.staff = User.objects.create_user("staff@example.com", "Password-2026!")
        self.outsider = User.objects.create_user("outsider@example.com", "Password-2026!")
        OrganisationMembership.objects.create(organisation=self.seller, user=self.admin, role="admin")
        OrganisationMembership.objects.create(organisation=self.seller, user=self.staff, role="member")
        OrganisationMembership.objects.create(organisation=self.other, user=self.outsider, role="admin")
        self.url = reverse("ecosystem-transaction-evidence-export", args=[self.transaction.id])

    def test_admin_can_export_json_and_csv_and_each_is_logged(self):
        self.client.force_authenticate(self.admin)
        json_response = self.client.get(self.url)
        self.assertEqual(json_response.status_code, 200)
        self.assertIn("attachment", json_response["Content-Disposition"])
        self.assertIn(self.transaction.reference, json_response.content.decode())
        self.assertIn("Evidence checked", json_response.content.decode())
        csv_response = self.client.get(self.url, {"file_format": "csv"})
        self.assertEqual(csv_response.status_code, 200)
        rows = list(csv.reader(io.StringIO(csv_response.content.decode())))
        self.assertEqual(rows[0], ["section", "field", "value"])
        self.assertTrue(any(row[1] == "reference" and row[2] == self.transaction.reference for row in rows))
        self.assertEqual(AccountAuditEvent.objects.filter(
            event_type="ecosystem.transaction_evidence_exported", actor=self.admin,
        ).count(), 2)

    def test_staff_and_wrong_organisation_cannot_export(self):
        for user in (self.staff, self.outsider):
            with self.subTest(user=user.email):
                self.client.force_authenticate(user)
                self.assertIn(self.client.get(self.url).status_code, (403, 404))
        self.assertFalse(AccountAuditEvent.objects.filter(
            event_type="ecosystem.transaction_evidence_exported",
        ).exists())

    def test_invalid_format_is_not_logged(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get(self.url, {"file_format": "pdf"}).status_code, 400)
        self.assertFalse(AccountAuditEvent.objects.filter(
            event_type="ecosystem.transaction_evidence_exported",
        ).exists())

    def test_verified_compliance_reviewer_can_export(self):
        partner = Organisation.objects.create(
            name="Compliance Desk", organisation_type="compliance_partner",
            verification_status="verified",
        )
        reviewer = User.objects.create_user("reviewer@example.com", "Password-2026!")
        OrganisationMembership.objects.create(organisation=partner, user=reviewer, role="reviewer")
        self.client.force_authenticate(reviewer)
        self.assertEqual(self.client.get(self.url).status_code, 200)
