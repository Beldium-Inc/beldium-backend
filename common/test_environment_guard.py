"""Tests for the environment guard on destructive and account-creating commands.

Each guarded command must refuse to run unless ENVIRONMENT is ci, local or
development. The refusal tests run with ENVIRONMENT set to production and
staging, and check two things: the command raises CommandError, and the
database is left exactly as it was.
"""

from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from accounts.models import User
from common.environment_guard import is_safe_environment, require_safe_environment

DEPLOYED_ENVIRONMENTS = ["production", "staging"]


def make_user(email):
    return User.objects.create_user(email=email, password="Str0ng-Passw0rd!")


class EnvironmentCheckTests(TestCase):
    def test_ci_local_and_development_are_allowed(self):
        for environment in ["ci", "local", "development", "CI", " Local "]:
            with self.subTest(environment=environment):
                with override_settings(ENVIRONMENT=environment):
                    self.assertTrue(is_safe_environment())
                    require_safe_environment("any_command")

    def test_deployed_and_unknown_environments_are_refused(self):
        for environment in DEPLOYED_ENVIRONMENTS + ["build", ""]:
            with self.subTest(environment=environment):
                with override_settings(ENVIRONMENT=environment):
                    self.assertFalse(is_safe_environment())
                    with self.assertRaises(CommandError) as raised:
                        require_safe_environment("any_command")
                    self.assertIn("any_command refuses to run", str(raised.exception))


class RefusalInProductionTests(TestCase):
    """Every guarded command refuses in production and staging and writes nothing."""

    def setUp(self):
        self.existing = make_user("keep-me@example.com")

    def assert_refused(self, command, *args, **options):
        for environment in DEPLOYED_ENVIRONMENTS:
            with self.subTest(command=command, environment=environment):
                with override_settings(ENVIRONMENT=environment):
                    with self.assertRaises(CommandError):
                        call_command(command, *args, stdout=StringIO(), stderr=StringIO(), **options)

    def test_flush_refuses_and_keeps_rows(self):
        self.assert_refused("flush", interactive=False)
        self.assertTrue(User.objects.filter(pk=self.existing.pk).exists())

    def test_delete_test_users_refuses_and_keeps_the_user(self):
        with override_settings(DELETE_TEST_USER_EMAILS="keep-me@example.com"):
            self.assert_refused("delete_test_users")
        self.assertTrue(User.objects.filter(pk=self.existing.pk).exists())

    def test_bootstrap_admin_refuses_and_creates_no_admin(self):
        with override_settings(ADMIN_EMAIL="admin@example.com", ADMIN_BOOTSTRAP_SECRET="not-a-real-secret-0123"):
            self.assert_refused("bootstrap_admin")
        self.assertFalse(User.objects.filter(email="admin@example.com").exists())

    def test_import_legacy_users_refuses_before_connecting(self):
        # The URL is unreachable on purpose. If the guard ran after the
        # connection attempt, this would fail with a database error instead.
        with override_settings(LEGACY_DATABASE_URL="postgres://nobody@127.0.0.1:1/legacy"):
            self.assert_refused("import_legacy_users")
        self.assertEqual(User.objects.count(), 1)

    def test_seed_test_accounts_refuses_and_creates_no_accounts(self):
        self.assert_refused("seed_test_accounts")
        self.assertFalse(User.objects.filter(email__endswith=".test").exists())
        self.assertEqual(User.objects.count(), 1)

    def test_purge_demo_data_refuses_and_keeps_the_user(self):
        self.assert_refused("purge_demo_data", yes=True)
        self.assertTrue(User.objects.filter(pk=self.existing.pk).exists())


class AllowedEnvironmentTests(TestCase):
    """The guard does not block the commands in the environments it allows."""

    def test_delete_test_users_runs_in_development(self):
        make_user("keep-me@example.com")
        out = StringIO()
        with override_settings(ENVIRONMENT="development", DELETE_TEST_USER_EMAILS=""):
            call_command("delete_test_users", stdout=out)
        self.assertIn("not set, skipping", out.getvalue())

    def test_bootstrap_admin_runs_in_ci(self):
        out = StringIO()
        with override_settings(ENVIRONMENT="ci", ADMIN_EMAIL="", ADMIN_BOOTSTRAP_SECRET=""):
            call_command("bootstrap_admin", stdout=out)
        self.assertIn("skipping admin bootstrap", out.getvalue())
