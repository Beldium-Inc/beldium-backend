# Seeds the Release 1 Q&C settings as rule version 1. Decided values are from
# the Q&C reply of 6 October (BLD-QC-DOC-RPL-024). Every null value is a
# placeholder whose owner has not given the value yet; any check that needs
# it refuses with rule_not_set. The list is copied here so a later edit of
# quality/rules.py cannot change history.

from django.db import migrations
from django.utils import timezone

SEED = [
    ("certificate_validity_days", "Certificate validity", "days", "positive_int", "Q&C Partner", 90, "Decision 4."),
    ("sampling_distance_limit_m", "Sampling distance limit from the registered site", "m", "positive_int", "Q&C Partner", 500, "Decision 5. Farther than this raises a HOLD."),
    ("expiry_alert_days", "Expiry alerts before the date", "days", "positive_int_list", "Q&C Partner", [90, 60, 30], "Decision 5."),
    ("laboratory_sees_party_names", "Laboratory sees supplier and buyer names", "", "boolean", "David", False, "Decision 10. Hidden by default, subject to David."),
    ("custody_time_gap_minutes", "Custody time gap", "minutes", "positive_int", "Q&C Partner", None, "TODO(Q&C Partner): value not defined yet."),
    ("hold_deadline_days", "HOLD deadline", "days", "positive_int", "Q&C Partner", None, "TODO(Q&C Partner): value not defined yet."),
    ("gps_tolerance_site_verification_m", "GPS tolerance at site verification", "m", "positive_int", "Q&C Partner", None, "TODO(Q&C Partner): value not defined yet."),
    ("gps_tolerance_custody_m", "GPS tolerance at custody handover", "m", "positive_int", "Q&C Partner", None, "TODO(Q&C Partner): value not defined yet."),
    ("device_clock_gap_seconds", "Phone clock against server clock gap", "seconds", "positive_int", "Q&C Partner", None, "TODO(Q&C Partner): value not defined yet."),
    ("borderline_rule", "BORDERLINE rule per parameter", "", "json", "Q&C Partner", None, "TODO(Q&C Partner): rule not defined yet. No comparison logic is built."),
    ("mineral_codes", "Mineral codes", "", "text_list", "Q&C Partner", None, "TODO(Q&C Partner): list not defined yet."),
    ("seal_number_prefix", "Seal number prefix", "", "text", "Q&C Partner", None, "TODO(Q&C Partner): prefix not defined yet."),
    ("assignment_number_prefix", "Assignment number prefix", "", "text", "Q&C Partner", None, "TODO(Q&C Partner): prefix not defined yet."),
    ("id_sequence_rule", "ID sequence rules", "", "json", "Q&C Partner", None, "TODO(Q&C Partner): rules not defined yet."),
]


def seed(apps, schema_editor):
    Setting = apps.get_model("quality", "QualitySetting")
    Change = apps.get_model("quality", "QualitySettingChange")
    Version = apps.get_model("quality", "QualityRuleVersion")
    if Version.objects.exists():
        return
    now = timezone.now()
    version = Version.objects.create(number=1, effective_from=now)
    for key, label, unit, value_type, owner, value, note in SEED:
        setting = Setting.objects.create(
            key=key, label=label, unit=unit, value_type=value_type, owner=owner, note=note,
        )
        Change.objects.create(
            setting=setting, value=value, effective_from=now, changed_at=now, rule_version=version,
            reason="Starting value for Release 1." if value is not None else "Placeholder. Value not given yet.",
        )


class Migration(migrations.Migration):

    dependencies = [
        ("quality", "0010_settings"),
    ]

    operations = [
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
