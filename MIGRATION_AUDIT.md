# Render to AWS migration: Phase 1 audit

Read-only audit of this repository. Branch `aws-migration`, cut from `feat/mining-review-feedback` at `102d6d4`. That commit is one commit ahead of `main` and only touches `mining/`, so nothing below changes if Phase 2 is based on `main`.

Every claim cites a file and line. Where the repo cannot answer a question (for example what is set in the Render dashboard), this document says so.

---

## Summary: what matters most

1. **The API does not use cookies.** The brief says frontends rely on httpOnly cookies scoped to `.beldium.com`. This repo never sets a cookie. Login returns `access` and `refresh` in the JSON body (`accounts/views.py:178-184`, `accounts/views.py:266-271`), refresh reads the token from the request body (`accounts/views.py:112`), and auth is `Authorization: Bearer` only (`core/settings.py:233`, `core/settings.py:275`). No `SESSION_COOKIE_DOMAIN`, `CSRF_COOKIE_DOMAIN`, `CORS_ALLOW_CREDENTIALS` or `set_cookie` appears anywhere. The only cookie settings are `SESSION_COOKIE_SECURE` and `CSRF_COOKIE_SECURE` (`core/settings.py:38-39`), which only affect Django admin. **Decision needed** (see end).
2. **Celery is configured but no worker runs.** `CELERY_TASK_ALWAYS_EAGER` defaults to true (`core/settings.py:197`) and the code says there is no worker (`accounts/services.py:73`). Email tasks run on a plain background thread in the web process (`accounts/services.py:10-35`). The hourly beat schedule (`core/settings.py:379-384`) therefore never runs.
3. **A missing `DATABASE_URL` silently falls back to SQLite** inside the container (`core/settings.py:101-126`). On Fargate this would boot a working-looking API on an empty, throwaway database.
4. **`DEBUG` defaults to true** (`core/settings.py:9`) and is not tied to `ENVIRONMENT`.
5. **Behind an AWS load balancer, rate limits will collapse to one bucket for everyone** unless `NUM_PROXIES` is set to 1 or more. Its default is 0 (`core/settings.py:247`), and with 0 the client IP is `REMOTE_ADDR` (`common/ip.py:22-23`), which will be the load balancer's address.
6. **The web start command also runs data-changing commands on every boot**: migrate, legacy user import, user deletion, and admin password reset (`Procfile:1`).
7. **No health endpoint exists.**
8. **Where uploads live in production today is unknown.** The code supports S3 (`core/settings.py:356-371`), but whether `AWS_STORAGE_BUCKET_NAME` is set on Render cannot be seen from the repo. If it is not, uploads are on Render's local disk.

---

## 1. Redis usage

Redis is used for two things in code: Celery broker/result backend, and the Django cache (in production only). It is not used for sessions or channels.

| Use | Evidence | Active in production today? |
|---|---|---|
| Celery broker | `CELERY_BROKER_URL = config("REDIS_URL", ...)` `core/settings.py:194` | Configured, but tasks run eagerly in-process (see below), so the broker is probably never used. Unclear what `CELERY_TASK_ALWAYS_EAGER` is set to on Render. |
| Celery result backend | `CELERY_RESULT_BACKEND = CELERY_BROKER_URL` `core/settings.py:195` | Same as above. |
| Django cache | `CACHE_URL` defaults to `REDIS_URL` when `ENVIRONMENT == "production"` `core/settings.py:207-209` | Yes, if `ENVIRONMENT=production` on Render. Holds DRF throttle counters (`core/settings.py:248-257`) and the platform stats cache (`organisations/views.py:364-377`). |
| Sessions | No `SESSION_ENGINE` set, so Django's default database sessions. Only used by `/admin/`. | Not Redis. |
| Channels / websockets | `channels` not installed (`requirements.txt`), `core/asgi.py` is plain Django ASGI. | Not used. |

