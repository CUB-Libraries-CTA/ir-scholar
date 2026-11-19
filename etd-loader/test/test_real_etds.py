"""bagit_builder.py on the real ETD zips in etd-loader/etds/ (skipped if there are none).

The set of zips changes, so each zip's expected outcome is worked out from its own
ProQuest record with a few independent rules (see expected_outcome), not hardcoded.
"""

import zipfile

import bagit
import pytest
import xmltodict

from helpers import (
    assert_counts_match_disk,
    bagit_builder,
    copy_zips,
    files_on_disk,
    output_dir_name,
    read_metadata_row,
    run_builder,
    summary,
    where_folders_went,
)

pytestmark = pytest.mark.slow
PROCESSED = output_dir_name("created")


def read_record(zip_path):
    """Return (record name, parsed DISS_submission or None if unreadable, names of the
    files in the zip), or (None, None, names) if the zip has no single record."""
    with zipfile.ZipFile(zip_path) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        records = [name for name in names if name.endswith(bagit_builder.RECORD_SUFFIX)]
        if len(records) != 1:
            return None, None, names
        try:
            parsed = xmltodict.parse(archive.read(records[0]), process_namespaces=True)
            return records[0], parsed["DISS_submission"], names
        except Exception:
            return records[0], None, names


def listed_files(submission):
    """Return the names of the files a record lists: the PDF, then any attachments."""
    content = submission["DISS_content"]
    attachments = content.get("DISS_attachment") or []
    attachments = attachments if isinstance(attachments, list) else [attachments]
    return [content["DISS_binary"]["#text"]] + [a["DISS_file_name"] for a in attachments]


def expected_outcome(zip_path):
    """Where the zip's folder should end up, judged from its record, and why."""
    record_name, submission, names = read_record(zip_path)
    if record_name is None:
        return "rejected", "no single *_DATA.xml record"
    if submission is None:
        return "rejected", "the record can't be parsed"
    repository = submission.get("DISS_repository")
    if not isinstance(repository, dict):
        return "rejected", "no DISS_repository"
    if repository.get("DISS_acceptance") in (None, "0"):
        return "unaccepted", f"DISS_acceptance is {repository.get('DISS_acceptance')!r}"
    present = {name.rsplit("/", 1)[-1] for name in names}
    missing = [name for name in listed_files(submission) if name not in present]
    if missing:
        return "rejected", f"listed files missing: {missing}"
    return "created", "accepted record with all its files"


@pytest.fixture(scope="module")
def real_run(real_etds, tmp_path_factory):
    parent = copy_zips(real_etds, tmp_path_factory.mktemp("real_run") / "run")
    return parent, run_builder(parent)


def test_each_etd_goes_where_its_record_says(real_etds, real_run):
    parent, result = real_run
    went = where_folders_went(parent)
    wrong = []
    for zip_path in sorted(real_etds.glob("*.zip")):
        outcome, why = expected_outcome(zip_path)
        folder = bagit_builder.folder_name(zip_path)
        actual = went.get(folder)
        if actual != output_dir_name(outcome):
            reason = ""
            if actual and actual != PROCESSED:
                logs = list((parent / actual / folder).glob("bagit_*.log"))
                reason = logs[0].read_text(encoding="utf-8").strip().splitlines()[-1] if logs else ""
            wrong.append(f"{zip_path.name}: expected {output_dir_name(outcome)}/ ({why}), "
                         f"got {actual}/ {reason}")
    assert not wrong, "\n".join(wrong)
    assert "Traceback" not in result.stdout


def test_bags_are_valid_and_attach_the_listed_files(real_etds, real_run):
    parent, _ = real_run
    for zip_path in sorted(real_etds.glob("*.zip")):
        if expected_outcome(zip_path)[0] != "created":
            continue
        bag = parent / PROCESSED / bagit_builder.folder_name(zip_path)
        bagit.Bag(str(bag)).validate()
        record_name, submission, _ = read_record(zip_path)
        assert (bag / record_name).is_file()
        # Bulkrax attaches everything in data/: the PDF and its attachments only.
        assert len(files_on_disk(bag / "data")) == len(listed_files(submission)), bag.name
        row = read_metadata_row(bag)
        assert row["source_identifier"] == record_name.removesuffix(".xml")
        assert row["replaces"] == zip_path.stem.split("_")[-1]
        assert row["title"]


def test_summary_matches_the_directories(real_run):
    parent, result = real_run
    assert_counts_match_disk(summary(result.stdout), parent)


def test_jobs_1_gives_the_same_result(real_etds, real_run, tmp_path):
    parent_default, _ = real_run
    parent_1 = copy_zips(real_etds, tmp_path / "jobs1")
    run_builder(parent_1, "--jobs", "1")
    assert files_on_disk(parent_1, include_logs=False) == files_on_disk(parent_default, include_logs=False)
