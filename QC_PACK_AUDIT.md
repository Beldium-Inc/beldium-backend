# QC_PACK_AUDIT: Beldium IT Build Pack (BLD-QC-DOC-VIS-021) against beldium-backend

Prepared for the Q&C Partner, Mr Imala. Date of audit: 2026-10-06. Branch audited: fix/deploy-wait-timeout.

Scope and method. This is a backend only repository. No frontend code is in it, so every screen in section I can only be judged by the API behind it. Only code was used as proof. Documents, comments and commit messages were ignored. No project file was changed except this report. No production or outside service was called. The existing test suite was run once locally on a throwaway SQLite database with the database settings overridden, so no real database was touched. No secret values are printed here.

Status words: BUILT AND WORKING, BUILT BUT NOT WIRED, PARTLY BUILT, NOT STARTED, CANNOT TELL.

## STEP 1. REPO MAP

| Item | Finding | Evidence |
|---|---|---|
| Apps | One Django project with 13 apps: accounts, organisations, compliance, mining, processing, export, quality, warehousing, logistics, marketplace, ecosystem, finance, careers. Plus common and core. | core/urls.py:6 |
| Framework | Django 6.0.1, Django REST Framework 3.16.1, Celery 5.6.2, Redis client, drf-spectacular for API docs. | requirements.txt |
| Language and runtime | Python 3.12 in the container image. | Dockerfile:4 |
| Database models | One models.py per app. The quality models are in quality/models.py. | quality/models.py:98 |
| Database engine | PostgreSQL when DATABASE_URL or DB_ENGINE=postgresql is set. SQLite otherwise. | core/settings.py:158 |
| Auth method | Email and password with JWT (SimpleJWT). Access token 30 minutes, refresh 7 days with rotation. Email and phone code verification exist. Social sign in (Google, Microsoft) exists. | core/settings.py:293, core/settings.py:327, accounts/models.py:124 |
| Default permission | IsAuthenticated for every view unless a view overrides it. | core/settings.py:295 |
| Deployment target | AWS ECS, deployed by GitHub Actions on push to main (production) and staging (staging). One image runs web, worker, beat and migrate. A legacy Procfile for Render is still in the repo. | .github/workflows/deploy.yml:5, Dockerfile:3, Procfile:1 |
| File storage | S3 when AWS_STORAGE_BUCKET_NAME is set, local disk otherwise. | core/settings.py:422 |
| Background jobs | Celery. Only one beat schedule exists: logistics credential expiry, hourly. | core/settings.py:453 |
| Module layout | Each vertical is its own Django app with its own models, serializers, views, urls and tests. Shared helpers sit in common. | core/urls.py:6 |
| Mining module | MineSite with latitude and longitude, evidence files, inspections, samples, licence documents, non conformities. | mining/models.py:104, mining/models.py:198, mining/models.py:366 |
| Processing module | Processor, facility, application, inspection, traceability run, non conformity. | processing/models.py:134 |
| Export module | Exporter, buyer, shipment, export documents, application review. | export/models.py:46, export/models.py:87 |
| Quality module | Applications, samples, certificates, buyer specs, non conformities, notifications. A simple five role flow. Much of the data sits in JSON lists on the parent record. | quality/models.py:98 |
| Warehousing module | Operator, facility, inventory lot, release request, certificate. | warehousing/models.py:46, warehousing/models.py:229 |
| Other modules | Marketplace (listings, orders, disputes), ecosystem (RFQ, transaction timeline, material batch), finance, logistics, careers. | marketplace/models.py:216, ecosystem/models.py:224 |
| Shared compliance primitive layer | There is no single shared layer. Each app repeats its own document, non conformity, notification and audit models. Only TimeStampedModel and a few helpers are shared. | common/models.py, common/files.py:4 |

## STEP 2. CHECKLIST

### A. Foundation