A system check refuses to start in production if the cache is per-process (`common/checks.py:44-65`), so Redis is required for production.

### Celery app

- App defined in `core/celery.py:7-9`, loaded from `core/__init__.py:1`. Uses `autodiscover_tasks()`.

### Every Celery task

| Task name | File | Called from |
|---|---|---|
| `accounts.send_email_verification` | `accounts/tasks.py:58-83` | `accounts/services.py:102` |
| `accounts.send_password_reset_email` | `accounts/tasks.py:86-98` | via `enqueue_account_email` `accounts/services.py:111-118` |
| `accounts.send_email_change_email` | `accounts/tasks.py:101-113` | same |
| `accounts.send_welcome_email` | `accounts/tasks.py:116-128` | same |
| `accounts.send_organisation_invitation_email` | `accounts/tasks.py:131-145` | same |
| `accounts.send_phone_verification` | `accounts/tasks.py:151-171` | same. SMS sending is disabled; it only logs (`accounts/tasks.py:156-171`). |
| `logistics.tasks.check_expiring_credentials` | `logistics/tasks.py:5-7` | beat schedule only |

### Beat schedule

- `logistics-credential-expiry`, runs `logistics.tasks.check_expiring_credentials` every 3600 seconds (`core/settings.py:379-384`).
- No beat process is started anywhere (`Procfile:1` starts only gunicorn), so this has never run in production unless someone ran `python manage.py monitor_logistics` by hand (`logistics/management/commands/monitor_logistics.py`). Unclear whether that happens.

### How workers are started today

- They are not. `Procfile:1` has one `web:` line and no `worker:` or `beat:` line.
- `accounts/services.py:70-79` states there is no separate worker and `CELERY_TASK_ALWAYS_EAGER=True`. `.delay()` is called inside a daemon thread (`accounts/services.py:33-35`), so emails are sent from the web process without blocking the request.
- `README.md:272` tells you to start a worker and use SendGrid. That section is out of date: the code uses Resend (`common/email_backends.py`), and no `sendgrid` import exists in any `.py` file even though `django-sendgrid-v5` and `sendgrid` are in `requirements.txt:19,51`.

**Risk of the thread approach on ECS:** a daemon thread dies with the container. An email in flight during a deploy or scale-in is lost. The code already accepts this (`accounts/services.py:17-19`).

---

## 2. File storage

- Backend choice: `core/settings.py:356-376`.
  - If `AWS_STORAGE_BUCKET_NAME` is set: `storages.backends.s3.S3Storage` (django-storages, `requirements.txt:20`), private objects (`AWS_DEFAULT_ACL = None`, `AWS_QUERYSTRING_AUTH = True`, lines 366-367). Supports a custom endpoint for R2/B2 (lines 361-365).
  - Otherwise: `FileSystemStorage` under `MEDIA_ROOT = BASE_DIR / "media"` (`core/settings.py:155`).
- No field sets its own `storage=`, so every field uses the default storage above.
- Media is not routed publicly on purpose (`core/urls.py:23-26`). Files are streamed through authenticated views via `common/files.py:4-14`. That helper never redirects to S3, so the bucket needs no CORS rule. (`README.md:251` says it redirects to a signed URL; the code says otherwise.)
- Local `media/` currently holds folders for `compliance`, `logistics`, `marketplace`, `mining`, `processing` (developer machine only; `media/` is gitignored, `.gitignore:8`).
- **Unclear:** whether Render production has `AWS_STORAGE_BUCKET_NAME` set. If it does not, every uploaded document lives on the Render service disk, and it must be copied off before cutover. If Render has no persistent disk attached, files uploaded before the last deploy may already be gone. Only the Render dashboard can answer this.

### Every FileField / ImageField

There are no ImageFields. All use default storage.

