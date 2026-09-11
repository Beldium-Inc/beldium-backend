"""What a mine site's review has to answer and evidence, section by section.

This is the compliance checklist, and it lives on the server for the same
reason as ``processing.checklist``: completeness is computed here, so the
definition of "complete" cannot be a client's opinion of it. Unlike the
processing register, a mine site's requirements do not vary by mineral, so
one checklist covers every site.
"""
from mining.models import SectionKey

# Prompts per section. `required` prompts gate completeness; the rest are
# recorded when offered but never block a submission.
PROMPTS = {
    SectionKey.CORPORATE: [
        ("Registered name", True),
        ("CAC RC number", True),
        ("Tax Identification Number (TIN)", True),
        ("Company type", True),
        ("Directors declared", True),
        ("Beneficial ownership disclosed", True),
    ],
    SectionKey.LICENCE: [
        ("Mining licence class", True),
        ("Issuing authority", True),
        ("Licence number", True),
        ("Renewal history", False),
    ],
    SectionKey.SITE: [
        ("Site address", True),
        ("Land title / C of O", True),
        ("Site area", True),
        ("Perimeter security", True),
        ("GPS coordinates", True),
        ("Access road condition", False),
    ],
    SectionKey.OWNERSHIP: [
        ("Beneficial owners declared", True),
        ("Community/host agreement", True),
        ("Royalty arrangement", False),
    ],
    SectionKey.ENVIRONMENTAL: [
        ("EIA / EMP status", True),
        ("Effluent discharge route", True),
        ("Air/dust monitoring", True),
        ("Rehabilitation plan", True),
        ("Community grievance log", False),
    ],
    SectionKey.SAFETY: [
        ("HSE officer appointed", True),
        ("PPE issuance register", True),
        ("Lost-time injuries (12 mo)", True),
        ("Emergency drill frequency", True),
        ("Workforce medical screening", False),
    ],
    SectionKey.EQUIPMENT: [
        ("Primary extraction equipment", True),
        ("Maintenance regime", True),
        ("Calibration programme", True),
        ("Equipment insurance", False),
    ],
    SectionKey.PRODUCTION: [
        ("Rated capacity", True),
        ("Current throughput", True),
        ("Production reconciliation frequency", True),
        ("Inventory control system", False),
    ],
    SectionKey.SAMPLING: [
        ("On-site or contracted laboratory", True),
        ("Assay method", True),
        ("Chain of custody", True),
        ("Retention sample policy", False),
    ],
    SectionKey.INSPECTION: [
        ("Preferred inspection window", True),
        ("Site access constraints", True),
        ("Previous inspection", False),
        ("Self-declared readiness", True),
    ],
}


def required_prompts(section_key):
    return [label for label, required in PROMPTS[section_key] if required]


def checklist():
    """The whole ten-section checklist, as the site form reads it."""
    return [
        {
            "key": key,
            "label": label,
            "prompts": [{"label": prompt, "required": required} for prompt, required in PROMPTS[key]],
            "required_prompts": required_prompts(key),
        }
        for key, label in SectionKey.choices
    ]
