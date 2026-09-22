"""Review timeline and activity feed for an organisation's verification.

Nothing here is stored: stages are derived from the organisation, its
compliance application and the audit trail, so the timeline can never drift
from the state the reviewers actually changed.
"""
from accounts.models import AccountAuditEvent

# Audit event types an applicant may see, with the sentence shown for each.
# Anything not listed (logins, membership changes, IP data) stays private.
ACTIVITY_LABELS = {
    "organisation.submitted": "Application submitted for review.",
    "organisation.verified": "Verification approved - full miner access granted.",
    "organisation.rejected": "Verification was not approved.",
    "compliance.application_created": "Application started.",
    "compliance.application_submitted": "Application submitted for review.",
    "compliance.application_decided": "The review team recorded a decision.",
    "compliance.document_requested": "The review team requested a document.",
    "compliance.document_reviewed": "A document was reviewed.",
    "compliance.condition_created": "An approval condition was issued.",
    "compliance.condition_evidence_submitted": "Evidence was submitted for a condition.",
    "compliance.condition_evidence_reviewed": "Condition evidence was reviewed.",
    "mining.section_reviewed": "A site review section was decided.",
    "mining.document_reviewed": "A site document was reviewed.",
    "mining.site_registered": "A mine site was registered.",
}

DECISION_LABELS = {
    "verified": "Verified - full miner access granted.",
    "conditionally_approved": "Conditionally approved - clear the listed conditions.",
    "rejected": "Not approved. See the reviewer's notes.",
    "action_required": "The review team needs more information from you.",
}


def _iso(value):
    return value.isoformat() if value else None


def _stage(key, title, description, state, at=None, detail=""):
    return {"key": key, "title": title, "description": description, "state": state, "at": _iso(at), "detail": detail}


def _last_event_time(events, *types):
    for event in events:  # newest first
        if event.event_type in types:
            return event.created_at
    return None


def _org_events(organisation):
    from django.db.models import Q

    site_ids = [str(pk) for pk in organisation.mine_sites.values_list("id", flat=True)]
    return list(
        AccountAuditEvent.objects.filter(
            Q(metadata__organisation_id=str(organisation.id)) | Q(metadata__site_id__in=site_ids)
        ).select_related("actor")
    )


def _site_progress(organisation):
    """(verified sites, total sites) - a site is verified once all ten sections are."""
    sites = list(organisation.mine_sites.all())
    verified = sum(1 for site in sites if site.status == "operational")
    return verified, len(sites)


def _document_states(organisation, application):
    """Every submitted document's review state: verified, awaiting, requested or rejected.

    Covers the compliance application's documents and the mining documents
    held against the organisation's sites. A mining document with no file yet
    is not counted - nothing has been submitted for a reviewer to check.
    """
    states = []
    if application:
        mapping = {"verified": "verified", "submitted": "awaiting", "requested": "requested", "rejected": "rejected"}
        states += [mapping.get(d.status, "awaiting") for d in application.documents.all()]
    from mining.models import DocumentRecord

    mapping = {"verified": "verified", "pending": "awaiting", "rejected": "rejected", "expired": "rejected"}
    for doc in DocumentRecord.objects.filter(site__organisation=organisation).exclude(file=""):
        states.append(mapping.get(doc.status, "awaiting"))
    return states


def build_timeline(organisation):
    events = _org_events(organisation)
    application = getattr(organisation, "compliance_application", None)
    status = application.status if application else organisation.verification_status
    submitted_at = (application.submitted_at if application else None) or organisation.submitted_at
    decided = status in {"verified", "rejected", "conditionally_approved"}
    needs_info = status == "action_required"
    submitted = bool(submitted_at) or organisation.verification_status != "draft"

    docs = _document_states(organisation, application)
    awaiting_review = [d for d in docs if d == "awaiting"]
    requested = [d for d in docs if d in {"requested", "rejected"}]
    docs_reviewed_at = _last_event_time(events, "compliance.document_reviewed", "mining.document_reviewed")
    verified_sites, total_sites = _site_progress(organisation)
    verified = organisation.verification_status == "verified" or status == "verified"
    rejected = status == "rejected"

    # Completion of each stage is decided once, from the facts. A verified
    # organisation is complete everywhere: whatever the desk did or did not
    # click along the way, the outcome is what the miner sees.
    docs_done = verified or decided or bool(docs and not awaiting_review and not requested)
    sites_done = verified or decided or bool(total_sites and verified_sites == total_sites)

    def open_state(done, attention):
        if done:
            return "complete"
        if not submitted:
            return "upcoming"
        return "attention" if attention else "current"

    stages = [
        _stage(
            "submitted", "Application submitted", "Organisation application received by Beldium.",
            "complete" if (submitted or verified) else "current",
            submitted_at or (organisation.verified_at if verified else None),
        )
    ]

    parts = []
    if docs:
        parts.append(f"{docs.count('verified')} of {len(docs)} documents verified")
        if awaiting_review:
            parts.append(f"{len(awaiting_review)} awaiting review")
        if requested:
            parts.append(f"{len(requested)} need attention")
    else:
        parts.append("No documents to review")
    stages.append(
        _stage(
            "document_review", "Document review", "Documents and mandatory fields are checked by a reviewer.",
            open_state(docs_done, bool(requested) or needs_info),
            docs_reviewed_at if docs_done else None, " · ".join(parts),
        )
    )

    stages.append(
        _stage(
            "site_verification", "Site verification", "Declared sites are reviewed section by section.",
            open_state(sites_done, False),
            None,
            f"{verified_sites} of {total_sites} sites verified" if total_sites else "No sites declared",
        )
    )

    if verified or decided:
        decided_at = (application.reviewed_at if application else None) or organisation.verified_at
        outcome = DECISION_LABELS.get(status, DECISION_LABELS["verified"] if verified else "")
        if rejected:
            outcome = organisation.rejection_reason or outcome
        stages.append(
            _stage("decision", "Decision", "Verification outcome issued.", "failed" if rejected else "complete", decided_at, outcome)
        )
    else:
        ready = submitted and docs_done and sites_done
        stages.append(
            _stage(
                "decision", "Decision", "Verification outcome issued.",
                "attention" if needs_info else "current" if ready else "upcoming", None,
                DECISION_LABELS["action_required"] if needs_info else ("Awaiting the review team's decision." if ready else ""),
            )
        )

    return {
        "status": organisation.verification_status,
        "application_status": application.status if application else None,
        "application_reference": application.reference if application else None,
        "documents": {
            "total": len(docs),
            "verified": docs.count("verified"),
            "awaiting": len(awaiting_review),
            "attention": len(requested),
        },
        "sites": {"verified": verified_sites, "total": total_sites},
        "stages": stages,
        "activity": [
            {
                "id": str(event.id),
                "event_type": event.event_type,
                "message": ACTIVITY_LABELS[event.event_type],
                "actor": _actor_label(event),
                "at": _iso(event.created_at),
            }
            for event in events
            if event.event_type in ACTIVITY_LABELS
        ][:50],
    }


def _actor_label(event):
    if not event.actor:
        return "Beldium"
    if event.actor.is_staff:
        return "Beldium review team"
    return event.actor.full_name or event.actor.email
