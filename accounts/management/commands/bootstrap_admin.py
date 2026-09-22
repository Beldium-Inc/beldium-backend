from django.conf import settings
from django.core.management.base import BaseCommand

from accounts.models import User


class Command(BaseCommand):
    """Creates (or promotes) a superuser from ADMIN_EMAIL/ADMIN_BOOTSTRAP_SECRET.

    Exists so you can reach /admin/ without Render shell access. Always syncs
    the password to ADMIN_BOOTSTRAP_SECRET on every boot — this is also the
    recovery path if that password is ever lost: change the env var, redeploy,
    log in with the new value. If ADMIN_EMAIL already belongs to an existing
    account (e.g. carried over from the legacy import), this takes it over as
    the admin login; use a dedicated address if that's not what you want.
    """

    help = "Create or promote the admin user from ADMIN_EMAIL/ADMIN_BOOTSTRAP_SECRET."

    def handle(self, *args, **options):
        email = getattr(settings, "ADMIN_EMAIL", "").strip().lower()
        password = getattr(settings, "ADMIN_BOOTSTRAP_SECRET", "")
        if not email or not password:
            self.stdout.write("ADMIN_EMAIL/ADMIN_BOOTSTRAP_SECRET not set, skipping admin bootstrap.")
            return

        user, created = User.objects.get_or_create(
            email=email,
            defaults={"is_staff": True, "is_superuser": True, "is_active": True},
        )
        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        user.set_password(password)
        user.save(update_fields=["is_staff", "is_superuser", "is_active", "password"])
        self.stdout.write(f"{'Created' if created else 'Synced'} admin user {email}.")
