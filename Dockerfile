# syntax=docker/dockerfile:1

# One image runs every role (web, worker, beat, migrate); the command picks
# which. See deploy/docker-entrypoint.sh.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000

WORKDIR /app

# Every dependency ships a manylinux wheel for 3.12 (psycopg-binary included),
# so no compiler or libpq-dev is needed in the image.
COPY requirements.txt .
RUN pip install -r requirements.txt

RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app

COPY --chown=app:app . .

# Static files are baked into the image and served by WhiteNoise. collectstatic
# only needs settings to import, so it runs as a local environment with a
# throwaway key; no real configuration exists at build time.
RUN ENVIRONMENT=build SECRET_KEY=collectstatic-only-not-a-real-key \
    python manage.py collectstatic --noinput \
 && chown -R app:app /app/staticfiles \
 && chmod +x deploy/docker-entrypoint.sh

# Defaults to production so an image started without ENVIRONMENT fails closed
# (strict settings validation) instead of running with development defaults.
# Staging overrides this with ENVIRONMENT=staging.
ENV ENVIRONMENT=production

USER app
EXPOSE 8000

ENTRYPOINT ["/app/deploy/docker-entrypoint.sh"]
CMD ["web"]
