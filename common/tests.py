from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
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
