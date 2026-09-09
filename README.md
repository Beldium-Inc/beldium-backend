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
- Processing Compliance register: processors, facilities, ten-section applications, findings, inspections, environmental alerts, incidents and batch traceability

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

The Processing Compliance dashboard is fully wired: every screen under `/processing` reads
this API and every review action writes to it. Seed a demo register first, or the screens
open empty:

```bash
python manage.py seed_processing --flush
```

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
| GET | `/api/v1/processing/me/` | Read the caller's processing audience and capabilities |
| GET | `/api/v1/processing/checklist/` | Read the evidence checklist for a processing type |
| GET | `/api/v1/processing/dashboard/` | Read every aggregate the processing dashboards show |
| GET/POST | `/api/v1/processing/processors/` | The register of processing companies |
| GET/POST | `/api/v1/processing/processors/{id}/facilities/` | List or add a processor's facilities |
| GET/POST | `/api/v1/processing/applications/` | List or start processor applications |
| PATCH | `/api/v1/processing/applications/{id}/sections/{key}/` | Applicant saves one evidence section |
| POST | `/api/v1/processing/applications/{id}/sections/{key}/review/` | Operator records a verdict on a section |
| GET/POST | `/api/v1/processing/applications/{id}/documents/` | List or upload section evidence |
| GET/POST | `/api/v1/processing/applications/{id}/risk-causes/` | Read or add weighted risk causes |
| POST | `/api/v1/processing/applications/{id}/submit/` | Submit a complete application |
| POST | `/api/v1/processing/applications/{id}/decide/` | Record the desk's decision |
| POST | `/api/v1/processing/applications/{id}/request-inspection/` | Raise a site inspection |
| GET | `/api/v1/processing/applications/{id}/activity/` | This application's activity feed |
| GET/POST | `/api/v1/processing/non-conformities/` | Read or raise findings |
| POST | `/api/v1/processing/non-conformities/{id}/evidence/` | Submit corrective-action evidence |
| POST | `/api/v1/processing/non-conformities/{id}/close/` | Accept the evidence, or reject and reopen |
| GET/POST | `/api/v1/processing/inspections/` | Inspection queue |
| GET/POST | `/api/v1/processing/environmental-alerts/` | Threshold exceedances |
| POST | `/api/v1/processing/environmental-alerts/{id}/status/` | Acknowledge or resolve an alert |
| GET/POST | `/api/v1/processing/incidents/` | Reportable facility events |
| GET/POST | `/api/v1/processing/runs/` | Batch traceability runs |
| GET | `/api/v1/processing/documents/expiring/` | Documents expired or lapsing within 60 days |
| POST | `/api/v1/processing/documents/{id}/review/` | Accept or reject one piece of evidence |
| GET | `/api/v1/processing/documents/{id}/download/` | Download stored evidence |
| GET | `/api/v1/processing/reports/` | Generated oversight reports |
| GET | `/api/v1/processing/audit/` | The processing audit trail |
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

## Processing Compliance

The `processing` app is the backend for the Processing Compliance vertical: companies that
crush, refine, smelt or sort mineral feedstock.

A **processor** is admitted to the register through an **application** carrying ten evidence
sections — corporate, regulatory, facility, environmental, health & safety, equipment,
operational, quality, waste and inspection. The desk reviews each section and each document
individually, then decides. After admission the register keeps accumulating evidence against
the processor: inspections, findings, environmental alerts, incidents and traceability runs.

### Audiences

Access follows organisation membership, never a request parameter. The frontend stores a
chosen dashboard role in the browser, and that choice must not be able to grant anything.

| Audience | Who | May |
|---|---|---|
| `operator` | Platform staff, or a member of a compliance-partner / inspection-body organisation | Review sections and documents, raise and close findings, schedule inspections, decide |
| `regulator` | A member of a regulator organisation | Read the whole register; change nothing |
| `processor` | A member of any other organisation | Read only its own records; submit its own evidence and incident reports |

Within the operator desk, acting on a review additionally requires an owner, administrator,
reviewer, inspector, compliance-manager or mining-compliance-officer role. `GET
/api/v1/processing/me/` returns the caller's audience and capabilities so the UI can render
against real permissions.

### Application stages

