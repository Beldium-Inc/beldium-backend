"""Hardening of the shared audit tables (task G10)."""
from django.contrib.admin.sites import site
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import AccountAuditEvent, User
from marketplace.models import MarketplaceAuditEvent
from quality.models import QualityRoleAssignment, RoleAssignmentStatus


def make_user(email, **extra):
    return User.objects.create_user(email=email, password="Str0ng-Passw0rd!", email_verified_at=timezone.now(), **extra)


def give_role(user, code):
    return QualityRoleAssignment.objects.create(
        user=user, role_id=code, status=RoleAssignmentStatus.ACTIVE, valid_from=timezone.localdate(),
    )


class ActorCopyTests(APITestCase):
    def test_copy_is_taken_on_create_and_kept_when_the_link_is_cleared(self):
        user = make_user("actor@beldium.test", first_name="Ada", last_name="Obi")
        event = AccountAuditEvent.objects.create(actor=user, event_type="mining.test")
        self.assertEqual(event.actor_email, "actor@beldium.test")
        self.assertEqual(event.actor_name, "Ada Obi")
        AccountAuditEvent.objects.filter(pk=event.pk).update(actor=None)
        event.refresh_from_db()
        from mining.serializers import MiningAuditEventSerializer
        self.assertEqual(MiningAuditEventSerializer(event).data["actor"], "Ada Obi")


class AdminSiteTests(APITestCase):
    def test_audit_admins_are_read_only(self):
        request = RequestFactory().get("/")
        request.user = make_user("super@beldium.test", is_staff=True, is_superuser=True)
        for model in (AccountAuditEvent, MarketplaceAuditEvent):
            model_admin = site._registry[model]
            self.assertFalse(model_admin.has_add_permission(request))
            self.assertFalse(model_admin.has_change_permission(request))
            self.assertFalse(model_admin.has_delete_permission(request))


class SectorAuditFeedTests(APITestCase):
    def setUp(self):
        self.admin = make_user("admin@beldium.test", is_staff=True)
        give_role(self.admin, "ADM")
        self.desk = make_user("desk@beldium.test", is_staff=True)

    def test_admin_role_is_refused_on_mining_and_processing_feeds(self):
        self.client.force_authenticate(self.admin)
        for name in ("mining-audit-list", "processing-audit-list"):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 403, name)
            self.assertEqual(response.data["error"]["code"], "audit_log_closed")

    def test_staff_without_admin_role_still_reads(self):
        self.client.force_authenticate(self.desk)
        for name in ("mining-audit-list", "processing-audit-list"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_admin_who_also_holds_a_reading_role_reads(self):
        give_role(self.admin, "TL")
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get(reverse("mining-audit-list")).status_code, 200)
