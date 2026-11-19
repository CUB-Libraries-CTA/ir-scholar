"""bagit_builder.py run end to end on the standard test zips (see helpers.make_standard_zips)."""

import shutil
from pathlib import Path

import bagit
import pytest

from helpers import (
    PDF,
    PDF_NAME,
    RECORD_NAME,
    STANDARD_DUPLICATE,
    STANDARD_EXPECTED,
    STANDARD_NOT_EXTRACTED,
    bagit_builder,
    copy_zips,
    files_on_disk,
    leftover_folders,
    log_name,
    make_existing_bag_zip,
    make_zip,
    outcome_for_dir,
    output_dir_name,
    read_metadata_row,
    record,
    renamed_zips,
    run_builder,
    summary,
    where_folders_went,
    zip_member_files,
)

PROCESSED = output_dir_name("created")


@pytest.fixture(scope="module")
def standard_run(standard_zips, tmp_path_factory):
    """The standard zips after one bagit_builder run (default --jobs), and its result."""
    parent = copy_zips(standard_zips, tmp_path_factory.mktemp("standard_run") / "run")
    return parent, run_builder(parent)


# --- Where everything ends up -----------------------------------------------------------


@pytest.mark.parametrize("folder, outcome", sorted(STANDARD_EXPECTED.items()))
def test_folder_goes_to_its_outcome_directory(standard_run, folder, outcome):
    parent, _ = standard_run
    expected = output_dir_name(outcome)
    assert where_folders_went(parent).get(folder) == expected, f"{folder} should be in {expected}/"


def test_duplicate_folder_name_second_zip_fails(standard_run):
    """'dup name.zip' and 'dup_name.zip' share the folder dup_name: the first is bagged,
    the second fails because that bag is already done."""
    parent, _ = standard_run
    first, second = STANDARD_DUPLICATE
    assert (parent / PROCESSED / "dup_name").is_dir()
    assert (parent / output_dir_name("failed") / "dup_name").is_dir()
    assert (parent / f"{first}.processed").exists() and (parent / second).exists()


def test_zip_named_like_an_output_directory_is_not_extracted(standard_run):
    parent, result = standard_run
    for zip_name, outcome in STANDARD_NOT_EXTRACTED.items():
        log = parent / f"{zip_name}.{log_name(outcome)}"
        assert "is the name of one of this script's output directories" in log.read_text()
        assert (parent / zip_name).exists()
        assert f"{outcome.upper()}: {parent / zip_name}" in result.stdout


def test_summary_and_exit_code(standard_run):
    parent, result = standard_run
    expected = {outcome: 0 for outcome in ("created", "validated", "failed", "rejected", "unaccepted", "skipped")}
    for outcome in STANDARD_EXPECTED.values():
        expected[outcome] += 1
    for outcome in STANDARD_NOT_EXTRACTED.values():
        expected[outcome] += 1
    # The duplicate pair: the first is bagged, the second fails.
    expected["created"] += 1
    expected["failed"] += 1
    assert summary(result.stdout) == expected
    # Failed and rejected zips make the exit code 1.
    assert result.returncode == 1
    assert "Traceback" not in result.stdout


def test_no_folders_or_temporary_directories_left_behind(standard_run):
    parent, _ = standard_run
    assert leftover_folders(parent) == []


def test_each_folder_has_the_log_for_its_outcome(standard_run):
    parent, _ = standard_run
    for folder, directory in where_folders_went(parent).items():
        if folder == "dup_name":
            continue  # in two directories; see test_duplicate_folder_name_second_zip_fails
        logs = sorted(path.name for path in (parent / directory / folder).glob("bagit_*.log"))
        expected_log = log_name(outcome_for_dir(directory))
        assert logs == ([expected_log] if expected_log else []), f"{directory}/{folder}"


def test_only_processed_zips_are_renamed(standard_run):
    """Exactly the zips whose folder is in processed/ were renamed, so the second of the
    duplicate pair and the zip that wasn't extracted keep their names."""
    parent, _ = standard_run
    renamed_folders = sorted(bagit_builder.bulkrax_safe_name(stem) for stem in renamed_zips(parent))
    assert renamed_folders == sorted(path.name for path in (parent / PROCESSED).iterdir())