| Item | Status | Evidence | Note |
|---|---|---|---|
| A1 Users and exactly 12 roles | PARTLY BUILT | accounts/models.py:27, organisations/models.py:72, quality/services.py:40 | A User model exists. There is no 12 role model. Quality roles are worked out on each request as operator, miner, partner, buyer or regulator. Organisation membership has 15 other roles that do not match the 12. |
| A2 Role based access with record level scoping | PARTLY BUILT | quality/views.py:260, quality/views.py:441 | Samples and certificates are filtered by organisation for miner, partner and buyer. Buyers see more than certificates (they see samples). No export agent, no legal read only, no assignment based scoping. |
| A3 Multi factor sign in | NOT STARTED | core/settings.py:293 | No one time password, authenticator or second factor code exists for sign in. |
| A4 Append only audit log | PARTLY BUILT | accounts/audit.py:9, quality/services.py:99, marketplace/models.py:241 | Audit rows are written by real code. Quality keeps its audit as a JSON list on each record, so it can be edited with the record. Denied attempts are not written in the quality module. Checked: AccountAuditEvent is registered in Django admin with default settings (accounts/admin.py:8), so a superuser can edit or delete it. No single audit table. |
| A5 Settings stored in a table | NOT STARTED | quality/views.py:413 | No settings table. Certificate validity is hard coded to 365 days, not 90. No 500 m, custody gap or alert days values exist. |
| A6 Notifications by email and SMS | PARTLY BUILT | accounts/tasks.py:155, accounts/tasks.py:171, quality/models.py:239 | Email sending code exists for account flows. SMS returns early and sends nothing. Quality notifications are in app rows only. |
| A7 Workflow engine (15 statuses, 3 overlays, 11 gates) | NOT STARTED | quality/services.py:115 | Quality has a 7 state sample flow, not S01 to S15. No overlays. No gates G-01 to G-11. A separate 24 stage timeline exists in ecosystem and does not match. |
| A8 Rules engine for automatic checks | NOT STARTED | quality/services.py:152 | Only a min and max pass or fail check per limit. No rules engine. |

### B. Record types

| Record | Status | Evidence | Note |
|---|---|---|---|
| SUPPLIER | PARTLY BUILT | organisations/models.py:24, mining/models.py:88 | Organisation of type mining company and a mining profile. No ASM-nnn ID. |
| MINE_SITE | BUILT AND WORKING | mining/models.py:104 | Has latitude and longitude. Used by ecosystem batches. |
| SUPPLIER_LICENCE | PARTLY BUILT | mining/models.py:236 | LicenceDoc exists. Not tied to batch declaration. |
| MCO_CHECK | NOT STARTED | no trace | |
| ONBOARDING_DECISION | PARTLY BUILT | quality/models.py:98, quality/views.py:108 | QualityApplication has status and decision_note. |
| BATCH | PARTLY BUILT | ecosystem/models.py:224 | MaterialBatch exists. Different stage list and ID format. Not linked to quality samples. |
| BATCH_PARAMETER | NOT STARTED | no trace | |
| SITE_VERIFICATION | PARTLY BUILT | mining/models.py:366 | Mining Inspection exists. No GPS or photo rules. |
| SAMPLING_ASSIGNMENT | NOT STARTED | no trace | |
| SAMPLING_RECORD | PARTLY BUILT | quality/models.py:141, mining/models.py:410 | Two Sample models exist. Neither holds device GPS, photos or seal. |
| COC_RECORD | BUILT BUT NOT WIRED | quality/models.py:141 | Custody is a JSON list on Sample. Not a table. |
| COC_ENTRY | BUILT BUT NOT WIRED | quality/views.py:315 | One actor, one entry. No second signature. |
| LAB_RECEIPT | NOT STARTED | no trace | Moving to received is a status change only (quality/views.py:315). |
| TEST_REQUEST | BUILT BUT NOT WIRED | quality/views.py:340 | JSON dict on Sample. |
| BUYER_SPEC | PARTLY BUILT | quality/models.py:125 | Table with limits as JSON. Seeded by migration. Not BD-SPEC-BYR format. |
| ASSAY_REPORT | NOT STARTED | no trace | No report file model for quality. |
| ASSAY_RESULT | BUILT BUT NOT WIRED | quality/services.py:166, quality/views.py:371 | JSON rows on Sample. |
| QUALITY_DECISION | BUILT BUT NOT WIRED | quality/views.py:393 | JSON dict quality_review on Sample. |
| HOLD | NOT STARTED | no trace | |
| NCR | PARTLY BUILT | quality/models.py:218 | Raised by hand. Not created automatically on FAIL. |
| EVIDENCE_ITEM | NOT STARTED | no trace | Mining Evidence exists for mining reviews, not for certificates (mining/models.py:198). |
| CERTIFICATE | PARTLY BUILT | quality/models.py:179 | Exists. Fields are thin. |
| DISPUTE | PARTLY BUILT | marketplace/models.py:216 | Marketplace dispute only. Not for certificates. |
| EVIDENCE_FILE | PARTLY BUILT | quality/models.py:196 | Application documents only. No version, no hash. |
| USER | BUILT AND WORKING | accounts/models.py:27 | UUID key, email login. |
| AUDIT_LOG | PARTLY BUILT | accounts/models.py:124, marketplace/models.py:241 | Per app tables. See A4. |
| PARTNER | PARTLY BUILT | organisations/models.py:24 | Organisation types compliance partner, laboratory, inspection body. |
| PARTNER_ACCREDITATION | NOT STARTED | no trace | |

