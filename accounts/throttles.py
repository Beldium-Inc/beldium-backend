import hashlib

from rest_framework.throttling import SimpleRateThrottle


class EmailOrIPRateThrottle(SimpleRateThrottle):
    """Rate limit by normalized email and IP without putting email addresses in cache keys."""

    def parse_rate(self, rate):
        if rate is None:
            return None, None
        number, period = rate.split("/", 1)
        units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
        digits = "".join(character for character in period if character.isdigit())
        unit = period[len(digits):].lower()
        return int(number), int(digits or "1") * units[unit]

    def get_cache_key(self, request, view):
        email = str(request.data.get("email", "")).strip().lower()
        email_digest = hashlib.sha256(email.encode()).hexdigest() if email else "missing"
        ident = f"{self.get_ident(request)}:{email_digest}"
        return self.cache_format % {"scope": self.scope, "ident": ident}


class VerificationIssueThrottle(EmailOrIPRateThrottle):
    scope = "verification_issue"


class VerificationAttemptThrottle(EmailOrIPRateThrottle):
    scope = "verification_attempt"