| Stage | Meaning | Moves on |
|---|---|---|
| `new` | Applicant is still filling it in | `submit/`, or the first section review |
| `in_review` | With the desk | A verdict, an inspection request, or `decide/` |
| `awaiting_info` | Back with the applicant | The applicant edits and resubmits |
| `inspection` | A site visit has been raised | `decide/` |
| `decided` | Final | Nothing |

`submit/` is refused with 409 `application_incomplete` unless completeness reaches 100%: every
section must carry data, and every document it lists must carry a file. `decide/` with
`approved` is refused while any section is unverified (`sections_not_verified`) or any finding
is open (`non_conformities_open`). `more_info_required` is not an outcome — it returns the
application to `awaiting_info` and the desk decides again later. Approval promotes the
processor on the register and scores it at `100 - risk_score`.

### The applicant path

A company joins the register by filing an application, filling it in, and submitting it:

| Step | Endpoint |
|---|---|
| Read what is required | `GET checklist/?processing_type=…` |
| Start an application | `POST applications/` |
| Answer a section | `PATCH applications/{id}/sections/{key}/` |
| Upload evidence | `POST applications/{id}/documents/` |
| Check what is still missing | `GET applications/{id}/` → `outstanding` |
| Submit | `POST applications/{id}/submit/` |
| Answer a finding | `POST non-conformities/{id}/evidence/` |

A first-time applicant names neither an organisation nor a processor — it has no register
record yet — and the application is attributed to the caller's own organisation. A caller who
belongs to more than one must say which (`400 organisation_required`); naming *another*
company's organisation or processor is refused outright.

Uploads are keyed on section plus document name, so re-uploading the same evidence replaces
it rather than leaving two rows the desk has to choose between, and clears whatever verdict
had been reached on the old file. Saving a section likewise returns it to the review queue: a
verdict on the previous content says nothing about the new content.

### The checklist

`processing/checklist.py` defines what each application must answer and evidence. It lives on
the server because completeness is computed from it — the definition of "complete" cannot be
a client's opinion of it — and because the requirements differ by processing type, which is a
policy question rather than a presentation one.

Every applicant answers the same prompts and supplies the same base documents. On top of
that, each process class evidences its own hazards: a chemical refinery adds an effluent
discharge permit, a reagent bund certification and a spill response plan; a smelter adds
stack emission monitoring, a slag disposal agreement and thermal PPE certification.

`GET /api/v1/processing/checklist/?processing_type=…` returns it, so the applicant form asks
for exactly what the server will judge.

### Derived values

Risk, completeness and document validity are computed on read, never stored, so they cannot
go stale between writes:

- **Risk score** is the sum of an application's weighted `risk_causes`, capped at 100. The
  band matches the badge the desk reads: 55+ is high, 30+ medium, below that low.
- **Completeness** is the percentage of the ten sections that are complete: every required
  prompt answered, and every required document supplied with a file. A document row with no
  file is a declared-but-unsupplied gap, which is exactly what completeness exists to expose,
  so an absent row and an empty one count the same.
- **Document validity** is `missing` with no file, `expired` past its date, `expiring` within
  60 days, otherwise `valid`. A document's *validity* is separate from the desk's *verdict*
  on it (`review_state`).
- **Run yield** comes from masses held in kilograms, so reconciliation is exact.

### Audit

Every processing action is written to the shared `AccountAuditEvent` table with a
`processing.` prefix, alongside the account and compliance trails, so one query answers "what
has this user done" across the platform. `GET /api/v1/processing/audit/` renders it for
operators and regulators; a single processor sees nothing there, because the trail spans
companies. The per-application feed at `applications/{id}/activity/` is the view its own
members get.

## Planned domain sequence beyond onboarding

1. Mining sites, ownership, licences, and GPS boundaries
2. Configurable scoring and risk assessments
3. Inspections, findings, non-conformities, and corrective actions
4. Production, inventory, sampling, and laboratory results
5. Advanced notifications and reports

## Review workflow and frontend integration

Application review now enforces these transitions:

