from datetime import timedelta
from pathlib import Path

from decouple import config

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config("SECRET_KEY", default="unsafe-development-key-change-this-before-any-real-deployment-2026")
DEBUG = str(config("DEBUG", default="true")).strip().lower() in {"1", "true", "yes", "on", "debug", "development"}
ENVIRONMENT = str(config("ENVIRONMENT", default="local")).strip().lower()
ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="localhost,127.0.0.1, api.beldium.com, compliance.beldium.com, miners.beldium.com", cast=lambda value: [x.strip() for x in value.split(",") if x.strip()])
# Render's own routing hits the app on its *.onrender.com hostname before any
# custom domain is attached to the request, so that host needs to be allowed too.
RENDER_EXTERNAL_HOSTNAME = config("RENDER_EXTERNAL_HOSTNAME", default="")
if RENDER_EXTERNAL_HOSTNAME:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)
CORS_ALLOWED_ORIGINS = config("CORS_ALLOWED_ORIGINS", default="http://localhost:8080,http://localhost:3000,http://localhost:5173, https://api.beldium.com, https://compliance.beldium.com, https://miners.beldium.com", cast=lambda value: [x.strip() for x in value.split(",") if x.strip()])

# Which frontend origin maps to which account portal (accounts.models.User.Portal).
# The frontends live on separate origins, so the browser's own Origin header
# — which JavaScript cannot forge or omit on a cross-origin request — is
# enough to tell them apart without either app declaring anything itself.
# See accounts/portal.py:portal_for_origin.
COMPLIANCE_PORTAL_ORIGINS = config(
    "COMPLIANCE_PORTAL_ORIGINS",
    default="https://compliance.beldium.com,http://localhost:8080,http://localhost:3000,http://localhost:5173",
    cast=lambda value: [x.strip() for x in value.split(",") if x.strip()],
)
MINER_PORTAL_ORIGINS = config(
    "MINER_PORTAL_ORIGINS",
    default="https://miners.beldium.com,http://localhost:5174",
    cast=lambda value: [x.strip() for x in value.split(",") if x.strip()],
)

if ENVIRONMENT == "production":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31_536_000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "django_filters",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "drf_spectacular",
    "common",
    "accounts",
    "organisations",
    "compliance",
    "processing",
    "logistics",
    "export",
    "warehousing",
    "mining",
    "marketplace",
    "quality",
    "finance",
    "ecosystem",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    # Gunicorn doesn't serve static files itself, and nothing else was — every
    # /static/admin/* request 404'd, leaving the admin (and drf-spectacular's
    # docs UI) completely unstyled. WhiteNoise serves them directly from this
    # same process; must sit right after SecurityMiddleware per its own docs.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "core.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
WSGI_APPLICATION = "core.wsgi.application"
ASGI_APPLICATION = "core.asgi.application"

DATABASE_URL = config("DATABASE_URL", default="")
if DATABASE_URL:
    # A single connection string (as Render's managed Postgres provides) beats
    # keeping DB_HOST/DB_USER/... in sync with it by hand.
    from urllib.parse import urlparse

    _db_url = urlparse(DATABASE_URL)
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": _db_url.path.lstrip("/"),
        "USER": _db_url.username,
        "PASSWORD": _db_url.password,
        "HOST": _db_url.hostname,
        "PORT": _db_url.port or 5432,
    }}
elif config("DB_ENGINE", default="sqlite") == "postgresql":
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": config("DB_NAME"),
        "USER": config("DB_USER"),
        "PASSWORD": config("DB_PASSWORD"),
        "HOST": config("DB_HOST"),
        "PORT": config("DB_PORT", default="5432"),
    }}
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}

# Old backend's database, read-only source for the one-time `import_legacy_users`
# credential carry-over (see accounts/management/commands/import_legacy_users.py).
# Unset once the import has run if you don't want it re-checked on every boot.
LEGACY_DATABASE_URL = config("LEGACY_DATABASE_URL", default="")

# Comma-separated emails to delete on next boot (see
# accounts/management/commands/delete_test_users.py). Unset once done.
DELETE_TEST_USER_EMAILS = config("DELETE_TEST_USER_EMAILS", default="")

# Bootstraps an admin login for /admin/ (see accounts/management/commands/
# bootstrap_admin.py) — the only way to get one without Render shell access.
ADMIN_EMAIL = config("ADMIN_EMAIL", default="")
ADMIN_BOOTSTRAP_SECRET = config("ADMIN_BOOTSTRAP_SECRET", default="")

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"

