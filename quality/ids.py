"""Platform issued ID numbers for Q&C records.

allocate() hands out the next number for a prefix and year from a locked
counter row, so two requests can never get the same number. issue() turns a
record kind into a full number using the format in the settings.

The final formats are not given yet (mineral codes, ID sequence rules, seal
and assignment prefixes). Until they are, issue() refuses with rule_not_set.
TODO(Q&C Partner): send the ID sequence rules. The stored shape expected in
the id_sequence_rule setting is {kind: {"prefix": text, "digits": number}},
for example {"sample": {"prefix": "BD-SMP", "digits": 6}}, which gives
BD-SMP-2026-000001. Confirm that shape when the rules arrive.
"""
from django.db import IntegrityError, transaction
from django.utils import timezone

from common.exceptions import AppError
from quality import models as m
from quality import rules

MAX_ATTEMPTS = 3

# Kinds whose prefix is its own setting rather than part of id_sequence_rule.
PREFIX_SETTINGS = {
    "seal": "seal_number_prefix",
    "assignment": "assignment_number_prefix",
}


def allocate(prefix, year=None):
    """The next number for this prefix and year, starting at 1."""
    year = year or timezone.localdate().year
    for attempt in range(MAX_ATTEMPTS):
        try:
            with transaction.atomic():
                counter, _ = m.QualityIdCounter.objects.select_for_update().get_or_create(prefix=prefix, year=year)
                counter.last_value += 1
                counter.save(update_fields=["last_value", "updated_at"])
                return counter.last_value
        except IntegrityError:
            # Two requests created the first row for this year at once. The
            # other one won; take the lock on its row and go again.
            if attempt == MAX_ATTEMPTS - 1:
                raise
    raise RuntimeError("unreachable")


def issue(kind, year=None):
    """A full number for a record kind, or rule_not_set while the format is a placeholder."""
    rule = rules.get("id_sequence_rule")
    entry = rule.get(kind) if isinstance(rule, dict) else None
    if not isinstance(entry, dict):
        raise rules.RuleNotSet(
            "Numbers for this record cannot be issued yet. A Q&C setting is missing.",
            details={"setting_key": "id_sequence_rule", "kind": kind},
        )
    if kind in PREFIX_SETTINGS:
        prefix = rules.get(PREFIX_SETTINGS[kind])
    else:
        prefix = entry.get("prefix")
    digits = entry.get("digits")
    if not isinstance(prefix, str) or not prefix.strip() or not isinstance(digits, int) or not 1 <= digits <= 12:
        raise AppError("The ID sequence rule for this record is not usable.", code="id_rule_invalid", status_code=503)
    year = year or timezone.localdate().year
    value = allocate(prefix.strip(), year)
    return f"{prefix.strip()}-{year}-{value:0{digits}d}"
