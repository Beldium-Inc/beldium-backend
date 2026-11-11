"""Refuse destructive or account-creating management commands outside test and dev.

Commands that create or delete user accounts, or wipe tables, must never run
against a production or staging database by accident. Each of them calls
``require_safe_environment`` first thing in ``handle``. The check reads
``settings.ENVIRONMENT`` at call time, so tests can switch it with
``override_settings``.
"""

from django.conf import settings
from django.core.management.base import CommandError

# Only these values allow the guarded commands to run. Anything else,
# including an empty value or a name we do not recognise, is refused.
SAFE_ENVIRONMENTS = frozenset({"ci", "local", "development"})


def is_safe_environment(environment=None):
    """Return True when the given (or configured) environment name is allowed."""
    if environment is None:
        environment = settings.ENVIRONMENT
    return str(environment).strip().lower() in SAFE_ENVIRONMENTS


def require_safe_environment(command_name):
    """Raise CommandError unless the app runs as ci, local or development."""
    if not is_safe_environment():
        raise CommandError(
            f"{command_name} refuses to run when ENVIRONMENT={settings.ENVIRONMENT}. "
            "It only runs when ENVIRONMENT is ci, local or development."
        )
