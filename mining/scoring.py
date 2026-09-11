"""Derived numbers for a mine site.

Completeness and the review summary are computed from the record on every
read rather than stored, for the same reason as ``processing.scoring``: they
move whenever a section is answered or evidence is uploaded, and a cached
copy would be wrong between those events and the next write. The compliance
score itself stays a stored field on ``MineSite`` (set by a review decision),
mirroring how ``Processor.compliance_score`` is only ever set at a decision
point, never recomputed on read.
"""
from mining import checklist
from mining.models import SectionKey

SECTION_COUNT = len(SectionKey.choices)


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


def section_gaps(section):
    """What this section is still missing: unanswered required prompts."""
    answers = _answers(section)
    missing_prompts = [prompt for prompt in checklist.required_prompts(section.key) if prompt not in answers]
    return {"missing_prompts": missing_prompts}


def section_is_complete(section):
    return not section_gaps(section)["missing_prompts"]


def completeness(site):
    """Percent of the ten review sections that are complete."""
    sections = list(site.sections.all())
    if not sections:
        return 0
    complete = sum(1 for section in sections if section_is_complete(section))
    return round(complete / SECTION_COUNT * 100)


def outstanding(site):
    """Every gap across the site's sections, section by section."""
    sections = {section.key: section for section in site.sections.all()}
    result = []
    for key, label in SectionKey.choices:
        section = sections.get(key)
        if section is None:
            result.append({"section": key, "label": label, "missing_prompts": checklist.required_prompts(key)})
            continue
        gaps = section_gaps(section)
        if gaps["missing_prompts"]:
            result.append({"section": key, "label": label, **gaps})
    return result


def review_summary(site):
    """How far the desk has got through the ten sections."""
    states = [section.status for section in site.sections.all()]
    return {
        "sections_total": SECTION_COUNT,
        "sections_present": len(states),
        "sections_verified": states.count("verified"),
        "sections_rejected": states.count("rejected"),
        "sections_flagged": states.count("flagged") + states.count("info_requested"),
    }


def weighted_score(site):
    """A site's compliance score as the weighted average of its review sections.

    Falls back to the score factors when no sections carry a score yet (a
    freshly admitted site), and to the stored value when neither is present —
    a decision point is what actually moves ``compliance_score``; this is
    only what a reviewer sees when deciding whether to move it.
    """
    sections = [s for s in site.sections.all() if s.weight]
    if sections:
        total_weight = sum(s.weight for s in sections)
        return round(sum(s.weight * s.score for s in sections) / total_weight)
    factors = [f for f in site.score_factors.all() if f.weight]
    if factors:
        total_weight = sum(f.weight for f in factors)
        return round(sum(f.weight * f.score for f in factors) / total_weight)
    return site.compliance_score
