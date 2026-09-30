import csv
import mimetypes
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import models


class Command(BaseCommand):
    """Copies every uploaded file the database refers to from local disk to S3.

    Objects keep the exact relative path stored in the FileField (e.g.
    compliance/documents/2026/09/licence.pdf) as their S3 key, so existing
    records resolve unchanged once AWS_STORAGE_BUCKET_NAME points at the bucket.

    Only reads the database. Never overwrites an object that already exists in
    the bucket, so it is safe to re-run. Records whose file is missing from
    local disk are reported, not fixed: those files are already gone.

    Credentials come from boto3's normal chain (env vars, profile or role).
    """

    help = "Copy uploaded files referenced in the database from MEDIA_ROOT to an S3 bucket."

    def add_arguments(self, parser):
        parser.add_argument("--bucket", required=True)
        parser.add_argument("--region", default="us-east-1")
        parser.add_argument("--source", default=str(settings.MEDIA_ROOT), help="Local media directory (default: MEDIA_ROOT).")
        parser.add_argument("--dry-run", action="store_true", help="Report what would be copied without uploading.")
        parser.add_argument("--report", help="Write a CSV row per file (model, pk, field, key, result) to this path.")

    def handle(self, *args, **options):
        source = Path(options["source"])
        bucket = options["bucket"]
        dry_run = options["dry_run"]
        s3 = boto3.client("s3", region_name=options["region"])

        counts = {"copied": 0, "would_copy": 0, "already_in_bucket": 0, "missing_locally": 0}
        rows = []

        for model, field in self._file_fields():
            names = (
                model._default_manager.exclude(**{field.name: ""})
                .exclude(**{f"{field.name}__isnull": True})
                .values_list("pk", field.name)
            )
            for pk, key in names.iterator():
                local = source / key
                if not local.is_file():
                    result = "missing_locally"
                elif self._exists(s3, bucket, key):
                    result = "already_in_bucket"
                elif dry_run:
                    result = "would_copy"
                else:
                    content_type = mimetypes.guess_type(key)[0] or "application/octet-stream"
                    s3.upload_file(str(local), bucket, key, ExtraArgs={"ContentType": content_type})
                    result = "copied"
                counts[result] += 1
                rows.append((model._meta.label, str(pk), field.name, key, result))
                if result == "missing_locally":
                    self.stderr.write(f"MISSING {model._meta.label} {pk} {field.name}: {key}")

        if options["report"]:
            with open(options["report"], "w", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["model", "pk", "field", "key", "result"])
                writer.writerows(rows)

        prefix = "[dry run] " if dry_run else ""
        self.stdout.write(prefix + " ".join(f"{name}={value}" for name, value in counts.items()))

    @staticmethod
    def _file_fields():
        for model in apps.get_models():
            for field in model._meta.concrete_fields:
                if isinstance(field, models.FileField):
                    yield model, field

    @staticmethod
    def _exists(s3, bucket, key):
        try:
            s3.head_object(Bucket=bucket, Key=key)
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise
