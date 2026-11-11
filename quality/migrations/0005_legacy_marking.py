# Marks every row that existed before this migration as legacy, adds the
# legacy flags, makes the certificate sample PROTECT, and makes real
# verification hashes unique.
#
# Order matters on PostgreSQL: the data check and the legacy update run
# around the schema changes, and the duplicate check runs before the unique
# constraint is added, so a duplicate stops the migration with a clear list
# instead of a failed constraint. Nothing is deleted or rewritten.

import django.db.models.deletion
from django.db import migrations, models


def check_no_duplicate_verification_hashes(apps, schema_editor):
    Certificate = apps.get_model("quality", "Certificate")
    seen = {}
    for pk, value in Certificate.objects.exclude(verification_hash="").values_list("pk", "verification_hash"):
        seen.setdefault(value, []).append(pk)
    duplicates = {value: pks for value, pks in seen.items() if len(pks) > 1}
    if duplicates:
        listing = "; ".join(f"{value!r} used by certificate ids {pks}" for value, pks in duplicates.items())
        raise RuntimeError(
            "Cannot add the unique verification hash constraint. Resolve these duplicates by hand, "
            f"then run the migration again: {listing}"
        )


def mark_existing_rows_legacy(apps, schema_editor):
    for model_name in ("Sample", "Certificate", "QualityNonConformity"):
        Model = apps.get_model("quality", model_name)
        Model.objects.update(is_legacy=True)


def noop(apps, schema_editor):
    # Reversing keeps the flags; dropping the columns below is enough to undo.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('quality', '0004_qualityapplication_compliance_application_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='certificate',
            name='is_legacy',
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name='qualitynonconformity',
            name='is_legacy',
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name='sample',
            name='is_legacy',
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AlterField(
            model_name='certificate',
            name='sample',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='certificates', to='quality.sample'),
        ),
        migrations.RunPython(check_no_duplicate_verification_hashes, noop),
        migrations.AddConstraint(
            model_name='certificate',
            constraint=models.UniqueConstraint(condition=models.Q(('verification_hash', ''), _negated=True), fields=('verification_hash',), name='unique_certificate_verification_hash'),
        ),
        migrations.RunPython(mark_existing_rows_legacy, noop),
    ]
