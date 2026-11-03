import shutil
import tempfile
from datetime import date

from django.contrib.admin import helpers
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import AccountAuditEvent, User
from logistics.models import (
    ApprovalCondition,
    LogisticsApplication,
    LogisticsCompany,
    LogisticsDocument,
    ScopeRestriction,
)
from organisations.models import Organisation, OrganisationMembership

MEDIA_ROOT = tempfile.mkdtemp()


@override_settings(
    MEDIA_ROOT=MEDIA_ROOT,
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
)
class RemoveTestAccountsAdminTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.superuser = User.objects.create_superuser("root@example.com", "password")
        self.tester = User.objects.create_user("tester@example.com", "password")
        self.organisation = Organisation.objects.create(name="Test Haulage", organisation_type="logistics_company")
        OrganisationMembership.objects.create(organisation=self.organisation, user=self.tester, role="owner")
        company = LogisticsCompany.objects.create(
            organisation=self.organisation, contact_name="T", contact_email="t@example.com", contact_phone="1",
        )
        application = LogisticsApplication.objects.create(company=company, created_by=self.tester)
        condition = ApprovalCondition.objects.create(
            application=application, title="Insurance", description="Upload it", due_date=date(2027, 1, 1),
        )
        document = LogisticsDocument.objects.create(
            application=application, domain="corporate", document_type="cac", title="CAC",
            file=SimpleUploadedFile("cac.pdf", b"%PDF-1.4"), original_name="cac.pdf",
            uploaded_by=self.tester, condition=condition,
        )
        ScopeRestriction.objects.create(
            company=company, service_scope="all", reason="Missing", source_document=document,
            source_condition=condition,
        )
        self.url = reverse("admin:accounts_user_changelist")

    def post_action(self, users, confirm=False):
        data = {"action": "remove_test_accounts", helpers.ACTION_CHECKBOX_NAME: [str(u.pk) for u in users]}
        if confirm:
            data["confirm"] = "yes"
        return self.client.post(self.url, data, follow=confirm)

    def test_plain_delete_is_still_refused_so_this_action_is_needed(self):
        self.client.force_login(self.superuser)
        response = self.client.post(self.url, {
            "action": "delete_selected", helpers.ACTION_CHECKBOX_NAME: [str(self.tester.pk)], "post": "yes",
        })
        self.assertContains(response, "Cannot delete user")
        self.assertTrue(User.objects.filter(pk=self.tester.pk).exists())

    def test_confirmation_page_lists_what_goes_and_deletes_nothing(self):
        self.client.force_login(self.superuser)
        response = self.post_action([self.tester])
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "tester@example.com")
        self.assertContains(response, "Logistics documents: 1")
        self.assertContains(response, "Test Haulage")
        self.assertTrue(User.objects.filter(pk=self.tester.pk).exists())

    def test_confirming_removes_account_logistics_records_and_its_organisation(self):
        self.client.force_login(self.superuser)
        response = self.post_action([self.tester], confirm=True)
        self.assertContains(response, "Account tester@example.com: Removed")
        self.assertFalse(User.objects.filter(pk=self.tester.pk).exists())
        self.assertFalse(LogisticsApplication.objects.exists())
        self.assertFalse(LogisticsDocument.objects.exists())
        self.assertFalse(ScopeRestriction.objects.exists())
        self.assertFalse(LogisticsCompany.objects.exists())
        self.assertFalse(Organisation.objects.filter(pk=self.organisation.pk).exists())
        self.assertTrue(AccountAuditEvent.objects.filter(
            event_type="account.test_account_removed", actor=self.superuser,
            metadata__email="tester@example.com",
        ).exists())

    def test_organisation_with_a_real_member_is_kept(self):
        colleague = User.objects.create_user("real@example.com", "password")
        OrganisationMembership.objects.create(organisation=self.organisation, user=colleague, role="member")
        self.client.force_login(self.superuser)
        self.post_action([self.tester], confirm=True)
        self.assertFalse(User.objects.filter(pk=self.tester.pk).exists())
        self.assertTrue(Organisation.objects.filter(pk=self.organisation.pk).exists())
        self.assertTrue(User.objects.filter(pk=colleague.pk).exists())

    def test_staff_accounts_are_skipped(self):
        staff = User.objects.create_user("staff@example.com", "password", is_staff=True)
        self.client.force_login(self.superuser)
        response = self.post_action([staff, self.tester], confirm=True)
        self.assertContains(response, "Account staff@example.com: Staff and superuser accounts are never removed")
        self.assertTrue(User.objects.filter(pk=staff.pk).exists())
        self.assertFalse(User.objects.filter(pk=self.tester.pk).exists())

    def test_staff_who_is_not_superuser_cannot_use_the_action(self):
        staff = User.objects.create_user("staff@example.com", "password", is_staff=True)
        staff.user_permissions.add(*Permission.objects.filter(
            codename__in=["view_user", "change_user", "delete_user"]
        ))
        self.client.force_login(staff)
        changelist = self.client.get(self.url)
        self.assertNotContains(changelist, "remove_test_accounts")
        self.post_action([self.tester], confirm=True)
        self.assertTrue(User.objects.filter(pk=self.tester.pk).exists())
