"""bootstrap_admin may run on a real environment only with its name typed (E3)."""
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from accounts.models import User


@override_settings(ENVIRONMENT="production", ADMIN_EMAIL="root@beldium.test", ADMIN_BOOTSTRAP_SECRET="Str0ng-Passw0rd!")
class ConfirmedEnvironmentTests(TestCase):
    def test_refused_without_confirmation(self):
        with self.assertRaises(CommandError):
            call_command("bootstrap_admin", stdout=StringIO())
        self.assertFalse(User.objects.filter(email="root@beldium.test").exists())

    def test_refused_with_the_wrong_name(self):
        with self.assertRaises(CommandError):
            call_command("bootstrap_admin", confirm_environment="staging", stdout=StringIO())

    def test_runs_with_the_right_name(self):
        call_command("bootstrap_admin", confirm_environment="production", stdout=StringIO())
        self.assertTrue(User.objects.get(email="root@beldium.test").is_superuser)

    def test_destructive_command_still_refused(self):
        with self.assertRaises(CommandError):
            call_command("delete_test_users", stdout=StringIO())
