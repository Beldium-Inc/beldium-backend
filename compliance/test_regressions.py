import json

from django.core.files.base import ContentFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import User
from compliance.models import ApplicationStatus, ComplianceApplication, ComplianceDocument, Personnel
from organisations.models import Organisation


DECLARATION = {
    "accuracy_confirmed": True, "documents_genuine": True, "compliance_agreed": True,
    "disclose_changes": True, "authorised": True, "confirmed": True,
    "signatory_name": "Ada Lovelace", "signatory_position": "Compliance Director",
}


@override_settings(MEDIA_ROOT="/tmp/beldium-compliance-tests")
class DeclarationDateTests(APITestCase):
    """A date in the declaration section used to reach the JSONField unserialised."""

    def setUp(self):
        self.owner = User.objects.create_user("owner@example.com", "SafePassword-2026!", email_verified_at=timezone.now())
        self.client.force_authenticate(self.owner)
        self.organisation = Organisation.objects.create(
            name="Assurance Ltd", organisation_type="compliance_partner", registration_number="RC-90101"
        )
        self.organisation.memberships.create(user=self.owner, role="owner", is_active=True)
        self.application = ComplianceApplication.objects.create(organisation=self.organisation)

    def _save(self, data):
        return self.client.patch(
            reverse("compliance-application-declaration-section", args=[self.application.id]),
            {"data": data}, format="json",
        )

    def test_declaration_date_is_stored_as_an_iso_string(self):
        response = self._save({**DECLARATION, "declaration_date": "2026-09-08"})

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.application.refresh_from_db()
        self.assertEqual(self.application.declaration["declaration_date"], "2026-09-08")

    def test_declaration_date_is_still_validated(self):
        self.assertEqual(self._save({**DECLARATION, "declaration_date": "not-a-date"}).status_code, status.HTTP_400_BAD_REQUEST)

    def test_declaration_date_remains_optional(self):
        self.assertEqual(self._save(DECLARATION).status_code, status.HTTP_200_OK)


@override_settings(MEDIA_ROOT="/tmp/beldium-compliance-tests")
class ApplicationDeleteScopeTests(APITestCase):
    """DELETE is enabled for the personnel sub-route only, never the application."""

    def setUp(self):
        self.owner = User.objects.create_user("owner@example.com", "SafePassword-2026!", email_verified_at=timezone.now())
        self.client.force_authenticate(self.owner)
        self.organisation = Organisation.objects.create(
            name="Assurance Ltd", organisation_type="compliance_partner", registration_number="RC-90102"
        )
        self.organisation.memberships.create(user=self.owner, role="owner", is_active=True)
        self.application = ComplianceApplication.objects.create(organisation=self.organisation)

    def test_personnel_can_be_deleted(self):
        person = self.client.post(
            reverse("compliance-application-personnel", args=[self.application.id]),
            {"full_name": "Ada Lovelace", "role": "Compliance Manager"}, format="json",
        )
        self.assertEqual(person.status_code, status.HTTP_201_CREATED, person.data)

        response = self.client.delete(
            reverse("compliance-application-personnel-detail", args=[self.application.id, person.data["id"]])
        )

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(self.application.personnel.count(), 0)

    def test_the_application_itself_cannot_be_deleted(self):
        response = self.client.delete(reverse("compliance-application-detail", args=[self.application.id]))

        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertTrue(ComplianceApplication.objects.filter(id=self.application.id).exists())


