"""The one SMS send path, with a delivery record.

Sending is behind the SMS_ENABLED setting, so it can be switched on when the
Termii sender ID is approved without a code change. While it is off, a
delivery row with status "skipped" is still written, so every attempt is on
record.
"""
import json
import logging
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings

from common.models import SmsDelivery, SmsStatus

logger = logging.getLogger(__name__)

TERMII_SEND_URL = "https://api.ng.termii.com/api/sms/send"


def _post(payload):
    request = Request(
        TERMII_SEND_URL, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read() or b"{}")


def send_sms(phone_number, message, *, purpose):
    to = (phone_number or "").strip().lstrip("+")
    delivery = SmsDelivery(to_last_digits=to[-4:], purpose=purpose[:60])
    api_key = getattr(settings, "TERMII_API_KEY", "")
    if not getattr(settings, "SMS_ENABLED", False) or not api_key:
        delivery.status = SmsStatus.SKIPPED
        delivery.save()
        return delivery
    payload = {
        "to": to,
        "from": getattr(settings, "TERMII_SENDER_ID", "N-Alert"),
        "sms": message,
        "type": "plain",
        "channel": "generic",
        "api_key": api_key,
    }
    try:
        body = _post(payload)
    except HTTPError as exc:
        delivery.status = SmsStatus.FAILED
        delivery.error = f"Termii returned {exc.code}"
    except (URLError, OSError, ValueError) as exc:
        delivery.status = SmsStatus.FAILED
        delivery.error = exc.__class__.__name__
    else:
        delivery.status = SmsStatus.SENT
        delivery.provider_message_id = str(body.get("message_id") or "")[:120]
    delivery.save()
    if delivery.status == SmsStatus.FAILED:
        logger.warning("SMS send failed", extra={"purpose": purpose, "to": delivery.to_last_digits, "error": delivery.error})
    return delivery
