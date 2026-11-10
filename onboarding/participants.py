"""What each sector requires before a participant application can be
submitted (BE-O3).

Mirrors PARTICIPANTS in the frontend's src/lib/onboarding/participants.ts.
The frontend checks the same things as the person types; this is the check
that counts. Keep the two in step when a sector's questions change.
"""
from organisations.models import OrganisationType

# Mining and logistics companies register on their own portals.
TYPE_FOR_SECTOR = {
    "processing": OrganisationType.PROCESSING_COMPANY,
    "warehousing": OrganisationType.WAREHOUSE_OPERATOR,
    "export": OrganisationType.EXPORTER,
    "marketplace": OrganisationType.MARKET_PARTICIPANT,
}

SECTIONS = ["company", "operations", "sites", "representative", "declarations"]
# Section 2: the company profile, the representative's signature and the
# declarations are the admin's to give. Staff fill in the rest of the draft.
ADMIN_SECTIONS = {"company", "representative", "declarations"}

COMPANY_REQUIRED = ["legalName", "rcNumber", "tin", "address", "email", "phone", "state"]
# The frontend asks for name and email; job title and phone are optional.
REPRESENTATIVE_REQUIRED = ["fullName", "email", "signature"]

COMMON_DECLARATIONS = ["accurate", "verify", "notLicence"]

CORPORATE_DOCS = [
    ("CAC Certificate of Incorporation", True),
    ("CAC Status Report (Form CO7)", True),
    ("Tax Identification Certificate", True),
]

REQUIREMENTS = {
    "processing": {
        "operations": ["processingTypes", "minerals", "designCapacity", "feedSources", "processingLicence"],
        "sites_min": 1,
        "documents": CORPORATE_DOCS + [
            ("Mineral Processing Licence", True),
            ("Environmental Impact Assessment / Permit", True),
            ("Factory registration (Occupational H&S)", False),
            ("Waste and tailings management plan", False),
            ("Feedstock sourcing policy", False),
        ],
        "declarations": COMMON_DECLARATIONS + ["sourcing"],
    },
    "warehousing": {
        "operations": ["storageTypes", "minerals", "totalCapacity", "weighbridge"],
        "sites_min": 1,
        "documents": CORPORATE_DOCS + [
            ("Warehouse operating licence / permit", True),
            ("Fire safety certificate", True),
            ("Insurance for goods in custody", True),
            ("Bonded warehouse licence (if bonded)", False),
            ("Site layout plan", False),
        ],
        "declarations": COMMON_DECLARATIONS + ["custody"],
    },
    "export": {
        "operations": ["minerals", "destinations", "annualVolume", "exportPermit", "nepc"],
        "sites_min": 0,
        "documents": CORPORATE_DOCS + [
            ("Mineral export permit", True),
            ("NEPC export registration certificate", True),
            ("Royalty payment receipts (last 6 months)", False),
            ("AML / KYC policy", False),
        ],
        "declarations": COMMON_DECLARATIONS + ["origin"],
    },
    "marketplace": {
        "operations": ["participantType", "minerals", "annualVolume", "endUse", "countries", "sanctionsConsent"],
        "sites_min": 1,
        "documents": [
            ("Certificate of incorporation", True),
            ("Tax identification certificate", True),
            ("Proof of registered address", True),
            ("Beneficial ownership declaration", True),
            ("AML / KYC policy", False),
            ("Bank reference letter", False),
        ],
        "declarations": COMMON_DECLARATIONS + ["responsible"],
    },
}

SUBTYPE_FOR_ANSWER = {"buyer": "buyer", "offtaker": "offtaker", "oem": "oem"}


def document_titles(sector):
    return [title for title, _ in REQUIREMENTS[sector]["documents"]]


def _filled(value):
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip()) and value != "Select…"
    if isinstance(value, (list, dict)):
        return bool(value)
    # Booleans and numbers: a "no" on a yes or no question is an answer.
    return True


def missing_items(application):
    """Everything that still stops this application from being submitted,
    as short messages the frontend can show."""
    sector = application.sector
    rules = REQUIREMENTS[sector]
    missing = []
    for key in COMPANY_REQUIRED:
        if not _filled(application.company.get(key)):
            missing.append(f"company.{key}")
    for key in rules["operations"]:
        if not _filled(application.operations.get(key)):
            missing.append(f"operations.{key}")
    if sector == "marketplace" and application.operations.get("sanctionsConsent") is not True:
        if "operations.sanctionsConsent" not in missing:
            missing.append("operations.sanctionsConsent")
    sites = [site for site in application.sites if isinstance(site, dict) and _filled(site.get("name"))]
    if len(sites) < rules["sites_min"]:
        missing.append("sites")
    for key in REPRESENTATIVE_REQUIRED:
        if not _filled(application.representative.get(key)):
            missing.append(f"representative.{key}")
    signature = str(application.representative.get("signature") or "").strip().lower()
    full_name = str(application.representative.get("fullName") or "").strip().lower()
    if signature and signature != full_name and "representative.signature" not in missing:
        # The frontend asks the representative to type their full name.
        missing.append("representative.signature")
    if application.representative.get("authorised") not in {"yes", True}:
        missing.append("representative.authorised")
    for key in rules["declarations"]:
        if application.declarations.get(key) is not True:
            missing.append(f"declarations.{key}")
    uploaded = set(application.documents.values_list("title", flat=True))
    for title, required in rules["documents"]:
        if required and title not in uploaded:
            missing.append(f"documents.{title}")
    return missing
