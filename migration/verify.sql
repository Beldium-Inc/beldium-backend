-- Verification queries for the old -> v2 merge. Read-only.
--
-- Run before and after the merge, save both, and compare:
--   psql "$TARGET" -X -A -F ',' -f migration/verify.sql > migration/out/verify-before.csv
--   python migration/merge.py ...
--   psql "$TARGET" -X -A -F ',' -f migration/verify.sql > migration/out/verify-after.csv
--   diff migration/out/verify-before.csv migration/out/verify-after.csv
--
-- Each table's "after" count minus its "before" count must equal the number of
-- "inserted" lines for that table in the merge log.

\echo '== 1. Exact row count per table'
SELECT t.table_name,
       (xpath('/row/n/text()',
              query_to_xml(format('SELECT count(*) AS n FROM %I.%I', t.table_schema, t.table_name), false, true, ''))
       )[1]::text::bigint AS row_count
FROM information_schema.tables t
WHERE t.table_schema = 'public' AND t.table_type = 'BASE TABLE'
ORDER BY t.table_name;

\echo '== 2. Foreign keys pointing at a missing row (every row here is a problem; expect none)'
WITH fks AS (
    SELECT c.conname,
           c.conrelid::regclass AS child,
           a.attname AS child_column,
           c.confrelid::regclass AS parent,
           pa.attname AS parent_column
    FROM pg_constraint c
    JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
    JOIN pg_attribute pa ON pa.attrelid = c.confrelid AND pa.attnum = c.confkey[1]
    WHERE c.contype = 'f'
      AND array_length(c.conkey, 1) = 1
      AND c.connamespace = 'public'::regnamespace
)
SELECT child, child_column, parent, orphans
FROM (
    SELECT child, child_column, parent,
           (xpath('/row/n/text()', query_to_xml(format(
               'SELECT count(*) AS n FROM %s ch WHERE ch.%I IS NOT NULL '
               'AND NOT EXISTS (SELECT 1 FROM %s p WHERE p.%I = ch.%I)',
               child, child_column, parent, parent_column, child_column), false, true, ''))
           )[1]::text::bigint AS orphans
    FROM fks
) checked
WHERE orphans > 0
ORDER BY child, child_column;

\echo '== 3. Foreign keys spanning several columns (not checked above; expect none)'
SELECT conname, conrelid::regclass AS child
FROM pg_constraint
WHERE contype = 'f' AND array_length(conkey, 1) > 1 AND connamespace = 'public'::regnamespace;

\echo '== 4. Users sharing an email once case and spaces are ignored (expect none)'
SELECT lower(trim(email)) AS email, count(*) AS accounts
FROM accounts_user
GROUP BY 1
HAVING count(*) > 1;

\echo '== 5. Users sharing a phone number, last 10 digits (review; can be legitimate)'
SELECT right(regexp_replace(phone_number, '\D', '', 'g'), 10) AS phone, count(*) AS accounts
FROM accounts_user
WHERE length(regexp_replace(phone_number, '\D', '', 'g')) >= 7
GROUP BY 1
HAVING count(*) > 1;

\echo '== 6. Organisations sharing a registration number within a country (expect none)'
SELECT country, registration_number, count(*) AS organisations
FROM organisations_organisation
WHERE registration_number <> ''
GROUP BY 1, 2
HAVING count(*) > 1;