ID formats. Platform made IDs exist, but none match the pack.

| ID | Status | Evidence | Note |
|---|---|---|---|
| Supplier ASM-nnn | NOT STARTED | organisations/models.py:24 | Supplier uses a UUID. |
| Batch BD-LI-2026-nnnnnn | NOT STARTED | ecosystem/models.py:36 | Current format is BATCH-year-random hex. |
| Sample BD-SMP-2026-nnnnnn | PARTLY BUILT | quality/models.py:20 | Current format BLD-QA-SMP-year-random hex. |
| Test request BLD-TR-2026-nnnn | NOT STARTED | quality/views.py:340 | Test request has a UUID inside JSON. |
| NCR-2026-nnnn | PARTLY BUILT | quality/models.py:28 | Current format BLD-QA-NCR-year-random hex. |
| Certificate BD-CERT-2026-nnnnnn | PARTLY BUILT | quality/models.py:24 | Current format BLD-QA-CERT-year-random hex. |
| Buyer spec BD-SPEC-BYR-nnn | NOT STARTED | quality/models.py:125 | Plain name only. |
| Seal GS-nnnn-nnnnnn | NOT STARTED | no trace | No seal number anywhere in quality. |
| Every record carries a Batch ID | NOT STARTED | quality/models.py:141 | Quality Sample has a lot text field, no batch link. |

### C. Supplier and batch

| Item | Status | Evidence | Note |
|---|---|---|---|
| C1 Onboarding queue and decision | PARTLY BUILT | quality/views.py:108, quality/models.py:98 | Application queue with status and decision note exist. Not the pack's supplier check. |
| C2 MCO licence check | NOT STARTED | no trace | Manual is acceptable in release 1, but no field or step exists. |
| C3 Company file with documents | PARTLY BUILT | quality/models.py:196, compliance/models.py:88 | Documents with status exist. No expiry, number or version in quality. |
| C4 Batch declaration with five point declaration | NOT STARTED | ecosystem/models.py:224 | No declaration form or text. |
| C5 Grade and quantity lock on submit | NOT STARTED | ecosystem/models.py:224 | Batch fields can be edited. |
| C6 Supplier blocked unless approved and licence valid | NOT STARTED | quality/views.py:279 | Any miner can register a sample. |
| C7 Declared GPS compared with mine site | NOT STARTED | no trace | |

### D. Field and chain of custody

| Item | Status | Evidence | Note |
|---|---|---|---|
| D1 Site inspection report | PARTLY BUILT | mining/models.py:366 | Inspection model exists in mining. Not linked to Q&C. |
| D2 Sampling GPS from device | NOT STARTED | no trace | No GPS fields on quality records. |
| D3 More than 500 m raises HOLD | NOT STARTED | no trace | |
| D4 Four photos taken in app | NOT STARTED | no trace | |
| D5 Seal format and uniqueness | NOT STARTED | no trace | |
| D6 Conflict of interest tick | NOT STARTED | no trace | |
| D7 Offline with original time | NOT STARTED | no trace | Server stamps its own time (quality/views.py:317). Nothing accepts a device time. |
| D8 Submit locks record, opens custody entry 1 | NOT STARTED | quality/views.py:315 | Custody can be added by any miner, partner or operator. No lock. |
| D9 Two signatures per handover | NOT STARTED | quality/views.py:315 | One actor per entry. No giver or receiver. Seal intact is a flag, default true. |
| D10 Seal and sample ID compared across records | NOT STARTED | no trace | |

### E. Laboratory and decision

