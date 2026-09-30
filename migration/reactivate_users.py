"""Reactivate the accounts that were inactive in the old database too.

FINDINGS.md: 2 users are inactive and unverified in both databases and have
never logged in, so they can't sign in. This turns is_active back on for
exactly those accounts. They then sign in, are told to verify their email,
and can request a new code (verification only looks up active users,
accounts/services.py:150).

Selected: in the target, inactive AND email not verified AND never logged in,
AND the same email is inactive in the old database. Nothing else is touched.

Preview (default, changes nothing):
    python migration/reactivate_users.py --old <dsn> --target <dsn>
Apply, in one transaction, refusing if the count isn't what you expect:
    python migration/reactivate_users.py --old <dsn> --target <dsn> --apply --expect 2
"""
import argparse
import sys

import psycopg


def mask(email):
    name, _, domain = email.partition("@")
    return f"{name[:2]}***@{domain}"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--old", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expect", type=int, help="With --apply: abort unless exactly this many accounts match")
    args = parser.parse_args()

    with psycopg.connect(args.old) as old:
        old.read_only = True
        inactive_in_old = {row[0] for row in old.execute(
            "SELECT lower(trim(email)) FROM accounts_user WHERE NOT is_active AND coalesce(trim(email), '') <> ''"
        )}

    with psycopg.connect(args.target) as target:
        candidates = [
            (pk, email) for pk, email in target.execute(
                "SELECT id, lower(trim(email)) FROM accounts_user "
                "WHERE NOT is_active AND email_verified_at IS NULL AND last_login IS NULL ORDER BY created_at"
            )
            if email in inactive_in_old
        ]
        print(f"{len(candidates)} account(s) to reactivate:")
        for pk, email in candidates:
            print(f"  {pk}  {mask(email)}")

        if not args.apply:
            print("Preview only; nothing changed. Re-run with --apply --expect N to reactivate.")
            return
        if args.expect is not None and args.expect != len(candidates):
            sys.exit(f"Expected {args.expect}, found {len(candidates)}. Nothing changed.")

        with target.transaction():
            for pk, _ in candidates:
                target.execute("UPDATE accounts_user SET is_active = true, updated_at = now() WHERE id = %s", (pk,))
        print(f"Reactivated {len(candidates)}. To undo: UPDATE accounts_user SET is_active = false WHERE id IN (the ids above).")


if __name__ == "__main__":
    main()
