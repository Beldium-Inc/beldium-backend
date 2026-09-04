from rest_framework import status


class AppError(Exception):
    """A safe, client-facing domain error raised outside serializer validation."""

    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "request_error"

    def __init__(self, message, *, code=None, status_code=None, details=None):
        self.message = str(message)
        self.code = code or self.default_code
        self.status_code = status_code or self.status_code
        self.details = details
        super().__init__(self.message)


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    default_code = "conflict"


class ResourceNotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    default_code = "not_found"