| Item | Status | Evidence | Note |
|---|---|---|---|
| E1 Lab sees test request without names | NOT STARTED | quality/serializers.py:100 | Sample serializer shows miner and buyer organisation to partner. |
| E2 Sample receipt form | NOT STARTED | no trace | |
| E3 Assay upload with uncertainty rule | PARTLY BUILT | quality/views.py:371, quality/services.py:180 | Results can be typed with value, unit, uncertainty. No file upload. A missing uncertainty does not block or hold. Any partner can edit, not only the assigned lab. |
| E4 Comparison with spec using uncertainty | PARTLY BUILT | quality/services.py:152 | Compares to min and max only. Uncertainty is ignored. No BORDERLINE rule exists, so no rule can be shown. |
| E5 BORDERLINE goes to HOLD | NOT STARTED | no trace | |
| E6 FAIL is final with auto NCR | PARTLY BUILT | quality/views.py:393, quality/services.py:115 | A FAIL review moves the sample to rejected and rejected is final. No automatic NCR, no supplier notice, no CEO record. |
| E7 PASS leads to evidence checking | NOT STARTED | quality/views.py:413 | A reviewed sample goes straight to certificate. |
| E8 No one approves what they submitted | NOT STARTED | quality/views.py:340, quality/views.py:393 | Same partner or operator can request, review and, if staff, certify. |

### F. Evidence and certificate

| Item | Status | Evidence | Note |
|---|---|---|---|
| F1 Evidence package of 12 items | NOT STARTED | no trace | |
| F2 Platform checks, G-07 disabled on failure | NOT STARTED | no trace | |
| F3 Certificate from verified data, quantity limit | PARTLY BUILT | quality/views.py:413 | Created by code from the sample. No quantity on the certificate, so no cap. |
| F4 E signature, QR code, no edit after issue | PARTLY BUILT | quality/views.py:413, quality/services.py:110 | A random hash is stored for the check link. No e signature, no QR image. No update route exists for certificates, only revoke. |
| F5 Public check page | BUILT AND WORKING | quality/views.py:571, quality/serializers.py:183 | No sign in, throttled, shows status and limited facts. Status words are active, revoked, draft, not the four named. Test coverage not confirmed. |
| F6 Automatic EXPIRED | NOT STARTED | quality/models.py:179 | valid_until is stored. No job sets expired, no EXPIRED status. No listing block. |
| F7 G-11 marketplace release | NOT STARTED | marketplace/models.py:115 | No link between certificate and listing found. |
| F8 Buyer certificate and dispute screen | PARTLY BUILT | quality/views.py:441, marketplace/models.py:216 | Buyer can list own certificates. No certificate dispute. |

### G. Partners

| Item | Status | Evidence | Note |
|---|---|---|---|
| G1 Six partner statuses with controlled moves | NOT STARTED | organisations/models.py:24 | Organisation has draft, under review, verified, rejected, suspended. Different list, no controlled moves. |
| G2 Qualification checklist and G-09 | NOT STARTED | no trace | |
| G3 Accreditation records with expiry | NOT STARTED | no trace | |
| G4 Pick lists by status and accreditation | NOT STARTED | no trace | |
| G5 Alerts at 90, 60, 30 days | NOT STARTED | core/settings.py:453 | Only logistics credential expiry runs. |
| G6 Suspension removes access at once | PARTLY BUILT | organisations/models.py:24 | A suspended status exists. Checked: quality role and scoping ignore organisation status, so suspension does not cut quality access (quality/services.py:40, quality/views.py:260). |

### H. Document vault

| Item | Status | Evidence | Note |
|---|---|---|---|
| H1 Upload by web, phone, on behalf | PARTLY BUILT | quality/models.py:196 | Web upload exists. No multi photo document, no on behalf flag or channel. |
| H2 Arrival checks: type, size, scan, hash | PARTLY BUILT | quality/serializers.py:21, quality/models.py:7 | Size limit 10 MB, content type list and extension list. No malware scan, no hash. |
| H3 Versioned storage, nothing deleted | PARTLY BUILT | compliance/views.py:61, compliance/views.py:239, core/settings.py:439 | Application delete is blocked. One person delete exists in compliance. S3 same name overwrite is off. No version history for documents. |
| H4 Restricted files masked, views logged | NOT STARTED | common/files.py:4 | Download streams the file after an access check. No masking, no view log. |
| H5 Lab and inspection reports not uploaded on behalf | NOT STARTED | no trace | |
| H6 Export of company file and date range | NOT STARTED | no trace | CSV report exists in export only (export/views.py:639). |
| H7 Backup | CANNOT TELL | docs/aws-deployment.md:207 | Backup is a cloud setting, not code. Only a setup guide mentions a retention flag. I would need the cloud console to see daily backup, restore tests and a second copy. |

