"""A declaration must be signed with the signer's full name, exactly.

Organisation applications: the representative's full name.
Q&C organisation applications: the full name on the signed in account.
Q&C professional applications: the full legal name on the form.
"""

import json
from tempfile import TemporaryDirectory

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import User
from compliance.models import ApplicationStatus, ComplianceApplication
from compliance.signatory import names_match
from organisations.models import Organisation
from quality.models import QualityProfessionalApplication
from quality.test_onboarding import SECTIONS as QUALITY_SECTIONS, pdf

DECLARATION = {
    "accuracy_confirmed": True, "documents_genuine": True, "compliance_agreed": True,
    "disclose_changes": True, "authorised": True, "confirmed": True,
    "signatory_position": "Director",
}


class NamesMatchTests(APITestCase):
    def test_only_surrounding_and_repeated_spaces_are_ignored(self):
        self.assertTrue(names_match("  Ada   Owner ", "Ada Owner"))
        self.assertFalse(names_match("ada owner", "Ada Owner"))
        self.assertFalse(names_match("Ada", "Ada Owner"))
        self.assertFalse(names_match("", ""))


class OrganisationDeclarationTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner@example.com", "SafePassword-2026!", email_verified_at=timezone.now())
        self.organisation = Organisation.objects.create(name="Assurance Ltd", organisation_type="compliance_partner")
        self.organisation.memberships.create(user=self.owner, role="owner", is_active=True)
        self.application = ComplianceApplication.objects.create(
            organisation=self.organisation, created_by=self.owner, representative={"full_name": "Ada Owner"},
        )
        self.client.force_authenticate(self.owner)

    def url(self, name):
        return reverse(f"compliance-application-{name}", args=[self.application.id])

    def sign(self, name):
        return self.client.patch(
            self.url("declaration-section"), {"data": {**DECLARATION, "signatory_name": name}}, format="json",
        )

    def test_matching_name_is_accepted(self):
        self.assertEqual(self.sign("Ada Owner").status_code, status.HTTP_200_OK)

    def test_different_name_is_refused(self):
        for name in ["Ada", "ada owner", "Someone Else", ""]:
            response = self.sign(name)
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, name)
            self.assertIn("signatory_name", response.data["error"]["details"]["data"])
        self.application.refresh_from_db()
        self.assertEqual(self.application.declaration, {})

    def test_declaration_before_a_representative_is_refused(self):
        self.application.representative = {}
        self.application.save()
        self.assertEqual(self.sign("Ada Owner").status_code, status.HTTP_400_BAD_REQUEST)

    def test_submit_is_refused_when_the_representative_changed_after_signing(self):
        self.assertEqual(self.sign("Ada Owner").status_code, status.HTTP_200_OK)
        ComplianceApplication.objects.filter(pk=self.application.pk).update(
            representative={"full_name": "Bola Director"},
        )
        response = self.client.post(self.url("submit"))
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["error"]["code"], "signatory_mismatch")
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, ApplicationStatus.DRAFT)

    def test_member_of_another_organisation_cannot_sign(self):
        outsider = User.objects.create_user("outsider@example.com", "SafePassword-2026!")
        self.client.force_authenticate(outsider)
        self.assertEqual(self.sign("Ada Owner").status_code, status.HTTP_404_NOT_FOUND)


class QualityDeclarationTests(APITestCase):
    def setUp(self):
        media = TemporaryDirectory()
        self.addCleanup(media.cleanup)
        override = override_settings(MEDIA_ROOT=media.name)
        override.enable()
        self.addCleanup(override.disable)
        self.owner = User.objects.create_user(
            "owner@lab.test", first_name="Ngozi", last_name="Eze", email_verified_at=timezone.now(),
        )
        self.organisation = Organisation.objects.create(name="Draft Lab", organisation_type="laboratory")
        self.organisation.memberships.create(user=self.owner, role="owner", is_active=True)
        self.application = ComplianceApplication.objects.create(
            organisation=self.organisation, created_by=self.owner, sector="quality",
        )
        self.client.force_authenticate(self.owner)

    def sign(self, signature):
        return self.client.patch(
            reverse("compliance-application-quality-section", args=[self.application.id, "declaration"]),
            {"data": {**QUALITY_SECTIONS["declaration"], "signature": signature}}, format="json",
        )

    def test_signature_must_be_the_account_full_name(self):
        self.assertEqual(self.sign("Ngozi Eze").status_code, status.HTTP_200_OK)
        refused = self.sign("Dr. Ngozi Eze")
        self.assertEqual(refused.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("signature", refused.data["error"]["details"]["data"])

    def test_account_without_a_name_cannot_sign(self):
        self.owner.first_name = self.owner.last_name = ""
        self.owner.save()
        self.assertEqual(self.sign("Ngozi Eze").status_code, status.HTTP_400_BAD_REQUEST)

    def test_submit_is_refused_for_someone_else_signature(self):
        self.assertEqual(self.sign("Ngozi Eze").status_code, status.HTTP_200_OK)
        colleague = User.objects.create_user(
            "admin@lab.test", first_name="Bayo", last_name="Ade", email_verified_at=timezone.now(),
        )
        self.organisation.memberships.create(user=colleague, role="admin", is_active=True)
        self.client.force_authenticate(colleague)
        response = self.client.post(reverse("compliance-application-submit", args=[self.application.id]))
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["error"]["code"], "signatory_mismatch")


class ProfessionalDeclarationTests(APITestCase):
    def setUp(self):
        media = TemporaryDirectory()
        self.addCleanup(media.cleanup)
        override = override_settings(MEDIA_ROOT=media.name)
        override.enable()
        self.addCleanup(override.disable)
        self.applicant = User.objects.create_user("inspector@field.test", email_verified_at=timezone.now())
        self.organisation = Organisation.objects.create(
            name="Sahel Inspection", organisation_type="inspection_body", verification_status="verified",
        )
        self.client.force_authenticate(self.applicant)

    def apply(self, signature):
        data = {
            "organisation": str(self.organisation.id),
            "role": "officer_inspector",
            "personal": {"full_legal_name": "Amina Bello", "date_of_birth": "1990-04-02", "national_id": "12345678901", "job_title": "Inspector"},
            "capability": {"capabilities": ["Sampling"], "minerals": ["Gold"]},
            "experience": {"years_experience": 8, "summary": "Field sampling."},
            "declaration": {"information_true": True, "consent_to_verification": True, "understands_verification": True, "signature": signature},
        }
        return self.client.post(
            reverse("quality-professional-application-list"),
            {"data": json.dumps(data), "professional_qualifications": pdf(), "inspection_credentials": pdf()},
            format="multipart",
        )

    def test_signature_must_be_the_full_legal_name(self):
        refused = self.apply("A. Bello")
        self.assertEqual(refused.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(QualityProfessionalApplication.objects.count(), 0)
        self.assertEqual(self.apply("Amina Bello").status_code, status.HTTP_201_CREATED)
