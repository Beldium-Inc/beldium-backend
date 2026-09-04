# Beldium Mining Compliance API

Backend API for the Beldium mining compliance platform. The project is a modular Django REST Framework application designed for mining companies, compliance partners, inspection bodies, laboratories, and regulators.

## Current implementation

The first domain slice includes:

- Email/password registration and JWT authentication
- Email verification with hashed, expiring, single-use OTPs
- Asynchronous verification email delivery through Celery and SendGrid
- Current-user profile endpoint
- Multi-organisation accounts
- Mining company, compliance partner, regulator, laboratory, and inspection-body organisation types
- Owner, administrator, reviewer, inspector, and member roles
- Private organisation membership boundaries
- Organisation invitations and invitation acceptance
- Join requests with transactional approval or rejection
- OpenAPI schema and Swagger UI

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python manage.py migrate
python manage.py runserver
```

SQLite is used by default for local development. Set `DB_ENGINE=postgresql` and the `DB_*` variables for PostgreSQL.

## API entry points

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/api/v1/auth/register/` | Register a user |
| POST | `/api/v1/auth/verify-email/` | Verify a six-digit signup code and receive JWTs |
| POST | `/api/v1/auth/resend-verification/` | Request another verification code |
| POST | `/api/v1/auth/token/` | Obtain JWT access and refresh tokens |
| POST | `/api/v1/auth/token/refresh/` | Refresh an access token |
| GET/PATCH | `/api/v1/auth/me/` | Read or update the current user |
| GET/POST | `/api/v1/organisations/` | List memberships or create an organisation |
| GET | `/api/v1/organisations/directory/` | Discover verified organisations |
| GET | `/api/v1/organisations/{id}/members/` | List organisation members |
| GET/POST | `/api/v1/organisations/{id}/invitations/` | Manage invitations |
| POST | `/api/v1/invitations/accept/` | Accept an invitation |
| GET/POST | `/api/v1/join-requests/` | Manage the current user's join requests |
| POST | `/api/v1/join-requests/{id}/decide/` | Approve or reject a request as an organisation administrator |
| GET | `/api/docs/` | Swagger UI |

Registration, verification, resend, and token issuance are public endpoints. All other endpoints require a bearer access token.

New accounts must verify their email before using the token endpoint. Registration sends a six-digit code that expires after 10 minutes. A code is single-use, is invalidated after five failed attempts, and requesting another code invalidates earlier codes. Resend requests are limited to five per email/IP pair every 10 minutes.

User and verification-code creation run in one database transaction. If either database write fails, neither record is retained. Email dispatch starts only after that transaction commits and provider failures are logged without reversing a successfully created account.

For local development, `EMAIL_BACKEND` defaults to Django's console backend, so verification emails and codes are printed in the API terminal. In a deployed environment, set `EMAIL_BACKEND=sendgrid_backend.SendgridBackend`, configure a valid `SENDGRID_API_KEY`, set `CELERY_TASK_ALWAYS_EAGER=False`, and start a worker:

```bash
celery -A core worker --loglevel=info
```

## Error response contract

Every API error uses the same JSON envelope:

```json
{
  "status": "failed",
  "message": "email: Enter a valid email address.",
  "error": {
    "code": "validation_error",
    "details": {
      "email": ["Enter a valid email address."]
    }
  }
}
```

`message` is the primary human-readable error and is safe to display to a user. `error.code` is stable and intended for frontend decisions. `error.details` contains the complete field-level structure for validation failures and is `null` when there are no additional details.

Standard codes include `validation_error`, `not_authenticated`, `authentication_failed`, `permission_denied`, `not_found`, `conflict`, `method_not_allowed`, `unsupported_media_type`, `throttled`, and `internal_server_error`. Domain workflows can provide more specific codes such as `join_request_already_decided`.

## Validation

```bash
python manage.py check
python manage.py test
```

## Planned domain sequence

1. Mining sites, ownership, licences, and GPS boundaries
2. Compliance applications and workflow history
3. Evidence documents and information requests
4. Reviews, configurable scoring, and risk assessments
5. Inspections, findings, non-conformities, and corrective actions
6. Production, inventory, sampling, and laboratory results
7. Notifications, audit trail, dashboard aggregates, and reports
