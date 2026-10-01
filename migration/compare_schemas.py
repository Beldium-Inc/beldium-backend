"""Compare the schemas of two Postgres databases and report what differs.

Reads only information_schema and row counts. Writes nothing to either
database. Output is Markdown.

    python migration/compare_schemas.py \
        --old postgresql://merge:local-only@127.0.0.1:5433/old \
        --v2  postgresql://merge:local-only@127.0.0.1:5434/v2 \
        --out migration/out/schema-diff.md
"""
import argparse
import sys
from pathlib import Path

import psycopg
from psycopg import sql

COLUMNS_QUERY = """
    SELECT table_name, column_name,
           CASE WHEN data_type IN ('character varying', 'character')
                THEN data_type || '(' || character_maximum_length || ')'
                WHEN data_type = 'USER-DEFINED' THEN udt_name
                ELSE data_type END AS type,
           is_nullable = 'YES' AS nullable
    FROM information_schema.columns
    WHERE table_schema = %s
      AND table_name IN (SELECT table_name FROM information_schema.tables
                         WHERE table_schema = %s AND table_type = 'BASE TABLE')
    ORDER BY table_name, ordinal_position
"""


def read_schema(dsn, schema):
    with psycopg.connect(dsn) as conn:
        conn.read_only = True
        tables = {}
        for table, column, type_, nullable in conn.execute(COLUMNS_QUERY, (schema, schema)):
            tables.setdefault(table, {})[column] = (type_, nullable)
        counts = {
            table: conn.execute(sql.SQL("SELECT count(*) FROM {}.{}").format(sql.Identifier(schema), sql.Identifier(table))).fetchone()[0]
            for table in tables
        }
    return tables, counts


def describe(column):
    type_, nullable = column
    return f"{type_}{'' if nullable else ' NOT NULL'}"


def report(old, old_counts, v2, v2_counts):
    lines = ["# Schema comparison: old vs v2", ""]
    only_old = sorted(set(old) - set(v2))
    only_v2 = sorted(set(v2) - set(old))
    both = sorted(set(old) & set(v2))

    lines += [f"- Tables in old: {len(old)}", f"- Tables in v2: {len(v2)}",
              f"- In both: {len(both)}", f"- Only in old: {len(only_old)}", f"- Only in v2: {len(only_v2)}", ""]

    lines += ["## Tables only in old", "", "Rows here have no table to go to in v2. Each one needs a decision: map it to a v2 table, or leave it behind.", ""]
    lines += ["| Table | Rows | Columns |", "|---|---|---|"]
    lines += [f"| {t} | {old_counts[t]} | {', '.join(old[t])} |" for t in only_old] or ["| (none) | | |"]

    lines += ["", "## Tables only in v2", "", "| Table | Rows |", "|---|---|"]
    lines += [f"| {t} | {v2_counts[t]} |" for t in only_v2] or ["| (none) | |"]

    lines += ["", "## Tables in both", ""]
    identical = []
    for table in both:
        old_cols, v2_cols = old[table], v2[table]
        missing_in_v2 = [c for c in old_cols if c not in v2_cols]
        missing_in_old = [c for c in v2_cols if c not in old_cols]
        changed = [c for c in old_cols if c in v2_cols and old_cols[c] != v2_cols[c]]
        if not (missing_in_v2 or missing_in_old or changed):
            identical.append(table)
            continue
        lines += [f"### {table}", "", f"Rows: old {old_counts[table]}, v2 {v2_counts[table]}", ""]
        lines += ["| Column | old | v2 | Note |", "|---|---|---|---|"]
        for c in missing_in_v2:
            lines.append(f"| {c} | {describe(old_cols[c])} | - | only in old: lost unless mapping.py uses it |")
        for c in missing_in_old:
            note = "only in v2: needs a default" if not v2_cols[c][1] else "only in v2 (nullable)"
            lines.append(f"| {c} | - | {describe(v2_cols[c])} | {note} |")
        for c in changed:
            lines.append(f"| {c} | {describe(old_cols[c])} | {describe(v2_cols[c])} | type or nullability differs |")
        lines.append("")

    lines += ["### Identical in both", ""]
    lines += [f"- {t} (rows: old {old_counts[t]}, v2 {v2_counts[t]})" for t in identical] or ["- (none)"]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--old", required=True, help="DSN of the restored old database")
    parser.add_argument("--v2", required=True, help="DSN of the restored v2 database")
    parser.add_argument("--schema", default="public")
    parser.add_argument("--out", type=Path, help="Write the report here instead of stdout")
    args = parser.parse_args()

    old, old_counts = read_schema(args.old, args.schema)
    v2, v2_counts = read_schema(args.v2, args.schema)
    text = report(old, old_counts, v2, v2_counts)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
        print(f"Wrote {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
