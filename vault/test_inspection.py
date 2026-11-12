"""Upload inspection and uploads on behalf of a company in the vault (G7)."""
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from vault.models import VaultDocument
from vault.tests import MEDIA_ROOT, VaultTests, pdf

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class VaultInspectionTests(VaultTests):
    """Reuses the vault fixtures. The inherited tests are switched off below
    so they run once, in vault.tests."""

    def test_upload_records_type_and_scan_status(self):
        response = self.upload(self.partner, file=pdf(b"%PDF-1.4 inspected"))
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["sniffed_type"], "application/pdf")
        self.assertEqual(response.data["scan_status"], "pending")
        self.assertFalse(response.data["on_behalf_of_company"])

    def test_disguised_file_is_refused(self):
        response = self.upload(self.partner, file=SimpleUploadedFile("report.pdf", PNG))
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.data["error"]["code"], "file_type_mismatch")
        program = SimpleUploadedFile("report.pdf", b"MZ\x90\x00 program")
        self.assertEqual(self.upload(self.partner, file=program).data["error"]["code"], "file_type_mismatch")
        self.assertFalse(VaultDocument.objects.exists())

    def test_infected_file_is_refused(self):
        verdict = {"sha256": "0" * 64, "size": 10, "sniffed_type": "application/pdf", "scan_status": "infected"}
        with mock.patch("vault.services.inspect_upload", return_value=verdict):
            response = self.upload(self.partner)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.data["error"]["code"], "malware_detected")
        self.assertFalse(VaultDocument.objects.exists())

    def test_on_behalf_upload_needs_how_and_when(self):
        response = self.upload(self.operator, organisation=str(self.lab.pk))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "received_from_required")
        response = self.upload(self.operator, organisation=str(self.lab.pk), received_from="whatsapp", received_date="2026-11-11")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(response.data["on_behalf_of_company"])
        self.assertEqual(response.data["received_from"], "whatsapp")
        self.assertEqual(response.data["status"], "awaiting_approval")

    def test_partner_own_upload_is_not_on_behalf(self):
        response = self.upload(self.partner, received_from="email", received_date="2026-11-11")
        self.assertFalse(response.data["on_behalf_of_company"])
        self.assertEqual(response.data["received_from"], "")


for _name in [name for name in dir(VaultTests) if name.startswith("test_")]:
    if _name not in VaultInspectionTests.__dict__:
        setattr(VaultInspectionTests, _name, None)
