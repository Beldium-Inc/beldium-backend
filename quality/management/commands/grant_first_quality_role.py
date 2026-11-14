"""Give the first Beldium Admin or Q&C Partner role, so the API can take over.

Role assignments are made through the API: Admin creates, Q&C approves. That
needs one Admin and one Q&C Partner to exist first. This command creates that
first active assignment and nothing more. It refuses once an active holder of
the role exists, so it cannot be used to skip the approval step later.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import User
from quality.models import QualityRole, QualityRoleAssignment, RoleAssignmentStatus

BOOTSTRAP_ROLES = ("ADM", "QC")


class Command(BaseCommand):
    help = "Create the first active ADM or QC role assignment. Refused once one exists."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--role", required=True, choices=BOOTSTRAP_ROLES)

    @transaction.atomic
    def handle(self, *args, email, role, **options):
        user = User.objects.filter(email__iexact=email, is_active=True).first()
        if user is None:
            raise CommandError("No active user has that email address.")
        if QualityRoleAssignment.objects.select_for_update().filter(role_id=role, status=RoleAssignmentStatus.ACTIVE).exists():
            raise CommandError(f"An active {role} already exists. Use the role assignment API instead.")
        assignment = QualityRoleAssignment.objects.create(
            user=user,
            role=QualityRole.objects.get(pk=role),
            valid_from=timezone.localdate(),
            status=RoleAssignmentStatus.ACTIVE,
            decided_at=timezone.now(),
            decision_reason="First holder of this role, created by the bootstrap command.",
        )
        self.stdout.write(f"Created active {role} assignment {assignment.pk}.")
