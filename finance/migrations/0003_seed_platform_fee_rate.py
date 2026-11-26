from datetime import datetime, timezone
from decimal import Decimal

from django.db import migrations


def seed_rate(apps, schema_editor):
    rate = apps.get_model("finance", "PlatformFeeRate")
    rate.objects.get_or_create(
        version=1,
        defaults={
            "rate_percent": Decimal("5.0000"),
            "effective_from": datetime(2026, 11, 26, tzinfo=timezone.utc),
            "note": "Initial platform fee rate",
        },
    )


class Migration(migrations.Migration):
    dependencies = [("finance", "0002_platformfeerate_invoice_platform_fee_amount_and_more")]
    operations = [migrations.RunPython(seed_rate, migrations.RunPython.noop)]