| Model | Field | upload_to | Evidence |
|---|---|---|---|
| compliance.Personnel | cv | `compliance/personnel/cv/%Y/%m/` | `compliance/models.py:84` |
| compliance.Personnel | certificate | `compliance/personnel/certificates/%Y/%m/` | `compliance/models.py:85` |
| compliance.ComplianceDocument | file | `compliance/documents/%Y/%m/` | `compliance/models.py:99-100` |
| compliance.ConditionEvidence | file | `compliance/conditions/%Y/%m/` | `compliance/models.py:158` |
| export.ExportDocument | file | `export/evidence/%Y/%m/` | `export/models.py:204` |
| logistics.LogisticsDocument | file | `logistics/evidence/%Y/%m/` | `logistics/models.py:176` |
| marketplace.ProductDocument | file | `marketplace/evidence/%Y/%m/` | `marketplace/models.py:148` |
| mining.Evidence | file | `mining/evidence/%Y/%m/` | `mining/models.py:217-218` |
| mining.LicenceDoc | file | `mining/licences/%Y/%m/` | `mining/models.py:252-253` |
| mining.DocumentRecord | file | `mining/documents/%Y/%m/` | `mining/models.py:277-278` |
| mining.CorrectiveSubmission | file | `mining/corrective-actions/%Y/%m/` | `mining/models.py:352-353` |
| processing.ProcessingDocument | file | `processing/documents/%Y/%m/` | `processing/models.py:330-331` |
| processing.NonConformityEvidence | file | `processing/non-conformities/%Y/%m/` | `processing/models.py:456-457` |
| processing.ComplianceReport | file | `processing/reports/%Y/%m/` | `processing/models.py:702-703` |
| warehousing.WarehousingDocument | file | `warehousing/evidence/%Y/%m/` | `warehousing/models.py:295` |

The database stores the relative path (for example `compliance/documents/2026/09/x.pdf`). If files are copied to S3 with the same keys at the bucket root, old records resolve without changes.

---

## 3. Static files

- WhiteNoise middleware, directly after `SecurityMiddleware` (`core/settings.py:78`).
- Storage: `whitenoise.storage.CompressedStaticFilesStorage` in both branches (`core/settings.py:370`, `core/settings.py:375`). Not the manifest variant, so no hashed filenames.
- `STATIC_URL = "static/"`, `STATIC_ROOT = BASE_DIR / "staticfiles"` (`core/settings.py:152-153`).
- `collectstatic` runs at every web boot (`Procfile:1`), not at build time.
- Only admin, DRF and drf-spectacular use static files; the frontends are separate apps.

---

## 4. Settings and environment variables

All read through `python-decouple` in `core/settings.py`. No other file reads the environment (`os.environ` appears only in `setdefault("DJANGO_SETTINGS_MODULE")` calls in `manage.py:7`, `core/wsgi.py:5`, `core/asgi.py:5`, `core/celery.py:5`).

"Silent" means the app boots and runs with the default. "Loud" means boot or the request fails.

