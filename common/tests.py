from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework import exceptions, status

from common.checks import secret_key_is_strong
from common.exception_handler import custom_exception_handler
from common.exceptions import ConflictError


class ExceptionHandlerTests(SimpleTestCase):
    def test_nested_validation_details_are_preserved(self):
        response = custom_exception_handler(
            exceptions.ValidationError({"profile": {"email": ["This field is required."]}}),
            {"view": None},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data, {
            "status": "failed",
            "message": "profile.email: This field is required.",
            "error": {
                "code": "validation_error",
                "details": {"profile": {"email": ["This field is required."]}},
            },
        })

    def test_domain_conflict_uses_provided_code(self):
        response = custom_exception_handler(
            ConflictError("Already completed.", code="already_completed"),
            {"view": None},
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["error"]["code"], "already_completed")
        self.assertIsNone(response.data["error"]["details"])

    @patch("common.exception_handler.logger.exception")
    def test_unhandled_exception_is_logged_and_hidden(self, logger):
        response = custom_exception_handler(RuntimeError("database password leaked"), {"view": None})

        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertEqual(response.data["error"]["code"], "internal_server_error")
        self.assertNotIn("password", response.data["message"])
        logger.assert_called_once()


class SecretKeyCheckTests(SimpleTestCase):
    """SECRET_KEY signs the JWTs, so a placeholder must not reach a deployment."""

    STRONG = "l4Nn8x-QaZ7vB2yTf0KpR9wMhCsE6uJdG3iOaVzXtYbQnLmPrSkFdHjWgU5cAe1o"

    @override_settings(SECRET_KEY=STRONG, ENVIRONMENT="production")
    def test_a_strong_key_passes(self):
        self.assertEqual(secret_key_is_strong(None), [])

    @override_settings(SECRET_KEY="change-me", ENVIRONMENT="local")
    def test_the_repository_placeholder_warns_in_development(self):
        messages = secret_key_is_strong(None)

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].id, "beldium.W001")

    @override_settings(SECRET_KEY="change-me", ENVIRONMENT="production")
    def test_the_repository_placeholder_is_fatal_in_production(self):
        messages = secret_key_is_strong(None)

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].id, "beldium.E001")
        self.assertTrue(messages[0].is_serious())

    @override_settings(SECRET_KEY="unsafe-development-key-change-this-before-any-real-deployment-2026", ENVIRONMENT="production")
    def test_the_settings_fallback_is_also_rejected(self):
        # Long enough to pass a length test, so it has to be matched by name.
        self.assertEqual(secret_key_is_strong(None)[0].id, "beldium.E001")

    @override_settings(SECRET_KEY="short", ENVIRONMENT="production")
    def test_a_short_key_is_rejected(self):
        self.assertEqual(secret_key_is_strong(None)[0].id, "beldium.E001")


class ServeStoredFileTests(SimpleTestCase):
    def test_streams_bytes_even_when_storage_hands_back_a_signed_url(self):
        # S3 storage returns an absolute signed URL. Redirecting to it breaks the
        # frontend's authenticated fetch (the bucket has no CORS rule), so the
        # bytes must come back from our own origin instead.
        from unittest import mock

        from django.core.files.base import ContentFile

        from common.files import serve_stored_file

        stored = mock.Mock()
        stored.url = "https://bucket.s3.amazonaws.com/doc.pdf?X-Amz-Signature=abc"
        stored.open.return_value = ContentFile(b"%PDF-1.4 test", name="doc.pdf")

        response = serve_stored_file(stored, "doc.pdf")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"%PDF-1.4 test")
        self.assertEqual(response["Content-Type"], "application/pdf")


class HealthCheckTests(SimpleTestCase):
    databases = {"default"}

    @patch("common.health._redis_ok", return_value=True)
    def test_healthy_when_database_and_redis_answer(self, _redis):
        response = self.client.get("/health/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "database": "ok", "redis": "ok"})

    @patch("common.health._redis_ok", return_value=False)
    def test_unhealthy_when_redis_is_down(self, _redis):
        response = self.client.get("/health/")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["redis"], "error")

    @patch("common.health._redis_ok", return_value=True)
    @patch("common.health._database_ok", return_value=False)
    def test_unhealthy_when_the_database_is_down(self, _db, _redis):
        response = self.client.get("/health/")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["database"], "error")

    @override_settings(ALLOWED_HOSTS=["api.beldium.com"], SECURE_SSL_REDIRECT=True)
    @patch("common.health._redis_ok", return_value=True)
    def test_answers_the_load_balancer_probe_by_ip_over_http(self, _redis):
        # The ALB sends the task's private IP as Host, over plain HTTP. Neither
        # ALLOWED_HOSTS nor the HTTPS redirect may turn that into a 400 or 301.
        response = self.client.get("/health/", HTTP_HOST="10.0.12.34:8000")

        self.assertEqual(response.status_code, 200)

    @override_settings(ALLOWED_HOSTS=["api.beldium.com"])
    def test_other_paths_still_validate_the_host(self):
        response = self.client.get("/api/v1/platform-stats/", HTTP_HOST="10.0.12.34:8000")

        self.assertEqual(response.status_code, 400)


