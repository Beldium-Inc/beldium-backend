#!/bin/sh
# Container roles. Each ECS service (and the one-off migration task) runs the
# same image with a different first argument.
set -eu

role="${1:-web}"
[ "$#" -gt 0 ] && shift

case "$role" in
  web)
    # Runs the project's system checks (SECRET_KEY strength, shared cache,
    # NUM_PROXIES) now that migrate no longer runs in this container.
    python manage.py check --deploy --tag security --fail-level ERROR
    exec gunicorn core.wsgi:application \
      --bind "0.0.0.0:${PORT:-8000}" \
      --workers "${GUNICORN_WORKERS:-3}" \
      --timeout "${GUNICORN_TIMEOUT:-90}" \
      --graceful-timeout 30 \
      --access-logfile - \
      --error-logfile -
    ;;
  worker)
    exec celery -A core worker \
      --loglevel "${CELERY_LOG_LEVEL:-INFO}" \
      --concurrency "${CELERY_CONCURRENCY:-2}" \
      --without-gossip --without-mingle
    ;;
  beat)
    # Exactly one beat task may run per environment, or jobs fire twice.
    # The schedule state file is disposable, so /tmp is fine.
    exec celery -A core beat \
      --loglevel "${CELERY_LOG_LEVEL:-INFO}" \
      --schedule /tmp/celerybeat-schedule
    ;;
  migrate)
    exec python manage.py migrate --noinput
    ;;
  manage)
    # One-off admin commands, e.g. `manage bootstrap_admin`.
    exec python manage.py "$@"
    ;;
  *)
    exec "$role" "$@"
    ;;
esac
