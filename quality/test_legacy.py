"""Legacy marking (G2): carried-over rows, PROTECT, unique hashes, public verify."""

import importlib
from datetime import timedelta
from unittest import mock

from django.apps import apps as global_apps

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from quality import models as m
# Migration files start with a digit, so they are loaded by name.
helpers = importlib.import_module("quality.migrations.0005_legacy_marking")
from quality.serializers import CertificateSerializer


def make_sample(**extra):
    return m.Sample.objects.create(material="Coltan concentrate", **extra)


def make_certificate(sample, **extra):
    return m.Certificate.objects.create(sample=sample, **extra)


class NewRowsAreNotLegacyTests(TestCase):
    def test_new_sample_certificate_and_nonconformity_default_to_not_legacy(self):
        sample = make_sample()
        cert = make_certificate(sample, verification_hash="hash-new-0001")
        nc = m.QualityNonConformity.objects.create(title="Test NC")
        self.assertFalse(sample.is_legacy)
        self.assertFalse(cert.is_legacy)
        self.assertFalse(nc.is_legacy)

    def test_serializer_exposes_the_flag_read_only(self):
        cert = make_certificate(make_sample(), verification_hash="hash-new-0002")
        data = CertificateSerializer(cert).data
        self.assertIs(data["is_legacy"], False)
        serializer = CertificateSerializer(cert, data={"is_legacy": True}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertIs(serializer.save().is_legacy, False)


class DataMigrationTests(TestCase):
    def test_marks_every_existing_row_legacy(self):
        sample = make_sample()
        make_certificate(sample, verification_hash="hash-old-0001")
        m.QualityNonConformity.objects.create(title="Old NC")
        helpers.mark_existing_rows_legacy(global_apps, None)
        self.assertTrue(m.Sample.objects.get(pk=sample.pk).is_legacy)
        self.assertTrue(m.Certificate.objects.get().is_legacy)
        self.assertTrue(m.QualityNonConformity.objects.get().is_legacy)

    def test_duplicate_hashes_stop_the_migration_and_name_them(self):
        fake_rows = [(1, "dup-hash"), (2, "dup-hash"), (3, "unique-hash"), (4, "")]
        fake_queryset = mock.Mock()
        fake_queryset.exclude.return_value.values_list.return_value = fake_rows
        fake_model = mock.Mock(objects=fake_queryset)
        fake_apps = mock.Mock()
        fake_apps.get_model.return_value = fake_model
        with self.assertRaises(RuntimeError) as raised:
            helpers.check_no_duplicate_verification_hashes(fake_apps, None)
        self.assertIn("dup-hash", str(raised.exception))
        self.assertIn("[1, 2]", str(raised.exception))
        self.assertNotIn("unique-hash", str(raised.exception))

    def test_no_duplicates_passes(self):
        fake_queryset = mock.Mock()
        fake_queryset.exclude.return_value.values_list.return_value = [(1, "a"), (2, "b")]
        fake_apps = mock.Mock()
        fake_apps.get_model.return_value = mock.Mock(objects=fake_queryset)
        helpers.check_no_duplicate_verification_hashes(fake_apps, None)


class ProtectAndUniqueTests(TestCase):
    def test_a_sample_with_a_certificate_cannot_be_deleted(self):
        sample = make_sample()
        make_certificate(sample, verification_hash="hash-protect-01")
        with self.assertRaises(ProtectedError):
            sample.delete()
        self.assertTrue(m.Sample.objects.filter(pk=sample.pk).exists())

    def test_two_certificates_cannot_share_a_real_hash(self):
        sample = make_sample()
        make_certificate(sample, verification_hash="hash-same-0001")
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_certificate(sample, verification_hash="hash-same-0001")

    def test_blank_hashes_do_not_collide(self):
        sample = make_sample()
        make_certificate(sample, verification_hash="")
        make_certificate(sample, verification_hash="")
        self.assertEqual(m.Certificate.objects.filter(verification_hash="").count(), 2)


class PublicVerifyTests(TestCase):
    def setUp(self):
        cache.clear()  # the verify endpoint is throttled per process
        self.client = APIClient()
        self.sample = make_sample()

    def verify(self, cert):
        return self.client.get(reverse("quality-certificate-verify", args=[cert.verification_hash]))

    def test_legacy_certificate_is_never_shown_as_verified(self):
        cert = make_certificate(
            self.sample, verification_hash="hash-verify-legacy", valid_until=timezone.now() + timedelta(days=30), is_legacy=True,
        )
        resp = self.verify(cert)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["verification"], "legacy_not_verified")
        self.assertIs(resp.data["valid"], False)
        self.assertIs(resp.data["is_legacy"], True)

    def test_current_certificate_is_valid(self):
        cert = make_certificate(self.sample, verification_hash="hash-verify-valid", valid_until=timezone.now() + timedelta(days=30))
        resp = self.verify(cert)
        self.assertEqual(resp.data["verification"], "valid")
        self.assertIs(resp.data["valid"], True)

    def test_certificate_past_valid_until_on_the_server_date_is_expired(self):
        cert = make_certificate(self.sample, verification_hash="hash-verify-expired", valid_until=timezone.now() - timedelta(days=2))
        resp = self.verify(cert)
        self.assertEqual(resp.data["verification"], "expired")
        self.assertIs(resp.data["valid"], False)

    def test_revoked_or_undated_certificate_is_not_verified(self):
        revoked = make_certificate(
            self.sample, verification_hash="hash-verify-revoked", status=m.CertificateStatus.REVOKED,
            valid_until=timezone.now() + timedelta(days=30),
        )
        undated = make_certificate(self.sample, verification_hash="hash-verify-undated", valid_until=None)
        self.assertEqual(self.verify(revoked).data["verification"], "revoked")
        self.assertEqual(self.verify(undated).data["verification"], "not_verified")