class DeployedSettingsTests(SimpleTestCase):
    """Settings must refuse to import on a deployed environment with gaps."""

    def _import_settings(self, **env):
        import os
        import subprocess
        import sys

        from django.conf import settings

        clean_env = {"PATH": os.environ.get("PATH", ""), "DJANGO_SETTINGS_MODULE": "core.settings", **env}
        return subprocess.run(
            [sys.executable, "-c", "import django; django.setup()"],
            cwd=settings.BASE_DIR, env=clean_env, capture_output=True, text=True,
        )

    def _complete_env(self, **overrides):
        return {
            "ENVIRONMENT": "production",
            "SECRET_KEY": "k" * 64,
            "DATABASE_URL": "postgres://user:pass@db.internal:5432/beldium",
            "REDIS_URL": "redis://cache.internal:6379/0",
            "ALLOWED_HOSTS": "api.beldium.com",
            "CSRF_TRUSTED_ORIGINS": "https://api.beldium.com",
            "CORS_ALLOWED_ORIGINS": "https://compliance.beldium.com",
            "COMPLIANCE_PORTAL_ORIGINS": "https://compliance.beldium.com",
            "MINER_PORTAL_ORIGINS": "https://miners.beldium.com",
            "LOGISTICS_PORTAL_ORIGINS": "https://logistics.beldium.com",
            "FRONTEND_URL": "https://compliance.beldium.com",
            "DEFAULT_FROM_EMAIL": "noreply@beldium.com",
            "EMAIL_HOST_PASSWORD": "re_test",
            "AWS_STORAGE_BUCKET_NAME": "beldium-uploads",
            "NUM_PROXIES": "1",
            # Set explicitly so a developer's own .env can't leak in.
            "DEBUG": "false",
            **overrides,
        }

    def test_a_complete_production_environment_imports(self):
        result = self._import_settings(**self._complete_env())

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_missing_database_url_refuses_to_start(self):
        env = self._complete_env()
        del env["DATABASE_URL"]

        result = self._import_settings(**env)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DATABASE_URL", result.stderr)

    def test_staging_is_held_to_the_same_rules(self):
        result = self._import_settings(ENVIRONMENT="staging")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("AWS_STORAGE_BUCKET_NAME", result.stderr)

    def test_debug_cannot_be_on_in_a_deployed_environment(self):
        result = self._import_settings(**self._complete_env(DEBUG="true"))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DEBUG must be off", result.stderr)


class CopyMediaToS3Tests(TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        from compliance.models import ComplianceApplication, Personnel
        from organisations.models import Organisation, OrganisationType

        self.media = Path(tempfile.mkdtemp())
        organisation = Organisation.objects.create(name="Acme Mining", organisation_type=OrganisationType.MINING_COMPANY)
        application = ComplianceApplication.objects.create(organisation=organisation)
        self.present = "compliance/personnel/cv/2026/09/present.pdf"
        self.missing = "compliance/personnel/cv/2026/09/missing.pdf"
        self.already = "compliance/personnel/cv/2026/09/already.pdf"
        for key in (self.present, self.already):
            (self.media / key).parent.mkdir(parents=True, exist_ok=True)
            (self.media / key).write_bytes(b"%PDF-1.4")
        for key in (self.present, self.missing, self.already):
            Personnel.objects.create(application=application, full_name="A", role="Geologist", cv=key)

    def _run(self, *extra):
        from io import StringIO

        from botocore.exceptions import ClientError
        from django.core.management import call_command

        s3 = patch("common.management.commands.copy_media_to_s3.boto3.client").start()
        self.addCleanup(patch.stopall)
        client = s3.return_value

        def head_object(Bucket, Key):
            if Key != self.already:
                raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
            return {}

        client.head_object.side_effect = head_object
        out, err = StringIO(), StringIO()
        call_command("copy_media_to_s3", "--bucket", "uploads", "--source", str(self.media), *extra, stdout=out, stderr=err)
        return client, out.getvalue(), err.getvalue()

    def test_copies_to_the_same_key_and_reports_what_is_missing(self):
        client, out, err = self._run()

        client.upload_file.assert_called_once_with(
            str(self.media / self.present), "uploads", self.present, ExtraArgs={"ContentType": "application/pdf"}
        )
        self.assertIn("copied=1", out)
        self.assertIn("already_in_bucket=1", out)
        self.assertIn("missing_locally=1", out)
        self.assertIn(self.missing, err)

    def test_dry_run_uploads_nothing(self):
        client, out, _ = self._run("--dry-run")

        client.upload_file.assert_not_called()
        self.assertIn("would_copy=1", out)
