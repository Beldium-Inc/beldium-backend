from django.db import migrations

# The starting folders for the Quality & Control vault. They can be renamed,
# reordered or switched off in the admin site; they cannot be removed once a
# document has been filed in them.
QUALITY_CATEGORIES = [
    ("accreditation", "Accreditation, licences and permits", "ISO 17025 scope, operating licences, regulator permits"),
    ("procedures", "Procedures and SOPs", "Sampling, testing and quality procedures, with their revisions"),
    ("laboratory-reports", "Laboratory reports", "Test reports and analysis results received from laboratories"),
    ("sampling-custody", "Sampling and custody records", "Field sampling sheets, seal logs, transfer and receipt forms"),
    ("equipment-calibration", "Equipment and calibration", "Calibration certificates and maintenance records"),
    ("contracts", "Contracts and agreements", "Service agreements, NDAs and letters of engagement"),
    ("training", "Staff training and competence", "Training records, qualifications and competence assessments"),
    ("other", "Other records", "Anything that does not fit the folders above"),
]


def add_categories(apps, schema_editor):
    VaultCategory = apps.get_model("vault", "VaultCategory")
    for order, (code, name, description) in enumerate(QUALITY_CATEGORIES, start=1):
        VaultCategory.objects.get_or_create(
            sector="quality", code=code,
            defaults={"name": name, "description": description, "sort_order": order * 10},
        )


class Migration(migrations.Migration):
    dependencies = [("vault", "0001_initial")]

    operations = [migrations.RunPython(add_categories, migrations.RunPython.noop)]
