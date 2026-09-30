"""After loading data into an environment, prove it works through the real API.

1. Logs in with a real account (asks for email and password): shows the old
   password works against the migrated database.
2. With --db (the environment's database, e.g. through deploy/db-tunnel.sh) and
   a staff account: downloads one current upload and one migrated old document,
   which proves the files are in S3 and readable by the app.

Reads only.

    python migration/smoke_test.py --api https://<env host> [--origin http://localhost:8080] [--db "$TARGET"]
"""
import argparse
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import psycopg

from pull_render_files import TIMEOUT, login, wake

CHECKS = [
    ("current upload (compliance document)",
     "SELECT application_id, id FROM compliance_compliancedocument WHERE coalesce(file, '') <> '' AND file NOT LIKE 'legacy/%' LIMIT 1",
     "/api/v1/compliance-applications/{0}/documents/{1}/download/"),
    ("migrated old document",
     "SELECT NULL, id FROM mining_documentrecord WHERE file LIKE 'legacy/%' LIMIT 1",
     "/api/v1/mining/documents/{1}/download/"),
    ("migrated old licence",
     "SELECT NULL, id FROM mining_licencedoc WHERE file LIKE 'legacy/%' LIMIT 1",
     "/api/v1/mining/licences/{1}/download/"),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", required=True)
    parser.add_argument("--origin", default="http://localhost:8080",
                        help="Frontend origin the account signs in through (staging: http://localhost:8080 compliance, http://localhost:5174 miners)")
    parser.add_argument("--db", help="Environment database DSN; enables the file download checks (needs a staff account)")
    args = parser.parse_args()
    api = args.api.rstrip("/")
    if not api.startswith("http"):
        api = f"https://{api}"

    wake(api)
    access, _ = login(api, args.origin)
    print("✅ login works (password accepted, email verified, portal allowed)")
    if not args.db:
        return

    failures = 0
    with psycopg.connect(args.db) as conn:
        conn.read_only = True
        for label, query, template in CHECKS:
            row = conn.execute(query).fetchone()
            if not row:
                print(f"–  {label}: none in this database, skipped")
                continue
            request = Request(f"{api}{template.format(*row)}",
                              headers={"Authorization": f"Bearer {access}", "Origin": args.origin, "User-Agent": "beldium-migration/1.0"})
            try:
                with urlopen(request, timeout=TIMEOUT) as response:
                    size = len(response.read())
                print(f"✅ {label}: downloaded {size} bytes")
            except HTTPError as exc:
                failures += 1
                hint = {404: "not visible to this account (is it staff?)", 500: "file missing from S3"}.get(exc.code, "")
                print(f"❌ {label}: HTTP {exc.code} {hint}")
            except (URLError, TimeoutError, OSError) as exc:
                failures += 1
                print(f"❌ {label}: {exc}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
