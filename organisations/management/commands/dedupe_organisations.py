from django.core.management.base import BaseCommand

from organisations.dedupe import apply_dedupe, plan_dedupe


class Command(BaseCommand):
    """Merge duplicate Organisation rows created before the create-time
    duplicate guard existed. See organisations/dedupe.py for the full
    keeper/loser rule. Defaults to a dry run; pass --apply to actually delete.
    """

    help = "Preview or apply cleanup of duplicate Organisation rows (same name + type)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply", action="store_true", help="Actually delete the duplicates (default is dry-run)."
        )

    def handle(self, *args, **options):
        report = plan_dedupe()

        if not report.groups and not report.skipped_ambiguous:
            self.stdout.write("No duplicate organisations found.")
            return

        for group in report.groups:
            self.stdout.write(
                f"\n{group.name} ({group.organisation_type}) — keeping {group.keeper_beldium_id or group.keeper_id}"
            )
            for loser in group.losers:
                members = ", ".join(loser["member_emails"]) or "no members"
                self.stdout.write(
                    f"  - remove {loser['beldium_id'] or loser['id']} "
                    f"[{loser['verification_status']}, {loser['site_count']} sites, "
                    f"{loser['mining_application_count']} mining applications, members: {members}]"
                )

        for skipped in report.skipped_ambiguous:
            self.stdout.write(
                self.style.WARNING(
                    f"\nSkipped {skipped['name']} ({skipped['organisation_type']}): {skipped['reason']}. "
                    "Resolve manually."
                )
            )

        self.stdout.write(
            f"\n{report.organisations_removed} organisation(s) would be removed across {len(report.groups)} group(s)."
        )

        if options["apply"]:
            apply_dedupe(report)
            self.stdout.write(self.style.SUCCESS(f"Removed {report.organisations_removed} duplicate organisation(s)."))
        else:
            self.stdout.write(self.style.NOTICE("Dry run only — re-run with --apply to delete."))