### I. Screens (backend view only, no frontend in this repo)

| Screen | Status | Evidence | Note |
|---|---|---|---|
| Q&C Workspace Home | BUILT BUT NOT WIRED | quality/urls.py:12 | Dashboard summary API exists. Frontend not here. |
| Onboarding queue | PARTLY BUILT | quality/urls.py:7 | Applications API. |
| Company File and documents | PARTLY BUILT | quality/models.py:196 | |
| Partners and qualification | NOT STARTED | no trace | |
| Batch workspace and gates | NOT STARTED | no trace | |
| Assignments | NOT STARTED | no trace | |
| HOLDs and NCRs | PARTLY BUILT | quality/urls.py:10 | NCR API only. No HOLD. |
| Certificates | PARTLY BUILT | quality/urls.py:9 | List, detail, revoke. |
| Document vault and exports | NOT STARTED | no trace | |
| Reports and monthly review | NOT STARTED | no trace | |
| Buyer specifications and settings | PARTLY BUILT | quality/urls.py:10 | Buyer specs API only. No settings. |
| Users and roles | NOT STARTED | no trace | No user or role admin API for the 12 roles. |
| Audit log | NOT STARTED | no trace | No API reading an audit log. |
| Supplier My profile and documents | PARTLY BUILT | quality/urls.py:7 | |
| Supplier Declare a batch | NOT STARTED | no trace | Only sample registration (quality/views.py:279). |
| Supplier My batches and status | PARTLY BUILT | quality/views.py:260 | Samples scoped to the miner. |
| Supplier My certificates | PARTLY BUILT | quality/views.py:441 | |
| Supplier Messages and notices | PARTLY BUILT | quality/views.py:584 | In app notifications API. |
| Partner My assignments | NOT STARTED | no trace | |
| Partner Site inspection report | NOT STARTED | no trace | |
| Partner Sampling record | NOT STARTED | no trace | |
| Partner Custody handover | NOT STARTED | quality/views.py:315 | Single actor entry only. |
| Partner Sample receipt | NOT STARTED | no trace | |
| Partner Test request and assay upload | PARTLY BUILT | quality/views.py:340 | Typed results, no upload. |
| Partner My accreditation and documents | NOT STARTED | no trace | |
| Public certificate check | BUILT AND WORKING | quality/urls.py:17 | |
| Buyer certificate and dispute | PARTLY BUILT | quality/views.py:441 | No dispute. |
| Export agent certificate and batch status | NOT STARTED | no trace | No export agent role in quality. |
| CEO sign offs | NOT STARTED | no trace | |
| Legal read only view | NOT STARTED | no trace | |

### J. Cross cutting risks

| Risk | Severity | Evidence | Note |
|---|---|---|---|
| No multi factor sign in | high | core/settings.py:293 | Required by A3. |
| Quality audit sits in editable JSON on the record | high | quality/models.py:147 | Not append only. |
| Quality actions are open to any member of the right organisation type, with no separation of duties | high | quality/views.py:340, quality/views.py:393 | The same person can request, review and (staff) certify. |
| Upload checks lack malware scan and content sniffing | medium | quality/serializers.py:21 | Client sent content type is trusted. |
| Rate limit cache is per process by default | medium | common/checks.py:65 | System check W002 fires. Needs a shared Redis in deployment. Not known if set. |
| No rate limit on authenticated quality endpoints | low | quality/views.py:20 | Only public verify is throttled (quality/views.py:573). |
| Public endpoints (no sign in) | low | accounts/views.py:63, careers/views.py:54, organisations/views.py:364, quality/views.py:572 | Auth, careers, platform counts, certificate check. All look intended. |
| Default permission class | low | core/settings.py:295 | Set to IsAuthenticated. Good. |
| Debug flag | low | core/settings.py:45 | Off by default. Startup fails if on while deployed. |
| Secret key default in code | low | core/settings.py:44 | A development default exists. Deployed environments must set their own (core/settings.py:22). JWT signs with this key (core/settings.py:335). Value: [REDACTED]. |
| Secrets in repo | low | .env.example | Placeholders only seen by name. Only a limited pattern scan was done. A full secret scan was not done. |
| CORS | low | core/settings.py:63 | Explicit origin list, no allow all. Defaults include localhost, but deployed environments must set their own list. |
| Legacy Procfile runs delete_test_users and bootstrap_admin on every start | medium | Procfile:1 | Whether Render still uses it: CANNOT TELL. |
| Staging environment | low | .github/workflows/deploy.yml:5 | Pipeline supports a staging branch. Whether it is live: CANNOT TELL. |
| CI | low | .github/workflows/deploy.yml:36 | Present. Tests run before deploy. |
| Tests | low | quality/tests.py | 434 tests ran and all passed locally. Quality has 8 test functions (checked by name, quality/tests.py:53 to 231). None cover custody rules, HOLD, evidence or expiry. |
| Migrations | low | quality/migrations | makemigrations check found no pending changes. Migration 0003 seeds buyer specs. |

