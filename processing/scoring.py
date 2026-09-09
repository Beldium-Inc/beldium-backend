"""Derived numbers for a processing application.

Risk and completeness are computed from the record on every read rather than
stored. They move whenever a cause is added, a prompt is answered or a document
is uploaded, and a cached copy would be wrong between those events and the next
write.
"""
from processing import checklist
from processing.models import SectionKey

# Matches the badge the review desk reads: 55 and above is high, 30 and above
# medium. Backend and UI must agree, or a decision is taken against one number
# and displayed as another.
HIGH_RISK_THRESHOLD = 55
MEDIUM_RISK_THRESHOLD = 30

SECTION_COUNT = len(SectionKey.choices)


def risk_score(application):
    """Sum of the weighted causes on record, capped at 100."""
    return min(100, sum(cause.weight for cause in application.risk_causes.all()))


def risk_band(score):
    if score >= HIGH_RISK_THRESHOLD:
        return "high"
    if score >= MEDIUM_RISK_THRESHOLD:
        return "medium"
    return "low"


def _answers(section):
    """``{prompt label: answer}``, ignoring blanks and malformed entries."""
    answers = {}
    for field in section.fields or []:
        if not isinstance(field, dict):
            continue
        label = field.get("label")
        value = field.get("value")
        if isinstance(label, str) and isinstance(value, str) and value.strip():
            answers[label] = value.strip()
    return answers


def section_gaps(application, section, documents_by_section=None):
    """What this section is still missing: unanswered prompts, absent files.

    A document row with no file is a declared-but-unsupplied piece of evidence,
    which is precisely the gap completeness exists to expose — so an absent row
    and an empty row count the same.
    """
    if documents_by_section is None:
        documents_by_section = _documents_by_section(application)

    answers = _answers(section)
    missing_prompts = [
        prompt for prompt in checklist.required_prompts(section.key) if prompt not in answers
    ]

    supplied = {
        document.name for document in documents_by_section.get(section.key, []) if document.file
    }
    required = checklist.required_documents_by_section(application.processing_type)[section.key]
    missing_documents = [name for name in required if name not in supplied]

    return {"missing_prompts": missing_prompts, "missing_documents": missing_documents}


def _documents_by_section(application):
    grouped = {}
    for document in application.documents.all():
        grouped.setdefault(document.section, []).append(document)
    return grouped


def section_is_complete(application, section, documents_by_section=None):
    gaps = section_gaps(application, section, documents_by_section)
    return not gaps["missing_prompts"] and not gaps["missing_documents"]


def completeness(application):
    """Percent of the ten evidence sections that are complete."""
    documents_by_section = _documents_by_section(application)
    complete = sum(
        1
        for section in application.sections.all()
        if section_is_complete(application, section, documents_by_section)
    )
    return round(complete / SECTION_COUNT * 100)


def outstanding(application):
    """Every gap across the application, so the applicant sees one list.

    Ordered by section so the list reads in the same order as the form.
    """
    documents_by_section = _documents_by_section(application)
    sections = {section.key: section for section in application.sections.all()}
    result = []
    for key, label in SectionKey.choices:
        section = sections.get(key)
        if section is None:
            # A section the API has not laid down yet is entirely outstanding.
            result.append({
                "section": key,
                "label": label,
                "missing_prompts": checklist.required_prompts(key),
                "missing_documents": checklist.required_documents_by_section(
                    application.processing_type
                )[key],
            })
            continue
        gaps = section_gaps(application, section, documents_by_section)
        if gaps["missing_prompts"] or gaps["missing_documents"]:
            result.append({"section": key, "label": label, **gaps})
    return result


def review_summary(application):
    """How far the desk has got through the ten sections."""
    states = [section.review_state for section in application.sections.all()]
    return {
        "sections_total": SECTION_COUNT,
        "sections_present": len(states),
        "sections_verified": states.count("verified"),
        "sections_rejected": states.count("rejected"),
        "sections_flagged": states.count("flagged") + states.count("info_requested"),
    }