| Current status | Allowed next status | Who / endpoint |
|---|---|---|
| `draft`, `rejected` | `under_review` | Application editor: `submit/` |
| `under_review` | `action_required`, `conditionally_approved`, `verified`, `rejected` | Platform staff: `decide/` |
| `action_required` | `under_review` | Application editor: `submit/` |
| `action_required` | `rejected` | Platform staff: `decide/` |
| `conditionally_approved` | `action_required`, `verified`, `rejected` | Platform staff: `decide/` |
| `verified` | None | Final for this onboarding application |

Application editors are active owners, administrators, compliance managers, or platform staff. General onboarding edits are allowed only in draft, action-required, and rejected applications. Staff document requests/reviews are available only while review is open. An organisation with a compliance application must use the application decision endpoint; the older organisation decision endpoint cannot bypass compliance checks.

Submission requires verified applicant email, all onboarding sections, personnel, all 15 standard documents, and every additional requested document. Requested or rejected documents do not count as complete, even if an older file is still present. Approval additionally requires staff to verify every document. Uploading corrections does not automatically resubmit: call `submit/` after completing them.

### Document deadlines

`POST /api/v1/compliance-applications/{id}/request-document/` accepts:

```json
{
  "document_type": "insurance",
  "title": "Professional indemnity insurance",
  "request_message": "Please upload current cover.",
  "due_date": "2027-01-31"
}
```

`due_date` is optional for document requests and cannot be in the past when supplied. Re-requesting a document resets its review and puts the application into `action_required`; the previous file remains available until replaced. A replacement must include an actual multipart `file`. Document responses expose `due_date` and `is_overdue`. The checklist includes additional requested documents as well as standard requirements.

### Approval conditions and evidence

All routes below are relative to `/api/v1/compliance-applications/{id}/`:

| Method | Route | Purpose |
|---|---|---|
| GET | `conditions/` | Members/staff view conditions and evidence history |
| POST | `conditions/` | Staff add a condition while review is open |
| POST | `conditions/{condition_id}/evidence/` | Editor uploads evidence as multipart `file` and optional `notes` |
| POST | `conditions/{condition_id}/evidence/{evidence_id}/review/` | Staff review evidence with `status: verified` or `rejected`, plus optional `notes` |

Conditional approval through `decide/` requires at least one outstanding structured condition, either already created through `conditions/` or supplied in the decision:

```json
{
  "status": "conditionally_approved",
  "notes": "Renew insurance before final verification.",
  "conditions": [
    {
      "title": "Renew insurance",
      "description": "Upload the renewed professional indemnity policy.",
      "due_date": "2027-01-31"
    }
  ]
}
```

Condition titles, descriptions and deadlines are required. Evidence may be uploaded while the application is `conditionally_approved` or `action_required`. Each condition follows `pending → submitted → cleared`, or `submitted → rejected → submitted` when replacement evidence is needed. Evidence versions and review notes are retained. A pending review or cleared condition cannot receive duplicate evidence. All conditions must be cleared before staff explicitly verifies the application; clearance does not automatically approve it.

Past deadlines set `is_overdue`; they do not automatically reject an application or prevent remediation. There is no automatic deadline reminder delivery in this change. `conditional_requirements` remains available as legacy display text; legacy text-only conditions must be converted to structured conditions by staff before final verification.

### Dashboard, messages and mineral experience

- `GET /api/v1/dashboard/` now includes `beldium_id`, structured `conditions` with evidence history, and `requested_documents` with deadlines and overdue indicators.
- Progress uses the creating applicant's active account and verified email. Older applications without `created_by` fall back to an active organisation owner. Phone verification remains a separate account feature and is not a submission prerequisite.
- `PATCH .../sections/professional-capability/` accepts `data.mineral_experience`, an optional list of up to 100 nonblank names, each at most 100 characters. Names must be unique ignoring case; e.g. `["Gold", "Tin"]`. The frontend's mineral selection should map to this field.
- Message `read_at` now means **read by the current user**. `messages/mark-read/` creates individual receipts and returns the number newly marked. One member reading a message does not clear another member's unread count. Internal messages remain staff-only.
- The old shared message timestamp is retained in the database for compatibility but no longer used by the API. Existing messages begin unread for each recipient because historical individual readers cannot be inferred.

Apply the database migration before starting the updated backend:

```bash
python manage.py migrate
```

The frontend still needs to call these endpoints. Email/SMS, social sign-in and S3 provider credentials must be configured and verified separately in the deployment environment.