| Variable | Line | Default | If missing |
|---|---|---|---|
| `SECRET_KEY` | 8 | `unsafe-development-key-change-this-before-any-real-deployment-2026` | Loud only when `ENVIRONMENT=production`: check `beldium.E001` (`common/checks.py:15-41`). **Security.** See note A. |
| `DEBUG` | 9 | `true` | **Silent. Security.** Debug on in production: full tracebacks and settings on error pages. |
| `ENVIRONMENT` | 10 | `local` | **Silent. Security.** Turns off SSL redirect, secure cookies, HSTS and proxy SSL header (lines 35-42), downgrades the SECRET_KEY and cache checks to warnings, and makes the cache per-process. |
| `ALLOWED_HOSTS` | 11 | `localhost,127.0.0.1, api.beldium.com, compliance.beldium.com, miners.beldium.com` | Silent. Default includes localhost. **Security** (minor). Default does not include any AWS hostname, so ECS health checks by IP or ECS URL would get 400. |
| `RENDER_EXTERNAL_HOSTNAME` | 14 | empty | Silent. Render-only; appended to `ALLOWED_HOSTS` (lines 15-16). |
| `CORS_ALLOWED_ORIGINS` | 17 | three localhost origins plus `api`, `compliance`, `miners` `.beldium.com` | **Silent. Security.** Production would accept requests from `http://localhost:*` origins. |
| `COMPLIANCE_PORTAL_ORIGINS` | 24-28 | `https://compliance.beldium.com` plus localhost | Silent. Used by `accounts/portal.py` to decide which portal a login belongs to. Localhost counted as the compliance portal. |
| `MINER_PORTAL_ORIGINS` | 29-33 | `https://miners.beldium.com,http://localhost:5174` | Silent. Same as above. |
| `DATABASE_URL` | 101 | empty | **Silent. Critical.** Falls to `DB_ENGINE`, then to SQLite at `BASE_DIR/db.sqlite3` (line 126). |
| `DB_ENGINE` | 116 | `sqlite` | Silent (as above). |
| `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST` | 119-122 | none | Loud, but only if `DB_ENGINE=postgresql` and `DATABASE_URL` unset. |
| `DB_PORT` | 123 | `5432` | Silent. |
| `LEGACY_DATABASE_URL` | 131 | empty | Silent. When set, every boot reads `accounts_user` from the old DB (`Procfile:1`, `accounts/management/commands/import_legacy_users.py`). |
| `DELETE_TEST_USER_EMAILS` | 135 | empty | Silent. **When set, every boot deletes those users** (`accounts/management/commands/delete_test_users.py`). Must not be copied into AWS by accident. |
| `ADMIN_EMAIL`, `ADMIN_BOOTSTRAP_SECRET` | 139-140 | empty | Silent. **When set, every boot creates or promotes that user to superuser and resets its password** (`accounts/management/commands/bootstrap_admin.py:27-35`). |
| `EMAIL_HOST` | 165 | `smtp.resend.com` | Silent. Only used by the SMTP fallback. |
| `EMAIL_PORT` | 166 | `587` | Silent. |
| `EMAIL_HOST_USER` | 167 | `resend` | Silent. |
| `EMAIL_HOST_PASSWORD` | 168 | empty | **Silent. Security.** This is the Resend API key. When empty, `EMAIL_BACKEND` becomes the console backend (lines 175-182), so no email is sent and every OTP code is written to stdout, meaning to CloudWatch logs on AWS. |
| `EMAIL_USE_TLS` | 169 | `true` | Silent. |
| `EMAIL_TIMEOUT` | 174 | `10` | Silent. |
| `EMAIL_BACKEND` | 175 | Resend API backend if key set, else console | Silent. |
| `DEFAULT_FROM_EMAIL` | 183 | `noreply@beldium.com` | Silent. |
| `FRONTEND_URL` | 184 | `http://localhost:8080` | Silent. Emails would link to localhost (`accounts/tasks.py:75,127,143`). |
| `TERMII_API_KEY` | 188 | empty | Silent. SMS is disabled in code anyway (`accounts/tasks.py:156-171`). |
| `TERMII_SENDER_ID` | 189 | `N-Alert` | Silent. |
| `GOOGLE_OAUTH_CLIENT_ID` | 190 | empty | Returns 503 at request time (`accounts/social.py:7-8`). Social login routes are disabled (`accounts/urls.py:36-39`). |
| `MICROSOFT_OAUTH_CLIENT_ID` | 191 | empty | Same (`accounts/social.py:21-22`). |
| `MICROSOFT_OAUTH_TENANT_ID` | 192 | `common` | Silent. |
| `REDIS_URL` | 194 | `redis://localhost:6379/0` | Loud at request time in production (throttled endpoints error when the cache cannot connect). Silent at boot. |
| `CELERY_TASK_ALWAYS_EAGER` | 197 | `true` | Silent. |
| `CELERY_TASK_EAGER_PROPAGATES` | 200 | `false` | Silent. |
| `CACHE_URL` | 207 | `REDIS_URL` if production, else empty | Loud in production if it ends up empty (`common/checks.py:44-65`, via `migrate`, see note A). |
| `NUM_PROXIES` | 247 | `0` | **Silent. Security.** See summary item 5. Must be set for the AWS load balancer. The check at `common/checks.py:68-84` only fails if it is not an int, so 0 passes. |
| `AWS_STORAGE_BUCKET_NAME` | 356 | empty | Silent. Falls back to local disk. On Fargate that disk is lost on every deploy. |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | 357-358 | empty string | Silent. On ECS we want the task role instead of keys. Whether django-storages treats an empty string the same as "not set" needs to be checked in Phase 2; unclear from this repo. |
| `AWS_S3_REGION_NAME` | 360 | `us-east-1` | Silent. |
| `AWS_S3_ENDPOINT_URL` | 361 | `None` | Silent. Leave unset for real S3. |

