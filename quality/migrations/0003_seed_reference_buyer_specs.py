from django.db import migrations

# Reference specifications so a fresh environment can register samples. This
# runs once per database (Django records the migration as applied), and uses
# get_or_create so a spec someone has since edited is never overwritten.
# `manage.py seed_quality` remains for refreshing a development database.
SPECS = [
    {
        "name": "Gold Doré Reference Spec",
        "material": "gold",
        "limits": [
            {"analyte": "Au", "unit": "%", "min": "95", "max": "", "method": "ICP-MS"},
            {"analyte": "Ag", "unit": "%", "min": "", "max": "5", "method": "ICP-MS"},
        ],
    },
    {
        "name": "Lithium Ore Reference Spec",
        "material": "lithium",
        "limits": [
            {"analyte": "Li2O", "unit": "%", "min": "5", "max": "", "method": "XRF"},
            {"analyte": "Moisture", "unit": "%", "min": "", "max": "10", "method": "Gravimetric"},
        ],
    },
]


def seed_specs(apps, schema_editor):
    BuyerSpec = apps.get_model("quality", "BuyerSpec")
    for spec in SPECS:
        BuyerSpec.objects.get_or_create(
            name=spec["name"],
            defaults={
                "buyer_org": "Beldium Reference Buyer",
                "material": spec["material"],
                "limits": spec["limits"],
            },
        )


class Migration(migrations.Migration):
    dependencies = [("quality", "0002_sample_buyer_organisation_sample_miner_organisation_and_more")]

    operations = [migrations.RunPython(seed_specs, migrations.RunPython.noop)]
