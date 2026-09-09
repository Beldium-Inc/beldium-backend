# Logistics compliance: UI audit and backend build specification

Reviewed 9 September 2026 against https://beldiumlogisticscompliance.lovable.app and the current Django repository.

## Scope and evidence

The public UI identifies itself as a mock-data prototype. This audit inspected its rendered landing page, route manifest, shared state/data bundle, and the application review, company, fleet, driver, application progress, document, information-request, risk, report and regulatory-alert screen bundles. It is a source-level assessment, not an authenticated browser acceptance test or verification of any regulator's actual requirements.

There is currently no logistics Django app or `/api/v1/logistics/` URL registration. All logistics routes proposed below are new work, not existing endpoints. This document is the implementation specification; no logistics runtime code or migrations have been introduced by this audit.

The UI's regulatory names, licence examples, review turnaround promises and thresholds are prototype examples. Treat them as configurable product requirements pending validation, not as an authoritative statement of legal obligations or live government integrations.

## What exists and can be reused

| Capability | Existing source | Logistics work |
|---|---|---|
| Registration, JWT login/refresh/logout, email/phone verification, password recovery, social identity | `accounts/` | Use existing APIs; replace demo-role sign-in with authenticated membership/capability routing |
| Organisation identity and Beldium organisation ID | `organisations/` | Link logistics company profiles to organisations; define explicit logistics participation |
| Invitations, join requests, active membership, roles | `organisations/` | Add logistics desk assignments and regulator visibility rules; avoid granting global access from an arbitrary organisation membership |
| File storage and authenticated evidence download patterns | `compliance/`, `processing/` | Dedicated logistics evidence metadata, ownership checks, versions and upload schemas |
| Applicant/reviewer workflows and approval conditions | `compliance/` | Reuse patterns, but model logistics applications and service-scope permissions separately |
| Domain reviews, scoring, register, dashboards and reports | `processing/` | Adapt patterns to nine logistics domains; do not store fleet or driver records inside processing models |
| Audit events and standard error envelope | `accounts/audit.py`, `common/` | Add logistics event types, company/application references and role-filtered queries |

Existing code is reusable infrastructure, not proof that the logistics UI already has working APIs. The mining 15-document checklist must not be reused as the logistics checklist.

## Screen-by-screen gap map

| Portal / screen | UI behaviour and data | Required backend |
|---|---|---|
| Sign-in | Select demo compliance partner, logistics company, regulator or placeholder admin; session stored in localStorage | Existing auth plus explicit logistics capabilities and organisation scope |
| Compliance partner dashboard | Portfolio overview and navigation into applications | Aggregated application, decision, overdue request, credential and risk metrics from stored records |
| Applications | Company/application register and reviewer workflow | Search, filtering, pagination, reviewer assignment, start-review action and current status |
| Application: company profile | Corporate identity, contacts, services, depots, employees, tonnage, fleet/driver snapshots | Logistics company profile, operating locations, service declarations, application detail |
| Application: compliance checks | Nine domains with checklist items, scores, linked documents and review notes | Versioned domain requirements, evidence links, reviewer verdicts, reasons and sign-off timestamps |
| Application: document review | Simulated inline viewer, metadata, status changes and notes | Actual private upload/download, metadata, version history, verification/rejection and notes |
| Application: score and risk | Overall score, domain breakdown, radar and risk factors | Server-calculated score, risk band, factors/mitigations and scoring policy version |
| Application: information requests | Reason, required items, message and deadline; company response | Structured request items, due dates, attachments, response history and reviewer acceptance/reopening |
| Application: approval decision | Approve, conditionally approve, reject or ask for information | State machine, evidence/sign-off gates, rationale, structured conditions and explicit permitted/restricted scopes |
| Application activity / audit history | Actor, time, action, outcome and target | Persistent append-only audit events; private notes excluded from applicant/regulator views as appropriate |
| Compliance partner: risk | Company ranking and domain scores | Same scoring output as application detail; searchable portfolio view |
| Compliance partner: expiring documents | Credential monitoring | Date-driven expiry state, upcoming-expiry windows and filters |
| Compliance partner: requests | Portfolio-wide request queue | Scoped open/responded/accepted/overdue request lists and counts |
| Compliance partner: reports | Throughput, risk and available reports | Real aggregate queries, report generation and authenticated exports |
| Notifications | Role-targeted notifications with read state | Company/user-scoped notification delivery and per-recipient read receipts |
| My profile | Reviewer identity/profile | Existing profile API plus logistics capabilities/assignment information |
| Company: dashboard/application progress | Stages, completeness, requests and restrictions | Per-company dashboard using actual evidence, counts, stage and scope decisions |
| Company: company information | Legal details, contact, services and locations | Read/update authorised company's profile and locations |
| Company: fleet | Registration/VIN/model search, compliance states, GPS and expiry details | Vehicle register, vehicle credentials, assignments and derived compliance status |
| Company: drivers | Licence, expiry, national ID, experience, training, medical and vehicle | Driver register, controlled identity visibility, training/medical credentials and vehicle assignment |
| Company: documents/requests/activity | Evidence library, reply/upload and activity | Own-company evidence and request APIs; real file storage and persisted replies |
| Regulator: overview/register/compliance | Read-only portfolio and credentials | Explicit oversight grants and read-only datasets; no review or decision mutations |
| Regulator: expiring/alerts/reports/notifications | Expiry, restrictions, oversight exports | Appropriate read-only views of monitoring events, scope restrictions and reports |
| Admin | Explicit placeholder | Reuse Django admin initially; a full administration portal is not specified by this UI |

