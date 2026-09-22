import logging

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import ProtectedError

from accounts.models import User

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Deletes specific users by email, via the ORM so CASCADE/SET_NULL/PROTECT
    rules across every app are respected — not a raw SQL DELETE, which only
    a plain foreign key check without app-level cascade would block or (worse)
    silently violate.

    Set DELETE_TEST_USER_EMAILS to a comma-separated list of emails to delete
    them on next boot; leave it unset otherwise. Unset it again once done —
    this re-checks the list on every boot for as long as it's set.
    """

    help = "Delete specific users by email (set DELETE_TEST_USER_EMAILS)."

    def handle(self, *args, **options):
        raw = getattr(settings, "DELETE_TEST_USER_EMAILS", "")
        emails = [e.strip().lower() for e in raw.split(",") if e.strip()]
        if not emails:
            self.stdout.write("DELETE_TEST_USER_EMAILS not set, skipping test user deletion.")
            return

        queryset = User.objects.filter(email__iexact=emails[0])
        for email in emails[1:]:
            queryset = queryset | User.objects.filter(email__iexact=email)

        found = list(queryset.values_list("email", flat=True))
        missing = [e for e in emails if e not in [f.lower() for f in found]]
        if missing:
            self.stdout.write(f"Not found (skipped): {', '.join(missing)}")

        try:
            deleted_count, _ = queryset.delete()
        except ProtectedError as exc:
            blocking = ", ".join(f"{obj._meta.label}(pk={obj.pk})" for obj in list(exc.protected_objects)[:10])
            self.stdout.write(
                f"Could not delete {', '.join(found)}: blocked by PROTECT-ed records: {blocking}"
            )
            return

        self.stdout.write(f"Deleted {deleted_count} row(s) for: {', '.join(found)}")
