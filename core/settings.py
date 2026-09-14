from datetime import timedelta
from pathlib import Path

from decouple import config

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config("SECRET_KEY", default="unsafe-development-key-change-this-before-any-real-deployment-2026")
DEBUG = str(config("DEBUG", default="true")).strip().lower() in {"1", "true", "yes", "on", "debug", "development"}
ENVIRONMENT = str(config("ENVIRONMENT", default="local")).strip().lower()
ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="localhost,127.0.0.1", cast=lambda value: [x.strip() for x in value.split(",") if x.strip()])
CORS_ALLOWED_ORIGINS = config("CORS_ALLOWED_ORIGINS", default="http://localhost:8080,http://localhost:3000,http://localhost:5173", cast=lambda value: [x.strip() for x in value.split(",") if x.strip()])

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
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
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

if config("DB_ENGINE", default="sqlite") == "postgresql":
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
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"

SENDGRID_API_KEY = config("SENDGRID_API_KEY", default="")
EMAIL_BACKEND = config(
    "EMAIL_BACKEND",
    default=(
        "sendgrid_backend.SendgridBackend"
        if ENVIRONMENT == "production" and SENDGRID_API_KEY
        else "django.core.mail.backends.console.EmailBackend"
    ),
)
DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL", default="noreply@beldium.com")
FRONTEND_URL = config("FRONTEND_URL", default="http://localhost:8080")
SMS_WEBHOOK_URL = config("SMS_WEBHOOK_URL", default="")
SMS_WEBHOOK_TOKEN = config("SMS_WEBHOOK_TOKEN", default="")
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
    AWS_DEFAULT_ACL = None
    AWS_QUERYSTRING_AUTH = True
    STORAGES = {
        "default": {"BACKEND": "storages.backends.s3.S3Storage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }

# Run Celery beat alongside the worker for continuous logistics monitoring.
CELERY_BEAT_SCHEDULE = {
    "logistics-credential-expiry": {
        "task": "logistics.tasks.check_expiring_credentials",
        "schedule": 3600.0,
    },
}
