import hashlib

from rest_framework.throttling import SimpleRateThrottle, UserRateThrottle


class MultiUnitRateMixin:
    """Accepts a multiplier in the period, e.g. "5/10m", which DRF's parser rejects.

    DRF reads only the first character of the period, so "10m" would resolve as
    "1"; every throttle in this module therefore has to share this parser rather
    than inherit the stock one.
    """

    def parse_rate(self, rate):
        if rate is None:
            return None, None
        number, period = rate.split("/", 1)
        units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
        digits = "".join(character for character in period if character.isdigit())
        unit = period[len(digits):].lower()
        return int(number), int(digits or "1") * units[unit]


class EmailOrIPRateThrottle(MultiUnitRateMixin, SimpleRateThrottle):
    """Rate limit by normalized email and IP without putting email addresses in cache keys."""

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


class EmailRateThrottle(EmailOrIPRateThrottle):
    """Keyed on the submitted email alone, so one account has a global budget.

    The IP-scoped buckets above are per-caller, which is the wrong shape for
    credential stuffing: a pool of hosts each stays under its own limit while
    the account under attack absorbs the sum of them.
    """

    def get_cache_key(self, request, view):
        email = str(request.data.get("email", "")).strip().lower()
        if not email:
            return None
        digest = hashlib.sha256(email.encode()).hexdigest()
        return self.cache_format % {"scope": self.scope, "ident": digest}


class RegistrationThrottle(IPRateThrottle):
    scope = "registration"


class SocialAuthThrottle(IPRateThrottle):
    scope = "social_auth"


class LoginThrottle(EmailOrIPRateThrottle):
    scope = "login"


class LoginEmailThrottle(EmailRateThrottle):
    scope = "login_email"


class TokenRefreshThrottle(IPRateThrottle):
    scope = "token_refresh"


class SensitiveActionThrottle(MultiUnitRateMixin, UserRateThrottle):
    """For authenticated endpoints that guess or change a credential."""

    scope = "sensitive_action"
