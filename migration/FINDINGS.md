# Findings from the Render database copies (2026-09-30)

These come from read-only `pg_dump` copies of both Render databases, restored into a local Postgres 18 (`copy_old`, `copy_v2`). This file holds counts only, no personal data.

## Users: nothing to merge

| | Old (`Beldium-backend`) | v2 (`Beldium-backend-v2`) |
|---|---|---|
| Users | 16 | 18 |
| Password format | all `pbkdf2_sha256` (Django standard) | all `pbkdf2_sha256` |

- All 16 old users are already in v2, matched by email. `import_legacy_users` copied them earlier.
- 14 of them have the same password hash as in old. 2 have since changed their password in v2, and v2 has the newer hash.
- Copying the v2 database to AWS therefore keeps every user's email and password working. No merge step is needed for users.
- 2 accounts are inactive and unverified. They were inactive and unverified in the old database too, and have never logged in. They can't log in today either (`accounts/serializers.py:106` requires a verified email, and verification only looks up active users, `accounts/services.py:150`). The migration doesn't change this.

## Old business records: not in v2

The old database comes from a different data model. Its rows that hold customer data, none of which exist in v2:

| Old table | Rows | In v2? | Notes |
|---|---|---|---|
| `accounts_minerprofile` | 6 | no | none verified; only 1 of the 6 owners belongs to a v2 organisation |
| `mining_sites_miningorganisation` | 6 | no | no registration numbers; no name matches a v2 organisation |
| `mining_sites_miningsite` | 6 | no | no name matches a v2 mine site |
| `accounts_complianceprofile` | 4 | no | no organisation name matches v2 |
| `accounts_compliancerole` | 4 | no | |
| `accounts_complianceteammember` | 1 | no | |
| `accounts_buyerprofile` | 1 | no | |
| `compliance_minerdocument` | 4 | no | 3 have files, stored as full Backblaze B2 URLs |
| `compliance_minerlicense` | 4 | no | 4 have files, stored as full Backblaze B2 URLs |
| `miners_minerwallet` | 5 | no | every amount is zero |

These don't map one-to-one onto v2 tables. An old miner profile's fields are spread across v2's `Organisation`, `MiningOrganisationProfile` and `MineSite`. Merging them needs product decisions, so `mapping.py` is not filled in for them yet.

## Uploaded files in v2

109 file references, all stored as relative paths (so they are on Render's disk, not a bucket):

- `compliance_compliancedocument`: 80
- `mining_documentrecord`: 14
- `mining_evidence`: 12
- `mining_licencedoc`: 2
- `compliance_personnel`: 1

`pull_render_files.py` downloads them through the live API (there is no Render Shell). Its report shows which files still exist on Render's disk.

The 109 references point to **22 distinct files**: many compliance-document records share the same file.

## Decisions (2026-09-30) and rehearsal results

| Question | Decision | Implemented in |
|---|---|---|
| Old business records | Move them into v2 | `mapping.py` |
| 2 inactive, unverified accounts | Reactivate them; they then verify their email | `reactivate_users.py` |
| Reuse Render's `SECRET_KEY` | No. Users sign in once more after cutover, with the same password | nothing to do |
| Render Shell access | None. Files are pulled through the live API with a staff login | `pull_render_files.py` |

Rehearsal on a throwaway copy of v2 (`merge_rehearsal`):

- Users: all 16 old users matched by email. Matching on phone was dropped: phones are shared between accounts, and it wrongly made one user ambiguous.
- Inserted: 10 organisations (6 mining companies, 4 compliance partners), 10 owner memberships, 6 mine sites, 4 licences, 11 site documents. Row counts rose by exactly the logged inserts.
- `verify.sql`: no orphaned foreign keys, no duplicate emails, no duplicate registration numbers.
- A re-run inserted nothing. The dry run changed nothing.
- Django `full_clean()` passed for all 41 inserted records. Every new organisation has a Beldium ID and an owner, and every site has a code.
- Licences arrive as `expired`: every old expiry date is before 2026-09-30.
- One old miner document has no file; it arrives without one, as in the old system.
- One owner already had a v2 organisation under an unrelated name. The old organisation is created separately; review it after cutover.
- `reactivate_users.py` matched exactly the 2 accounts, and refuses to run if the count differs.
- `pull_render_files.py` tested against a local server: staff can reach every file type, downloads are byte-identical, and shared files are fetched once.
- `copy_legacy_files.py`: 7 distinct B2 files. The planned keys match the `legacy/` keys the merge stores exactly.

Not moved, on purpose: wallets (all zero), buyer profile (a code only), custom compliance roles and the team member (the only member is the profile owner, who gets an owner membership), government ID documents (6 links; they would become visible to site reviewers). Profile fields v2 has no column for (e.g. mining method, depth range, estimated output) stay in the archived old dump.
