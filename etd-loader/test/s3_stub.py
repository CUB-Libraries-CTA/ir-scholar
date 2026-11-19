"""Run bagit_builder.py with boto3's S3 client replaced by a stub (used by test_s3.py).

Usage: python s3_stub.py PARENT_DIR [bagit_builder options]

Environment:
  STUB_MODE  'ok' (uploads succeed), 'lookupfail' (the bucket lookup after the upload
             fails with a ClientError) or 'uploadfail' (the upload fails the way boto3
             reports it, with S3UploadFailedError)
  STUB_LOG   file each attempted upload is appended to, as "<bucket>/<key>"

The stub is installed when this file is imported, so --jobs worker processes (which
import it as __mp_main__) get it too. Run it with every AWS credential source blanked
(see helpers.blank_aws_environment) so a missing stub can't reach a real bucket.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import boto3  # noqa: E402
from boto3.exceptions import S3UploadFailedError  # noqa: E402
from botocore.exceptions import ClientError  # noqa: E402

MODE = os.environ["STUB_MODE"]
LOG = os.environ["STUB_LOG"]


class StubS3:
    def upload_file(self, source, bucket, key):
        with open(LOG, "a", encoding="utf-8") as log:
            log.write(f"{bucket}/{key}\n")
        if MODE == "uploadfail":
            raise S3UploadFailedError(
                "Failed to upload: An error occurred (AccessDenied) when calling the "
                "PutObject operation: Access Denied"
            )

    def get_bucket_location(self, Bucket):
        if MODE == "lookupfail":
            raise ClientError(
                {"Error": {"Code": "AccessDenied", "Message": "Access Denied"}}, "GetBucketLocation"
            )
        return {"LocationConstraint": "us-west-2"}


boto3.client = lambda *args, **kwargs: StubS3()

if __name__ == "__main__":
    import bagit_builder

    sys.exit(bagit_builder.main())
