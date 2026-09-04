import logging

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.db import IntegrityError
from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from common.exceptions import AppError

logger = logging.getLogger(__name__)


def _plain_detail(value):
    """Convert DRF ErrorDetail objects into JSON-safe strings without losing structure."""
    if isinstance(value, dict):
        return {str(key): _plain_detail(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_detail(item) for item in value]
    return str(value)


def _first_message(value):
    if isinstance(value, dict):
        if "detail" in value:
            return _first_message(value["detail"])
        for item in value.values():
            return _first_message(item)
    elif isinstance(value, (list, tuple)) and value:
        return _first_message(value[0])
    elif value not in (None, ""):
        return str(value)
    return "Request failed."


def _first_validation_message(value, path=""):
    if isinstance(value, dict):
        for field, item in value.items():
            field_path = f"{path}.{field}" if path else str(field)
            return _first_validation_message(item, field_path)
    elif isinstance(value, (list, tuple)) and value:
        return _first_validation_message(value[0], path)
    elif value not in (None, ""):
        message = str(value)
        return f"{path}: {message}" if path else message
    return "Request validation failed."


def error_response(*, message, code, status_code, details=None, headers=None):
    payload = {
        "status": "failed",
        "message": str(message),
        "error": {
            "code": code,
            "details": details,
        },
    }
    return Response(payload, status=status_code, headers=headers)


def _api_error_code(exc, status_code):
    if isinstance(exc, exceptions.ValidationError):
        return "validation_error"
    if isinstance(exc, exceptions.NotAuthenticated):
        return "not_authenticated"
    if isinstance(exc, exceptions.AuthenticationFailed):
        return "authentication_failed"
    if isinstance(exc, (exceptions.PermissionDenied, DjangoPermissionDenied)):
        return "permission_denied"
    if isinstance(exc, (exceptions.NotFound, Http404)):
        return "not_found"
    if isinstance(exc, exceptions.MethodNotAllowed):
        return "method_not_allowed"
    if isinstance(exc, exceptions.NotAcceptable):
        return "not_acceptable"
    if isinstance(exc, exceptions.UnsupportedMediaType):
        return "unsupported_media_type"
    if isinstance(exc, exceptions.Throttled):
        return "throttled"
    if isinstance(exc, exceptions.ParseError):
        return "parse_error"
    return f"http_{status_code}"


def custom_exception_handler(exc, context):
    if isinstance(exc, AppError):
        return error_response(
            message=exc.message,
            code=exc.code,
            status_code=exc.status_code,
            details=exc.details,
        )

    if isinstance(exc, IntegrityError):
        logger.warning("Database integrity conflict", exc_info=True, extra={"view": context.get("view")})
        return error_response(
            message="The request conflicts with an existing resource.",
            code="conflict",
            status_code=status.HTTP_409_CONFLICT,
        )

    response = drf_exception_handler(exc, context)
    if response is not None:
        details = _plain_detail(response.data)
        is_validation = isinstance(exc, exceptions.ValidationError)
        message = _first_validation_message(details) if is_validation else _first_message(details)
        return error_response(
            message=message,
            code=_api_error_code(exc, response.status_code),
            status_code=response.status_code,
            details=details if is_validation else None,
            headers=response.headers,
        )

    logger.exception(
        "Unhandled API exception",
        exc_info=(type(exc), exc, exc.__traceback__),
        extra={"view": context.get("view")},
    )
    return error_response(
        message="An unexpected error occurred.",
        code="internal_server_error",
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )
