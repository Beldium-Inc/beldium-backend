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


class IPRateThrottle(EmailOrIPRateThrottle):
    """Keyed on the caller's address alone.

    The email-scoped key above is right for endpoints that act on an existing
    address, but wrong for ones that mint records: abuse there is a single
    caller cycling through addresses, and an email-scoped bucket hands every
    new address a fresh allowance.
    """

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class RegistrationThrottle(IPRateThrottle):
    scope = "registration"


class SocialAuthThrottle(IPRateThrottle):
    scope = "social_auth"
