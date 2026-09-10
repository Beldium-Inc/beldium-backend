"""What a processing application has to answer and evidence.

This is the compliance checklist, and it lives on the server for two reasons:
completeness is computed here, so the definition of "complete" cannot be a
client's opinion of it; and the requirements differ by processing type, which
is a policy question rather than a presentation one.

Each section carries prompts the applicant answers and documents it must
supply. A chemical refinery answers the same prompts as a crushing plant but
evidences more, so the type-specific requirements are folded in per section.
"""
from processing.models import ProcessingType, SectionKey

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
    SectionKey.REGULATORY: [
        ("Mining/Processing licence class", True),
        ("Issuing authority", True),
        ("NESREA registration", True),
        ("State environmental permit", True),
    ],
    SectionKey.FACILITY: [
        ("Site address", True),
        ("Land title / C of O", True),
        ("Site area", True),
        ("Perimeter security", True),
        ("Weighbridge", False),
        ("Power source", True),
    ],
    SectionKey.ENVIRONMENTAL: [
        ("EIA / EMP status", True),
        ("Effluent discharge route", True),
        ("Air quality monitoring", True),
        ("Last effluent test", False),
        ("Community grievance log", False),
    ],
    SectionKey.HEALTH_SAFETY: [
        ("HSE officer appointed", True),
        ("PPE issuance register", True),
        ("Lost-time injuries (12 mo)", True),
        ("Emergency drill frequency", True),
        ("Workforce medical screening", False),
    ],
    SectionKey.EQUIPMENT: [
        ("Primary process line", True),
        ("Installed capacity", True),
        ("Maintenance regime", True),
        ("Calibration programme", True),
    ],
    SectionKey.OPERATIONAL: [
        ("Batch traceability system", True),
        ("Input source verification", True),
        ("Shift logging", False),
        ("Chain of custody", True),
        ("Reconciliation frequency", True),
    ],
    SectionKey.QUALITY: [
        ("On-site laboratory", True),
        ("Assay method", True),
        ("Third-party verification lab", False),
        ("Retention sample policy", True),
    ],
    SectionKey.WASTE: [
        ("Waste streams identified", True),
        ("Licensed waste handler", True),
        ("Tailings storage", True),
        ("Waste manifest system", True),
    ],
    SectionKey.INSPECTION: [
        ("Preferred inspection window", True),
        ("Site access constraints", True),
        ("Previous inspection", False),
        ("Self-declared readiness", True),
    ],
}

# Documents every applicant supplies, whatever it processes.
# (section, document name, issuer hint, expiry expected)
BASE_DOCUMENTS = [
    (SectionKey.CORPORATE, "CAC Certificate of Incorporation", "CAC", False),
    (SectionKey.CORPORATE, "CAC Status Report (CAC 1.1)", "CAC", True),
    (SectionKey.CORPORATE, "FIRS Tax Clearance Certificate", "FIRS", True),
    (SectionKey.REGULATORY, "Mineral Processing Licence", "Mining Cadastre Office", True),
    (SectionKey.REGULATORY, "NESREA Facility Registration", "NESREA", True),
    (SectionKey.REGULATORY, "State Environmental Permit", "State Ministry of Environment", True),
    (SectionKey.FACILITY, "Certificate of Occupancy", "State Land Bureau", False),
    (SectionKey.FACILITY, "Weighbridge Calibration Certificate", "SON approved calibrator", True),
    (SectionKey.FACILITY, "Site Layout & Process Flow Drawing", "Applicant", False),
    (SectionKey.ENVIRONMENTAL, "Environmental Management Plan", "Accredited consultant", True),
    (SectionKey.ENVIRONMENTAL, "Quarterly Effluent Analysis Report", "Independent laboratory", False),
    (SectionKey.HEALTH_SAFETY, "HSE Policy & Procedures Manual", "Applicant", False),
    (SectionKey.HEALTH_SAFETY, "Fire Safety Certificate", "State Fire Service", True),
    (SectionKey.HEALTH_SAFETY, "Employee Group Accident Cover", "Insurer", True),
    (SectionKey.EQUIPMENT, "Equipment Asset Register", "Applicant", False),
    (SectionKey.EQUIPMENT, "Pressure Vessel Integrity Test", "Certified inspector", True),
    (SectionKey.OPERATIONAL, "Standard Operating Procedures Pack", "Applicant", False),
    (SectionKey.OPERATIONAL, "Input Supplier Due-Diligence Register", "Applicant", False),
    (SectionKey.QUALITY, "Laboratory Quality Manual", "Applicant", False),
    (SectionKey.QUALITY, "XRF Calibration Certificate", "OEM service", True),
    (SectionKey.WASTE, "Waste Handler Contract", "Licensed handler", True),
    (SectionKey.WASTE, "Waste Manifest Sample Set", "Applicant", False),
    (SectionKey.INSPECTION, "Site Access & Induction Pack", "Applicant", False),
]

