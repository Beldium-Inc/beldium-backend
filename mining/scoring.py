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


def risk_band(score, *, has_active_critical=False):
    """Banding rules shown on the review page: 80+ low, 60-79 medium, below 60 high."""
    if has_active_critical or score < 60:
        return "high"
    return "low" if score >= 80 else "medium"


def apply_review_outcome(site):
    """Move the site's stored score, risk band and status after a section verdict.

    A site becomes operational only when every one of its ten sections is
    verified; it never leaves that state silently, because a later rejection
    or flag puts it back under review.
    """
    from mining.models import Application, NonConformity, PendingReview

    site.compliance_score = weighted_score(site)
    critical = site.non_conformities.filter(severity="critical").exclude(status="closed").exists()
    site.risk = risk_band(site.compliance_score, has_active_critical=critical)
    reasons = []
    if critical:
        reasons.append("An active critical non-conformity escalates the band to High.")
    summary = review_summary(site)
    if summary["sections_rejected"]:
        reasons.append(f"{summary['sections_rejected']} section(s) rejected.")
    if summary["sections_flagged"]:
        reasons.append(f"{summary['sections_flagged']} section(s) flagged or awaiting information.")
    site.risk_reasons = reasons

    all_verified = summary["sections_verified"] == SECTION_COUNT
    if all_verified:
        site.status = "operational"
        PendingReview.objects.filter(site=site).exclude(status="completed").update(status="completed")
        # The miner's admission application for this site is what their
        # dashboard lists, so it has to follow the desk's outcome.
        Application.objects.filter(site=site, status__in=["pending", "under_review", "info_requested"]).update(
            status="approved", stage="Verified"
        )
    elif site.status == "operational":
        site.status = "under_review"
    site.save(update_fields=["compliance_score", "risk", "risk_reasons", "status", "updated_at"])
    return site
