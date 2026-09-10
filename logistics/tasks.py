from celery import shared_task
from logistics.services import monitor_expiries


@shared_task
def check_expiring_credentials():
    return monitor_expiries()