# Default backend is Resend's HTTPS API (common.email_backends), not its SMTP
# relay: Render's outbound SMTP to smtp.resend.com repeatedly hung or timed
# out in production well past EMAIL_TIMEOUT, while HTTPS (port 443) doesn't
# have this problem. These EMAIL_HOST* settings stay for EMAIL_HOST_PASSWORD
# (reused as the Resend API key) and as a documented fallback if you ever
# explicitly set EMAIL_BACKEND back to the SMTP one.
EMAIL_HOST = config("EMAIL_HOST", default="smtp.resend.com")
EMAIL_PORT = config("EMAIL_PORT", default=587, cast=int)
EMAIL_HOST_USER = config("EMAIL_HOST_USER", default="resend")
EMAIL_HOST_PASSWORD = config("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = str(config("EMAIL_USE_TLS", default="true")).strip().lower() in {"1", "true", "yes", "on"}
# Without a timeout a blocked/slow SMTP connection hangs forever. Registration
# currently sends this email synchronously (eager Celery, no separate worker),
# so an unbounded hang here blocks the whole request until gunicorn kills the
# worker outright (a 500 with no logged exception). Fail fast instead.
EMAIL_TIMEOUT = config("EMAIL_TIMEOUT", default=10, cast=int)
EMAIL_BACKEND = config(
    "EMAIL_BACKEND",
    default=(
        "common.email_backends.ResendAPIEmailBackend"
        if EMAIL_HOST_PASSWORD
        else "django.core.mail.backends.console.EmailBackend"
    ),
)
DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL", default="noreply@beldium.com")
FRONTEND_URL = config("FRONTEND_URL", default="http://localhost:8080")
# Phone OTPs send through Termii (see accounts/tasks.py:send_phone_verification).
# TERMII_SENDER_ID defaults to Termii's shared "N-Alert" ID, which works
# out of the box in Nigeria without registering a custom sender ID first.
TERMII_API_KEY = config("TERMII_API_KEY", default="")
TERMII_SENDER_ID = config("TERMII_SENDER_ID", default="N-Alert")
GOOGLE_OAUTH_CLIENT_ID = config("GOOGLE_OAUTH_CLIENT_ID", default="")
MICROSOFT_OAUTH_CLIENT_ID = config("MICROSOFT_OAUTH_CLIENT_ID", default="")
MICROSOFT_OAUTH_TENANT_ID = config("MICROSOFT_OAUTH_TENANT_ID", default="common")

CELERY_BROKER_URL = config("REDIS_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_TASK_ALWAYS_EAGER = str(config("CELERY_TASK_ALWAYS_EAGER", default="true")).strip().lower() in {
    "1", "true", "yes", "on"
}
CELERY_TASK_EAGER_PROPAGATES = str(
    config("CELERY_TASK_EAGER_PROPAGATES", default="false")
).strip().lower() in {"1", "true", "yes", "on"}

# Throttle counters live in the cache, so a per-process cache means each gunicorn
# worker enforces its own private allowance and every limit below is effectively
# multiplied by the worker count. Anything that rate-limits needs a shared cache.
CACHE_URL = config("CACHE_URL", default=CELERY_BROKER_URL if ENVIRONMENT == "production" else "")
if CACHE_URL:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": CACHE_URL}}
else:
    CACHES = {"default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "beldium-local",
    }}

