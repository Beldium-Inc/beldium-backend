"""The compliance desk hears by email about new miner and logistics sign-ups
and logistics submissions, and only the desk working that sector does."""
import threading
from unittest.mock import patch

from django.core import mail
from django.urls import reverse
from rest_framework.test import APITestCase

from accounts.models import User
from accounts.services import issue_email_verification
from compliance.models import ComplianceApplication
from organisations import desk_alerts
from organisations.models import MembershipRole, Organisation, OrganisationMembership, OrganisationType

MINER_ORIGIN = "https://miners.beldium.com"
LOGISTICS_ORIGIN = "https://logistics.beldium.com"
COMPLIANCE_ORIGIN = "https://compliance.beldium.com"


def join_background_sends():
    for thread in threading.enumerate():
        if thread is not threading.current_thread() and thread.daemon:
            thread.join(timeout=5)


def desk_mail(subject_fragment):
    return sorted(
        address
        for message in mail.outbox if subject_fragment in message.subject
        for address in message.to
    )


class DeskAlertTestCase(APITestCase):
    def setUp(self):
        self.mining_partner = self.partner("Mining Desk Ltd", "mining", email="mining-desk@example.com")
        self.logistics_partner = self.partner("Haulage Desk Ltd", "logistics", email="logistics-desk@example.com")
        # An older partner that never recorded a sector works every desk.
        self.any_partner = self.partner("General Desk Ltd", "", email="general-desk@example.com")
        # Unverified partners and other sectors hear nothing.
        self.partner("Pending Desk Ltd", "mining", email="pending@example.com", status="pending")
        self.partner("Export Desk Ltd", "export", email="export@example.com")

    def partner(self, name, sector, *, email="", status="verified"):
        organisation = Organisation.objects.create(
            name=name, organisation_type=OrganisationType.COMPLIANCE_PARTNER,
            verification_status=status, email=email,
        )
        ComplianceApplication.objects.create(organisation=organisation, sector=sector)
        return organisation


class DeskRecipientsTests(DeskAlertTestCase):
    def test_recipients_are_the_verified_partners_for_that_sector(self):
        self.assertEqual(desk_alerts.desk_recipients("mining"), ["general-desk@example.com", "mining-desk@example.com"])
        self.assertEqual(desk_alerts.desk_recipients("logistics"), ["general-desk@example.com", "logistics-desk@example.com"])

    def test_a_partner_without_an_org_email_is_reached_through_its_members(self):
        self.mining_partner.email = ""
        self.mining_partner.save(update_fields=["email"])
        member = User.objects.create_user("reviewer@mining-desk.test", "SafePassword-2026!")
        OrganisationMembership.objects.create(organisation=self.mining_partner, user=member, role=MembershipRole.REVIEWER)
        self.assertIn("reviewer@mining-desk.test", desk_alerts.desk_recipients("mining"))


@patch("accounts.services.generate_verification_code", return_value="123456")
class SignupAlertTests(DeskAlertTestCase):
    def verify(self, email, origin, portal=""):
        user = User.objects.create_user(email, "SafePassword-2026!", first_name="Ada", last_name="Bello", portal=portal)
        issue_email_verification(user)
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("verify-email"), {"email": email, "code": "123456"}, HTTP_ORIGIN=origin)
        self.assertEqual(response.status_code, 200, response.data)
        join_background_sends()

    def test_a_new_miner_is_announced_to_the_mining_desk_only(self, _code):
        self.verify("miner@example.com", MINER_ORIGIN, portal="miner")
        self.assertEqual(desk_mail("New mining sign-up"), ["general-desk@example.com", "mining-desk@example.com"])
        message = next(m for m in mail.outbox if "New mining sign-up" in m.subject)
        self.assertIn("miner@example.com", message.body)

    def test_a_new_logistics_operator_is_announced_to_the_logistics_desk_only(self, _code):
        self.verify("haulier@example.com", LOGISTICS_ORIGIN)
        self.assertEqual(desk_mail("New logistics sign-up"), ["general-desk@example.com", "logistics-desk@example.com"])
        self.assertEqual(desk_mail("New mining sign-up"), [])

    def test_a_compliance_portal_signup_alerts_nobody(self, _code):
        self.verify("partner@example.com", COMPLIANCE_ORIGIN)
        self.assertEqual(desk_mail("sign-up"), [])


class LogisticsSubmissionAlertTests(DeskAlertTestCase):
    def test_submitting_a_logistics_application_emails_the_logistics_desk(self):
        from logistics.models import LogisticsApplication, LogisticsCompany
        from logistics.views import notify_desk_of_submission

        organisation = Organisation.objects.create(name="Ilesa Heavy Haulage", organisation_type=OrganisationType.MINING_COMPANY)
        company = LogisticsCompany.objects.create(
            organisation=organisation, contact_name="Ada Bello", contact_email="ops@ilesa.example",
            contact_phone="+2348000000000", employees=5, annual_tonnage="100.00", services=["general freight"],
        )
        owner = User.objects.create_user("owner@ilesa.example", "SafePassword-2026!")
        application = LogisticsApplication.objects.create(company=company, created_by=owner)
        notify_desk_of_submission(application)
        join_background_sends()
        self.assertEqual(
            desk_mail("New logistics application from Ilesa Heavy Haulage"),
            ["general-desk@example.com", "logistics-desk@example.com"],
        )
        message = mail.outbox[0]
        self.assertIn("/logistics/operator/applications", message.body)