## Nine compliance domains

Use stable keys matching the inspected UI:

1. `corporate`: registration, ownership/directors, tax and legal identity.
2. `regulatory`: applicable transport permits and licences.
3. `fleet`: registration, roadworthiness, vehicle details and GPS/telematics evidence.
4. `driver`: licence, competency, training and medical fitness.
5. `insurance`: motor, goods-in-transit and employer/liability coverage.
6. `hs`: safety policy, incident records, emergency response and training.
7. `operational`: journey management, depots, capacity and service delivery.
8. `mineral`: mineral-haulage authorisation, custody, security/escort and sealing evidence.
9. `data`: data-protection attestation, platform administrators, retention and integration readiness.

Each application needs applicable/non-applicable domain and item rules. A company that only offers general freight must not receive a zero mineral score simply because mineral haulage is outside its requested scope.

## Proposed data model

- `LogisticsCompany`: organisation link, logistics reference, company profile, contact, employees, annual tonnage, declared services and current register status.
- `OperatingLocation`: company, address, state, location type and staffing/capacity.
- `LogisticsApplication`: company, reference, submission/stage, reviewer, decision, rationale, timestamps and policy version. Support later renewal/reassessment without destroying earlier decisions.
- `DomainReview` and checklist items: application, domain, required evidence, applicability, result, score, reviewer and notes.
- `Vehicle`: company, registration, VIN, make/model/year/type, capacity/unit, ownership, location and GPS status/source timestamp.
- `Driver`: company, name, licence/class/expiry, protected identity reference, experience and assignments. Licence/medical/training evidence should be structured credentials rather than a single free-text status.
- `LogisticsDocument` and versions: company/application, domain, optional vehicle/driver, document type, issuer/reference, issue/expiry dates, file metadata, review result and notes. Versions must retain the exact evidence used for a decision.
- `InformationRequest`, request items and responses: application, reason, message, requester, deadline, status, attachments and acceptance/reopening history.
- `ApprovalCondition` and evidence: deadlines, pending/submitted/rejected/cleared state and reviewer decisions.
- `ScopeRestriction`: company/service, reason, source credential/condition, effective dates, applied/lifted actor and resolution evidence.
- `MonitoringEvent`, notifications/read receipts and report records. Reuse shared audit events with a `logistics.` prefix.

## Proposed API contract

Base prefix: `/api/v1/logistics/`. These routes are planned, not implemented.

| Method | Route | Responsibility |
|---|---|---|
| GET | `me/` | Backend-authorised audience, active organisation and capabilities |
| GET | `dashboard/` | Audience-scoped metrics, progress, action items and restrictions |
| GET/POST | `companies/` | Register/list logistics companies |
| GET/PATCH | `companies/{id}/` | Company detail/update |
| GET/POST | `companies/{id}/locations/` | Operating locations |
| GET/POST | `vehicles/` | Scoped fleet register and creation |
| GET/PATCH | `vehicles/{id}/` | Vehicle details/update |
| GET/POST | `drivers/` | Scoped driver register and creation |
| GET/PATCH | `drivers/{id}/` | Driver details/update |
| GET/POST | `applications/` | List/create applications |
| GET | `applications/{id}/` | Full case, progress, review outcomes and permitted scopes |
| PATCH | `applications/{id}/sections/{key}/` | Applicant saves a domain's submitted information |
| POST | `applications/{id}/submit/` | Validate and submit/resubmit |
| POST | `applications/{id}/assign-reviewer/` | Authorised desk assigns an eligible reviewer |
| POST | `applications/{id}/start-review/` | Start a submitted case review |
| POST | `applications/{id}/sections/{key}/review/` | Record domain verdict and reasons |
| GET/POST | `applications/{id}/documents/` | Evidence list and multipart upload |
| GET | `documents/{id}/download/` | Authorised private preview/download |
| POST | `documents/{id}/review/` | Review a specific evidence version |
| GET/POST | `documents/{id}/notes/` | Scoped document notes |
| GET/POST | `applications/{id}/requests/` | List/raise structured requests |
| GET | `requests/` | Audience-scoped request queue |
| POST | `requests/{id}/responses/` | Applicant response and attachments |
| POST | `requests/{id}/review-response/` | Accept response or reopen outstanding items |
| POST | `applications/{id}/decide/` | Gated approval/conditional approval/rejection |
| GET/POST | `applications/{id}/conditions/` | Structured approval conditions |
| POST | `conditions/{id}/evidence/` | Upload remediation evidence |
| POST | `conditions/{id}/review/` | Review submitted evidence and clear/reject |
| GET | `applications/{id}/activity/` | Case-specific audit/activity |
| GET | `risk/` | Scores, bands, factors and policy explanations |
| GET | `documents/expiring/` | Expired/soon-expiring credentials |
| GET/POST | `restrictions/` | List/apply authorised service restrictions |
| POST | `restrictions/{id}/resolve/` | Lift restriction after required checks |
| GET | `alerts/` | Monitoring events |
| GET | `notifications/` | Current recipient's notifications |
| POST | `notifications/{id}/mark-read/` | Mark own notification read |
| GET | `audit/` | Filtered audit trail |
| GET/POST | `reports/` | List/request allowed report types |
| GET | `reports/{id}/download/` | Authenticated generated export |

