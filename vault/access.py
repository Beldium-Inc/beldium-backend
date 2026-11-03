"""Who may see and add what in each sector's vault.

A sector is switched on by giving it a resolver here. The resolver answers,
from the caller's memberships and never from the request, one of:

``desk``    the sector's Beldium desk: sees every document in the sector,
            files Beldium's own records or records for any organisation, and
            gives the second approval
``member``  a partner organisation: sees and files only its own records
None        no access to this sector's vault
"""
from dataclasses import dataclass

from django.db.models import Q

from compliance.models import Sector

DESK = "desk"
MEMBER = "member"


@dataclass(frozen=True)
class Standing:
    audience: str
    organisation: object = None

    @property
    def is_desk(self):
        return self.audience == DESK


def _quality(user):
    from quality import services

    role = services.derive_role(user)
    if role == "operator":
        return Standing(DESK)
    if role == "partner":
        organisation = services.user_organisation(user)
        if organisation:
            return Standing(MEMBER, organisation)
    return None


RESOLVERS = {
    Sector.QUALITY: _quality,
}


def enabled_sectors():
    return [sector for sector in Sector if sector in RESOLVERS]


def standing(user, sector):
    if not user or not user.is_authenticated or not user.is_active:
        return None
    resolver = RESOLVERS.get(sector)
    return resolver(user) if resolver else None


def visible_filter(user):
    """A filter for every vault document the caller may read, across sectors."""
    condition = Q(pk__in=[])
    for sector in enabled_sectors():
        found = standing(user, sector)
        if found is None:
            continue
        if found.is_desk:
            condition |= Q(sector=sector)
        else:
            condition |= Q(sector=sector, organisation=found.organisation)
    return condition


def can_approve(user, document):
    """A second approver is someone on the sector desk other than the uploader.

    TODO(Q&C, David): the reply of 6 October proposes David as the second
    approver for the pilot. Until a named approver list is agreed, any desk
    member other than the uploader may approve.
    """
    found = standing(user, document.sector)
    return bool(found and found.is_desk and document.uploaded_by_id != user.pk)
