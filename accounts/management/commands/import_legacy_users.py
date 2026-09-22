import logging
from urllib.parse import urlparse

import psycopg
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import User

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """One-time credential carry-over from the old backend's database.

    Copies email + password hash (plus activity/verification flags) from the
    legacy accounts_user table so existing users can log in unchanged here.
    Read-only against the legacy database. Idempotent: any email that already
    exists in this database is left untouched, so this is safe to run on
    every boot, not just once.
    """

    help = "Import user credentials from the legacy backend's database (set LEGACY_DATABASE_URL)."

    def handle(self, *args, **options):
        legacy_url = getattr(settings, "LEGACY_DATABASE_URL", "")
        if not legacy_url:
            self.stdout.write("LEGACY_DATABASE_URL not set, skipping legacy user import.")
            return

        parsed = urlparse(legacy_url)
        conn = psycopg.connect(
            host=parsed.hostname,
            port=parsed.port or 5432,
            dbname=parsed.path.lstrip("/"),
            user=parsed.username,
            password=parsed.password,
        )
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, email, password, is_active, is_staff, is_superuser,
                           phone_number, account_verified, account_verified_at
                    FROM accounts_user
                    """
                )
                rows = cur.fetchall()
                columns = [desc.name for desc in cur.description]
        finally:
            conn.close()

        existing_emails = set(User.objects.values_list("email", flat=True))
        created = 0
        skipped = 0
        failed = 0

        with transaction.atomic():
            for row in rows:
                data = dict(zip(columns, row))
                email = (data["email"] or "").strip().lower()
                if not email or email in existing_emails:
                    skipped += 1
                    continue
                try:
                    User.objects.create(
                        id=data["id"],
                        email=email,
                        password=data["password"],
                        is_active=data["is_active"],
                        is_staff=data["is_staff"],
                        is_superuser=data["is_superuser"],
                        phone_number=(data["phone_number"] or "")[:30],
                        email_verified_at=data["account_verified_at"] if data["account_verified"] else None,
                    )
                    existing_emails.add(email)
                    created += 1
                except Exception:
                    logger.exception("Failed to import legacy user %s", email)
                    failed += 1

        self.stdout.write(
            f"Legacy user import complete: created={created} skipped_existing={skipped} failed={failed}"
        )
