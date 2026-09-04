from unittest.mock import patch

from django.test import SimpleTestCase
from rest_framework import exceptions, status

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
