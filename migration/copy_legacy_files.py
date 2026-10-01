"""Copy the old platform's documents from Backblaze B2 into the AWS uploads bucket.

The old database stores full B2 URLs. merge.py stores those documents in v2
under legacy/<path> (mapping.legacy_key). This copies each file to exactly that
key. Only files that merge.py moves are copied; government ID documents are not.

The B2 bucket is private, so it needs a B2 application key with read access
(Backblaze dashboard -> Application Keys). Files are fetched with B2's native
download API rather than its S3-compatible one: the old object names contain
doubled slashes ("...documents//file.pdf"), and B2 rejects S3-style signatures
for those paths (SignatureDoesNotMatch). AWS access comes from your normal
AWS login (`aws login`).

The dry run checks the B2 key and that every file is readable, without copying.

Never overwrites an object already in the target bucket, so it is safe to re-run.

    export B2_KEY_ID=...  B2_APPLICATION_KEY=...
    python migration/copy_legacy_files.py --old postgresql:///copy_old --bucket beldium-staging-uploads --dry-run
    python migration/copy_legacy_files.py --old postgresql:///copy_old --bucket beldium-staging-uploads
"""
import argparse
import base64
import json
import mimetypes
import os
import sys
from urllib.error import HTTPError
from urllib.parse import quote, unquote, urlparse
from urllib.request import Request, urlopen

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


B2_AUTHORIZE_URL = "https://api.backblazeb2.com/b2api/v3/b2_authorize_account"


class B2Error(Exception):
    pass


class B2:
    """The two calls needed from B2's native API: authorize, then download by name."""

    def __init__(self, key_id, application_key, authorize_url=B2_AUTHORIZE_URL):
        basic = base64.b64encode(f"{key_id}:{application_key}".encode()).decode()
        try:
            with urlopen(Request(authorize_url, headers={"Authorization": f"Basic {basic}"}), timeout=30) as response:
                info = json.loads(response.read())
        except HTTPError as exc:
            raise B2Error(f"Backblaze rejected the key ({exc.code}). Check the keyID and applicationKey "
                          "were pasted into the right prompts, with nothing extra.") from exc
        self.token = info["authorizationToken"]
        storage = info.get("apiInfo", {}).get("storageApi", {})
        self.download_url = (storage.get("downloadUrl") or info["downloadUrl"]).rstrip("/")

    def _request(self, bucket, name, method):
        url = f"{self.download_url}/file/{quote(bucket)}/{quote(name, safe='/')}"
        return Request(url, method=method, headers={"Authorization": self.token})

    def status(self, bucket, name):
        """'ok', 'missing_in_b2' or 'no_access' without downloading the file."""
        try:
            with urlopen(self._request(bucket, name, "HEAD"), timeout=60):
                return "ok"
        except HTTPError as exc:
            return {404: "missing_in_b2", 401: "no_access", 403: "no_access"}.get(exc.code, f"b2_http_{exc.code}")

    def download(self, bucket, name):
        with urlopen(self._request(bucket, name, "GET"), timeout=300) as response:
            return response.read()


def exists(client, bucket, key):
    from botocore.exceptions import ClientError
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise


def copy(jobs, source, target, bucket, dry_run):
    counts = {}
    for _endpoint, source_bucket, source_key, dest_key in jobs:
        if exists(target, bucket, dest_key):
            result = "already_in_bucket"
        else:
            readable = source.status(source_bucket, source_key)
            if readable != "ok":
                result = readable
            elif dry_run:
                result = "would_copy"
            else:
                body = source.download(source_bucket, source_key)
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

    # Credentials from `aws login` refresh through AWS's sign-in service, which
    # needs a default region even though every client below names its own.
    os.environ.setdefault("AWS_DEFAULT_REGION", args.region)
    os.environ.setdefault("AWS_REGION", args.region)
    import boto3

    jobs = plan(args.old)
    print(f"{len(jobs)} files to copy from B2.")
    target = boto3.client("s3", region_name=args.region)

    key_id, secret = os.environ.get("B2_KEY_ID", "").strip(), os.environ.get("B2_APPLICATION_KEY", "").strip()
    if not (key_id and secret):
        sys.exit("Set B2_KEY_ID and B2_APPLICATION_KEY (a read-only Backblaze application key).")
    try:
        source = B2(key_id, secret)
    except B2Error as exc:
        sys.exit(str(exc))
    print("Backblaze key accepted.")

    counts = copy(jobs, source, target, args.bucket, args.dry_run)
    print(("[dry run] " if args.dry_run else "") + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))


if __name__ == "__main__":
    main()
