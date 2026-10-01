"""Insert rows from the old database that are missing from v2.

- Matches on natural keys, never primary keys (merge_engine.py explains the rules).
- Missing rows get new primary keys; foreign keys are remapped to the v2 IDs
  of their parents, whether the parent was matched or newly inserted.
- Duplicates are skipped, and so are ambiguous rows and rows whose parent was
  skipped. Nothing that already exists in v2 is updated or deleted.
- Every old row is logged (inserted or skipped, and why) to a JSON Lines file.
- The whole run is one transaction on the target. --dry-run performs every
  insert, then rolls back, so constraint errors surface without changing
  anything. Any error in a real run rolls back everything.

The old database is opened read-only.

    python migration/merge.py --old <dsn> --target <dsn> --dry-run
    python migration/merge.py --old <dsn> --target <dsn> --log migration/out/merge.jsonl
"""
import argparse
import sys
from pathlib import Path

import psycopg

from mapping import MAPPINGS, PENDING
from merge_engine import ConfigError, Merger, print_summary


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--old", required=True, help="DSN of the old database (read-only)")
    parser.add_argument("--target", required=True, help="DSN of the database to insert into")
    parser.add_argument("--dry-run", action="store_true", help="Do everything, then roll back")
    parser.add_argument("--log", type=Path, default=Path("migration/out/merge.jsonl"))
    args = parser.parse_args()
    args.log.parent.mkdir(parents=True, exist_ok=True)

    with psycopg.connect(args.old) as old, psycopg.connect(args.target, autocommit=False) as target:
        old.read_only = True
        # Django creates FKs as DEFERRABLE INITIALLY DEFERRED. Checking them per
        # statement makes a bad row fail at its own savepoint, not at COMMIT.
        target.execute("SET CONSTRAINTS ALL IMMEDIATE")
        merger = Merger(old, target, MAPPINGS, execute=True, log_path=args.log)
        try:
            summary = merger.run()
        except ConfigError as exc:
            target.rollback()
            sys.exit(str(exc))
        except BaseException:
            target.rollback()
            print("Merge failed; everything rolled back.", file=sys.stderr)
            raise
        finally:
            merger.close()

        if args.dry_run:
            target.rollback()
            heading = "DRY RUN, rolled back, nothing changed:"
        else:
            target.commit()
            heading = "Committed:"

    print_summary(summary, heading)
    print(f"Log: {args.log}")
    if PENDING:
        print(f"Deliberately left in the old database: {', '.join(t for t, _, _ in PENDING)}")


if __name__ == "__main__":
    main()