**Not configured at all (relevant to the brief):**

- `CSRF_TRUSTED_ORIGINS`: not set anywhere. Only affects `/admin/` form posts, since the API uses bearer tokens and DRF skips CSRF for JWT auth.
- `SESSION_COOKIE_DOMAIN`, `CSRF_COOKIE_DOMAIN`, `SESSION_COOKIE_HTTPONLY` overrides, `CORS_ALLOW_CREDENTIALS`: not set.
- `SECURE_PROXY_SSL_HEADER` is set only in production (`core/settings.py:36`). ALB sends `X-Forwarded-Proto`, so this works on AWS too.

**Note A: when checks actually run.** The custom checks in `common/checks.py` run on `manage.py` commands, not inside gunicorn. Today they fire because `migrate` runs in the same start command before gunicorn (`Procfile:1`). If Phase 2 moves `migrate` into a separate one-off task, the web container would no longer run those checks at boot. Phase 2 should run `manage.py check --deploy` (or equivalent) at container start.

**`.env.example` is gitignored** (`.gitignore:12`), although `README.md:251` refers to it. Phase 2 will need to remove that ignore line to commit the new one.

---

## 5. Render-specific coupling

| Item | Evidence |
|---|---|
| `render.yaml` | Not present in the repo. Service config lives in the Render dashboard, which I cannot see. |
| Build command | Not in the repo. Unclear; probably `pip install -r requirements.txt` set in the dashboard. |
| Start command | `Procfile:1`: `collectstatic && migrate && import_legacy_users && delete_test_users && bootstrap_admin && gunicorn core.wsgi:application --timeout 90`. Render does not read Procfiles on its own, so whether the dashboard start command matches this file is unclear. |
| gunicorn | No worker count or bind address set (`Procfile:1`), so gunicorn defaults to 1 worker and relies on Render's `PORT` env var. Unclear if Render sets `WEB_CONCURRENCY`. |
| Render-only env var | `RENDER_EXTERNAL_HOSTNAME` (`core/settings.py:14-16`). |
| Hardcoded Render hostnames | None. No `onrender.com` string in any tracked file. |
| Render-shaped comments | `core/settings.py:12-13`, `103-104`, `159-164`, `216-219`; `accounts/management/commands/bootstrap_admin.py:10`; `common/email_backends.py:14-18`. Comments only, no behavior. |
| Python version | Not pinned (no `runtime.txt`, `.python-version`, or `pyproject.toml`). Django 6.0.1 (`requirements.txt:16`) needs Python 3.12 or newer. Unclear what version Render runs. |

---

## 6. External services and outbound calls