def test_reasons_are_logged(standard_run):
    parent, _ = standard_run
    reasons = {
        ("rejected", "no_repository"): "KeyError: 'DISS_repository'",
        ("rejected", "broken"): "ExpatError",
        ("rejected", "two_records"): "several ProQuest records",
        ("rejected", "missing_pdf"): f"missing from the package: {PDF_NAME}",
        ("rejected", "conflicting_csv"): "would be read by the Bulkrax importer",
        ("rejected", "nothing"): "no ProQuest record",
        ("rejected", "old_csv_only"): "no ProQuest record",
        ("rejected", "no_issue_date"): "AttributeError",
        ("unaccepted", "not_accepted"): "is not accepted",
        ("unaccepted", "no_acceptance"): "is not accepted",
        ("unaccepted", "empty_repository"): "is not accepted",
        ("unaccepted", "acceptance_attribute"): "is not accepted",
        ("failed", "etdadmin_upload_129"): "can't rename a;b.pdf to a_b.pdf",
        ("failed", "dup_name"): "already exists in",
    }
    for (outcome, folder), reason in reasons.items():
        log = parent / output_dir_name(outcome) / folder / log_name(outcome)
        assert reason in log.read_text(encoding="utf-8"), f"{log}"


# --- Folders that weren't bagged keep the zip's contents --------------------------------


@pytest.mark.parametrize(
    "folder, outcome",
    [(f, o) for f, o in sorted(STANDARD_EXPECTED.items()) if o in ("failed", "rejected", "unaccepted")],
)
def test_unbagged_folder_holds_the_zips_original_files(standard_run, standard_zips, folder, outcome):
    parent, _ = standard_run
    on_disk = [
        name for name in files_on_disk(parent / output_dir_name(outcome) / folder)
        if not name.startswith("bagit_")
    ]
    zip_name = "dup_name.zip" if folder == "dup_name" else f"{folder}.zip"
    assert on_disk == zip_member_files(standard_zips / zip_name)


# --- The bags ---------------------------------------------------------------------------


def test_processed_bags_are_valid(standard_run):
    parent, _ = standard_run
    bags = sorted((parent / PROCESSED).iterdir())
    assert bags
    for bag in bags:
        bagit.Bag(str(bag)).validate()


def test_bag_layout(standard_run):
    """The record and metadata.csv are tag files in the bag root; data/ holds only what
    Bulkrax should attach to the work."""
    parent, _ = standard_run
    bag = parent / PROCESSED / "etdadmin_upload_123"
    root_files = sorted(path.name for path in bag.iterdir() if path.is_file())
    assert RECORD_NAME in root_files
    assert bagit_builder.METADATA_CSV_NAME in root_files
    assert files_on_disk(bag / "data") == ["Doe_supp/data_table.csv", PDF_NAME]
    # The tag manifest covers the record and metadata.csv.
    tagmanifest = (bag / "tagmanifest-sha256.txt").read_text()
    assert RECORD_NAME in tagmanifest and bagit_builder.METADATA_CSV_NAME in tagmanifest


def test_existing_metadata_csv_is_moved_to_the_bag_root(standard_run):
    parent, _ = standard_run
    bag = parent / PROCESSED / "has_metadata_csv"
    assert (bag / bagit_builder.METADATA_CSV_NAME).is_file()
    assert files_on_disk(bag / "data") == ["a.pdf"]


def test_stray_xml_is_an_ordinary_file(standard_run):
    parent, _ = standard_run
    assert "notes.xml" in files_on_disk(parent / PROCESSED / "stray_xml" / "data")


def test_single_top_level_folder_is_unwrapped(standard_run):
    parent, _ = standard_run
    bag = parent / PROCESSED / "etdadmin_upload_124"
    assert (bag / RECORD_NAME).is_file() and (bag / "data" / PDF_NAME).is_file()


