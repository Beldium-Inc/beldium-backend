import logging

import redis
from django.conf import settings
from django.db import connections
from django.http import JsonResponse

logger = logging.getLogger(__name__)

HEALTH_PATH = "/health/"
PROBE_TIMEOUT_SECONDS = 2


def _database_ok():
    try:
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return True
    except Exception:
        logger.exception("Health check: database unreachable")
        return False


def _redis_ok():
    try:
        client = redis.Redis.from_url(
            settings.CELERY_BROKER_URL,
            socket_connect_timeout=PROBE_TIMEOUT_SECONDS,
            socket_timeout=PROBE_TIMEOUT_SECONDS,
        )
        try:
            return bool(client.ping())
        finally:
            client.close()
    except Exception:
        logger.exception("Health check: Redis unreachable")
        return False


class HealthCheckMiddleware:
    """Answers GET /health/ for the load balancer, ahead of everything else.

    It has to run before SecurityMiddleware and CommonMiddleware: the ALB
    probes each task over plain HTTP with the task's private IP as the Host
    header, which would otherwise be redirected to HTTPS or rejected by
    ALLOWED_HOSTS. Nothing here reads the Host header or the session, and the
    body carries no detail beyond up/down per dependency.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path != HEALTH_PATH or request.method not in {"GET", "HEAD"}:
            return self.get_response(request)

        checks = {"database": _database_ok(), "redis": _redis_ok()}
        healthy = all(checks.values())
        return JsonResponse(
            {"status": "ok" if healthy else "error", **{k: "ok" if v else "error" for k, v in checks.items()}},
            status=200 if healthy else 503,
        )
