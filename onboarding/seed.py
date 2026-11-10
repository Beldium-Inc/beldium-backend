"""The onboarding options as first seeded (BE-O1).

Copied from FALLBACK in the frontend's src/lib/onboarding/options.ts. The
0001 migration loads this once; after that the rows are edited in Django
admin, and this file is not read again. Do not change it to change the
live list: add a migration or edit the rows.
"""

REGULATORY = {
    "key": "regulatory",
    "kind": "regulatory",
    "label": "Regulatory access",
    "description": (
        "For a government regulator or oversight agency. Ask for read-only access to one or more "
        "sectors. A Beldium operator checks your official email or agency letter and approves it."
    ),
    "role": "regulator-org",
    "note": "Read only · approved by Beldium",
}

REGULATOR_OFFICER = {
    "key": "regulator-officer",
    "kind": "specific",
    "label": "Join your agency's regulator account",
    "description": "Your agency already has approved access. Enter the join code your agency administrator shared.",
    "role": "regulator-officer",
    "org_path": "existing",
    "note": "Join code from your agency",
}

JOIN_DESCRIPTION = (
    "Your company is already on Beldium. Use the join code from your administrator, "
    "or the link in your invitation email."
)


def partner_side(sector_name):
    return {
        "key": "partner",
        "label": "Compliance partner",
        "description": f"Firms and professionals that review {sector_name.lower()} applicants for Beldium.",
        "options": [
            {
                "key": "register",
                "kind": "register",
                "label": "Register a new compliance firm",
                "description": "Your firm is not on Beldium yet. You become its administrator and complete its application.",
                "role": "compliance-org",
                "org_path": "new",
                "note": "You become the admin",
            },
            {
                "key": "join",
                "kind": "join",
                "label": "Join an existing compliance firm",
                "description": (
                    "Your firm is already on Beldium. Use the join code from your administrator, "
                    "or the link in your invitation email."
                ),
                "role": "compliance-officer",
                "org_path": "existing",
                "note": "Staff until your admin changes your role",
            },
            REGULATORY,
            {
                "key": "independent",
                "kind": "specific",
                "label": "Independent compliance professional",
                "description": "An individual who takes review assignments without belonging to a firm.",
                "role": "independent",
                "note": "Individual",
            },
            REGULATOR_OFFICER,
        ],
    }


def applicant_side(label, description, register, join_label, extra=()):
    register_option = {
        "key": "register",
        "kind": "register",
        "role": "participant",
        "org_path": "new",
        "note": "You become the admin",
    }
    register_option.update(register)
    return {
        "key": "applicant",
        "label": label,
        "description": description,
        "options": [
            register_option,
            {
                "key": "join",
                "kind": "join",
                "label": join_label,
                "description": JOIN_DESCRIPTION,
                "role": "participant-member",
                "org_path": "existing",
                "note": "Staff until your admin changes your role",
            },
            REGULATORY,
            *extra,
        ],
    }


