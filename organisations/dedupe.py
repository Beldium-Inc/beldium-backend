"""Cleanup for duplicate Organisation rows created before the create-time
guard in OrganisationSerializer.validate() existed (see git history: onboarding
minted a fresh Organisation per account with no name/type duplicate check).

Grouping key is (name lower-cased, organisation_type) — the same key the
serializer now blocks on. Within a group, the keeper is the one that's
already verified when exactly one is (never demote a verified organisation
to clean up its own duplicates); otherwise the oldest by created_at, since
that's the one most likely to be the account the org's members actually use.
A group with more than one verified organisation is left alone entirely and
flagged for manual review rather than guessed at.

MineSite and mining.Application point at Organisation with on_delete=SET_NULL,
so a plain `.delete()` on a loser organisation would leave its sites and
applications behind as orphaned rows with organisation=None — which is
exactly the duplicate "Eton"/"Kaduna" clutter on the Mining Sites register.
Those are deleted explicitly alongside the organisation; everything else
related (memberships, invitations, join requests, the compliance application,
the mining profile) cascades on delete.
"""

from collections import defaultdict
from dataclasses import dataclass, field

from django.db import transaction

from mining.models import Application as MiningApplication
from mining.models import MineSite
from organisations.models import Organisation


@dataclass
class DedupeGroup:
    name: str
    organisation_type: str
    keeper_id: str
    keeper_beldium_id: str | None
    loser_ids: list[str] = field(default_factory=list)
    losers: list[dict] = field(default_factory=list)


@dataclass
class DedupeReport:
    groups: list[DedupeGroup] = field(default_factory=list)
    skipped_ambiguous: list[dict] = field(default_factory=list)

    @property
    def organisations_removed(self) -> int:
        return sum(len(g.loser_ids) for g in self.groups)


def _describe(org: Organisation) -> dict:
    return {
        "id": str(org.id),
        "beldium_id": org.beldium_id,
        "name": org.name,
        "organisation_type": org.organisation_type,
        "verification_status": org.verification_status,
        "created_at": org.created_at.isoformat(),
        "member_emails": list(org.memberships.select_related("user").values_list("user__email", flat=True)),
        "site_count": org.mine_sites.count(),
        "mining_application_count": org.mining_applications.count(),
    }


def plan_dedupe() -> DedupeReport:
    """Read-only: computes what a run would do without touching the database."""
    report = DedupeReport()
    by_key: dict[tuple[str, str], list[Organisation]] = defaultdict(list)
    for org in Organisation.objects.all().order_by("created_at"):
        by_key[(org.name.strip().lower(), org.organisation_type)].append(org)

    for (name, organisation_type), orgs in by_key.items():
        if len(orgs) < 2:
            continue

        verified = [o for o in orgs if o.verification_status == "verified"]
        if len(verified) > 1:
            report.skipped_ambiguous.append(
                {
                    "name": name,
                    "organisation_type": organisation_type,
                    "reason": "more than one verified organisation in this group",
                    "organisations": [_describe(o) for o in orgs],
                }
            )
            continue

        keeper = verified[0] if verified else orgs[0]  # orgs is ordered oldest-first
        losers = [o for o in orgs if o.id != keeper.id]

        report.groups.append(
            DedupeGroup(
                name=keeper.name,
                organisation_type=organisation_type,
                keeper_id=str(keeper.id),
                keeper_beldium_id=keeper.beldium_id,
                loser_ids=[str(o.id) for o in losers],
                losers=[_describe(o) for o in losers],
            )
        )

    return report


@transaction.atomic
def apply_dedupe(report: DedupeReport) -> DedupeReport:
    """Deletes every loser organisation named in `report` (from a prior plan_dedupe() call)."""
    loser_ids = [loser_id for group in report.groups for loser_id in group.loser_ids]
    MiningApplication.objects.filter(organisation_id__in=loser_ids).delete()
    MineSite.objects.filter(organisation_id__in=loser_ids).delete()
    Organisation.objects.filter(id__in=loser_ids).delete()
    return report
