"""--upload-xml-to-s3 and --test, with S3 stubbed (see s3_stub.py). Nothing is sent to
AWS: the client is replaced and every credential source is blanked."""

import bagit
import pytest

from helpers import (
    PDF,
    PDF_NAME,
    RECORD_NAME,
    TEST_DIR,
    blank_aws_environment,
    log_name,
    make_zip,
    output_dir_name,
    read_metadata_row,
    record,
    run_builder,
    summary,
)
from xmltos3 import key_prefix, test_key_prefix

STUB = TEST_DIR / "s3_stub.py"
PROCESSED = output_dir_name("created")


@pytest.fixture
def zips(tmp_path):
    """Two good records, and one that is accepted but can't be converted."""
    parent = tmp_path / "run"
    parent.mkdir()
    for number in (301, 302):
        make_zip(parent, f"etdadmin_upload_{number}", {RECORD_NAME: record(), PDF_NAME: PDF})
    make_zip(parent, "etdadmin_upload_303", {RECORD_NAME: record(dates=False), PDF_NAME: PDF})
    return parent


def run_stubbed(parent, mode, *args, jobs="1"):
    upload_log = parent.parent / "uploads.log"
    upload_log.touch()
    env = blank_aws_environment(STUB_MODE=mode, STUB_LOG=str(upload_log))
    result = run_builder(parent, "--upload-xml-to-s3", "--jobs", jobs, *args, runner=STUB, env=env)
    return result, upload_log.read_text().splitlines()


def replaces(parent, folder):
    return read_metadata_row(parent / PROCESSED / folder)["replaces"]


@pytest.mark.parametrize("jobs", ["1", "3"])
def test_upload_puts_the_url_in_replaces(zips, jobs):
    result, uploads = run_stubbed(zips, "ok", jobs=jobs)
    key = f"{key_prefix}/{RECORD_NAME}"
    # Only the two records that convert are uploaded, also from --jobs workers.
    assert uploads == [f"cubl-static/{key}"] * 2
    for number in (301, 302):
        assert replaces(zips, f"etdadmin_upload_{number}") == (
            f"{number}|https://s3-us-west-2.amazonaws.com/cubl-static/{key}"
        )
        # xmltos3's message is prefixed with the zip it is about.
        assert f"etdadmin_upload_{number}.zip: Upload XML file to S3: {RECORD_NAME}" in result.stdout


def test_record_that_cant_be_converted_is_rejected_and_not_uploaded(zips):
    # etdadmin_upload_303 has no issue date.
    result, uploads = run_stubbed(zips, "ok")
    assert (zips / output_dir_name("rejected") / "etdadmin_upload_303").is_dir()
    assert len(uploads) == 2
    assert "etdadmin_upload_303.zip: Upload" not in result.stdout


def test_test_flag_uploads_under_the_test_prefix(zips):
    result, uploads = run_stubbed(zips, "ok", "--test")
    key = f"{test_key_prefix}/{RECORD_NAME}"
    assert uploads == [f"cubl-static/{key}"] * 2
    assert replaces(zips, "etdadmin_upload_301").endswith(f"/cubl-static/{key}")


def test_failed_bucket_lookup_uses_the_known_region(zips):
    result, uploads = run_stubbed(zips, "lookupfail")
    assert summary(result.stdout)["created"] == 2
    assert replaces(zips, "etdadmin_upload_301") == (
        f"301|https://s3-us-west-2.amazonaws.com/cubl-static/{key_prefix}/{RECORD_NAME}"
    )
    assert "etdadmin_upload_301.zip: Bucket lookup failed" in result.stdout


def test_failed_upload_goes_to_failed(zips):
    result, uploads = run_stubbed(zips, "uploadfail")
    counts = summary(result.stdout)
    assert (counts["created"], counts["failed"], counts["rejected"]) == (0, 2, 1)
    for number in (301, 302):
        log = zips / output_dir_name("failed") / f"etdadmin_upload_{number}" / log_name("failed")
        assert "UploadError" in log.read_text()
    assert result.returncode == 1


def test_uploaded_bag_is_still_valid(zips):
    """The upload happens after bagging, then metadata.csv and the tag manifests are
    rewritten; the bag must still validate."""
    run_stubbed(zips, "ok")
    for number in (301, 302):
        bagit.Bag(str(zips / PROCESSED / f"etdadmin_upload_{number}")).validate()


def test_folder_that_fails_bagging_is_not_uploaded(tmp_path):
    """Renaming 'a;b.pdf' to 'a_b.pdf' would overwrite another file, so bagging fails:
    the record must not have been uploaded."""
    parent = tmp_path / "run"
    parent.mkdir()
    make_zip(parent, "etdadmin_upload_401", {
        RECORD_NAME: record(), PDF_NAME: PDF, "a;b.pdf": b"one", "a_b.pdf": b"two",
    })
    result, uploads = run_stubbed(parent, "ok")
    assert (parent / output_dir_name("failed") / "etdadmin_upload_401").is_dir()
    assert uploads == []


def test_test_flag_needs_the_upload_flag(tmp_path):
    result = run_builder(tmp_path, "--test")
    assert result.returncode == 2
    assert "--test only applies with --upload-xml-to-s3" in result.stdout