class CollectionAuthenticationTests(APITestCase):
    """IsApplicationMember replaces the IsAuthenticated default, so the collection
    routes have to name it explicitly or they end up ungated."""

    def test_listing_requires_authentication(self):
        response = self.client.get(reverse("compliance-application-list"))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.data["error"]["code"], "not_authenticated")

    def test_creating_requires_authentication(self):
        response = self.client.post(
            reverse("compliance-application-list"),
            {"organisation": "00000000-0000-0000-0000-000000000000"}, format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_a_member_can_still_list_their_own(self):
        owner = User.objects.create_user("owner@example.com", "SafePassword-2026!", email_verified_at=timezone.now())
        organisation = Organisation.objects.create(
            name="Assurance Ltd", organisation_type="compliance_partner", registration_number="RC-90103"
        )
        organisation.memberships.create(user=owner, role="owner", is_active=True)
        ComplianceApplication.objects.create(organisation=organisation)
        self.client.force_authenticate(owner)

        response = self.client.get(reverse("compliance-application-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)


@override_settings(MEDIA_ROOT="/tmp/beldium-compliance-tests")
class DecisionRequiresSubmissionTests(APITestCase):
    """A decision records reviewed_at and moves the organisation's verification
    status, so it must not be possible against an application never submitted."""

    def setUp(self):
        self.staff = User.objects.create_superuser("staff@example.com", "SafePassword-2026!")
        self.client.force_authenticate(self.staff)
        self.organisation = Organisation.objects.create(
            name="Assurance Ltd", organisation_type="compliance_partner", registration_number="RC-90104"
        )

    def _application(self, status_value, organisation=None):
        # ComplianceApplication.organisation is a OneToOne, so each application
        # in this class needs an organisation of its own.
        organisation = organisation or self.organisation
        application = ComplianceApplication.objects.create(organisation=organisation, status=status_value)
        if status_value != ApplicationStatus.DRAFT:
            ComplianceApplication.objects.filter(pk=application.pk).update(submitted_at=timezone.now())
        return application

    def _other_organisation(self, suffix):
        return Organisation.objects.create(
            name=f"Assurance {suffix}", organisation_type="compliance_partner",
            registration_number=f"RC-9011{suffix}",
        )

    def _decide(self, application, status_value):
        return self.client.post(
            reverse("compliance-application-decide", args=[application.id]),
            {"status": status_value, "notes": "reviewed"}, format="json",
        )

    def test_a_draft_cannot_be_decided(self):
        application = self._application(ApplicationStatus.DRAFT)

        response = self._decide(application, ApplicationStatus.VERIFIED)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["error"]["code"], "application_not_submitted")

    def test_a_refused_decision_writes_nothing(self):
        application = self._application(ApplicationStatus.DRAFT)

        self._decide(application, ApplicationStatus.VERIFIED)

        application.refresh_from_db()
        self.organisation.refresh_from_db()
        self.assertEqual(application.status, ApplicationStatus.DRAFT)
        self.assertIsNone(application.reviewed_at)
        self.assertEqual(application.review_notes, "")
        self.assertEqual(self.organisation.verification_status, "draft")
        self.assertIsNone(self.organisation.verified_at)

    def test_every_submitted_state_can_still_be_decided(self):
        for index, (current, target) in enumerate([
            (ApplicationStatus.UNDER_REVIEW, ApplicationStatus.ACTION_REQUIRED),
            (ApplicationStatus.ACTION_REQUIRED, ApplicationStatus.REJECTED),
            (ApplicationStatus.CONDITIONALLY_APPROVED, ApplicationStatus.VERIFIED),
        ]):
            with self.subTest(current=current):
                application = self._application(current, self._other_organisation(index))

                response = self._decide(application, target)

                self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
                self.assertEqual(response.data["status"], target)


@override_settings(MEDIA_ROOT="/tmp/beldium-compliance-download-tests")
class PrivateDownloadTests(APITestCase):
    """Uploads are private. MEDIA_ROOT is not routed at all, so the only way to
    read one is through an action that has already checked the caller."""

    def setUp(self):
        self.owner = User.objects.create_user("owner@example.com", "SafePassword-2026!", email_verified_at=timezone.now())
        self.outsider = User.objects.create_user("outsider@example.com", "SafePassword-2026!", email_verified_at=timezone.now())
        self.organisation = Organisation.objects.create(
            name="Assurance Ltd", organisation_type="compliance_partner", registration_number="RC-90105"
        )
        self.organisation.memberships.create(user=self.owner, role="owner", is_active=True)
        self.application = ComplianceApplication.objects.create(organisation=self.organisation)

        self.document = ComplianceDocument.objects.create(
            application=self.application, document_type="company_profile", title="Company Profile"
        )
        self.document.file.save("company-profile.pdf", ContentFile(b"%PDF-1.4 private"), save=True)

        self.person = Personnel.objects.create(
            application=self.application, full_name="Ada Lovelace", role="Compliance Manager"
        )
        self.person.cv.save("ada-cv.pdf", ContentFile(b"%PDF-1.4 private cv"), save=True)

    def _document_url(self):
        return reverse("compliance-application-download-document", args=[self.application.id, self.document.id])

    def _cv_url(self):
        return reverse("compliance-application-download-personnel-file",
                       args=[self.application.id, self.person.id, "cv"])

    def test_media_root_is_not_routed(self):
        from django.urls import Resolver404, resolve

        with self.assertRaises(Resolver404):
            resolve(f"/media/{self.document.file.name}")

    def test_a_document_needs_authentication(self):
        self.assertEqual(self.client.get(self._document_url()).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_a_document_is_hidden_from_other_organisations(self):
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get(self._document_url()).status_code, status.HTTP_404_NOT_FOUND)

    def test_a_member_can_download_a_document(self):
        self.client.force_authenticate(self.owner)

        response = self.client.get(self._document_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(b"".join(response.streaming_content), b"%PDF-1.4 private")
        # Storage may add a suffix when a name collides, so the download is
        # named after what was stored rather than what was uploaded.
        self.assertIn(self.document.original_name, response["Content-Disposition"])
        self.assertTrue(response["Content-Disposition"].startswith("attachment;"))

    def test_personnel_files_are_gated_the_same_way(self):
        self.assertEqual(self.client.get(self._cv_url()).status_code, status.HTTP_401_UNAUTHORIZED)

        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get(self._cv_url()).status_code, status.HTTP_404_NOT_FOUND)

        self.client.force_authenticate(self.owner)
        response = self.client.get(self._cv_url())
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(b"".join(response.streaming_content), b"%PDF-1.4 private cv")

    def test_a_missing_file_is_not_found(self):
        self.client.force_authenticate(self.owner)
        url = reverse("compliance-application-download-personnel-file",
                      args=[self.application.id, self.person.id, "certificate"])

        self.assertEqual(self.client.get(url).status_code, status.HTTP_404_NOT_FOUND)

    def test_the_serialiser_advertises_the_guarded_url(self):
        self.client.force_authenticate(self.owner)

        response = self.client.get(reverse("compliance-application-detail", args=[self.application.id]))

        self.assertIn(self._document_url(), response.data["documents"][0]["file_url"])
        self.assertIn(self._cv_url(), response.data["personnel"][0]["cv_url"])
        self.assertNotIn("/media/", response.data["documents"][0]["file_url"])


class ApplicationReferenceTests(APITestCase):
    """Every application carries a quotable reference, including ones created
    before the field existed."""

    def setUp(self):
        self.owner = User.objects.create_user("owner@example.com", "SafePassword-2026!", email_verified_at=timezone.now())
        self.organisation = Organisation.objects.create(
            name="Assurance Ltd", organisation_type="compliance_partner", registration_number="RC-90106"
        )
        self.organisation.memberships.create(user=self.owner, role="owner", is_active=True)

    def test_a_new_application_is_given_one(self):
        application = ComplianceApplication.objects.create(organisation=self.organisation)

        self.assertIsNotNone(application.reference)
        self.assertTrue(application.reference.startswith("BLD-APP-"))

    def test_references_are_distinct(self):
        references = set()
        for index in range(5):
            organisation = Organisation.objects.create(
                name=f"Other {index}", organisation_type="compliance_partner",
                registration_number=f"RC-9020{index}",
            )
            references.add(ComplianceApplication.objects.create(organisation=organisation).reference)

        self.assertEqual(len(references), 5)

    def test_it_survives_a_partial_save(self):
        # Most writes in the viewset use save(update_fields=...), which would
        # drop a value assigned in save() if it were not already persisted.
        application = ComplianceApplication.objects.create(organisation=self.organisation)
        original = application.reference

        application.status = ApplicationStatus.UNDER_REVIEW
        application.save(update_fields=["status", "updated_at"])
        application.refresh_from_db()

        self.assertEqual(application.reference, original)

    def test_the_migration_backfills_rows_that_predate_the_field(self):
        import importlib

        from django.apps import apps

        # The module name starts with a digit, so it cannot be imported normally.
        migration = importlib.import_module(
            "compliance.migrations.0002_complianceapplication_reference"
        )
        application = ComplianceApplication.objects.create(organisation=self.organisation)
        ComplianceApplication.objects.filter(pk=application.pk).update(reference=None)

        migration.backfill_references(apps, None)

        application.refresh_from_db()
        self.assertIsNotNone(application.reference)
        self.assertTrue(application.reference.startswith("BLD-APP-"))

    def test_the_api_returns_it(self):
        application = ComplianceApplication.objects.create(organisation=self.organisation)
        self.client.force_authenticate(self.owner)

        detail = self.client.get(reverse("compliance-application-detail", args=[application.id]))
        dashboard = self.client.get(reverse("dashboard-list"))

        self.assertEqual(detail.data["reference"], application.reference)
        self.assertEqual(dashboard.data["applications"][0]["reference"], application.reference)


class ActivityFeedTests(APITestCase):
    """The feed is read by applicants, so it must not carry reviewer detail."""

    def setUp(self):
        self.owner = User.objects.create_user("owner@example.com", "SafePassword-2026!", email_verified_at=timezone.now(), first_name="Ada", last_name="Lovelace")
        self.outsider = User.objects.create_user("outsider@example.com", "SafePassword-2026!", email_verified_at=timezone.now())
        self.reviewer = User.objects.create_superuser("reviewer@example.com", "SafePassword-2026!")
        self.organisation = Organisation.objects.create(
            name="Assurance Ltd", organisation_type="compliance_partner", registration_number="RC-90107"
        )
        self.organisation.memberships.create(user=self.owner, role="owner", is_active=True)
        self.application = ComplianceApplication.objects.create(organisation=self.organisation)
        ComplianceApplication.objects.filter(pk=self.application.pk).update(submitted_at=timezone.now())

    def _url(self):
        return reverse("compliance-application-activity", args=[self.application.id])

    def _generate_events(self):
        self.client.force_authenticate(self.owner)
        self.client.post(
            reverse("compliance-application-personnel", args=[self.application.id]),
            {"full_name": "Grace Hopper", "role": "Compliance Manager"}, format="json",
        )
        self.client.force_authenticate(self.reviewer)
        self.client.post(
            reverse("compliance-application-request-document", args=[self.application.id]),
            {"document_type": "code_of_conduct", "title": "Code of Conduct"}, format="json",
        )

    def test_it_requires_authentication(self):
        self.assertEqual(self.client.get(self._url()).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_it_is_hidden_from_other_organisations(self):
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get(self._url()).status_code, status.HTTP_404_NOT_FOUND)

    def test_it_describes_what_happened(self):
        self._generate_events()
        self.client.force_authenticate(self.owner)

        response = self.client.get(self._url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        descriptions = [row["description"] for row in response.data["results"]]
        self.assertIn("Added Grace Hopper to key personnel", descriptions)
        self.assertIn("Reviewer requested Code of Conduct", descriptions)

    def test_a_colleague_is_named_but_a_reviewer_is_not(self):
        self._generate_events()
        self.client.force_authenticate(self.owner)

        rows = {row["description"]: row["actor"] for row in self.client.get(self._url()).data["results"]}

        self.assertEqual(rows["Added Grace Hopper to key personnel"], "Ada Lovelace")
        self.assertEqual(rows["Reviewer requested Code of Conduct"], "Beldium review team")

    def test_it_leaks_no_reviewer_detail(self):
        self._generate_events()
        self.client.force_authenticate(self.owner)

        payload = json.dumps(self.client.get(self._url()).data)

        for leaked in ["reviewer@example.com", "ip_address", "user_agent", "metadata"]:
            self.assertNotIn(leaked, payload)

    def test_only_this_application_appears(self):
        other_organisation = Organisation.objects.create(
            name="Other Ltd", organisation_type="compliance_partner", registration_number="RC-90108"
        )
        other_organisation.memberships.create(user=self.owner, role="owner", is_active=True)
        other = ComplianceApplication.objects.create(organisation=other_organisation)
        self.client.force_authenticate(self.owner)
        self.client.post(
            reverse("compliance-application-personnel", args=[other.id]),
            {"full_name": "Someone Else", "role": "Analyst"}, format="json",
        )
        self._generate_events()
        self.client.force_authenticate(self.owner)

        descriptions = [row["description"] for row in self.client.get(self._url()).data["results"]]

        self.assertNotIn("Added Someone Else to key personnel", descriptions)