SEED = {
    "mining": [
        applicant_side(
            "Mining company",
            "Miners and producers registering their organisation, sites and licences.",
            {
                "label": "Register a mining company",
                "description": (
                    "Mining companies register on the Miner Portal, which takes your sites, "
                    "licences and production records."
                ),
                "external_portal": "miner",
                "external_label": "Continue on the Miner Portal",
                "note": "Opens the Miner Portal",
            },
            "Join an existing mining company",
        ),
        partner_side("Mining"),
    ],
    "processing": [
        applicant_side(
            "Processing company",
            "Processors, concentrators, smelters and refiners applying for compliance verification.",
            {
                "label": "Register a processing company",
                "description": "Register your company and plants, then complete the processing compliance application.",
            },
            "Join an existing processing company",
        ),
        partner_side("Processing"),
    ],
    "warehousing": [
        applicant_side(
            "Warehouse operator",
            "Operators of mineral warehouses, depots and bonded stores applying for accreditation.",
            {
                "label": "Register a warehouse operator",
                "description": "Register your company and facilities, then complete the warehouse accreditation application.",
            },
            "Join an existing warehouse operator",
        ),
        partner_side("Warehousing"),
    ],
    "export": [
        applicant_side(
            "Exporter",
            "Licensed mineral exporters submitting consignments for verification.",
            {
                "label": "Register an exporting company",
                "description": "Register your company and export authorisations, then complete the exporter admission application.",
            },
            "Join an existing exporter",
        ),
        partner_side("Export"),
    ],
    "marketplace": [
        applicant_side(
            "Buyer, offtaker or OEM",
            "Companies buying verified material, contracting supply or tracing it into manufacturing.",
            {
                "label": "Register a buying company",
                "description": "Register as a buyer, offtaker or OEM, then complete the participant verification (KYC).",
            },
            "Join an existing buying company",
        ),
        partner_side("Marketplace"),
    ],
    "logistics": [
        applicant_side(
            "Logistics company",
            "Haulage companies, carriers and fleet owners registering their fleet and drivers.",
            {
                "label": "Register a logistics company",
                "description": (
                    "Logistics companies register on the Logistics Portal, which takes your fleet, "
                    "drivers and trips."
                ),
                "external_portal": "logistics",
                "external_label": "Continue on the Logistics Portal",
                "note": "Opens the Logistics Portal",
            },
            "Join an existing logistics company",
            extra=[
                {
                    "key": "driver",
                    "kind": "specific",
                    "label": "Driver",
                    "description": "Drivers are added by their company on the Logistics Portal and sign in there.",
                    "role": "participant-member",
                    "external_portal": "logistics",
                    "external_label": "Open the Logistics Portal",
                    "note": "Added by your company",
                }
            ],
        ),
        partner_side("Logistics"),
    ],
    "quality": [
        {
            "key": "partner",
            "label": "Quality & Control partner",
            "description": "Laboratories, assay, inspection and conformity assessment organisations and their staff.",
            "options": [
                {
                    "key": "register",
                    "kind": "register",
                    "label": "Register a laboratory or Q&C organisation",
                    "description": (
                        "Your organisation is not on Beldium yet. You become its administrator and "
                        "complete the 11-step application."
                    ),
                    "role": "compliance-org",
                    "org_path": "new",
                    "note": "Organisation · you become the admin",
                },
                {
                    "key": "join",
                    "kind": "join",
                    "label": "Join an existing Q&C organisation as an officer or inspector",
                    "description": "Individual inspectors and sampling officers working for a registered Q&C organisation.",
                    "role": "compliance-officer",
                    "org_path": "existing",
                    "note": "Individual · 8 application steps",
                },
                REGULATORY,
                {
                    "key": "analyst",
                    "kind": "specific",
                    "label": "Laboratory analyst",
                    "description": "Bench analysts running accredited methods inside a registered laboratory.",
                    "role": "laboratory-analyst",
                    "org_path": "existing",
                    "note": "Individual",
                },
                {
                    "key": "independent",
                    "kind": "specific",
                    "label": "Independent quality professional",
                    "description": "An individual providing eligible quality and control services independently.",
                    "role": "independent",
                    "note": "Individual",
                },
                REGULATOR_OFFICER,
            ],
        }
    ],
}

OPTION_FIELDS = ["kind", "label", "description", "role", "org_path", "external_portal", "external_label", "note"]


def load(OnboardingDashboard, OnboardingOption):
    for sector, dashboards in SEED.items():
        for dashboard_order, dashboard in enumerate(dashboards):
            row, _ = OnboardingDashboard.objects.update_or_create(
                sector=sector, key=dashboard["key"],
                defaults={"label": dashboard["label"], "description": dashboard["description"], "order": dashboard_order},
            )
            for option_order, option in enumerate(dashboard["options"]):
                OnboardingOption.objects.update_or_create(
                    dashboard=row, key=option["key"],
                    defaults={"order": option_order, **{field: option.get(field, "") for field in OPTION_FIELDS}},
                )
