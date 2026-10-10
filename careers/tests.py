import json
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from careers.models import PARTNER_AGREEMENT_KEYS, REQUIRED_PARTNER_DOCUMENTS, Application, PartnerApplication
from careers.throttles import CareersSubmitThrottle

PDF = b"%PDF-1.4 test"
PNG = b"\x89PNG\r\n\x1a\n test"


def pdf(name="cv.pdf"):
    return SimpleUploadedFile(name, PDF, content_type="application/pdf")


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    CELERY_TASK_ALWAYS_EAGER=True,
    CAREERS_NOTIFY_EMAIL="careers@example.test",
)
class CareersTestCase(APITestCase):
    def setUp(self):
        media = TemporaryDirectory()
        self.addCleanup(media.cleanup)
        settings_override = override_settings(MEDIA_ROOT=media.name)
        settings_override.enable()
        self.addCleanup(settings_override.disable)
        # Throttle counters live in the cache and would leak between tests.
        cache.clear()


class ApplicationTests(CareersTestCase):
    url = reverse("careers-application-create")

    def payload(self, **overrides):
        return {
            "pathway": "internship",
            "full_name": "Ada Obi",
            "email": "ada@example.test",
            "phone": "08031234567",
            "country": "Nigeria",
            "state": "Lagos",
            "city": "Ikeja",
            "linkedin": "https://linkedin.com/in/ada",
            "answers": json.dumps({"university": "UNILAG", "motivation": "Line one\nLine two"}),
            "resume": pdf(),
            **overrides,
        }

    def test_submission_is_stored_and_staff_are_emailed(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.url, self.payload(), format="multipart")

        self.assertEqual(response.status_code, 201, response.data)
        application = Application.objects.get()
        self.assertEqual(response.data, {"reference_id": application.reference_id})
        self.assertRegex(application.reference_id, r"^BLD-INT-[A-Z2-9]{6}$")
        self.assertEqual(application.answers["university"], "UNILAG")
        self.assertEqual(application.resume.read(), PDF)
        self.assertIn(application.reference_id, application.resume.name)

        [message] = mail.outbox
        self.assertEqual(message.to, ["careers@example.test"])
        self.assertIn(application.reference_id, message.subject)
        html = message.alternatives[0][0]
        self.assertIn("UNILAG", html)
        self.assertIn(f"/admin/careers/application/{application.pk}/change/", html)

    def test_a_stale_bearer_token_does_not_block_a_public_submission(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer not-a-real-token")
        response = self.client.post(self.url, self.payload(), format="multipart")
        self.assertEqual(response.status_code, 201, response.data)

    def test_resume_is_required(self):
        payload = self.payload()
        del payload["resume"]
        response = self.client.post(self.url, payload, format="multipart")
        self.assertEqual(response.status_code, 400)
        self.assertIn("resume", response.data["error"]["details"])

    def test_file_contents_must_match_the_extension(self):
        disguised = SimpleUploadedFile("cv.pdf", b"MZ\x90\x00 executable", content_type="application/pdf")
        response = self.client.post(self.url, self.payload(resume=disguised), format="multipart")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Application.objects.exists())

    def test_oversized_file_is_rejected(self):
        big = SimpleUploadedFile("cv.pdf", PDF + b"0" * (8 * 1024 * 1024), content_type="application/pdf")
        response = self.client.post(self.url, self.payload(resume=big), format="multipart")
        self.assertEqual(response.status_code, 400)

    def test_partnership_needs_a_company_profile(self):
        response = self.client.post(self.url, self.payload(pathway="partnership"), format="multipart")
        self.assertEqual(response.status_code, 400)
        self.assertIn("company_profile", response.data["error"]["details"])

        response = self.client.post(
            self.url, self.payload(pathway="partnership", company_profile=pdf("profile.pdf")), format="multipart",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(response.data["reference_id"].startswith("BLD-PAR-"))

    def test_submissions_are_rate_limited_per_address(self):
        # DRF binds the rates to the class at import, so settings overrides miss it.
        rates = {**CareersSubmitThrottle.THROTTLE_RATES, "careers_submit": "1/1h"}
        with patch.object(CareersSubmitThrottle, "THROTTLE_RATES", rates):
            self.assertEqual(self.client.post(self.url, self.payload(), format="multipart").status_code, 201)
            self.assertEqual(self.client.post(self.url, self.payload(), format="multipart").status_code, 429)


class PartnerApplicationTests(CareersTestCase):
    def create(self, sector=None):
        payload = {
            "company": {
                "companyName": "Haulage Ltd",
                "rcNumber": "RC123456",
                "companyEmail": "ops@haulage.test",
                "phoneNumber": "08031234567",
                "businessAddress": "1 Depot Road, Kaduna",
                "contactPerson": "Musa Bello",
            },
            "agreements": [{"key": key, "signedName": "Musa Bello"} for key in PARTNER_AGREEMENT_KEYS],
        }
        if sector:
            payload["sector"] = sector
        response = self.client.post(
            reverse("careers-partner-create"),
            payload,
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def upload(self, created, key, file=None, token=None):
        return self.client.post(
            reverse("careers-partner-document", args=[created["application_id"]]),
            {"key": key, "file": file or pdf(f"{key}.pdf"), "upload_token": token or created["upload_token"]},
            format="multipart",
        )

    def submit(self, created):
        return self.client.post(
            reverse("careers-partner-submit", args=[created["application_id"]]),
            {"upload_token": created["upload_token"]},
            format="json",
        )

    def status(self, created):
        return self.client.get(reverse("careers-partner-status", args=[created["application_id"]]))

    def test_full_submission(self):
        created = self.create()
        self.assertRegex(created["application_id"], r"^BLD-LOG-[A-Z2-9]{6}$")
        self.assertEqual(created["sector"], "logistics")
        self.assertEqual(created["status"], "draft")
        self.assertEqual(created["required_documents"], list(REQUIRED_PARTNER_DOCUMENTS))
        self.assertIn("fleetRegister", created["optional_documents"])
        # A draft is invisible to the tracker and to staff until it is submitted.
        self.assertEqual(self.status(created).status_code, 404)
        self.assertEqual(mail.outbox, [])

        for key in REQUIRED_PARTNER_DOCUMENTS:
            self.assertEqual(self.upload(created, key).status_code, 201)
        image = SimpleUploadedFile("fleet.png", PNG, content_type="image/png")
        self.assertEqual(self.upload(created, "fleetRegister", image).status_code, 201)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.submit(created)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["application_id"], created["application_id"])
        self.assertEqual(response.data["sector"], "logistics")
        self.assertEqual(response.data["status"], "submitted")
        tracked = self.status(created).data
        self.assertEqual(tracked["status"], "submitted")
        self.assertEqual(tracked["required_documents"], list(REQUIRED_PARTNER_DOCUMENTS))

        application = PartnerApplication.objects.get()
        self.assertIsNotNone(application.submitted_at)
        self.assertEqual(application.documents.count(), 6)
        self.assertTrue(all(item["signedAt"] for item in application.agreements))

        [message] = mail.outbox
        html = message.alternatives[0][0]
        self.assertIn("Haulage Ltd", html)
        self.assertIn("Logistics", html)
        self.assertIn("Cac Certificate", html)

    def test_warehousing_submission_uses_its_sector_checklist(self):
        created = self.create("warehousing")
        self.assertRegex(created["application_id"], r"^BLD-WHS-[A-Z2-9]{6}$")
        self.assertEqual(created["sector"], "warehousing")
        self.assertIn("warehouseOperatingPermit", created["optional_documents"])
        self.assertNotIn("fleetRegister", created["optional_documents"])

        self.assertEqual(self.upload(created, "fleetRegister").status_code, 400)
        for key in created["required_documents"]:
            self.assertEqual(self.upload(created, key).status_code, 201)
        self.assertEqual(self.upload(created, "warehouseOperatingPermit").status_code, 201)

        response = self.submit(created)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["sector"], "warehousing")

    def test_submit_needs_every_required_document(self):
        created = self.create()
        self.upload(created, REQUIRED_PARTNER_DOCUMENTS[0])
        response = self.submit(created)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "documents_missing")
        self.assertEqual(response.data["error"]["details"]["missing"], list(REQUIRED_PARTNER_DOCUMENTS[1:]))

    def test_every_agreement_must_be_signed(self):
        response = self.client.post(
            reverse("careers-partner-create"),
            {
                "company": {
                    "companyName": "Haulage Ltd", "rcNumber": "RC1", "companyEmail": "ops@haulage.test",
                    "phoneNumber": "08031234567", "businessAddress": "Kaduna", "contactPerson": "Musa",
                },
                "agreements": [{"key": PARTNER_AGREEMENT_KEYS[0], "signedName": "Musa"}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_upload_needs_the_token_issued_for_that_application(self):
        created, other = self.create(), self.create()
        self.assertEqual(self.upload(created, "cacCertificate", token="forged").status_code, 403)
        self.assertEqual(self.upload(created, "cacCertificate", token=other["upload_token"]).status_code, 403)
        self.assertEqual(PartnerApplication.objects.get(application_id=created["application_id"]).documents.count(), 0)

    def test_reuploading_a_document_replaces_it(self):
        created = self.create()
        self.upload(created, "cacCertificate", pdf("first.pdf"))
        self.upload(created, "cacCertificate", pdf("second.pdf"))
        [document] = PartnerApplication.objects.get().documents.all()
        self.assertIn("second", document.file.name)

    def test_unknown_document_key_and_wrong_type_are_rejected(self):
        created = self.create()
        self.assertEqual(self.upload(created, "somethingElse").status_code, 400)
        word = SimpleUploadedFile("cac.docx", b"PK\x03\x04", content_type="application/octet-stream")
        self.assertEqual(self.upload(created, "cacCertificate", word).status_code, 400)

    def test_nothing_can_be_added_after_submission(self):
        created = self.create()
        for key in REQUIRED_PARTNER_DOCUMENTS:
            self.upload(created, key)
        self.assertEqual(self.submit(created).status_code, 200)
        self.assertEqual(self.upload(created, "fleetRegister").status_code, 400)
        self.assertEqual(self.submit(created).status_code, 400)
