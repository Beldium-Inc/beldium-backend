"""Guarded replacement for Django's built-in ``flush`` command.

Django's ``flush`` deletes every row in every table. This subclass checks the
environment first and then runs the normal command. Because this app is listed
in INSTALLED_APPS, ``manage.py flush`` and ``call_command("flush")`` both use
this version.

Note: Django's TransactionTestCase also calls ``flush`` after each test, so
the test environment must stay one of ci, local or development.
"""

from django.core.management.commands.flush import Command as DjangoFlushCommand

from common.environment_guard import require_safe_environment


class Command(DjangoFlushCommand):
    help = DjangoFlushCommand.help + " Refuses to run unless ENVIRONMENT is ci, local or development."

    def handle(self, **options):
        require_safe_environment("flush")
        return super().handle(**options)
