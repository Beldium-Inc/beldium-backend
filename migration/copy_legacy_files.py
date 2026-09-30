"""Copy the old platform's documents from Backblaze B2 into the AWS uploads bucket.

The old database stores full B2 URLs. merge.py stores those documents in v2
under legacy/<path> (mapping.legacy_key). This copies each file to exactly that
key. Only files that merge.py moves are copied; government ID documents are not.

The B2 bucket is private, so it needs a B2 application key with read access
(Backblaze dashboard -> Application Keys). AWS access comes from your normal
AWS login (`aws login`).

Never overwrites an object already in the target bucket, so it is safe to re-run.

    export B2_KEY_ID=...  B2_APPLICATION_KEY=...
    python migration/copy_legacy_files.py --old postgresql:///copy_old --bucket beldium-staging-uploads --dry-run
    python migration/copy_legacy_files.py --old postgresql:///copy_old --bucket beldium-staging-uploads
"""
import argparse
import mimetypes
import os
import sys
from urllib.parse import unquote, urlparse

import psycopg

from mapping import legacy_key

# The URL columns merge.py moves (mapping.LICENCES, MINER_DOCUMENTS, PROFILE_DOCUMENTS).
URL_QUERY = """
    SELECT document FROM compliance_minerlicense WHERE coalesce(document, '') <> ''
    UNION SELECT file FROM compliance_minerdocument WHERE coalesce(file, '') <> ''
    UNION SELECT environmental_compliance_document FROM accounts_minerprofile WHERE coalesce(environmental_compliance_document, '') <> ''
    UNION SELECT license_certificate FROM accounts_minerprofile WHERE coalesce(license_certificate, '') <> ''
"""


def parse_b2_url(url):
    """https://<bucket>.s3.<region>.backblazeb2.com/<key> -> (endpoint, bucket, key).

    The key keeps its exact characters, including doubled slashes, because
    that is how the object is named in B2.
    """
    parsed = urlparse(url.strip())
    bucket, _, rest = parsed.netloc.partition(".s3.")
    if not rest.endswith("backblazeb2.com"):
        raise ValueError(f"not a B2 S3 URL: {parsed.netloc}")
    return f"https://s3.{rest}", bucket, unquote(parsed.path[1:])


def plan(old_dsn):
    with psycopg.connect(old_dsn) as conn:
        conn.read_only = True
        urls = sorted(row[0] for row in conn.execute(URL_QUERY))
    return [(*parse_b2_url(url), legacy_key(url)) for url in urls]


def exists(client, bucket, key):
    from botocore.exceptions import ClientError
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise


def copy(jobs, source_client_for, target, bucket, dry_run):
    counts = {}
    for endpoint, source_bucket, source_key, dest_key in jobs:
        if exists(target, bucket, dest_key):
            result = "already_in_bucket"
        elif dry_run:
            result = "would_copy"
        else:
            body = source_client_for(endpoint).get_object(Bucket=source_bucket, Key=source_key)["Body"].read()
            content_type = mimetypes.guess_type(dest_key)[0] or "application/octet-stream"
            target.put_object(Bucket=bucket, Key=dest_key, Body=body, ContentType=content_type)
            result = "copied"
        counts[result] = counts.get(result, 0) + 1
        print(f"{result:<18} {dest_key}")
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--old", required=True, help="DSN of the restored old database")
    parser.add_argument("--bucket", required=True, help="AWS uploads bucket, e.g. beldium-staging-uploads")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    import boto3

    jobs = plan(args.old)
    print(f"{len(jobs)} files to copy from B2.")
    target = boto3.client("s3", region_name=args.region)

    clients = {}

    def source_client_for(endpoint):
        if endpoint not in clients:
            key_id, secret = os.environ.get("B2_KEY_ID"), os.environ.get("B2_APPLICATION_KEY")
            if not (key_id and secret):
                sys.exit("Set B2_KEY_ID and B2_APPLICATION_KEY (a read-only Backblaze application key).")
            region = urlparse(endpoint).netloc.split(".")[1]  # s3.<region>.backblazeb2.com
            clients[endpoint] = boto3.client("s3", endpoint_url=endpoint, region_name=region,
                                             aws_access_key_id=key_id, aws_secret_access_key=secret)
        return clients[endpoint]

    counts = copy(jobs, source_client_for, target, args.bucket, args.dry_run)
    print(("[dry run] " if args.dry_run else "") + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))


if __name__ == "__main__":
    main()