# Without this, app-level logger.info/logger.exception calls (accounts.tasks,
# accounts.services) never reach Render's log stream: Python's logging module
# falls back to a WARNING-only "last resort" handler when nothing is configured,
# so OTP-send failures were being logged into the void.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {"class": "logging.StreamHandler"},
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework_simplejwt.authentication.JWTAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": "common.pagination.DefaultPagination",
    "EXCEPTION_HANDLER": "common.exception_handler.custom_exception_handler",
    # How many proxies run in front of this service. Every throttle and every
    # audit record derives the caller's address from this: leave it at 0 when
    # nothing proxies us, and set it to the real hop count behind a load
    # balancer. It must never be unset, or X-Forwarded-For is taken on trust.
    "NUM_PROXIES": config("NUM_PROXIES", default=0, cast=int),
    "DEFAULT_THROTTLE_RATES": {
        "verification_issue": "5/10m",
        "verification_attempt": "10/10m",
        "registration": "10/1h",
        "social_auth": "20/10m",
        "login": "10/15m",
        "login_email": "20/1h",
        "token_refresh": "120/1h",
        "sensitive_action": "10/1h",
    },
    "PAGE_SIZE": 20,
}
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
}
SPECTACULAR_SETTINGS = {
    "COMPONENT_SPLIT_REQUEST": True,
    # Several apps model a "status" or a "section" of their own. Without these
    # the generator invents names like "Status80bEnum" for each collision, and
    # a generated client ends up with unreadable, unstable type names.
    "ENUM_NAME_OVERRIDES": {
        "ProcessingApplicationStage": "processing.models.ApplicationStage.choices",
        "ProcessingApplicationDecision": "processing.models.ApplicationDecision.choices",
        "ProcessingProcessorStatus": "processing.models.ProcessorStatus.choices",
        "ProcessingSectionKey": "processing.models.SectionKey.choices",
        "ProcessingReviewState": "processing.models.ReviewState.choices",
        "ProcessingType": "processing.models.ProcessingType.choices",
        "OrganisationMembershipRole": "organisations.models.MembershipRole.choices",
        "ComplianceDocumentStatus": [
            ("requested", "Requested"),
            ("submitted", "Submitted"),
            ("verified", "Verified"),
            ("rejected", "Rejected"),
        ],
        "ComplianceConditionStatus": [
            ("pending", "Pending evidence"),
            ("submitted", "Evidence submitted"),
            ("rejected", "Evidence rejected"),
            ("cleared", "Cleared"),
        ],
        "EvidenceReviewStatus": [
            ("verified", "verified"),
            ("rejected", "rejected"),
        ],
        "LogisticsApplicationStatus": "logistics.models.ApplicationStatus.choices",
        "LogisticsDomain": "logistics.models.Domain.choices",
        "ExportApplicationStatus": "export.models.ApplicationStatus.choices",
        "ExportDomain": "export.models.Domain.choices",
        "WarehousingApplicationStatus": "warehousing.models.ApplicationStatus.choices",
        "WarehousingDomain": "warehousing.models.Domain.choices",
        "LogisticsDomainReviewStatus": [
            ("pending", "Pending"),
            ("passed", "Passed"),
            ("attention", "Attention"),
            ("failed", "Failed"),
        ],
        "EvidenceStatus": [
            ("pending", "Pending review"),
            ("verified", "Verified"),
            ("rejected", "Rejected"),
        ],
        "LogisticsInformationRequestStatus": [
            ("open", "Open"),
            ("responded", "Responded"),
            ("accepted", "Accepted"),
        ],
        "LogisticsReviewDecisionStatus": [
            ("approved", "Approved"),
            ("conditionally_approved", "Conditionally approved"),
            ("rejected", "Rejected"),
        ],
        "MarketplaceSellerStatus": "marketplace.models.SellerStatus.choices",
        "MarketplaceListingStatus": "marketplace.models.ListingStatus.choices",
        "MarketplaceProductCategory": "marketplace.models.ProductCategory.choices",
        "MarketplaceOrderStatus": "marketplace.models.OrderStatus.choices",
        "MarketplacePaymentStatus": "marketplace.models.PaymentStatus.choices",
    },
    "TITLE": "Beldium Mining Compliance API",
    "DESCRIPTION": "API for mining organisations, compliance partners, and regulators.",
    "VERSION": "1.0.0",
}

# Compliance uploads use Django's storage API. Local development uses the
# filesystem; production can switch to any S3-compatible provider without
# changing application code.
if config("AWS_STORAGE_BUCKET_NAME", default=""):
    AWS_ACCESS_KEY_ID = config("AWS_ACCESS_KEY_ID", default="")
    AWS_SECRET_ACCESS_KEY = config("AWS_SECRET_ACCESS_KEY", default="")
    AWS_STORAGE_BUCKET_NAME = config("AWS_STORAGE_BUCKET_NAME")
    AWS_S3_REGION_NAME = config("AWS_S3_REGION_NAME", default="us-east-1")
    AWS_S3_ENDPOINT_URL = config("AWS_S3_ENDPOINT_URL", default=None)
    if AWS_S3_ENDPOINT_URL and not AWS_S3_ENDPOINT_URL.startswith(("http://", "https://")):
        # botocore rejects a bare hostname outright; a scheme-less endpoint
        # is always meant to be https, never http, so this is safe to assume.
        AWS_S3_ENDPOINT_URL = f"https://{AWS_S3_ENDPOINT_URL}"
    AWS_DEFAULT_ACL = None
    AWS_QUERYSTRING_AUTH = True
    STORAGES = {
        "default": {"BACKEND": "storages.backends.s3.S3Storage"},
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
    }
else:
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
    }

# Run Celery beat alongside the worker for continuous logistics monitoring.
CELERY_BEAT_SCHEDULE = {
    "logistics-credential-expiry": {
        "task": "logistics.tasks.check_expiring_credentials",
        "schedule": 3600.0,
    },
}
