"""The document checklist every Logistics account is measured against."""

from compliance.models import Sector

# (document_type, title, required, applies_when)
# A blank `applies_when` on an optional document means it is optional for everyone.
LOGISTICS_DOCUMENTS = (
    ("logistics_cac_incorporation", "CAC incorporation evidence", True, ""),
    ("logistics_tax_documentation", "Tax documentation", True, ""),
    ("logistics_representative_declaration", "Authorized representative declaration", True, ""),
    ("logistics_transport_authorization", "Applicable transport authorization", False, ""),
    ("logistics_mineral_haulage_authorization", "Applicable mineral haulage authorization", False, "If you haul minerals or bulk cargo"),
    ("logistics_vehicle_schedule", "Vehicle registration and roadworthiness schedule", True, ""),
    ("logistics_lease_declaration", "Lease / contracted fleet declaration", False, "If any vehicle is leased or contracted"),
    ("logistics_driver_schedule", "Driver licence and competence schedule", True, ""),
    ("logistics_maintenance_procedures", "Maintenance and safety procedures", True, ""),
    ("logistics_cargo_custody_procedure", "Cargo custody and delivery procedure", False, "If you haul minerals or bulk cargo"),
    ("logistics_container_seal_procedure", "Container seal procedure", False, "If you run container trucks"),
    ("logistics_vehicle_insurance", "Commercial vehicle insurance declaration", True, ""),
    ("logistics_hse_declaration", "Journey management and HSE declaration", True, ""),
    ("logistics_hazardous_cargo_procedure", "Hazardous cargo handling procedure", False, "If you carry hazardous cargo"),
    ("logistics_corridor_risk_declaration", "Interstate corridor risk declaration", False, "If you operate in more than one state"),
)
LOGISTICS_DOCUMENT_TITLES = {key: title for key, title, _, _ in LOGISTICS_DOCUMENTS}
LOGISTICS_REQUIRED_DOCUMENT_TYPES = {key for key, _, required, _ in LOGISTICS_DOCUMENTS if required}
LOGISTICS_DOCUMENT_CONDITIONS = {key: applies_when for key, _, _, applies_when in LOGISTICS_DOCUMENTS}

# Which review domain each document is filed under on a logistics company's application.
LOGISTICS_DOCUMENT_DOMAINS = {
    "logistics_cac_incorporation": "corporate",
    "logistics_tax_documentation": "corporate",
    "logistics_representative_declaration": "corporate",
    "logistics_transport_authorization": "regulatory",
    "logistics_mineral_haulage_authorization": "mineral",
    "logistics_vehicle_schedule": "fleet",
    "logistics_lease_declaration": "fleet",
    "logistics_driver_schedule": "driver",
    "logistics_maintenance_procedures": "hs",
    "logistics_cargo_custody_procedure": "mineral",
    "logistics_container_seal_procedure": "operational",
    "logistics_vehicle_insurance": "insurance",
    "logistics_hse_declaration": "hs",
    "logistics_hazardous_cargo_procedure": "hs",
    "logistics_corridor_risk_declaration": "operational",
}
LOGISTICS_EVIDENCE_DOMAINS = {LOGISTICS_DOCUMENT_DOMAINS[key] for key in LOGISTICS_REQUIRED_DOCUMENT_TYPES}


def is_logistics_application(application):
    return application.sector == Sector.LOGISTICS
