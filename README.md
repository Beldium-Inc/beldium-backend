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
- Password reset, password change, verified email change, and JWT logout
- Organisation member role changes, suspension, activation, and removal
- Organisation submission and platform verification decisions
- Role capability responses and account/organisation audit events
- OpenAPI schema and Swagger UI
- Phone OTP verification and Google/Microsoft sign-in
- Full compliance onboarding sections, personnel, declarations, and a 15-item document checklist
- S3-compatible uploads, requested-document review, applicant messaging, and dashboard aggregates

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

## Connected frontend

`../beldium-new-frontend` talks to this API. Its dev server runs on port 8080, which is the
first entry in the default `CORS_ALLOWED_ORIGINS`; the frontend reads the API origin from
`VITE_API_URL` (default `http://localhost:8000`). Run both to work on the connected app:

```bash
python manage.py runserver          # this project, port 8000
bun run dev                         # beldium-new-frontend, port 8080
```

Registration, the six-digit email code, sign-in, the current-user profile, the organisation
register and join requests are wired through. With the default console email backend the
verification code is printed in this terminal, not emailed.

## API entry points

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/api/v1/auth/register/` | Register a user |
| POST | `/api/v1/auth/verify-email/` | Verify a six-digit signup code and receive JWTs |
| POST | `/api/v1/auth/resend-verification/` | Request another verification code |
| POST | `/api/v1/auth/token/` | Obtain JWT access and refresh tokens |
| POST | `/api/v1/auth/token/refresh/` | Refresh an access token |
| POST | `/api/v1/auth/logout/` | Blacklist a refresh token |
| POST | `/api/v1/auth/password-reset/request/` | Request a password-reset code |
| POST | `/api/v1/auth/password-reset/confirm/` | Verify the code and set a new password |
| POST | `/api/v1/auth/change-password/` | Change password using the current password |
| POST | `/api/v1/auth/change-email/request/` | Send a code to a proposed new email |
| POST | `/api/v1/auth/change-email/confirm/` | Verify and activate the new email |
| GET | `/api/v1/auth/audit/` | Read the current user's security audit trail |
| GET/PATCH | `/api/v1/auth/me/` | Read or update the current user |
| POST | `/api/v1/auth/verify-phone/request/` | Send a phone verification OTP |
| POST | `/api/v1/auth/verify-phone/confirm/` | Verify a phone OTP |
| POST | `/api/v1/auth/social/` | Exchange a verified Google/Microsoft ID token for JWTs |
| POST | `/api/v1/auth/social/link/` | Link a provider to an authenticated account |
| GET/POST | `/api/v1/organisations/` | List memberships or create an organisation |
| GET | `/api/v1/organisations/directory/` | Discover verified organisations |
| GET | `/api/v1/organisations/{id}/members/` | List organisation members |
| GET/POST | `/api/v1/organisations/{id}/invitations/` | Manage invitations |
| POST | `/api/v1/invitations/accept/` | Accept an invitation |
| GET/POST | `/api/v1/join-requests/` | Manage the current user's join requests |
| POST | `/api/v1/join-requests/{id}/decide/` | Approve or reject a request as an organisation administrator |
| GET | `/api/v1/organisations/{id}/my-permissions/` | Read the current membership and capabilities |
| PATCH | `/api/v1/organisations/{id}/members/{membership_id}/` | Change a member's role or profile |
| POST | `/api/v1/organisations/{id}/members/{membership_id}/suspend/` | Suspend a member |
| POST | `/api/v1/organisations/{id}/members/{membership_id}/activate/` | Reactivate a member |
| DELETE | `/api/v1/organisations/{id}/members/{membership_id}/remove/` | Remove a member |
| POST | `/api/v1/organisations/{id}/invitations/{invitation_id}/revoke/` | Revoke an invitation |
| POST | `/api/v1/organisations/{id}/submit/` | Submit an organisation for Beldium verification |
| POST | `/api/v1/organisations/{id}/decide/` | Verify or reject an organisation as platform staff |
| GET | `/api/v1/organisations/{id}/audit/` | Read organisation membership and verification events |
| GET/POST | `/api/v1/compliance-applications/` | List or start compliance applications |
| PATCH | `/api/v1/compliance-applications/{id}/sections/organisation/` | Save the complete legal organisation profile |
| PATCH | `/api/v1/compliance-applications/{id}/sections/representative/` | Save the authorised representative |
| PATCH | `/api/v1/compliance-applications/{id}/sections/services/` | Save services and geographic coverage |
| PATCH | `/api/v1/compliance-applications/{id}/sections/professional-capability/` | Save professional staffing capability |
| PATCH | `/api/v1/compliance-applications/{id}/sections/inspection-capability/` | Save physical inspection capability |
| PATCH | `/api/v1/compliance-applications/{id}/sections/conflict-declaration/` | Save independence and conflicts |
| PATCH | `/api/v1/compliance-applications/{id}/sections/declaration/` | Save final declarations |
| GET/POST | `/api/v1/compliance-applications/{id}/personnel/` | List or add key personnel |
| PATCH/DELETE | `/api/v1/compliance-applications/{id}/personnel/{personnel_id}/` | Update or remove personnel |
| GET | `/api/v1/compliance-applications/{id}/personnel/{personnel_id}/download/{cv\|certificate}/` | Download a personnel file |
| GET/POST | `/api/v1/compliance-applications/{id}/documents/` | List or upload compliance documents |
| GET | `/api/v1/compliance-applications/{id}/documents/{document_id}/download/` | Download an uploaded document |
| GET | `/api/v1/compliance-applications/{id}/document-requirements/` | Read checklist status |
| GET | `/api/v1/compliance-applications/{id}/activity/` | Read this application's activity feed |
| POST | `/api/v1/compliance-applications/{id}/submit/` | Submit a complete application |
| POST | `/api/v1/compliance-applications/{id}/decide/` | Record a staff review decision |
| POST | `/api/v1/compliance-applications/{id}/request-document/` | Request an additional document |
| POST | `/api/v1/compliance-applications/{id}/documents/{document_id}/review/` | Review an uploaded document |
| GET/POST | `/api/v1/compliance-applications/{id}/messages/` | Read or send application messages |
| POST | `/api/v1/compliance-applications/{id}/messages/mark-read/` | Mark visible messages as read |
| GET | `/api/v1/dashboard/` | Read application progress and requested actions |
| GET | `/api/docs/` | Swagger UI |

Registration, verification, resend, and token issuance are public endpoints. All other endpoints require a bearer access token.

Registration follows the authentication prototype at [miningcomplianceauth.lovable.app](https://miningcomplianceauth.lovable.app/). It accepts the prototype role identifiers `compliance-org`, `compliance-officer`, `regulatory-org`, `regulatory-officer`, and `independent`, together with country, matching password confirmation, and acceptance of the terms.

Organisation join requests carry the requested role, job title, employee/professional ID, and optional administrator message. An administrator may modify the role while approving the request. Organisation verification remains separate from account verification, so an authenticated user can continue onboarding while the organisation is under review.

The onboarding section identifiers are `organisation`, `representative`, `services`, `professional-capability`, `inspection-capability`, `conflict-declaration`, and `declaration`. Save each as `{ "data": { ... } }`; missing required values use the standard validation envelope.

## File storage and external identity

Uploads accept PDF, Word, JPEG, and PNG files up to 10 MB. Local development stores files under `media/`, which is deliberately not routed: uploads are private, so they are read only through the authenticated download endpoints above, which apply the same membership check as the rest of the application. With an S3-compatible backend those endpoints redirect to a signed URL instead of streaming the file. Supplying the `AWS_*` settings from `.env.example` switches uploads to private AWS S3, Cloudflare R2, Backblaze B2, or another S3-compatible provider through Django's storage API.

Phone OTP delivery uses `SMS_WEBHOOK_URL` and `SMS_WEBHOOK_TOKEN`. Google requires `GOOGLE_OAUTH_CLIENT_ID`; Microsoft requires `MICROSOFT_OAUTH_CLIENT_ID` and `MICROSOFT_OAUTH_TENANT_ID`. Provider tokens are validated server-side. Existing password accounts must authenticate before linking a social identity; an email match never silently links an account.

`/api/v1/organisations/` and `/api/v1/organisations/directory/` accept `?search=` (name or registration number), `?organisation_type=`, `?country=`, `?state=`, `?ordering=`, and `?page=`/`?page_size=`. The directory only ever returns verified organisations.

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

## Planned domain sequence beyond onboarding

1. Mining sites, ownership, licences, and GPS boundaries
2. Configurable scoring and risk assessments
3. Inspections, findings, non-conformities, and corrective actions
4. Production, inventory, sampling, and laboratory results
5. Advanced notifications and reports
