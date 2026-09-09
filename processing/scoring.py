"""Derived numbers for a processing application.

Risk and completeness are computed from the record on every read rather than
stored. They move whenever a cause is added, a section is filled in or a
document is uploaded, and a cached copy would be wrong between those events
and the next write.
"""
from processing.models import SectionKey

# Matches the badge the review desk reads: 55 and above is high, 30 and above
# medium. Backend and UI must agree, or a decision is taken against one number
# and displayed as another.
HIGH_RISK_THRESHOLD = 55
MEDIUM_RISK_THRESHOLD = 30

SECTION_COUNT = len(SectionKey.choices)


def risk_score(application):
    """Sum of the weighted causes on record, capped at 100."""
    causes = application.risk_causes.all()
    return min(100, sum(cause.weight for cause in causes))


def risk_band(score):
    if score >= HIGH_RISK_THRESHOLD:
        return "high"
    if score >= MEDIUM_RISK_THRESHOLD:
        return "medium"
    return "low"


def section_is_complete(section, documents_by_section):
    """A section counts once it carries data and every document it lists a file.

    A document row with no file is a declared-but-unsupplied piece of evidence,
    which is precisely the gap completeness is meant to expose.
    """
    if not section.fields:
        return False
    return all(document.file for document in documents_by_section.get(section.key, []))


def completeness(application):
    """Percent of the ten evidence sections that are complete."""
    documents_by_section = {}
    for document in application.documents.all():
        documents_by_section.setdefault(document.section, []).append(document)
    complete = sum(
        1 for section in application.sections.all() if section_is_complete(section, documents_by_section)
    )
    return round(complete / SECTION_COUNT * 100)


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
