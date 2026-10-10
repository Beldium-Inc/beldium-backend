# Beldium Q&C build: ground rules

Context: We are building the Quality and Control flow (reference BLD-QC-DOC-VIS-021) by growing the existing quality app in place. We do not start a new app. Target: a supervised pilot of test T-01 (one batch from supplier request to issued certificate, stages S01 to S10) with real partners in about March 2027. Everything after stage 10 (marketplace block, buyer dispute, export agent and legal views, CEO sign off screen, reports) is out of scope for now.

Decisions already made by Q&C (6 October reply, BLD-QC-DOC-RPL-024):
1. Pilot covers stages 1 to 10 only. David's sign off for the first batch is on paper.
2. Field app is online first for batch one. Offline comes later.
3. 12 roles with an access grid. Decisions and uploads made on behalf of a company need a second approver (proposed: David for the pilot).
4. Certificate validity is 90 days, stored as a setting, never hardcoded.
5. Starting settings: sampling distance limit 500 m; expiry alerts at 90, 60 and 30 days. All of these are settings.
6. Old samples and certificates stay read only and are marked "legacy, not verified". No legacy certificate is ever shown as a Beldium verification certificate.
7. Seal number is compared across 5 records at custody (sampling record, custody header, every transfer, laboratory receipt, certificate) and across 3 at evidence sign off.
8. Admin has no access to the audit log.
9. Only Q&C raises and closes nonconformities. The responsible party submits the root cause. Everyone else reads.
10. The laboratory must not see supplier or buyer names (subject to David, build it as a switch that defaults to hidden).

Not defined yet. Build as settings with a clearly named placeholder value and a TODO that names the owner. Do NOT invent values or logic:
custody time gap, HOLD deadline, the two other GPS tolerances, phone clock versus server clock gap, BORDERLINE rule per parameter, mineral codes, seal and assignment prefix, ID sequence rules, and the FAIL dispute window with one retest at a second laboratory (do not build this at all until the rule arrives).

Engineering rules:
- Every setting has an effective date and a change log. Every decision (PASS, FAIL, BORDERLINE, HOLD) stores the rule version it used.
- Audit records, certificates and custody records are append only. No update or delete path in the API, the admin site or the ORM layer used by the app. Corrections are new records that point to the old one.
- Every state change on a batch goes through one service function that checks the gate rules, the role, and writes the audit entry in the same database transaction.
- BORDERLINE always goes to HOLD. FAIL is final. A certificate can only be issued when the 12 item evidence package is complete and the result is PASS.
- Object level permissions on every endpoint. Never trust an id from the client.
- Every new endpoint ships with tests, including a test that the wrong role and the wrong owner are refused.
- Plain language in code comments and commit messages. No emojis. No double hyphens or dashes in prose.
- Do not touch the 434 existing tests except to fix them when we change behaviour on purpose, and say so in the commit message.
- Never run destructive management commands (delete_test_users, bootstrap_admin, flush) against anything except a local or throwaway database.