Company/applicant CRUD, response schemas, allowed filters and error codes should be documented in Swagger as each slice is implemented. File uploads must declare multipart binary fields and have schema regression tests, avoiding the previous mining upload mismatch.

## Workflow and enforcement

1. Authenticate and resolve real membership/desk/oversight grants.
2. Create/select company; save profile, services, operating locations, fleet and drivers.
3. Upload applicable evidence and submit a complete application.
4. Assign reviewer and begin nine-domain review.
5. Review individual evidence versions and sign off domains; compute score/risk server-side.
6. Request missing/corrected items. Applicant replies with evidence; reviewer accepts or reopens. Upload alone does not mean acceptance.
7. Issue a reasoned decision with structured conditions and explicit allowed/restricted service scopes.
8. Monitor credentials continuously. Expiry can restrict affected service/vehicle/driver scope under a configured policy; unrelated services remain available.
9. Accept renewed evidence and explicitly clear restrictions when the relevant rules pass. Retain historical evidence and audit events.

Permissions must be enforced by the backend, including downloads and exports. A browser-selected role must never grant desk or regulator access. Company users see only their own organisation's records. Desk staff need explicit logistics authority and assignment scope; avoid copying blanket cross-company access based solely on organisation type. Regulator reads must respect permitted datasets and redact driver identity/medical details where unnecessary. Regulator read-receipt actions may update their own notifications, but not compliance records.

Scheduled monitoring should be idempotent: a daily/hourly worker detects thresholds, records one event per relevant credential/version/threshold, updates derived restrictions, and dispatches recipient-scoped notifications. A UI expiry badge alone is not continuous monitoring. Do not automatically lift a reviewer-imposed restriction simply because a replacement file was uploaded.

## Prototype mismatches to resolve

- Shared demo data declares 24 vehicles and 36 drivers for the main company, but contains only eight detailed vehicles and eight detailed drivers. Application progress text separately says 12 vehicles and 14 drivers. Use actual database counts.
- Progress says 18 of 20 documents, while the inspected sample library contains 18 records and no complete configurable 20-item requirement definition. Confirm the real checklist by service/domain; do not hard-code 20 from the display.
- The sample compliance score is 82, yet no authoritative scoring weights/formula were found in the inspected shared state. The risk screen says lower scores need earlier intervention. Define `compliance_score` separately from `risk_band`, with configurable/versioned weights and blocking requirements.
- The document library includes XLSX and ZIP examples, including a 12.6 MB ZIP. Existing mining upload limits/formats do not cover these. Define logistics-specific formats/size limits and safe handling; do not silently relax all existing upload policies.
- The sample credential viewer explicitly says it is simulated. Real file storage, private downloads and document metadata are needed.
- The decision form collects conditional text, but the inspected decision state action persists only the decision summary. Conditions and deadlines need their own durable records.
- Information-request deadlines are automatically set ten days ahead in the mock code. This must become an explicit configurable deadline, not an unexplained hard-coded promise.
- Corporate/FRSC/tax matching, GPS API connectivity and automatic restrictions appear as sample text/data. They do not demonstrate live integrations. Store manually reviewed evidence first; implement provider integrations only when the actual service contracts are available.
- The admin portal is a placeholder; a full admin product should not be inferred from it.

## Implementation sequence and acceptance gates

1. **Foundation and company portal:** logistics namespace/models/migrations; explicit capabilities; company, locations, fleet, drivers; private evidence upload and metadata; saved application/progress. Test tenant isolation, suspended users, ownership, fields, dates and multipart schemas.
2. **Compliance review:** nine domains, reviewer assignment, request/response loop, decision conditions, scope restrictions, evidence history and audit. Test invalid transitions, incomplete/expired evidence, duplicate decisions, concurrent updates, reviewer privileges and conditional clearance.
3. **Monitoring and oversight:** expiry scheduler, derived credential statuses, scoped notifications/read receipts, regulator register/risk/alerts, generated exports and dashboard metrics. Test repeated scheduler runs, expiry boundaries, restriction clearance, read-only regulators and export visibility.
4. **Frontend integration and live verification:** replace localStorage mock actions, map enums/IDs, remove hard-coded counts, display API errors, verify upload/download against deployed storage, and execute complete company → reviewer → regulator flows.

Policy inputs still needed before production enforcement: approved evidence requirements per service, scoring weights/risk bands, review/expiry warning windows, applicable restriction rules, logistics upload formats/limits and oversight data grants. Keep these explicit and versioned; prototype values are not final policy.
