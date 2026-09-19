"""Remove the demo business data created by the ``seed_*`` commands.

Deletes the seeded *company* organisations (mining companies, buyers,
exporters, warehouse operators...) and everything that hangs off them, plus
the seeded applicant/company users that belonged only to them. The seeded
compliance-desk and regulator organisations and their users are kept, so the
review desk can still be logged into.

Dry run by default: nothing is committed unless ``--yes`` is passed.
"""
from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import models
from django.db import transaction
from django.db.models import ProtectedError

from accounts.models import User
from organisations.models import Organisation

SEEDED_COMPANY_PREFIX = "Beldium "
EXTRA_SEEDED_COMPANIES = ["Ilesa Mineral Processing Ltd"]
SEED_EMAIL_SUFFIXES = (".test", "@beldium.local")


class Command(BaseCommand):
    help = "Delete seeded demo companies and their data (dry run unless --yes)."

    def add_arguments(self, parser):
        parser.add_argument("--yes", action="store_true", help="Commit the deletion.")

    def handle(self, *args, **options):
        companies = Organisation.objects.filter(organisation_type="mining_company").filter(
            name__startswith=SEEDED_COMPANY_PREFIX
        ) | Organisation.objects.filter(name__in=EXTRA_SEEDED_COMPANIES)
        companies = companies.distinct()
        company_ids = list(companies.values_list("id", flat=True))

        users = [
            u
            for u in User.objects.filter(is_staff=False, is_superuser=False)
            if u.email.endswith(SEED_EMAIL_SUFFIXES)
            and not u.organisation_memberships.exclude(organisation_id__in=company_ids).exists()
        ]

        self.stdout.write("Organisations to delete:")
        for org in companies.order_by("name"):
            self.stdout.write(f"  - {org.name}")
        self.stdout.write("Users to delete:")
        for user in users:
            self.stdout.write(f"  - {user.email}")

        with transaction.atomic():
            totals = {}
            # SET_NULL relations (mine sites, applications...) would otherwise be
            # orphaned rather than removed, and keep showing on the desk.
            for model in apps.get_models():
                for field in model._meta.get_fields():
                    if (
                        isinstance(field, models.ForeignKey)
                        and field.related_model is Organisation
                        and field.remote_field.on_delete is models.SET_NULL
                        and model is not User
                    ):
                        self._delete(model.objects.filter(**{f"{field.name}__in": company_ids}), totals)
            self._delete(companies, totals)
            self._delete(User.objects.filter(pk__in=[u.pk for u in users]), totals)
            self.stdout.write("\nRows removed:")
            for model, count in sorted(totals.items()):
                self.stdout.write(f"  {model}: {count}")
            if not options["yes"]:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING("\nDry run - nothing was deleted. Re-run with --yes."))
            else:
                self.stdout.write(self.style.SUCCESS("\nDeleted."))

    def _delete(self, queryset, totals, depth=0):
        """Delete, first removing whatever PROTECTs the rows (transactions, invoices, RFQs...).

        Those records only exist because they reference a seeded company, so
        they are demo data too. Bounded so a cycle cannot loop forever.
        """
        if depth > 8:
            raise RuntimeError("Protected relations nest too deeply; aborting.")
        try:
            _, counts = queryset.delete()
        except ProtectedError as error:
            by_model = {}
            for obj in error.protected_objects:
                by_model.setdefault(type(obj), []).append(obj.pk)
            for model, pks in by_model.items():
                self._delete(model.objects.filter(pk__in=pks), totals, depth + 1)
            _, counts = queryset.delete()
        for model, count in counts.items():
            totals[model] = totals.get(model, 0) + count