| Service | Purpose | Evidence | Outbound IP concerns |
|---|---|---|---|
| Resend HTTPS API (`api.resend.com`) | All email | `common/email_backends.py:8,52-67`; selected at `core/settings.py:175-182` | Resend does not require IP allowlisting by default. Unclear if the Beldium Resend account has any IP restriction on its API key. Sending domain DNS (SPF/DKIM) stays at Truehost and does not depend on our IP. |
| Termii (`api.ng.termii.com`) | SMS OTP | `accounts/tasks.py:148` | Currently disabled (code returns before sending, `accounts/tasks.py:171`). No outbound call today. |
| Google OAuth token verification | Social login | `accounts/social.py:12` | Routes disabled (`accounts/urls.py:36-39`). No IP allowlist. |
| Microsoft JWKS (`login.microsoftonline.com`) | Social login | `accounts/social.py:32-34` | Same as Google. |
| Old Render Postgres | `import_legacy_users` on every boot when `LEGACY_DATABASE_URL` is set | `accounts/management/commands/import_legacy_users.py:33-39` | **Yes.** Render Postgres external connections are controlled by an IP allowlist in the Render dashboard. If AWS tasks need to reach it during cutover, the AWS NAT gateway's IP must be added. Unclear what the allowlist is today. |
| S3 or S3-compatible storage | Uploads, if configured | `core/settings.py:356-371` | Only if the bucket has a policy tied to Render IPs. Unclear. |
| Payment provider | None called. Marketplace has a webhook receiver (`marketplace/views.py:289-312`) that requires a logged-in user (`IsAuthenticated`, line 293) and checks no signature. No payment provider is called outbound. | | None today. |
| Maps / geocoding | None found. | | None. |

---

## 7. Database

### Migrations (31)

```
accounts/0001_initial
accounts/0002_emailverificationcode
accounts/0003_user_country_user_terms_accepted_at_and_more
accounts/0004_user_onboarding_role
accounts/0005_user_phone_verified_at_phoneverificationcode
accounts/0006_socialidentity
accounts/0007_user_portal
compliance/0001_initial
compliance/0002_complianceapplication_created_by_and_more
compliance/0003_complianceapplication_reference          (RunPython backfill, line 40)
ecosystem/0001_initial
ecosystem/0002_initial
export/0001_initial
export/0002_shipment_decision_at_shipment_decision_by_and_more
finance/0001_initial
logistics/0001_initial
logistics/0002_alter_driver_options_and_more
logistics/0003_compliancefinding_movement_logisticstransaction_and_more
marketplace/0001_initial
mining/0001_initial
mining/0002_inventoryitem_productionrecord
organisations/0001_initial
organisations/0002_organisation_beldium_id_and_more
organisations/0003_joinrequest_employee_id_joinrequest_job_title
processing/0001_initial
processing/0002_processingdocument_review_note_and_more
processing/0003_alter_compliancereport_options_compliancereport_kind_and_more
quality/0001_initial
warehousing/0001_initial
warehousing/0002_inspector_facility_bay_count_facility_built_area_and_more
warehousing/0003_inventorylot_actual_weighbridge_quantity_and_more
```

Plus Django built-ins: `admin`, `auth`, `contenttypes`, `sessions`, and `token_blacklist` from simplejwt (`core/settings.py:54`).

### Primary keys

Every project model uses a UUID primary key: `common/models.py:7` (`TimeStampedModel`) and `accounts/models.py:39` (`User`). UUIDs from two databases will not collide. Integer keys exist only in Django/simplejwt tables: `auth_group`, `auth_permission`, `django_content_type`, `django_admin_log`, `token_blacklist_outstandingtoken`, `token_blacklist_blacklistedtoken`, `django_session`. None of these need merging, except possibly admin logs.

### Models holding customer data, with natural keys

