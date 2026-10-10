"""Carries a Quality & Control organisation's onboarding application to the Q&C desk.

The applicant fills in a ComplianceApplication (compliance/quality_sections.py),
because that is the record the account, sign-in gating and onboarding dashboard
all hang off. The Q&C operators work from QualityApplication. This module keeps
the second a faithful copy of the first, and carries their decisions back.
"""
from compliance.models import ApplicationStatus as ComplianceStatus, ComplianceDocument
from compliance.workflow import TRANSITIONS, transition
from quality import models as m, services

_CATEGORY = {
    "qc_organisation_registration": "organisation",
    "qc_tin": "organisation",
    "qc_laboratory_registration": "laboratory",
    "qc_accreditation_certificate": "laboratory",
    "qc_accreditation_scope": "laboratory",
    "qc_testing_method_evidence": "laboratory",
    "qc_equipment_calibration_certificates": "laboratory",
    "qc_quality_management_documents": "professional",
    "qc_regulatory_approvals": "conditional",
    "qc_insurance": "conditional",
    "qc_other_supporting_documents": "conditional",
}

_DOC_STATUS = {
    ComplianceDocument.Status.VERIFIED: m.DocStatus.VERIFIED,
    ComplianceDocument.Status.REJECTED: m.DocStatus.FLAGGED,
}


def _documents(application):
    return [
        {
            # The compliance document's own id, so a status set at the Q&C desk
            # can find its way back to the file it was set on.
            "id": str(document.id),
            "name": document.title,
            "category": _CATEGORY.get(document.document_type, "conditional"),
            "reference": document.original_name,
            "issuer": "",
            "issued": document.created_at.date().isoformat(),
            "expires": None,
            "status": _DOC_STATUS.get(document.status, m.DocStatus.PENDING),
            "note": document.review_notes,
            "conditional_on": "",
            "file_id": str(document.id),
        }
        for document in application.documents.all()
        if document.file
    ]


def sync_partner_application(application, user):
    """Create or refresh the Q&C desk's record for a submitted application."""
    profile = application.quality_profile
    organisation = profile.get("organisation", {})
    accreditation = profile.get("accreditation", {})
    laboratories = profile.get("laboratories", {}).get("laboratories", [])
    personnel = profile.get("personnel", {}).get("personnel", [])
    applicant = application.created_by

    fields = {
        "organisation": application.organisation,
        "organisation_data": {
            "legal_name": organisation.get("legal_name", application.organisation.name),
            "trading_name": organisation.get("trading_name", ""),
            "organisation_type": organisation.get("organisation_type", ""),
            "registration_no": organisation.get("registration_number", ""),
            "tax_identifier": organisation.get("tax_identifier", ""),
            "registered_address": organisation.get("registered_address", ""),
            "country": organisation.get("country", ""),
            "city": "",
            "incorporated": "",
            "website": organisation.get("website", ""),
            "beneficial_owners": [],
            "contact": {
                "name": getattr(applicant, "full_name", "") or "",
                "email": getattr(applicant, "email", "") or "",
                "phone": getattr(applicant, "phone_number", "") or "",
            },
        },
        "capability_data": {
            "lead_assessor": personnel[0]["full_name"] if personnel else "",
            "credential": personnel[0].get("role", "") if personnel else "",
            "years_experience": 0,
            "registry": "",
            "registry_id": "",
            "staff": [
                {"name": p["full_name"], "role": p.get("role", ""), "competency": p.get("email", ""), "verified": False}
                for p in personnel
            ],
            **profile.get("services", {}),
            "sampling": profile.get("sampling", {}),
        },
        "laboratory_data": {
            "facility": laboratories[0]["name"] if laboratories else "",
            "accreditation": accreditation.get("standard", ""),
            "accreditation_body": accreditation.get("accreditation_body", ""),
            "certificate_no": accreditation.get("accreditation_number", ""),
            "valid_until": accreditation.get("accreditation_expiry", ""),
            "last_surveillance": "",
            "proficiency_testing": "",
            "scope": [
                {"method": method, "matrix": "", "analyte": "", "loq": "", "accredited": True}
                for method in profile.get("testing-methods", {}).get("testing_methods", [])
            ],
            "accredited_scope": accreditation.get("accredited_scope", ""),
            "laboratories": laboratories,
            "equipment": profile.get("equipment", {}).get("equipment", []),
        },
        "documents": _documents(application),
    }

    mirror = m.QualityApplication.objects.filter(compliance_application=application).first()
    if mirror is None:
        mirror = m.QualityApplication(compliance_application=application, audit=[])
        note = ("submitted", "Application submitted")
    elif application.status == ComplianceStatus.UNDER_REVIEW and mirror.status != m.ApplicationStatus.SUBMITTED:
        # Back from the applicant after an information request or a rejection.
        mirror.status = m.ApplicationStatus.SUBMITTED
        mirror.submitted_at = application.submitted_at
        note = ("resubmitted", "Application resubmitted")
    else:
        note = ("updated", "Applicant updated the application")
    for name, value in fields.items():
        setattr(mirror, name, value)
    mirror.audit = [*mirror.audit, services.audit_entry(user, *note)]
    mirror.save()
    return mirror


_DECISION = {
    m.ApplicationStatus.APPROVED: ComplianceStatus.VERIFIED,
    m.ApplicationStatus.REJECTED: ComplianceStatus.REJECTED,
    m.ApplicationStatus.INFO_REQUESTED: ComplianceStatus.ACTION_REQUIRED,
}


def carry_decision_back(mirror, reviewer, note):
    """Apply a Q&C desk decision to the onboarding application it mirrors."""
    application = mirror.compliance_application
    target = _DECISION.get(mirror.status)
    if application is None or target is None or target not in TRANSITIONS[application.status]:
        return
    application.review_notes = note
    application.reviewed_by = reviewer
    application.reviewed_at = mirror.updated_at
    transition(application, target, reviewer=reviewer)


def carry_document_status_back(mirror, doc_id, doc_status, reviewer):
    """A document verified or flagged at the Q&C desk is the applicant's document."""
    application = mirror.compliance_application
    target = {
        m.DocStatus.VERIFIED: ComplianceDocument.Status.VERIFIED,
        m.DocStatus.FLAGGED: ComplianceDocument.Status.REJECTED,
        m.DocStatus.EXPIRED: ComplianceDocument.Status.REJECTED,
        m.DocStatus.PENDING: ComplianceDocument.Status.SUBMITTED,
    }.get(doc_status)
    if application is None or target is None:
        return
    application.documents.filter(pk=doc_id).update(status=target, reviewed_by=reviewer, reviewed_at=mirror.updated_at)
