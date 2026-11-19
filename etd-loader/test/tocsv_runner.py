"""Call tocsv() the way main.py does, with its side effects stubbed, and print a JSON
record of everything it did (used by test_main_py.py).

Usage: python tocsv_runner.py CODE_DIR CASE_DIR S3_MODE RAKE_STATUS

CODE_DIR holds the tocsv.py and xmltos3.py to run. CASE_DIR holds etd_123/, the
extracted package, as main.py would pass it for a zip named etd_123.zip. S3_MODE is
'ok' or 'clienterror' (the bucket lookup fails); RAKE_STATUS is what `rake` returns.
Nothing is written outside CASE_DIR and nothing is sent anywhere.
"""

import contextlib
import io
import json
import logging
import os
import sys

code_dir, case_dir, s3_mode, rake_status = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
sys.path.insert(0, code_dir)
events = []


class RecordingHandler(logging.Handler):
    def emit(self, record):
        events.append(("log", record.levelname, record.getMessage()))


def fake_file_handler(path, *args, **kwargs):
    events.append(("open_log_file", os.path.dirname(path)))
    return RecordingHandler()


logging.FileHandler = fake_file_handler
logging.error = lambda message, *args, **kwargs: events.append(("root_log_error", str(message)))

import boto3  # noqa: E402
from botocore.exceptions import ClientError  # noqa: E402


class StubS3:
    def upload_file(self, source, bucket, key):
        events.append(("s3_upload", os.path.basename(source), bucket, key))

    def get_bucket_location(self, Bucket):
        if s3_mode == "clienterror":
            raise ClientError({"Error": {"Code": "AccessDenied", "Message": "denied"}}, "GetBucketLocation")
        return {"LocationConstraint": "us-west-2"}


boto3.client = lambda *args, **kwargs: StubS3()
os.chdir = lambda path: events.append(("chdir", path))


def fake_system(command):
    events.append(("system", command))
    return rake_status if command.startswith("rake") else 0


os.system = fake_system

from tocsv import tocsv  # noqa: E402

output = io.StringIO()
with contextlib.redirect_stdout(output):
    tocsv(case_dir + "/etd_123", "123", "etd_123", case_dir + "/etd_123.zip",
          case_dir + "/rejected", case_dir + "/unaccepted")
csv_path = os.path.join(case_dir, "etd_123", "dissertation_dataload.csv")
csv_text = open(csv_path, encoding="utf-8").read() if os.path.exists(csv_path) else None
print(json.dumps({
    "events": [[str(part).replace(case_dir, "<CASE>") for part in event] for event in events],
    "stdout": output.getvalue().replace(case_dir, "<CASE>"),
    "csv": csv_text,
}))