| Area | Model | Natural key candidates | Evidence |
|---|---|---|---|
| Users | accounts.User | `email` (unique), `phone_number` | `accounts/models.py:27-55` |
| Users | accounts.SocialIdentity | (`provider`, `subject`) unique | `accounts/models.py:140-150` |
| Users | accounts.AccountAuditEvent | none; child of User | `accounts/models.py:123` |
| Companies | organisations.Organisation | `beldium_id` (unique), (`country`, `registration_number`) unique when not blank, `email`, `phone_number`, name | `organisations/models.py:23-62` |
| Companies | organisations.OrganisationMembership | (`organisation`, `user`) unique | `organisations/models.py:89-99` |
| Companies | organisations.OrganisationInvitation, JoinRequest | `token`; constraint at 139 | `organisations/models.py:105-139` |
| Compliance | compliance.ComplianceApplication | `reference` (unique) | `compliance/models.py:45-60` |
| Compliance | compliance.Personnel | `registration_number` (not unique) | `compliance/models.py:76-85` |
| Documents | compliance.ComplianceDocument | (`application`, `document_type`) unique | `compliance/models.py:88-111` |
| Compliance | compliance.ApplicationMessage, ApprovalCondition, ConditionEvidence | none; children | `compliance/models.py:118-158` |
| Miners | mining.MiningOrganisationProfile, MineSite (`code` unique; `organisation`+`name` unique), Application (`reference`), Evidence, LicenceDoc, DocumentRecord, NonConformity, Inspection, Sample, ProductionRecord, InventoryItem, etc. | as listed | `mining/models.py:88-608` |
| Processors | processing.Processor (`reference`, `rc_number`), ProcessingApplication (`reference`, `rc_number`, `contact_email`), Facility (`processor`+`name`), documents and reports | as listed | `processing/models.py:134-703` |
| Buyers | export.Buyer (`contact_email`, not unique) | | `export/models.py:87-92` |
| Exporters | export.Exporter (`reference`, `contact_email`, `contact_phone`) | | `export/models.py:46-51` |
| Logistics | logistics.LogisticsCompany (`reference`, `contact_email`), Vehicle (`registration`, `vin` unique), Driver (`licence_number` unique), plus transport records | | `logistics/models.py:44-457` |
| Warehousing | warehousing.WarehouseOperator (`reference`, `contact_email`), Inspector (`email`) | | `warehousing/models.py:46-222` |
| Marketplace | marketplace.SellerProfile (`reference`, `contact_email`), MarketplaceOrder (`reference`) | | `marketplace/models.py:84-195` |
| Finance | finance.Invoice, Payment (`reference` unique) | | `finance/models.py:61-115` |
| Ecosystem | ecosystem.Rfq, Transaction, MaterialBatch, LogisticsMove (`reference` unique) | | `ecosystem/models.py:91-286` |
| Quality | quality.QualityApplication, Sample, Certificate, QualityNonConformity (`reference` unique) | | `quality/models.py:93-183` |

### Raw SQL and Postgres-specific features

- Raw SQL: only one query, against the **old** database, in `accounts/management/commands/import_legacy_users.py:42-47`. It reads columns `account_verified` and `account_verified_at`, which do not exist on the v2 User model. That means **the old database was produced by a different codebase with a different schema**, not an older version of this one. Phase 3 schema comparison will show large differences. Also, it inserts old user IDs as-is (`id=data["id"]`, line 68), so old user IDs must be UUIDs for that to have worked. Unclear whether the old DB uses UUIDs for other tables.
- `RunPython` data migration: `compliance/migrations/0003_complianceapplication_reference.py:40`.
- `JSONField` (stored as `jsonb` on Postgres) used widely, for example `compliance/models.py:54-60`, `accounts/models.py:134`, `export/models.py:54-55`.
- Partial unique constraints (`condition=`): `organisations/models.py:60-62`, `export/models.py:217`, `logistics/models.py:189`, `marketplace/models.py:162`, `warehousing/models.py:308`.
- `select_for_update()` used in many views and `logistics/services.py:107-112`. Needs a real row-locking database; fine on RDS.
- No `django.contrib.postgres`, no ArrayField, no full-text search, no `.raw()` or `RawSQL`.
- Driver: `psycopg` 3 (`requirements.txt:35-36`). Postgres 18 on Render; RDS supports Postgres 18, but confirm the exact minor version is available in the target region when creating the instance.

