# Seeds the 12 Q&C roles and the access grid from Figure 5 of the Build Pack.
# The values come from quality/roles.py as it stood when this migration was
# written, copied here so a later edit of that file cannot change history.
# A later change to the grid is a new migration.

from django.db import migrations

ROLES = [
    ("QC", "Q&C Partner", True),
    ("CEO", "CEO", True),
    ("TL", "Technical Lead", True),
    ("SUP", "Supplier", False),
    ("SMP", "Sampler", False),
    ("INS", "Inspector", False),
    ("LAB", "Laboratory", False),
    ("LOG", "Logistics (custody transport)", False),
    ("EXP", "Export agent", False),
    ("BYR", "Buyer", False),
    ("ADM", "Beldium Admin", True),
    ("LGL", "Legal Counsel", True),
]
NO_SECOND_FACTOR = {"SUP", "BYR", "EXP"}
COLUMNS = ["QC", "CEO", "TL", "SUP", "SMP", "INS", "LAB", "LOG", "EXP", "BYR", "ADM", "LGL"]
GRID = {
    "supplier_profile":         ["RA", "R", "R", "CRU", "", "", "", "", "", "", "RU", "R"],
    "batch_declaration":        ["RA", "R", "R", "CR", "R", "R", "", "", "", "", "R", ""],
    "site_inspection_report":   ["RA", "R", "R", "", "", "CR", "", "", "", "", "R", ""],
    "sampling_record":          ["RA", "R", "R", "", "CRU", "", "R*", "", "", "", "R", ""],
    "chain_of_custody":         ["RA", "R", "R", "", "CRU", "", "CRU", "CRU", "", "", "R", ""],
    "test_request":             ["CRA", "R", "R", "", "", "", "R*", "", "", "", "R", ""],
    "assay_report":             ["RA", "R", "R", "", "", "", "CR", "", "", "", "R", ""],
    "quality_decision":         ["CRA", "R", "R", "", "", "", "", "", "", "", "R", ""],
    "ncr_capa":                 ["CRUA", "R", "R", "RU", "RU", "RU", "RU", "RU", "", "", "R", ""],
    "evidence_package":         ["RA", "R", "R", "", "", "", "", "", "", "", "R", ""],
    "verification_certificate": ["CRA", "R", "R", "R", "", "", "", "", "R", "R", "R", "R"],
    "partner_register":         ["CRUA", "RA", "R", "", "R", "R", "R", "R", "", "", "RU", "R"],
    "audit_log":                ["R", "R", "R", "", "", "", "", "", "", "", "", "R"],
    "reports":                  ["R", "R", "R", "", "", "", "", "", "", "", "R", ""],
    "users_and_roles":          ["A", "R", "RU", "", "", "", "", "", "", "", "CRU", ""],
}


def seed(apps, schema_editor):
    Role = apps.get_model("quality", "QualityRole")
    Grant = apps.get_model("quality", "QualityRoleGrant")
    for code, name, internal in ROLES:
        Role.objects.update_or_create(
            code=code,
            defaults={"name": name, "is_internal": internal, "second_factor_required": code not in NO_SECOND_FACTOR},
        )
    for record_type, cells in GRID.items():
        for code, cell in zip(COLUMNS, cells):
            if not cell:
                continue
            Grant.objects.update_or_create(
                role_id=code, record_type=record_type,
                defaults={"letters": cell.rstrip("*"), "restricted": cell.endswith("*")},
            )


class Migration(migrations.Migration):

    dependencies = [
        ("quality", "0006_roles"),
    ]

    operations = [
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