def test_existing_bag_is_only_validated(standard_run):
    parent, result = standard_run
    bag = parent / PROCESSED / "existing_bag"
    assert files_on_disk(bag / "data") == ["sub/x.pdf"]
    assert not (bag / bagit_builder.METADATA_CSV_NAME).exists()
    assert "(already a bag)" in result.stdout


# --- metadata.csv -----------------------------------------------------------------------


def test_metadata_csv_columns_and_values(standard_run):
    parent, _ = standard_run
    row = read_metadata_row(parent / PROCESSED / "etdadmin_upload_123")
    assert row["source_identifier"] == RECORD_NAME.removesuffix(".xml")
    assert row["model"] == "GraduateThesisOrDissertation"
    assert row["title"] == "Café α-helix"
    assert row["resource_type"] == "Dissertation"
    assert (row["creator_1"], row["creator_2"]) == ("Doe, Jane Q", "Roe, Rick ")
    assert (row["keyword_1"], row["keyword_2"]) == ("quarks", "gluons")
    assert [row[f"subject_{n}"] for n in (1, 2, 3)] == ["quarks", "gluons", "Physics"]
    assert row["date_issued"] == "2024-05-02" and row["graduation_year"] == "2024"
    # Without --upload-xml-to-s3, replaces is the file number from the zip name.
    assert row["replaces"] == "123"
    # admin_set_id is left to the importer; files come from the bag, not a column.
    assert "admin_set_id" not in row and "files" not in row and "file" not in row


def test_embargo_columns(standard_run):
    parent, _ = standard_run
    embargoed = read_metadata_row(parent / PROCESSED / "etdadmin_upload_123")
    assert embargoed["visibility"] == "embargo"
    assert embargoed["embargo_release_date"] == "2099-05-01"
    assert embargoed["visibility_during_embargo"] == "restricted"
    assert embargoed["visibility_after_embargo"] == "open"
    open_access = read_metadata_row(parent / PROCESSED / "etdadmin_upload_124")
    assert open_access["visibility"] == "open"
    assert not any("embargo" in column for column in open_access)


def test_undeclared_latin1_record_is_decoded(standard_run):
    parent, _ = standard_run
    assert read_metadata_row(parent / PROCESSED / "etdadmin_upload_125")["title"] == "Café"


def test_attachment_next_to_the_pdf_is_bagged(standard_run):
    """transform() would look for attachments in a subfolder; bagging doesn't need it to."""
    parent, _ = standard_run
    assert files_on_disk(parent / PROCESSED / "toplevel_attachment" / "data") == [PDF_NAME, "data.csv"]


def test_file_number_comes_from_the_zip_name(standard_run):
    """As in main.py, not from the folder name, whose space became '_'."""
    parent, _ = standard_run
    assert read_metadata_row(parent / PROCESSED / "etdadmin_upload_77_(1)")["replaces"] == "77 (1)"


# --- Names Bulkrax can't handle ---------------------------------------------------------


def test_unsafe_characters_are_replaced(standard_run):
    parent, result = standard_run
    bag = parent / PROCESSED / "etd_upload_128_x_y"
    assert files_on_disk(bag / "data") == [
        PDF_NAME,
        "Supp__Files/data_table.csv",
        "Supp__Files/data_table_v2.csv",
        "Supp__Files/time_10_30.txt",
    ]
    assert "Renamed " in result.stdout


def test_rename_clash_fails_and_restores_the_original_names(standard_run):
    parent, _ = standard_run
    folder = parent / output_dir_name("failed") / "etdadmin_upload_129"
    assert {"a;b.pdf", "a_b.pdf"} <= set(files_on_disk(folder))
    assert (folder / "a;b.pdf").read_bytes() == b"one"


# --- Runs that depend on the order or on earlier runs -----------------------------------


def test_jobs_1_and_4_give_the_same_result(standard_run, standard_zips, tmp_path):
    parent_4, result_4 = standard_run
    parent_1 = copy_zips(standard_zips, tmp_path / "jobs1")
    result_1 = run_builder(parent_1, "--jobs", "1")
    assert summary(result_1.stdout) == summary(result_4.stdout)
    assert files_on_disk(parent_1, include_logs=False) == files_on_disk(parent_4, include_logs=False)
    log_names = lambda parent: sorted(p for p in files_on_disk(parent) if p.endswith(".log"))  # noqa: E731
    assert log_names(parent_1) == log_names(parent_4)


