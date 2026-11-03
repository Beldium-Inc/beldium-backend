"""Every change to the vault goes through here, so the access check, the new
record and its activity entry are written in one transaction."""
import hashlib

from django.db import transaction
from rest_framework.exceptions import PermissionDenied

from common.exceptions import ConflictError
from vault import access
from vault.models import VaultAction, VaultActivity, VaultApproval, VaultDocument


def fingerprint(uploaded_file):
    digest = hashlib.sha256()
    for chunk in uploaded_file.chunks():
        digest.update(chunk)
    uploaded_file.seek(0)
    return digest.hexdigest()


def _log(document, user, action, detail=""):
    VaultActivity.objects.create(document=document, actor=user, action=action, detail=detail[:255])


def _refuse_duplicate(sector, organisation, sha256):
    existing = VaultDocument.objects.filter(sector=sector, organisation=organisation, sha256=sha256).first()
    if existing:
        raise ConflictError(
            f"This exact file is already backed up as {existing.reference} ({existing.title}).",
            code="duplicate_file",
        )


@transaction.atomic
def upload(user, *, sector, category, title, uploaded_file, description="", organisation=None):
    found = access.standing(user, sector)
    if found is None:
        raise PermissionDenied("You cannot add documents to this vault.")
    if category.sector != sector or not category.is_active:
        raise PermissionDenied("That category does not belong to this vault.")
    if found.is_desk:
        owner = organisation
    else:
        if organisation is not None and organisation.pk != found.organisation.pk:
            raise PermissionDenied("You can only add documents for your own organisation.")
        owner = found.organisation

    sha256 = fingerprint(uploaded_file)
    _refuse_duplicate(sector, owner, sha256)
    document = VaultDocument.objects.create(
        sector=sector, category=category, organisation=owner, title=title, description=description,
        file=uploaded_file, original_name=uploaded_file.name[:255],
        content_type=getattr(uploaded_file, "content_type", "") or "", size_bytes=uploaded_file.size,
        sha256=sha256, uploaded_by=user,
    )
    _log(document, user, VaultAction.UPLOADED, uploaded_file.name)
    return document


@transaction.atomic
def add_version(user, current, *, uploaded_file, note=""):
    # Lock the current version so two people cannot both replace it.
    current = VaultDocument.objects.select_for_update().get(pk=current.pk)
    found = access.standing(user, current.sector)
    allowed = found and (found.is_desk or (current.organisation_id and current.organisation_id == found.organisation.pk))
    if not allowed:
        raise PermissionDenied("You cannot add a version to this document.")
    if VaultDocument.objects.filter(supersedes=current).exists():
        raise ConflictError("A newer version already exists. Add the new file to the latest version.")

    sha256 = fingerprint(uploaded_file)
    if sha256 == current.sha256:
        raise ConflictError("This file is identical to the current version.", code="duplicate_file")
    document = VaultDocument.objects.create(
        sector=current.sector, category=current.category, organisation=current.organisation,
        family=current.family, version=current.version + 1, supersedes=current,
        title=current.title, description=current.description, version_note=note,
        file=uploaded_file, original_name=uploaded_file.name[:255],
        content_type=getattr(uploaded_file, "content_type", "") or "", size_bytes=uploaded_file.size,
        sha256=sha256, uploaded_by=user,
    )
    _log(document, user, VaultAction.NEW_VERSION, f"Replaces {current.reference} v{current.version}. {note}".strip())
    return document


@transaction.atomic
def approve(user, document, *, note=""):
    document = VaultDocument.objects.select_for_update().get(pk=document.pk)
    if not access.can_approve(user, document):
        if document.uploaded_by_id == user.pk:
            raise PermissionDenied("A second person must approve your own upload.")
        raise PermissionDenied("Only the Beldium desk can approve vault documents.")
    if VaultApproval.objects.filter(document=document).exists():
        raise ConflictError("This document has already been approved.")
    approval = VaultApproval.objects.create(document=document, approved_by=user, note=note)
    _log(document, user, VaultAction.APPROVED, note)
    return approval


@transaction.atomic
def record_download(user, document):
    _log(document, user, VaultAction.DOWNLOADED)


@transaction.atomic
def check_integrity(user, document):
    digest = hashlib.sha256()
    with document.file.open("rb") as stored:
        for chunk in stored.chunks():
            digest.update(chunk)
    computed = digest.hexdigest()
    intact = computed == document.sha256
    _log(document, user, VaultAction.INTEGRITY_CHECKED, "Intact" if intact else f"Mismatch, stored copy hashes to {computed}")
    return {"intact": intact, "recorded_sha256": document.sha256, "computed_sha256": computed}
