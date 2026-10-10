"""Organisation-level verification decided by the mining compliance desk.

Verification is staged and happens inside a claimed application's review:

1. the mining organisation, once every organisation-level document it filed
   (incorporation, tax clearance) has been verified;
2. then its mining site, once every site document is verified too.

Readiness is computed on read, never stored.
"""
from mining.models import DocumentRecord

# The miner application files these two as organisation papers; everything
# else it files (licence, environmental plan, survey…) belongs to the site.
ORGANISATION_DOCUMENT_NAMES = {"certificate of incorporation", "tax clearance certificate"}


def is_organisation_document(document):
    return document.name.strip().lower() in ORGANISATION_DOCUMENT_NAMES


def filed_documents(organisation):
    """The documents verification is judged on.

    A copy the miner has since replaced is left out: once the desk rejects a
    document and accepts its replacement, the rejected original must not go on
    holding the organisation back.
    """
    return [
        d
        for d in DocumentRecord.objects.filter(site__organisation=organisation)
        .exclude(file="")
        .exclude(status=DocumentRecord.Status.SUPERSEDED)
    ]


def file_replacement(document, upload, user):
    """File ``upload`` as the new copy of ``document`` and retire the old one.

    The replacement keeps the original's name and category, so it is reviewed
    in the same stage (organisation or site) and under the same heading.
    """
    replacement = DocumentRecord.objects.create(
        site=document.site, name=document.name, category=document.category,
        file=upload, uploaded_by=user, replaces=document,
    )
    document.status = DocumentRecord.Status.SUPERSEDED
    document.save(update_fields=["status", "updated_at"])
    return replacement


def split_documents(organisation):
    docs = filed_documents(organisation)
    org_docs = [d for d in docs if is_organisation_document(d)]
    site_docs = [d for d in docs if not is_organisation_document(d)]
    return org_docs, site_docs


def site_blockers(organisation):
    """Why the mine site cannot be verified yet."""
    blockers = []
    if organisation is None or organisation.verification_status != "verified":
        blockers.append("Verify the mining organisation first.")
    _, site_docs = split_documents(organisation) if organisation else ([], [])
    if not site_docs:
        blockers.append("No mining site documents have been submitted.")
    else:
        unverified = [d for d in site_docs if d.status != "verified"]
        if unverified:
            blockers.append(f"{len(unverified)} mining site document(s) not verified.")
    return blockers


def readiness(organisation):
    sites = list(organisation.mine_sites.all())
    org_docs, site_docs = split_documents(organisation)
    blockers = []
    if not org_docs:
        blockers.append("No organisation documents have been submitted.")
    unverified_org_docs = [d for d in org_docs if d.status != "verified"]
    if unverified_org_docs:
        blockers.append(f"{len(unverified_org_docs)} organisation document(s) not verified.")
    if hasattr(organisation, "compliance_application"):
        blockers.append("This organisation is decided through its compliance application.")
    return {
        "sites_total": len(sites),
        "sites_verified": len([s for s in sites if s.status == "operational"]),
        "documents_total": len(org_docs),
        "documents_verified": len(org_docs) - len(unverified_org_docs),
        "site_documents_total": len(site_docs),
        "site_documents_verified": len([d for d in site_docs if d.status == "verified"]),
        "blockers": blockers,
        "ready": not blockers,
    }
