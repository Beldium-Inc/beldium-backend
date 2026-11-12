"""ID counters (task G6)."""
from django.test import TestCase
from django.utils import timezone

from quality import ids, rules
from quality.models import QualityIdCounter, QualitySetting
from quality.test_access import make_user


class IdCounterTests(TestCase):
    def setUp(self):
        self.qc = make_user("qc@beldium.test", is_staff=True)

    def set_rule(self, key, value):
        rules.change(QualitySetting.objects.get(pk=key), value=value, effective_from=timezone.now(),
                     reason="test", user=self.qc)

    def test_allocate_counts_per_prefix_and_year(self):
        self.assertEqual(ids.allocate("X", 2026), 1)
        self.assertEqual(ids.allocate("X", 2026), 2)
        self.assertEqual(ids.allocate("X", 2027), 1)
        self.assertEqual(ids.allocate("Y", 2026), 1)

    def test_counter_cannot_be_deleted(self):
        ids.allocate("X", 2026)
        with self.assertRaises(PermissionError):
            QualityIdCounter.objects.get().delete()

    def test_issue_refuses_while_rule_is_a_placeholder(self):
        with self.assertRaises(rules.RuleNotSet) as caught:
            ids.issue("sample")
        self.assertEqual(caught.exception.status_code, 503)
        self.assertFalse(QualityIdCounter.objects.exists())

    def test_issue_uses_the_rule_once_given(self):
        self.set_rule("id_sequence_rule", {"sample": {"prefix": "BD-SMP", "digits": 6}, "seal": {"digits": 5}})
        self.assertEqual(ids.issue("sample", 2026), "BD-SMP-2026-000001")
        self.assertEqual(ids.issue("sample", 2026), "BD-SMP-2026-000002")
        with self.assertRaises(rules.RuleNotSet):
            ids.issue("batch")
        # The seal prefix is its own setting and is still a placeholder.
        with self.assertRaises(rules.RuleNotSet):
            ids.issue("seal")
        self.set_rule("seal_number_prefix", "SL")
        self.assertEqual(ids.issue("seal", 2026), "SL-2026-00001")
