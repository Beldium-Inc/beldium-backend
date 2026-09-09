from django.core.management.base import BaseCommand
from logistics.services import monitor_expiries


class Command(BaseCommand):
    help = 'Detect expiring logistics credentials and apply scoped expiry restrictions.'

    def handle(self, *args, **options):
        self.stdout.write(str(monitor_expiries()))