---

## 8. Health check

- There is no health endpoint.
- Public endpoints that exist: `/api/v1/platform-stats/` (`organisations/urls.py:12`, `organisations/views.py:359-377`). It is `AllowAny` but caches its DB result for 5 minutes, so it does not prove the DB is up, and it would fail if Redis is down.
- `/admin/login/` returns 200 without auth but does not touch the DB on GET, and it goes through `SECURE_SSL_REDIRECT` (`core/settings.py:37`), so an HTTP health check from the load balancer would get a 301.
- Phase 2 must add `/health/`, exempt it from the SSL redirect (`SECURE_REDIRECT_EXEMPT`), and make sure the host the load balancer uses is in `ALLOWED_HOSTS`.

---

## 9. Migrations on deploy

- Run in the web start command on every boot, before gunicorn (`Procfile:1`).
- If more than one web instance starts at once, each runs `migrate` concurrently. Nothing prevents that today.
- The same line also runs, on every boot: `import_legacy_users`, `delete_test_users`, `bootstrap_admin`. Each is a no-op when its env var is unset (see section 4). Phase 2 should move all of these out of the web start command.

---

## Other things found along the way

- `logistics.services.monitor_expiries` is wrapped in `@transaction.atomic` (`logistics/services.py:107`), so running it from a Celery beat task will work.
- The Resend backend sends one HTTPS request per message with `EMAIL_TIMEOUT` (default 10s) (`common/email_backends.py:66`).
- `db.sqlite3` (4 MB) sits in the working directory. It is gitignored (`.gitignore:7`) and must stay out of the Docker image.

---

## Decisions I need from you before Phase 2

1. **Cookies.** The API returns JWTs in the JSON body and reads them from the `Authorization` header. It sets no cookies. Do you want Phase 2 to (a) keep bearer tokens as they are and only keep `.beldium.com` scoping for admin cookies, or (b) move auth to httpOnly cookies scoped to `.beldium.com`? Option (b) is a feature change with frontend work, CSRF handling and `CORS_ALLOW_CREDENTIALS`. It should not be bundled into the infra migration. I recommend (a). Or, if cookies are set somewhere else (a frontend server or proxy), tell me where.
2. **Celery worker.** Today no worker runs. Should Phase 2 (a) keep eager mode plus background threads and add only a beat service for the hourly logistics job, or (b) turn eager mode off and run a real worker and beat as separate ECS services? I recommend (b): it survives deploys and gets the hourly job running for the first time. Note the hourly job has never run in production, so its first run may create many expiry events and scope restrictions at once.
3. **Current upload location.** Please check the Render dashboard: is `AWS_STORAGE_BUCKET_NAME` set on `api-beldium-backend`, and if so, which provider (S3, R2, B2)? Is a persistent disk attached? This decides whether we copy files from a bucket or from the Render disk.
4. **Boot-time commands.** Can `import_legacy_users`, `delete_test_users`, and `bootstrap_admin` be dropped from the AWS start command? I propose keeping them as commands you can run as one-off ECS tasks, but never at web start. Phase 3 replaces `import_legacy_users` for the cutover.
5. **Proxy count.** On ECS Express Mode, the ALB is one proxy hop, so `NUM_PROXIES=1`. Is anything in front of the ALB (Cloudflare, CloudFront)? If yes, it becomes 2. Truehost DNS alone adds no hop.
6. **Python version.** Render's version is not pinned in the repo. OK to use Python 3.12 slim for the image? (Django 6.0 needs 3.12+.)
7. **Base branch.** Phase 2 continues on `aws-migration`, which is cut from `feat/mining-review-feedback`. Should I rebase it onto `main` instead?
8. **AWS region.** Render is in Virginia, so I assume `us-east-1`. Confirm.