## STEP 3. OVERLAP WITH EXISTING MODULES

| Area | Can reuse | Duplicates or conflicts |
|---|---|---|
| Mining | MineSite and its coordinates (mining/models.py:104). Licence documents. Inspection model. | Two Sample models (mining/models.py:410, quality/models.py:141). Mining NonConformity (mining/models.py:295) overlaps with quality NCR. |
| Processing | Review section and document patterns. | Own non conformity and inspection models (processing/models.py:392, 480). |
| Export | Exporter and buyer records, export documents. | Own Buyer model (export/models.py:87) and domain reviews. A buyer already exists there and in marketplace. |
| Quality | Buyer spec, sample, certificate, NCR, public check route. | Sample and Certificate conflict with the new batch and certificate design. IDs differ. |
| Warehousing | Inventory lot as the place a certified batch is stored. | A second Certificate model (warehousing/models.py:229). |
| Batch | MaterialBatch (ecosystem/models.py:224) is the closest. | Different statuses and ID format. Missing declaration, lock, parameters. |
| Certificate | Quality Certificate plus public check. | Name clash with warehousing Certificate. Fields differ. |
| Document | Compliance and quality upload patterns, serve_stored_file (common/files.py:4). | At least six separate document models, none versioned or hashed. |
| Audit log | accounts audit helper (accounts/audit.py:9). | Five separate audit styles (accounts, organisations, processing, marketplace, quality JSON). |
| Roles | Organisation types and membership. | Quality role is derived at runtime. Membership roles do not match the 12. |

## STEP 4. SUGGESTED STRUCTURE

| Piece | Proposal | Reason |
|---|---|---|
| New app `qc` | Put all new Q&C records in one new Django app, separate from `quality`. | The current quality app uses JSON lists and cannot hold gates, HOLDs or custody. A new app avoids breaking live data. |
| Keep `quality` read only for old data | Freeze old samples and certificates. Migrate or link them later. | Protects what already exists and avoids two writers. |
| Role model in `accounts` | Add one table for the 12 roles, linked to the user and optionally an organisation. | Roles are derived today. Record scoping needs a stored role. |
| Shared audit service in `common` | One append only audit table with a single write function. | Five styles exist today. One table is simpler to lock and review. |
| Settings table in `qc` | Rows for distance, time gap, alert days, validity, each with a changed by and date. | Pack requires dated and logged changes. Nothing like it exists. |
| Workflow service in `qc` | One module holding S01 to S15, overlays and gates, with a status change log. | Must not be skippable, so one entry point is needed. Existing code has none to reuse. |
| Reuse MineSite | Link batches and site verification to MineSite. | Coordinates already exist. |
| Reuse Organisation | Suppliers and partners stay as organisations. Add a partner status and accreditation table. | Organisation already carries type and verification. |
| Reuse the public check route | Keep the throttled public certificate check and extend its status list. | Already built and throttled. |
| Document vault in `common` | One versioned, hashed document model with a scan step, reused by `qc`. | Today's models are per app and unversioned. |
| Celery beat | Add expiry and alert jobs next to the logistics one. | Beat is already running. |

## STEP 5. RELEASE MAPPING