def test_rerun_reports_earlier_outcomes_without_extracting(standard_run, tmp_path):
    # A second run over a copy of the standard run's results.
    first_parent, first = standard_run
    parent = tmp_path / "rerun"
    shutil.copytree(first_parent, parent)
    before = files_on_disk(parent, include_logs=False)
    second = run_builder(parent)
    assert "Extracting" not in second.stdout
    assert files_on_disk(parent, include_logs=False) == before
    counts = summary(second.stdout)
    assert counts["created"] == counts["validated"] == 0
    for outcome in ("failed", "rejected", "unaccepted"):
        assert counts[outcome] == summary(first.stdout)[outcome]
    # Each zip whose folder is in an output directory gets that outcome's log next to it.
    for folder, directory in where_folders_went(parent).items():
        if directory == PROCESSED:
            continue
        zip_log = log_name(outcome_for_dir(directory))
        zip_logs = [
            path for path in parent.glob(f"*.zip.{zip_log}")
            if bagit_builder.folder_name(Path(path.name.removesuffix(f".{zip_log}"))) == folder
        ]
        assert zip_logs, f"no {zip_log} next to the zip of {directory}/{folder}"
        assert "already exists from an earlier run" in zip_logs[0].read_text()
    assert second.returncode == 1


def test_retry_after_deleting_the_folder(tmp_path):
    make_zip(tmp_path, "not_accepted", {RECORD_NAME: record(acceptance="0"), PDF_NAME: PDF})
    run_builder(tmp_path)
    unaccepted = tmp_path / output_dir_name("unaccepted")
    run_builder(tmp_path)  # leaves not_accepted.zip.bagit_unaccepted.log next to the zip
    stale_log = tmp_path / f"not_accepted.zip.{log_name('unaccepted')}"
    assert stale_log.exists()
    shutil.rmtree(unaccepted / "not_accepted")
    result = run_builder(tmp_path)
    assert f"Extracting {tmp_path / 'not_accepted.zip'}" in result.stdout
    assert (unaccepted / "not_accepted" / log_name("unaccepted")).exists()
    assert not stale_log.exists()


def test_exit_code_is_0_with_only_processed_and_unaccepted(tmp_path):
    make_zip(tmp_path, "good", {RECORD_NAME: record(), PDF_NAME: PDF})
    make_zip(tmp_path, "declined", {RECORD_NAME: record(acceptance="0"), PDF_NAME: PDF})
    result = run_builder(tmp_path, "--jobs", "1")
    assert summary(result.stdout)["unaccepted"] == 1
    assert result.returncode == 0


def test_existing_bag_already_processed_is_skipped(tmp_path):
    make_existing_bag_zip(tmp_path, "already_done")
    (tmp_path / PROCESSED / "already_done").mkdir(parents=True)
    result = run_builder(tmp_path)
    skipped = tmp_path / output_dir_name("skipped") / "already_done"
    assert (skipped / log_name("skipped")).exists()
    assert summary(result.stdout)["skipped"] == 1
    assert result.returncode == 0


def test_leftover_folder_blocks_its_zip(tmp_path):
    make_zip(tmp_path, "etdadmin_upload_5", {RECORD_NAME: record(), PDF_NAME: PDF})
    (tmp_path / "etdadmin_upload_5").mkdir()
    result = run_builder(tmp_path)
    log = tmp_path / f"etdadmin_upload_5.zip.{log_name('failed')}"
    assert "perhaps left by an earlier run" in log.read_text()
    assert result.returncode == 1


def test_parent_directory_that_is_a_bag_is_refused(tmp_path):
    (tmp_path / "bagit.txt").write_text("BagIt-Version: 0.97\n")
    result = run_builder(tmp_path)
    assert result.returncode != 0 and "is itself a bag" in result.stdout
