from django.conf import settings
from django.core.management.base import BaseCommand

from accounts.models import User


class Command(BaseCommand):
    """Creates (or promotes) a superuser from ADMIN_EMAIL/ADMIN_BOOTSTRAP_SECRET.

    Exists so you can reach /admin/ without Render shell access. On first run
    it creates the account with that password; on later runs it only makes
    sure is_staff/is_superuser are set — it never overwrites a password you
    may have since changed through the admin itself.
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
        if created:
            user.set_password(password)
            user.save(update_fields=["password"])
            self.stdout.write(f"Created admin user {email}.")
            return

        changed_fields = []
        for field in ("is_staff", "is_superuser", "is_active"):
            if not getattr(user, field):
                setattr(user, field, True)
                changed_fields.append(field)
        if changed_fields:
            user.save(update_fields=changed_fields)
            self.stdout.write(f"Promoted existing user {email} to admin ({', '.join(changed_fields)}).")
        else:
            self.stdout.write(f"{email} is already an admin; left password unchanged.")