| Release | Done | Partly done | Missing |
|---|---|---|---|
| 1 Foundation | User model, JWT sign in, email verification. | Roles (derived only), audit (several styles), document upload (no versioning), email notices. | 12 role model, MFA, one append only audit log, settings table, ID lists in pack formats, versioned vault with scan and hash, backup and restore proof, SMS. |
| 2 Supplier and batch | MineSite, organisation register. | Onboarding applications, company documents, partner records. | MCO check, licence rules, batch declaration with five points and lock, GPS compare, partner register with six statuses, Q&C Home. |
| 3 Field to laboratory | Mining inspection model. | Custody (one actor JSON), test request (JSON), typed results. | Phone sampling with device GPS and photos, offline sync and time check, seal rules, two signature custody, HOLD triggers, lab blind view, receipt form, assay upload with uncertainty. |
| 4 Decision and certificate | Public check route, certificate record, reject is final. | Review step, NCR by hand, revoke. | Uncertainty based comparison, BORDERLINE and HOLD, auto NCR, 12 item evidence package and checks, e signature, QR, expiry job, marketplace release gate, buyer dispute. |
| 5 Reporting and refinement | Marketplace and export CSV reports exist in other apps. | none | Q&C monthly review, exports of company file and date range, reports screen. |

ESTIMATE of missing work, in developer weeks, for the CTO, one backend developer and one designer.

| Release | Backend developer weeks | CTO weeks | Designer weeks |
|---|---|---|---|
| 1 Foundation | 5 | 2 | 1 |
| 2 Supplier and batch | 5 | 1 | 3 |
| 3 Field to laboratory | 8 | 2 | 4 |
| 4 Decision and certificate | 8 | 2 | 3 |
| 5 Reporting and refinement | 3 | 1 | 2 |
| Total | 29 | 8 | 13 |

Assumptions for the estimate:
1. Backend work only is counted in the backend column. The frontend and the phone app are not in this repo, and their build time is not included. Who builds them is an open question.
2. The CTO covers design of the gate engine, review of security work and deployment. These weeks run alongside the backend developer, so the calendar time is about 29 weeks for the backend developer at best.
3. Work is done in one new app with the existing sign in and organisation code reused.
4. Third party work (SMS provider approval, malware scanner, e signature method, QR) is not delayed by outside parties.
5. No data migration of live quality records is needed, only a link.
6. One round of review change per release is included. Legal text and the 12 item checklist are supplied by the Q&C team.
7. Testing is written with the code. No separate test team.
This is an ESTIMATE from reading code only. It is not a quote.

## STEP 6. OPEN QUESTIONS

1. Is the frontend (Q&C Workspace, supplier portal, phone app) built elsewhere, and in which repository? None is in this one.
2. Should the existing quality samples and certificates be kept, migrated or retired?
3. Is a daily database backup running, has a restore ever been tested, and is there a second location copy? The code cannot say.
4. Is a staging environment actually live, and is a shared Redis set for rate limits?
5. Is the Render Procfile still used, given it runs delete_test_users and bootstrap_admin on start?
6. Who decides the exact BORDERLINE rule with measurement uncertainty? The code has none.
7. Which SMS provider and sender ID will be used? Sending is switched off now (accounts/tasks.py:171).
8. Which e signature method and QR format are acceptable?
9. Should the existing 15 membership roles be kept next to the new 12 roles?
10. Should anyone be allowed to edit audit tables in Django admin? The admin site is mounted (core/urls.py:7) and AccountAuditEvent is editable there (accounts/admin.py:8).
11. Which existing buyer record (export, marketplace, quality spec) is the master for International Buyer?
12. Is there any live data in the existing quality tables that would block changes?

## PLAIN SUMMARY

1. This repo is a Django backend with 13 apps. It has no frontend.
2. Quality exists as a simple five role flow for samples, certificates and NCRs.
3. Most of the Build Pack is not started. This includes gates, HOLD, custody signatures, GPS, evidence package and partner accreditation.
4. The public certificate check is built and works.
5. Records exist but custody, results and audit are JSON on one record, not real tables.
6. There is no multi factor sign in, no settings table, no 12 role model and SMS is off.
7. IDs are made by the platform but none match the pack formats.
8. All 434 existing tests passed locally. They do not cover the new Q&C rules.
9. Backend effort for the missing work is about 29 developer weeks (ESTIMATE).
10. Backup, staging and the frontend cannot be judged from code.

Count of items in each status (rows in sections A to I, 121 in total):

| Status | Count |
|---|---|
| BUILT AND WORKING | 4 |
| BUILT BUT NOT WIRED | 6 |
| PARTLY BUILT | 44 |
| NOT STARTED | 66 |
| CANNOT TELL | 1 |
