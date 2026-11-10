from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from rest_framework import serializers
from django.utils import timezone

from common.exceptions import ConflictError
from onboarding.models import ParticipantApplication, RegulatorAccessRequest
from onboarding.participants import TYPE_FOR_SECTOR
from organisations import rights
from organisations.audit import record_event
from organisations.models import (
    MembershipRole,
    Organisation,
    OrganisationMembership,
    OrganisationType,
    SectorAccessGrant,
)


def active_membership_in_sector(user, sector):
    """The caller's active membership in a company of ``sector``, if any.
    The pilot allows one active company per sector (D7)."""
    for membership in OrganisationMembership.objects.filter(user=user, is_active=True).select_related(
        "organisation__compliance_application"
    ).prefetch_related("organisation__sector_grants"):
        if rights.organisation_matches(membership.organisation, sector) is True:
            return membership
    return None


@transaction.atomic
def register_participant(request, *, sector, name, registration_number="", participant_subtype=""):
    """Create the company, make the caller its active owner and open its
    draft application, all in one transaction (BE-O3)."""
    user = request.user
    if active_membership_in_sector(user, sector):
        raise ConflictError(
            "You already belong to a company in this sector. The pilot allows one per person.",
            code="already_in_sector",
        )
    organisation_type = TYPE_FOR_SECTOR[sector]
    if Organisation.objects.filter(name__iexact=name, organisation_type=organisation_type).exclude(
        verification_status="rejected"
    ).exists():
        raise ConflictError(
            f'A company named "{name}" is already registered. Ask its administrator to invite you instead.',
            code="organisation_exists",
        )
    try:
        with transaction.atomic():
            organisation = Organisation.objects.create(
                name=name,
                organisation_type=organisation_type,
                participant_subtype=participant_subtype,
                registration_number=registration_number,
            )
    except IntegrityError:
        raise ConflictError(
            "A company with this registration number is already registered.",
            code="organisation_exists",
        )
    OrganisationMembership.objects.create(
        organisation=organisation, user=user, role=MembershipRole.OWNER,
        approved_at=timezone.now(),
    )
    application = ParticipantApplication.objects.create(
        organisation=organisation,
        sector=sector,
        participant_subtype=participant_subtype,
        company={"legalName": name, "rcNumber": registration_number} if registration_number else {"legalName": name},
        created_by=user,
    )
    record_event(request, "organisation.created", organisation=organisation, sector=sector)
    return application


# The answers in the company section that the organisation's own profile
# keeps too.
COMPANY_PROFILE_FIELDS = {
    "legalName": "name",
    "rcNumber": "registration_number",
    "tin": "tax_identifier",
    "email": "email",
    "phone": "phone_number",
    "website": "website",
    "address": "address",
    "state": "state",
}


def sync_company_profile(organisation, company):
    changed = []
    for answer, field in COMPANY_PROFILE_FIELDS.items():
        if answer not in company:
            continue
        value = str(company.get(answer) or "").strip()
        if field == "state" and value == "Select…":
            value = ""
        if field == "name" and not value:
            continue
        max_length = Organisation._meta.get_field(field).max_length
        if max_length:
            value = value[:max_length]
        if getattr(organisation, field) != value:
            setattr(organisation, field, value)
            changed.append(field)
    if not changed:
        return
    others = [f.name for f in Organisation._meta.fields if f.name not in changed]
    try:
        organisation.full_clean(exclude=others, validate_unique=False, validate_constraints=False)
    except DjangoValidationError as exc:
        raise serializers.ValidationError({"company": exc.message_dict})
    try:
        with transaction.atomic():
            organisation.save(update_fields=changed + ["updated_at"])
    except IntegrityError:
        raise ConflictError(
            "A company with this registration number is already registered.",
            code="organisation_exists",
        )


@transaction.atomic
def approve_regulator_request(request, access_request, sectors):
    """Create the agency's organisation, make the requester its owner and
    write the read-only grants for the approved sectors (BE-O5)."""
    from logistics.models import LogisticsAccessGrant, LogisticsCompany
    from mining.models import MiningAccessGrant

    access_request = RegulatorAccessRequest.objects.select_for_update().get(pk=access_request.pk)
    if access_request.status != RegulatorAccessRequest.Status.PENDING:
        raise ConflictError("This request has already been decided.", code="regulator_request_decided")
    now = timezone.now()
    organisation = Organisation.objects.create(
        name=access_request.agency_name,
        organisation_type=OrganisationType.REGULATOR,
        email=access_request.official_email,
        state=access_request.state,
        address=access_request.office,
        verification_status="verified",
        submitted_at=access_request.created_at,
        verified_at=now,
        verified_by=request.user,
    )
    OrganisationMembership.objects.update_or_create(
        organisation=organisation,
        user=access_request.requester,
        defaults={
            "role": MembershipRole.OWNER,
            "title": access_request.requester_title,
            "is_active": True,
            "approved_by": request.user,
            "approved_at": now,
        },
    )
    for sector in sectors:
        SectorAccessGrant.objects.create(organisation=organisation, sector=sector, granted_by=request.user)
    requester = access_request.requester
    if "logistics" in sectors:
        for company in LogisticsCompany.objects.only("id"):
            LogisticsAccessGrant.objects.update_or_create(
                company=company, user=requester, role="regulator", defaults={"is_active": True},
            )
    if "mining" in sectors:
        for mining_org in Organisation.objects.filter(organisation_type=OrganisationType.MINING_COMPANY).only("id"):
            MiningAccessGrant.objects.update_or_create(
                organisation=mining_org, user=requester, role="regulator", defaults={"is_active": True},
            )
    access_request.status = RegulatorAccessRequest.Status.APPROVED
    access_request.approved_sectors = list(sectors)
    access_request.decided_by = request.user
    access_request.decided_at = now
    access_request.organisation = organisation
    access_request.save(update_fields=[
        "status", "approved_sectors", "decided_by", "decided_at", "organisation", "updated_at",
    ])
    record_event(
        request, "regulator.access_approved", organisation=organisation,
        request_id=str(access_request.id), sectors=list(sectors),
    )
    return access_request


@transaction.atomic
def reject_regulator_request(request, access_request, reason):
    access_request = RegulatorAccessRequest.objects.select_for_update().get(pk=access_request.pk)
    if access_request.status != RegulatorAccessRequest.Status.PENDING:
        raise ConflictError("This request has already been decided.", code="regulator_request_decided")
    access_request.status = RegulatorAccessRequest.Status.REJECTED
    access_request.rejection_reason = reason
    access_request.decided_by = request.user
    access_request.decided_at = timezone.now()
    access_request.save(update_fields=["status", "rejection_reason", "decided_by", "decided_at", "updated_at"])
    record_event(request, "regulator.access_rejected", request_id=str(access_request.id))
    return access_request
