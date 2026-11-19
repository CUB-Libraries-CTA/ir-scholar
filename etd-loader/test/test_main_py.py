"""tocsv() (which main.py runs) behaves as the original loader did.

The original tocsv.py and xmltos3.py are read from commit 8792734 with git, plus the
`is` => `==` string comparison fixes made before bagit_builder was added, and run side
by side with the current ones through tocsv_runner.py. Everything tocsv() does is
compared: log messages, rake and mv commands, S3 calls, printed output and the CSV.
The intended differences: XML declared as UTF-8 is now decoded as UTF-8, and records
with an empty DISS_repository or an attribute on DISS_acceptance are now unaccepted.
"""

import json
import subprocess
import sys
import zipfile

import pytest

from helpers import ETD_LOADER, PDF, PDF_NAME, RECORD_NAME, TEST_DIR, record

ORIGINAL_COMMIT = "8792734"
RUNNER = TEST_DIR / "tocsv_runner.py"
IS_FIXES = [("is not '0'", "!= '0'"), ("is '0'", "== '0'"), ("is ''", "== ''")]


@pytest.fixture(scope="module")
def original_code(tmp_path_factory):
    """Directory holding the original tocsv.py and xmltos3.py."""
    directory = tmp_path_factory.mktemp("original_code")
    for name in ("tocsv.py", "xmltos3.py"):
        shown = subprocess.run(
            ["git", "show", f"{ORIGINAL_COMMIT}:etd-loader/{name}"],
            cwd=ETD_LOADER, capture_output=True, text=True,
        )
        if shown.returncode != 0:
            pytest.skip(f"can't read {name} from commit {ORIGINAL_COMMIT}: {shown.stderr.strip()}")
        source = shown.stdout
        for old, new in IS_FIXES:
            source = source.replace(old, new)
        (directory / name).write_text(source, encoding="utf-8")
    return directory


def run_tocsv(code_dir, case_dir, s3_mode="ok", rake_status=0):
    result = subprocess.run(
        [sys.executable, str(RUNNER), str(code_dir), str(case_dir), s3_mode, str(rake_status)],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    run = json.loads(result.stdout)
    # When the log file is opened differs on purpose (on import before, now when tocsv()
    # runs), so only what is logged and done is compared.
    run["events"] = [event for event in run["events"] if event[0] != "open_log_file"]
    return run, result.stderr


def make_case(directory, files):
    package = directory / "etd_123"
    package.mkdir(parents=True)
    for name, data in files.items():
        (package / name).parent.mkdir(parents=True, exist_ok=True)
        (package / name).write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    return directory


ASCII = dict(title="Cafe")
LATIN1 = '<?xml version="1.0" encoding="ISO-8859-1"?>\n'
CASES = {
    # name: (package files, s3 mode, rake status)
    "accepted": ({RECORD_NAME: record(**ASCII), PDF_NAME: PDF}, "ok", 0),
    "rake_fails": ({RECORD_NAME: record(**ASCII), PDF_NAME: PDF}, "ok", 1),
    "embargoed": ({RECORD_NAME: record(**ASCII, embargo="2"), PDF_NAME: PDF}, "ok", 0),
    "not_accepted": ({RECORD_NAME: record(**ASCII, acceptance="0"), PDF_NAME: PDF}, "ok", 0),
    "no_acceptance": ({RECORD_NAME: record(**ASCII, acceptance=None), PDF_NAME: PDF}, "ok", 0),
    "no_repository": ({RECORD_NAME: record(**ASCII, repository=False), PDF_NAME: PDF}, "ok", 0),
    "broken": ({RECORD_NAME: "<DISS_submission>", PDF_NAME: PDF}, "ok", 0),
    "two_records": ({"a_DATA.xml": record(**ASCII), "b_DATA.xml": record(**ASCII), PDF_NAME: PDF}, "ok", 0),
    "bucket_lookup_fails": ({RECORD_NAME: record(**ASCII), PDF_NAME: PDF}, "clienterror", 0),
    "attachments": ({
        RECORD_NAME: record(**ASCII, attachments=("data.csv",)), PDF_NAME: PDF, "supp/data.csv": "a\n",
    }, "ok", 0),
    "latin1_declared": ({
        RECORD_NAME: record(title="Café", declaration=LATIN1).encode("latin-1"), PDF_NAME: PDF,
    }, "ok", 0),
    "latin1_undeclared": ({
        RECORD_NAME: record(title="Café", declaration="").encode("latin-1"), PDF_NAME: PDF,
    }, "ok", 0),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_tocsv_behaves_as_before(original_code, tmp_path, name):
    files, s3_mode, rake_status = CASES[name]
    original, original_stderr = run_tocsv(original_code, make_case(tmp_path / "original", files), s3_mode, rake_status)
    current, current_stderr = run_tocsv(ETD_LOADER, make_case(tmp_path / "current", files), s3_mode, rake_status)
    assert current == original
    assert current_stderr == original_stderr


def test_utf8_record_is_now_decoded_correctly(original_code, tmp_path):
    files = {RECORD_NAME: record(title="Café α-helix"), PDF_NAME: PDF}
    original, _ = run_tocsv(original_code, make_case(tmp_path / "original", files))
    current, _ = run_tocsv(ETD_LOADER, make_case(tmp_path / "current", files))
    assert "CafÃ© Î±-helix" in original["csv"]  # read as ISO-8859-1 before
    assert "Café α-helix" in current["csv"]
    assert current["events"] == original["events"]


def moved_to(run):
    """Where tocsv() moved the zip, from the mv commands it ran."""
    return [event[1].split()[-1] for event in run["events"]
            if event[0] == "system" and event[1].startswith("mv ")]


@pytest.mark.parametrize("name, xml, original_destination", [
    # Empty <DISS_repository/>: an AttributeError sent it to rejected before.
    ("empty_repository", record(**ASCII, acceptance=None, dates=False), "<CASE>/rejected"),
    # DISS_acceptance with an attribute: compared as a dict, it counted as accepted.
    ("acceptance_attribute", record(**ASCII).replace(
        "<DISS_acceptance>1</DISS_acceptance>", '<DISS_acceptance type="x">0</DISS_acceptance>'
    ), "<CASE>/etd_123.zip.processed"),
])
def test_malformed_acceptance_is_now_unaccepted(original_code, tmp_path, name, xml, original_destination):
    files = {RECORD_NAME: xml, PDF_NAME: PDF}
    original, _ = run_tocsv(original_code, make_case(tmp_path / "original", files))
    current, _ = run_tocsv(ETD_LOADER, make_case(tmp_path / "current", files))
    assert moved_to(original) == [original_destination]
    assert moved_to(current) == ["<CASE>/unaccepted"]


@pytest.mark.slow
def test_real_etds_behave_as_before(original_code, real_etds, tmp_path):
    for zip_path in sorted(real_etds.glob("*.zip")):
        cases = {}
        for version in ("original", "current"):
            case = tmp_path / zip_path.stem / version
            # Extracted the way main.py does.
            with zipfile.ZipFile(zip_path) as archive:
                archive.extractall(case / "etd_123")
            cases[version] = case
        original, _ = run_tocsv(original_code, cases["original"])
        current, _ = run_tocsv(ETD_LOADER, cases["current"])
        assert current == original, zip_path.name
