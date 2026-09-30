"""Download every uploaded file from the running Render API, without shell access.

Render has no bucket configured, so uploads only exist on the Render service's
disk. This logs in to the live API as a staff user (staff can read every
compliance application, compliance/views.py:84, and the whole mining register,
organisations/access.py:74) and fetches each file through the same
authenticated download endpoints the frontends use.

Files are saved under --out at their stored path (e.g.
compliance/documents/2026/09/x.pdf), which is the S3 key they need. Upload the
folder afterwards with:  aws s3 sync <out> s3://<bucket>/

Reads only: GET requests, plus one login. Already-downloaded files are skipped,
so it is safe to re-run (e.g. again right before cutover with a fresh dump).

    python migration/pull_render_files.py --db postgresql:///copy_v2 \
        --api https://api.beldium.com --out migration/dumps/media

It asks for the staff email and password; they are never stored.
"""
import argparse
import csv
import getpass
import json
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import psycopg

# (label, SQL returning id-like parts and the stored key, URL template)
SOURCES = [
    ("compliance document",
     "SELECT application_id, id, file FROM compliance_compliancedocument WHERE coalesce(file, '') <> ''",
     "/api/v1/compliance-applications/{0}/documents/{1}/download/"),
    ("personnel cv",
     "SELECT application_id, id, cv FROM compliance_personnel WHERE coalesce(cv, '') <> ''",
     "/api/v1/compliance-applications/{0}/personnel/{1}/download/cv/"),
    ("personnel certificate",
     "SELECT application_id, id, certificate FROM compliance_personnel WHERE coalesce(certificate, '') <> ''",
     "/api/v1/compliance-applications/{0}/personnel/{1}/download/certificate/"),
    ("mining evidence",
     "SELECT s.site_id, e.id, e.file FROM mining_evidence e JOIN mining_reviewsection s ON s.id = e.section_id "
     "WHERE coalesce(e.file, '') <> ''",
     "/api/v1/mining/sites/{0}/evidence/{1}/download/"),
    ("mining licence",
     "SELECT NULL, id, file FROM mining_licencedoc WHERE coalesce(file, '') <> ''",
     "/api/v1/mining/licences/{1}/download/"),
    ("mining document",
     "SELECT NULL, id, file FROM mining_documentrecord WHERE coalesce(file, '') <> ''",
     "/api/v1/mining/documents/{1}/download/"),
]
# Other upload fields (export, logistics, marketplace, processing, warehousing,
# compliance condition evidence) held no files in the 2026-09-30 copy; see
# migration/FINDINGS.md. The count check below catches it if that changes.
ALL_FILE_COLUMNS = [
    ("compliance_personnel", "cv"), ("compliance_personnel", "certificate"),
    ("compliance_compliancedocument", "file"), ("compliance_conditionevidence", "file"),
    ("export_exportdocument", "file"), ("logistics_logisticsdocument", "file"),
    ("marketplace_productdocument", "file"), ("mining_evidence", "file"), ("mining_licencedoc", "file"),
    ("mining_documentrecord", "file"), ("mining_correctivesubmission", "file"),
    ("processing_processingdocument", "file"), ("processing_nonconformityevidence", "file"),
    ("processing_compliancereport", "file"), ("warehousing_warehousingdocument", "file"),
]


def post_json(url, body, origin):
    request = Request(url, data=json.dumps(body).encode(), method="POST",
                      headers={"Content-Type": "application/json", "Origin": origin, "User-Agent": "beldium-migration/1.0"})
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def login(api, origin):
    email = input("Staff email: ").strip()
    password = getpass.getpass("Password (not shown): ")
    try:
        tokens = post_json(f"{api}/api/v1/auth/token/", {"email": email, "password": password}, origin)
    except HTTPError as exc:
        sys.exit(f"Login failed ({exc.code}): {exc.read().decode(errors='replace')[:300]}")
    return tokens["access"], tokens["refresh"]


def refresh_access(api, refresh, origin):
    return post_json(f"{api}/api/v1/auth/token/refresh/", {"refresh": refresh}, origin)["access"]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", required=True, help="DSN of a restored copy of the live database (read-only use)")
    parser.add_argument("--api", default="https://api.beldium.com")
    parser.add_argument("--origin", default="https://compliance.beldium.com",
                        help="Frontend origin to log in through; use https://miners.beldium.com if the account belongs to the miner portal")
    parser.add_argument("--out", type=Path, default=Path("migration/dumps/media"))
    args = parser.parse_args()
    api = args.api.rstrip("/")

    with psycopg.connect(args.db) as conn:
        conn.read_only = True
        jobs = [(label, str(key), template.format(a, b)) for label, query, template in SOURCES
                for a, b, key in conn.execute(query)]
        total = sum(conn.execute(f"SELECT count(*) FROM {t} WHERE coalesce({c}, '') <> ''").fetchone()[0]
                    for t, c in ALL_FILE_COLUMNS)
    if total != len(jobs):
        sys.exit(f"The database references {total} files but this script knows how to fetch {len(jobs)}. "
                 "A new upload type has files; add it to SOURCES first.")
    print(f"{len(jobs)} files referenced in the database.")

    access, refresh = login(api, args.origin)
    args.out.mkdir(parents=True, exist_ok=True)
    report_path = args.out.parent / "media-pull-report.csv"
    counts = {}
    with open(report_path, "w", newline="") as handle:
        report = csv.writer(handle)
        report.writerow(["type", "key", "result"])
        for index, (label, key, path) in enumerate(jobs, 1):
            dest = args.out / key
            if dest.is_file() and dest.stat().st_size > 0:
                result = "already_downloaded"
            else:
                result = None
                for attempt in range(2):
                    request = Request(f"{api}{path}", headers={"Authorization": f"Bearer {access}", "Origin": args.origin,
                                                               "User-Agent": "beldium-migration/1.0"})
                    try:
                        with urlopen(request, timeout=120) as response:
                            data = response.read()
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(data)
                        result = "downloaded"
                    except HTTPError as exc:
                        if exc.code == 401 and attempt == 0:
                            access = refresh_access(api, refresh, args.origin)
                            continue
                        # 404: record gone; 500: record exists but the file is
                        # no longer on Render's disk (storage open fails).
                        result = f"http_{exc.code}"
                    except URLError as exc:
                        result = f"network_error: {exc.reason}"
                    break
                time.sleep(0.2)  # stay well clear of any rate limit
            counts[result] = counts.get(result, 0) + 1
            report.writerow([label, key, result])
            print(f"[{index}/{len(jobs)}] {result:<20} {label}")

    print("\n" + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print(f"Report: {report_path}")
    print(f"Next: aws s3 sync {args.out} s3://<bucket>/ --dryrun   (then without --dryrun)")


if __name__ == "__main__":
    main()
