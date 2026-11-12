"""Upload inspection (E5) and the SMS path (E6)."""
import hashlib
import io
import zipfile
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from common.models import SmsDelivery, SmsStatus
from common.scanning import inspect_upload, matches_extension
from common.sms import send_sms

PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def office_file(part):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(part, "<x/>")
    return buffer.getvalue()


class InspectUploadTests(TestCase):
    def test_hash_size_and_type_from_bytes(self):
        upload = SimpleUploadedFile("report.pdf", PDF, content_type="image/png")
        result = inspect_upload(upload)
        self.assertEqual(result["sha256"], hashlib.sha256(PDF).hexdigest())
        self.assertEqual(result["size"], len(PDF))
        self.assertEqual(result["sniffed_type"], "application/pdf")
        # The file can still be read after inspection.
        self.assertEqual(upload.read(), PDF)

    def test_no_scanner_means_pending_not_clean(self):
        self.assertEqual(inspect_upload(SimpleUploadedFile("a.png", PNG))["scan_status"], "pending")

    def test_office_files_are_told_apart(self):
        docx = inspect_upload(SimpleUploadedFile("a.docx", office_file("word/document.xml")))
        xlsx = inspect_upload(SimpleUploadedFile("a.xlsx", office_file("xl/workbook.xml")))
        self.assertTrue(docx["sniffed_type"].endswith("wordprocessingml.document"))
        self.assertTrue(xlsx["sniffed_type"].endswith("spreadsheetml.sheet"))

    def test_mismatch_is_detected(self):
        renamed = inspect_upload(SimpleUploadedFile("photo.pdf", PNG))
        self.assertFalse(matches_extension("photo.pdf", renamed["sniffed_type"]))
        self.assertTrue(matches_extension("photo.png", renamed["sniffed_type"]))
        self.assertFalse(matches_extension("script.exe", "application/octet-stream"))

    @override_settings(CLAMAV_HOST="clamd.test")
    def test_scanner_replies(self):
        for reply, expected in ((b"stream: OK\0", "clean"), (b"stream: Eicar-Test-Signature FOUND\0", "infected")):
            conn = mock.MagicMock()
            conn.__enter__.return_value = conn
            conn.recv.return_value = reply
            with mock.patch("common.scanning.socket.create_connection", return_value=conn):
                self.assertEqual(inspect_upload(SimpleUploadedFile("a.pdf", PDF))["scan_status"], expected)

    @override_settings(CLAMAV_HOST="clamd.test")
    def test_scanner_down_leaves_pending(self):
        with mock.patch("common.scanning.socket.create_connection", side_effect=OSError("refused")):
            self.assertEqual(inspect_upload(SimpleUploadedFile("a.pdf", PDF))["scan_status"], "pending")


class SendSmsTests(TestCase):
    def test_off_by_default_and_still_recorded(self):
        with mock.patch("common.sms._post") as post:
            delivery = send_sms("+2348012345678", "Code 123456", purpose="phone_verification")
        post.assert_not_called()
        self.assertEqual(delivery.status, SmsStatus.SKIPPED)
        self.assertEqual(delivery.to_last_digits, "5678")
        self.assertEqual(SmsDelivery.objects.count(), 1)

    @override_settings(SMS_ENABLED=True, TERMII_API_KEY="test-key")
    def test_sent_when_switched_on(self):
        with mock.patch("common.sms._post", return_value={"message_id": "abc"}) as post:
            delivery = send_sms("+2348012345678", "Code 123456", purpose="phone_verification")
        self.assertEqual(post.call_args.args[0]["to"], "2348012345678")
        self.assertEqual(delivery.status, SmsStatus.SENT)
        self.assertEqual(delivery.provider_message_id, "abc")

    @override_settings(SMS_ENABLED=True, TERMII_API_KEY="test-key")
    def test_failure_is_recorded_without_the_message(self):
        with mock.patch("common.sms._post", side_effect=OSError("down")):
            delivery = send_sms("+2348012345678", "Code 123456", purpose="phone_verification")
        self.assertEqual(delivery.status, SmsStatus.FAILED)
        self.assertNotIn("123456", delivery.error)

    def test_phone_verification_task_uses_the_path(self):
        from accounts.tasks import send_phone_verification

        self.assertEqual(send_phone_verification.run("user-id", "+2348012345678", "123456"), "skipped")
        self.assertEqual(SmsDelivery.objects.get().purpose, "phone_verification")
