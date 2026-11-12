"""Certificate status worked out on the server (E10 support)."""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from quality import models as m
from quality.serializers import CertificateSerializer, CertificateVerificationSerializer


def certificate(**extra):
    sample = m.Sample.objects.create(material="gold")
    return m.Certificate.objects.create(sample=sample, verification_hash=extra.pop("hash", "h"), **extra)


class DisplayStatusTests(TestCase):
    def test_statuses(self):
        cases = [
            (dict(hash="a", valid_until=timezone.now() + timedelta(days=5)), "valid", None),
            (dict(hash="b", valid_until=timezone.now() - timedelta(days=2)), "expired", None),
            (dict(hash="c", valid_until=timezone.now() + timedelta(days=5), status="revoked"), "revoked", None),
            (dict(hash="d", valid_until=None), "not_verified", None),
            (dict(hash="e", valid_until=timezone.now() + timedelta(days=5), is_legacy=True),
             "legacy_not_verified", "Legacy, not verified"),
        ]
        for fields, expected, label in cases:
            data = CertificateSerializer(certificate(**fields)).data
            self.assertEqual(data["display_status"], expected, fields)
            self.assertEqual(data["legacy_label"], label)

    def test_public_check_has_label_and_server_time(self):
        data = CertificateVerificationSerializer(certificate(hash="z", is_legacy=True)).data
        self.assertEqual(data["verification"], "legacy_not_verified")
        self.assertEqual(data["legacy_label"], "Legacy, not verified")
        self.assertIsNotNone(data["checked_at"])
