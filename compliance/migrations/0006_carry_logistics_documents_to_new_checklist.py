from django.db import migrations

# The two old required documents that are the same evidence under the new
# Logistics checklist, so nobody has to upload them a second time.
CARRIED_OVER = (
    ("certificate_of_incorporation", "logistics_cac_incorporation", "CAC incorporation evidence", "Certificate of Incorporation"),
    ("tin_evidence", "logistics_tax_documentation", "Tax documentation", "TIN Evidence"),
)


def carry_over(apps, schema_editor):
    documents = apps.get_model("compliance", "ComplianceDocument").objects.filter(application__sector="logistics")
    for old_type, new_type, new_title, _ in CARRIED_OVER:
        documents.filter(document_type=old_type).update(document_type=new_type, title=new_title)


def carry_back(apps, schema_editor):
    documents = apps.get_model("compliance", "ComplianceDocument").objects.filter(application__sector="logistics")
    for old_type, new_type, _, old_title in CARRIED_OVER:
        documents.filter(document_type=new_type).update(document_type=old_type, title=old_title)


class Migration(migrations.Migration):
    dependencies = [("compliance", "0005_application_sector")]

    operations = [migrations.RunPython(carry_over, carry_back)]