# What each process class has to evidence on top of the base set. These are the
# hazards that distinguish one plant from another: acid handling, stack
# emissions, dust, wash water.
TYPE_DOCUMENTS = {
    ProcessingType.CRUSHING_MILLING: [
        (SectionKey.ENVIRONMENTAL, "Dust suppression plan", "Applicant", False),
        (SectionKey.ENVIRONMENTAL, "Noise & vibration survey", "Accredited consultant", True),
        (SectionKey.EQUIPMENT, "Crusher calibration records", "Applicant", True),
    ],
    ProcessingType.CHEMICAL_REFINING: [
        (SectionKey.REGULATORY, "NESREA effluent discharge permit", "NESREA", True),
        (SectionKey.OPERATIONAL, "Chemical inventory & MSDS register", "Applicant", False),
        (SectionKey.WASTE, "Tailings / effluent containment design", "Accredited consultant", False),
        (SectionKey.HEALTH_SAFETY, "Emergency chemical spill response plan", "Applicant", False),
        (SectionKey.FACILITY, "Reagent storage bund certification", "Certified inspector", True),
    ],
    ProcessingType.SMELTING: [
        (SectionKey.ENVIRONMENTAL, "Stack emission monitoring", "Independent laboratory", True),
        (SectionKey.WASTE, "Slag disposal agreement", "Licensed handler", True),
        (SectionKey.HEALTH_SAFETY, "Thermal PPE certification", "Supplier", True),
    ],
    ProcessingType.SORTING_BALING: [
        (SectionKey.ENVIRONMENTAL, "Wash water recycling plan", "Applicant", False),
        (SectionKey.HEALTH_SAFETY, "Manual handling risk assessment", "Applicant", False),
    ],
}


def documents_for(processing_type):
    """Every document this process class must supply, base plus type-specific."""
    return BASE_DOCUMENTS + TYPE_DOCUMENTS.get(processing_type, [])


def required_documents_by_section(processing_type):
    """``{section key: [document name, ...]}`` for this process class."""
    by_section = {key: [] for key, _ in SectionKey.choices}
    for section, name, _issuer, _expires in documents_for(processing_type):
        by_section[section].append(name)
    return by_section


def required_prompts(section_key):
    return [label for label, required in PROMPTS[section_key] if required]


def checklist(processing_type):
    """The whole checklist for one process class, as the applicant form reads it."""
    documents = required_documents_by_section(processing_type)
    return [
        {
            "key": key,
            "label": label,
            "prompts": [{"label": prompt, "required": required} for prompt, required in PROMPTS[key]],
            "documents": [
                {"name": name, "issuer": issuer, "expires": expires}
                for section, name, issuer, expires in documents_for(processing_type)
                if section == key
            ],
            "required_prompts": required_prompts(key),
            "required_documents": documents[key],
        }
        for key, label in SectionKey.choices
    ]
