"""Q&C settings, the change log and rule versions (task G5)."""
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from quality import rules
from quality.models import (
    QualityAuditEntry,
    QualityRuleVersion,
    QualitySetting,
    QualitySettingChange,
)
from quality.test_access import QualityAccessBase, make_user
from quality.test_roles import grant


class SeedTests(QualityAccessBase):
    def test_decided_values(self):
        self.assertEqual(rules.get("certificate_validity_days"), 90)
        self.assertEqual(rules.get("sampling_distance_limit_m"), 500)
        self.assertEqual(rules.get("expiry_alert_days"), [90, 60, 30])
        self.assertIs(rules.get("laboratory_sees_party_names"), False)
        self.assertEqual(rules.current_rule_version(), "1")

    def test_placeholders_refuse_to_run(self):
        for key in ("custody_time_gap_minutes", "hold_deadline_days", "borderline_rule", "mineral_codes",
                    "seal_number_prefix", "id_sequence_rule", "device_clock_gap_seconds"):
            self.assertTrue(rules.is_placeholder(key), key)
            with self.assertRaises(rules.RuleNotSet) as caught:
                rules.get(key)
            self.assertEqual(caught.exception.details, {"setting_key": key})
            self.assertTrue(QualitySetting.objects.get(pk=key).owner)

    def test_change_log_is_append_only(self):
        row = QualitySettingChange.objects.first()
        with self.assertRaises(PermissionError):
            row.save()
        with self.assertRaises(PermissionError):
            QualitySettingChange.objects.all().update(reason="x")
        with self.assertRaises(PermissionError):
            QualityRuleVersion.objects.all().delete()


class SettingsApiTests(QualityAccessBase):
    def setUp(self):
        super().setUp()
        self.ceo = make_user("ceo@beldium.test", is_staff=True)
        grant(self.ceo, "CEO")
        self.admin = make_user("admin@beldium.test", is_staff=True)
        grant(self.admin, "ADM")
        self.list_url = reverse("quality-setting-list")
        self.changes_url = reverse("quality-setting-changes", args=["certificate_validity_days"])

    def post_change(self, user, **body):
        self.as_user(user)
        payload = {"value": 120, "effective_from": (timezone.now() + timedelta(days=1)).isoformat(), "reason": "Pilot review"}
        payload.update(body)
        return self.client.post(self.changes_url, payload, format="json")

    def test_readers(self):
        for user in (self.operator, self.ceo):
            self.as_user(user)
            response = self.client.get(self.list_url)
            self.assertEqual(response.status_code, 200, user.email)
            self.assertEqual(response.data["rule_version"], "1")
            row = next(item for item in response.data["results"] if item["setting_key"] == "hold_deadline_days")
            self.assertTrue(row["is_placeholder"])
        for user in (self.admin, self.lab1, self.miner_a, self.regulator, self.nobody):
            self.as_user(user)
            self.assertEqual(self.client.get(self.list_url).status_code, 403, user.email)

    def test_only_qc_changes(self):
        for user in (self.ceo, self.admin, self.lab1, self.miner_a):
            self.assertEqual(self.post_change(user).status_code, 403, user.email)
        response = self.post_change(self.operator)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["rule_version"], "2")
        # Not in force yet.
        self.assertEqual(rules.get("certificate_validity_days"), 90)
        self.assertEqual(rules.get("certificate_validity_days", at=timezone.now() + timedelta(days=2)), 120)
        self.assertTrue(QualityAuditEntry.objects.filter(action="setting.changes", record_label="certificate_validity_days").exists())

    def test_change_rules(self):
        past = (timezone.now() - timedelta(days=1)).isoformat()
        self.assertEqual(self.post_change(self.operator, effective_from=past).data["error"]["code"], "effective_date_in_past")
        self.assertEqual(self.post_change(self.operator, value=-5).data["error"]["code"], "value_out_of_range")
        self.assertEqual(self.post_change(self.operator, value="ninety").data["error"]["code"], "value_out_of_range")
        self.assertEqual(self.post_change(self.operator).status_code, 201)
        self.assertEqual(self.post_change(self.operator).data["error"]["code"], "change_already_pending")

    def test_placeholder_can_be_given_a_value(self):
        self.as_user(self.operator)
        url = reverse("quality-setting-changes", args=["hold_deadline_days"])
        response = self.client.post(url, {"value": 14, "effective_from": timezone.now().isoformat(), "reason": "Q&C letter"}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(rules.get("hold_deadline_days"), 14)

    def test_history_and_no_patch(self):
        self.post_change(self.operator)
        response = self.client.get(self.changes_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 2)
        detail = reverse("quality-setting-detail", args=["certificate_validity_days"])
        self.assertEqual(self.client.patch(detail, {"value": 1}, format="json").status_code, 405)
        self.assertEqual(self.client.delete(detail).status_code, 405)
        self.assertEqual(self.client.get(reverse("quality-setting-detail", args=["nope"])).status_code, 404)


class DecisionsUseSettingsTests(QualityAccessBase):
    def certify(self):
        self.request_test(laboratory=self.lab_one)
        self.sample.refresh_from_db()
        result_id = self.sample.results[0]["id"]
        self.as_user(self.lab1)
        self.client.patch(reverse("quality-sample-result", args=[self.sample.pk, result_id]), {"value": "97"}, format="json")
        self.as_user(self.operator)
        self.client.post(reverse("quality-sample-review", args=[self.sample.pk]), {"verdict": "pass"}, format="json")
        return self.client.post(reverse("quality-sample-certificate", args=[self.sample.pk]))

    def test_certificate_uses_validity_setting_and_stores_rule_version(self):
        before = timezone.now()
        response = self.certify()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["rule_version"], "1")
        from django.utils.dateparse import parse_datetime
        valid_until = parse_datetime(response.data["valid_until"])
        self.assertAlmostEqual((valid_until - before).total_seconds() / 86400, 90, delta=0.01)
        self.sample.refresh_from_db()
        self.assertEqual(self.sample.quality_review["rule_version"], "1")

    def test_laboratory_does_not_see_party_names_by_default(self):
        self.request_test(laboratory=self.lab_one)
        self.as_user(self.lab1)
        response = self.client.get(reverse("quality-sample-detail", args=[self.sample.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["miner_org"], "")
        self.assertIsNone(response.data["miner_organisation"])
        listing = self.client.get(reverse("quality-sample-list"))
        self.assertEqual(listing.data["results"][0]["miner_org"], "")
        self.as_user(self.operator)
        self.assertEqual(self.client.get(reverse("quality-sample-detail", args=[self.sample.pk])).data["miner_org"], "Mine A")

    def test_switch_shows_names(self):
        self.request_test(laboratory=self.lab_one)
        rules.change(QualitySetting.objects.get(pk="laboratory_sees_party_names"), value=True,
                     effective_from=timezone.now(), reason="David agreed", user=self.operator)
        self.as_user(self.lab1)
        response = self.client.get(reverse("quality-sample-detail", args=[self.sample.pk]))
        self.assertEqual(response.data["miner_org"], "Mine A")
