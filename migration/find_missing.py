"""Report rows in the old database that are missing from v2.

Matches on natural keys (email, phone, registration number, company name),
never on primary keys. Uses exactly the same rules as merge.py
(merge_engine.py), so what this reports missing is what merge.py inserts.

Both databases are opened read-only. Nothing is written to either.

    python migration/find_missing.py \
        --old postgresql://merge:local-only@127.0.0.1:5433/old \
        --v2  postgresql://merge:local-only@127.0.0.1:5434/v2 \
        --out-dir migration/out

Writes <out-dir>/missing-<table>.csv (one row per missing record) and
<out-dir>/find-missing.jsonl (every old row and what would happen to it).
"""
import argparse
import csv
import json
import sys
from pathlib import Path

import psycopg

from mapping import MAPPINGS, PENDING
from merge_engine import ConfigError, Merger, print_summary


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--old", required=True)
    parser.add_argument("--v2", required=True)
    parser.add_argument("--out-dir", type=Path, default=Path("migration/out"))
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    log_path = args.out_dir / "find-missing.jsonl"

    with psycopg.connect(args.old) as old, psycopg.connect(args.v2) as v2:
        old.read_only = True
        v2.read_only = True
        merger = Merger(old, v2, MAPPINGS, execute=False, log_path=log_path)
        try:
            summary = merger.run()
        except ConfigError as exc:
            sys.exit(str(exc))
        finally:
            merger.close()

    by_table = {}
    review = []
    for line in log_path.read_text().splitlines():
        entry = json.loads(line)
        if entry["action"] == "missing":
            by_table.setdefault(entry["table"], []).append(entry)
        if entry.get("review") or entry["action"].startswith("skipped_") and entry["action"] != "skipped_duplicate":
            review.append(entry)

    for table, entries in by_table.items():
        path = args.out_dir / f"missing-{table}.csv"
        keys = sorted({k for e in entries for k in e["natural_key"]})
        with path.open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["source_pk", *keys])
            for e in entries:
                writer.writerow([e["source_pk"], *[" | ".join(e["natural_key"].get(k, [])) for k in keys]])

    print_summary(summary, "Old rows by outcome (missing = would be inserted):")
    if review:
        print(f"\n{len(review)} rows need a person to look at them (skipped, or matched on a secondary key only).")
        print(f"See entries with review=true or action skipped_* in {log_path}")
    if PENDING:
        print(f"\nDeliberately left in the old database: {', '.join(t for t, _, _ in PENDING)}")


if __name__ == "__main__":
    main()
