# Old database merge (Phase 3)

The old Render database (`Beldium-backend`) holds customer records that are not in v2 (`Beldium-backend-v2`). These scripts find those records and insert them into v2 without touching anything already there.

**Everything here runs against local Postgres in Docker.** Nothing connects to Render or AWS. The production run happens once, at cutover, after a rehearsal on local copies has come out clean.

## Files

| File | What it does | Writes to a database? |
|---|---|---|
| `docker-compose.yml` | Two local Postgres 18 containers, `old_db` (port 5433) and `v2_db` (port 5434) | - |
| `compare_schemas.py` | Lists tables and columns that differ between old and v2, with row counts | No |
| `find_missing.py` | Lists old rows that are missing from v2, matched on natural keys | No (both opened read-only) |
| `merge.py` | Inserts the missing rows, remaps foreign keys, logs every row | Target only, one transaction; `--dry-run` rolls back |
| `merge_engine.py` | Matching and insert rules shared by `find_missing.py` and `merge.py` | - |
| `mapping.py` | Which old table feeds which v2 table, and the natural keys | - |
| `verify.sql` | Row counts per table, orphaned foreign keys, duplicate emails and registration numbers | No |

Dumps go in `dumps/`, output goes in `out/`. Both are gitignored because they contain customer data.

## How matching works

IDs from the two databases are unrelated, so rows are never matched on primary key. Each table has natural keys in priority order:

- Users: email, then phone.
- Organisations: country plus registration number, then email, then name plus type.

Normalisation before comparing:

- Emails: lower case, spaces trimmed.
- Phones: last 10 digits, so `+2348012345678` equals `08012345678`.
- Registration numbers: punctuation and an `RC` prefix dropped.
- Company names: lower case, punctuation dropped, `limited` treated as `ltd`.

| Situation | Result |
|---|---|
| No key matches anything in v2 | **Inserted** with a new UUID |
| One v2 row matches | **Skipped as duplicate**. Children of this row are attached to the v2 row. |
| Matched only on a secondary key (same phone, different email) | Skipped as duplicate, marked `review: true` |
| Two different v2 rows match different keys | **Skipped as ambiguous**, for a person to decide |
| Every key empty | **Skipped**: no way to tell if it is a duplicate |
| Its parent row was skipped | **Skipped** (unresolved foreign key) |
| Insert breaks a constraint | **Skipped** at its own savepoint; the rest continues |

Rows inserted earlier in the same run are matched too, so duplicates inside the old database are only inserted once. Existing v2 rows are never updated or deleted.

## Steps

### 1. Take dumps (read-only on Render)

Use each database's **external** connection string from the Render dashboard. Render's IP allowlist must include your machine. `pg_dump` only reads.

```bash
pg_dump --format=custom --no-owner --no-acl "$OLD_RENDER_URL" -f migration/dumps/old.dump
pg_dump --format=custom --no-owner --no-acl "$V2_RENDER_URL"  -f migration/dumps/v2.dump
```

Use a `pg_dump` from Postgres 18 (`pg_dump --version`).

### 2. Restore locally

```bash
docker compose -f migration/docker-compose.yml up -d --wait
docker exec old_db pg_restore --no-owner --no-acl -U merge -d old /dumps/old.dump
docker exec v2_db  pg_restore --no-owner --no-acl -U merge -d v2  /dumps/v2.dump
```

```bash
export OLD=postgresql://merge:local-only@127.0.0.1:5433/old
export V2=postgresql://merge:local-only@127.0.0.1:5434/v2
```

### 3. Compare schemas

```bash
python migration/compare_schemas.py --old "$OLD" --v2 "$V2" --out migration/out/schema-diff.md
```

The old database came from a different codebase (see `MIGRATION_AUDIT.md`, section 7), so expect many differences. Use the report to fill in `mapping.py`: move each table from `PENDING` into `MAPPINGS` as a `TableMap`, parents first. The scripts refuse to run if a mapping names a column that doesn't exist, or leaves a NOT NULL v2 column without a value.

### 4. Find missing rows

```bash
python migration/find_missing.py --old "$OLD" --v2 "$V2" --out-dir migration/out
```

This produces `out/missing-<table>.csv` per table, plus `out/find-missing.jsonl` with every old row and its outcome. Review every entry that has `review: true` or an action starting with `skipped_`, apart from `skipped_duplicate`.

### 5. Rehearse the merge

```bash
psql "$V2" -X -A -F , -f migration/verify.sql > migration/out/verify-before.csv
python migration/merge.py --old "$OLD" --target "$V2" --dry-run --log migration/out/merge-dry.jsonl
python migration/merge.py --old "$OLD" --target "$V2" --log migration/out/merge.jsonl
psql "$V2" -X -A -F , -f migration/verify.sql > migration/out/verify-after.csv
diff migration/out/verify-before.csv migration/out/verify-after.csv
```

Checks:

- For each table, the after count minus the before count equals the number of `"action": "inserted"` lines for that table in `merge.jsonl`.
- Section 2 of `verify-after.csv` (orphaned foreign keys) has 0 rows.
- Sections 4 and 6 (duplicate emails, duplicate registration numbers) have 0 rows.
- Log in to the app against the local copy as a merged user with their old password.

Running `merge.py` a second time must insert nothing.

### 6. Cutover (production, once)

Not run by these scripts. Outline for sign-off:

1. Stop writes on Render (maintenance mode or scale the web service to 0).
2. Take a final dump of both Render databases.
3. Restore v2 into RDS.
4. Run `merge.py --dry-run`, then `merge.py`, from inside the VPC with `--old` pointing at a restored copy of the old dump, never at Render directly.
5. Run `verify.sql` and compare with the rehearsal.
6. Copy uploads (`docs/aws-deployment.md`, "Moving uploaded files off Render").
7. Point DNS at AWS.

## Known limits

- Only `accounts_user` is mapped. Its old columns are known from `accounts/management/commands/import_legacy_users.py`. All other tables wait on step 3.
- `created_at` for merged users is set to the merge time, unless the old table turns out to have a join date to map.
- Old users already copied into v2 by `import_legacy_users` kept their old IDs. They match on email and are skipped as duplicates, which is correct.
- Uploaded files referenced by merged old rows are not copied. If the old database has file columns, their files need a separate copy.
- Foreign keys spanning several columns are not checked by `verify.sql`; section 3 lists any that exist (none in v2 today).
