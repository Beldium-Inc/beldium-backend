"""Q&C rule settings: read a value, read the rule version, change a value.

Every rule value the Q&C flow needs is read through get(). A value Q&C has
not given yet is stored as a placeholder (null) and get() refuses it with
rule_not_set, so no check ever runs on an invented value.
"""
from django.db import transaction
from django.utils import timezone

from common.exceptions import AppError
from quality import models as m


class RuleNotSet(AppError):
    status_code = 503
    default_code = "rule_not_set"


# The Release 1 settings. Decided values are from the Q&C reply of
# 6 October (BLD-QC-DOC-RPL-024). Every null is a placeholder: the owner named
# here has not given the value yet. Do not fill one in without that owner.
# Key, label, unit, type, owner, starting value, note.
SEED = [
    ("certificate_validity_days", "Certificate validity", "days", m.SettingValueType.POSITIVE_INT,
     "Q&C Partner", 90, "Decision 4."),
    ("sampling_distance_limit_m", "Sampling distance limit from the registered site", "m",
     m.SettingValueType.POSITIVE_INT, "Q&C Partner", 500, "Decision 5. Farther than this raises a HOLD."),
    ("expiry_alert_days", "Expiry alerts before the date", "days", m.SettingValueType.POSITIVE_INT_LIST,
     "Q&C Partner", [90, 60, 30], "Decision 5."),
    ("laboratory_sees_party_names", "Laboratory sees supplier and buyer names", "",
     m.SettingValueType.BOOLEAN, "David", False, "Decision 10. Hidden by default, subject to David."),
    ("custody_time_gap_minutes", "Custody time gap", "minutes", m.SettingValueType.POSITIVE_INT,
     "Q&C Partner", None, "TODO(Q&C Partner): value not defined yet."),
    ("hold_deadline_days", "HOLD deadline", "days", m.SettingValueType.POSITIVE_INT,
     "Q&C Partner", None, "TODO(Q&C Partner): value not defined yet."),
    ("gps_tolerance_site_verification_m", "GPS tolerance at site verification", "m",
     m.SettingValueType.POSITIVE_INT, "Q&C Partner", None, "TODO(Q&C Partner): value not defined yet."),
    ("gps_tolerance_custody_m", "GPS tolerance at custody handover", "m", m.SettingValueType.POSITIVE_INT,
     "Q&C Partner", None, "TODO(Q&C Partner): value not defined yet."),
    ("device_clock_gap_seconds", "Phone clock against server clock gap", "seconds",
     m.SettingValueType.POSITIVE_INT, "Q&C Partner", None, "TODO(Q&C Partner): value not defined yet."),
    ("borderline_rule", "BORDERLINE rule per parameter", "", m.SettingValueType.JSON,
     "Q&C Partner", None, "TODO(Q&C Partner): rule not defined yet. No comparison logic is built."),
    ("mineral_codes", "Mineral codes", "", m.SettingValueType.TEXT_LIST,
     "Q&C Partner", None, "TODO(Q&C Partner): list not defined yet."),
    ("seal_number_prefix", "Seal number prefix", "", m.SettingValueType.TEXT,
     "Q&C Partner", None, "TODO(Q&C Partner): prefix not defined yet."),
    ("assignment_number_prefix", "Assignment number prefix", "", m.SettingValueType.TEXT,
     "Q&C Partner", None, "TODO(Q&C Partner): prefix not defined yet."),
    ("id_sequence_rule", "ID sequence rules", "", m.SettingValueType.JSON,
     "Q&C Partner", None, "TODO(Q&C Partner): rules not defined yet."),
]


def _effective_change(setting_key, at=None):
    at = at or timezone.now()
    return (
        m.QualitySettingChange.objects.filter(setting_id=setting_key, effective_from__lte=at)
        .order_by("-effective_from", "-changed_at")
        .first()
    )


def get(setting_key, at=None):
    """The value in force, or RuleNotSet when it is a placeholder."""
    if not m.QualitySetting.objects.filter(pk=setting_key).exists():
        raise KeyError(f"Unknown Q&C setting {setting_key!r}.")
    change = _effective_change(setting_key, at)
    if change is None or change.value is None:
        raise RuleNotSet(
            "This check cannot run yet. A Q&C setting is missing.",
            details={"setting_key": setting_key},
        )
    return change.value


def is_placeholder(setting_key, at=None):
    change = _effective_change(setting_key, at)
    return change is None or change.value is None


def current_rule_version(at=None):
    at = at or timezone.now()
    version = m.QualityRuleVersion.objects.filter(effective_from__lte=at).order_by("-number").first()
    return str(version.number) if version else ""


def validate_value(value_type, value):
    """The cleaned value, or None when it is not allowed for this type."""
    def positive_int(item):
        return isinstance(item, int) and not isinstance(item, bool) and item > 0

    if value is None:
        return None
    if value_type == m.SettingValueType.POSITIVE_INT:
        return value if positive_int(value) else None
    if value_type == m.SettingValueType.POSITIVE_INT_LIST:
        if isinstance(value, list) and value and all(positive_int(item) for item in value):
            return sorted(set(value), reverse=True)
        return None
    if value_type == m.SettingValueType.BOOLEAN:
        return value if isinstance(value, bool) else None
    if value_type == m.SettingValueType.TEXT:
        return value.strip() if isinstance(value, str) and value.strip() else None
    if value_type == m.SettingValueType.TEXT_LIST:
        if isinstance(value, list) and value and all(isinstance(item, str) and item.strip() for item in value):
            return [item.strip() for item in value]
        return None
    if value_type == m.SettingValueType.JSON:
        return value if isinstance(value, (dict, list)) and value else None
    return None


@transaction.atomic
def change(setting, *, value, effective_from, reason, user):
    """Record a new value. It takes effect at effective_from and starts a new
    rule version. Nothing is overwritten."""
    now = timezone.now()
    if effective_from < now - timezone.timedelta(minutes=1):
        raise AppError("The effective date cannot be in the past.", code="effective_date_in_past")
    cleaned = validate_value(setting.value_type, value)
    if cleaned is None:
        raise AppError("That value is not allowed for this setting.", code="value_out_of_range")
    if m.QualitySettingChange.objects.filter(setting=setting, effective_from__gt=now).exists():
        raise AppError(
            "A change for this setting is already waiting to take effect.",
            code="change_already_pending", status_code=409,
        )
    # Versions stay in the order they take effect, so a version number always
    # names one set of values.
    latest = m.QualityRuleVersion.objects.select_for_update().order_by("-number").first()
    if latest and effective_from < latest.effective_from:
        raise AppError(
            "Another change takes effect later than this date. Pick a date on or after it.",
            code="effective_date_before_pending_change", status_code=409,
        )
    version = m.QualityRuleVersion.objects.create(
        number=(latest.number + 1) if latest else 1, effective_from=effective_from,
    )
    name = (getattr(user, "full_name", "") or "").strip() or getattr(user, "email", "")
    return m.QualitySettingChange.objects.create(
        setting=setting, value=cleaned, effective_from=effective_from, reason=reason,
        changed_by_id=user.pk, changed_by_name=name, changed_at=now, rule_version=version,
    )
