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

`copy_media_to_s3 --dry-run`, run on the Render service, shows how many of the 109 files still exist on disk.
