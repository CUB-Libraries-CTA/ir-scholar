"""Shared helpers for the bagit_builder tests: building test zips, running the script and
reading its results."""

import csv
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import bagit

TEST_DIR = Path(__file__).resolve().parent
ETD_LOADER = TEST_DIR.parent
SCRIPT = ETD_LOADER / "bagit_builder.py"
ETDS_DIR = ETD_LOADER / "etds"

# The tests import the modules under test directly too.
if str(ETD_LOADER) not in sys.path:
    sys.path.insert(0, str(ETD_LOADER))

import bagit_builder  # noqa: E402

PDF = b"%PDF-1.4 test file"
RECORD_NAME = "Doe_ucolorado_0051E_123_DATA.xml"
PDF_NAME = "Doe_ucolorado_0051E_123.pdf"


# --- Building zips ----------------------------------------------------------------------


def record(
    *,
    title: str = "Café α-helix",
    acceptance: str | None = "1",
    repository: bool = True,
    embargo: str = "0",
    remove: str = "05/01/2099",
    attachments: tuple[str, ...] = (),
    binary: str = PDF_NAME,
    dates: bool = True,
    declaration: str = '<?xml version="1.0" encoding="UTF-8"?>\n',
) -> str:
    """Return a ProQuest DISS_submission record with the given variations.

    acceptance=None leaves DISS_acceptance out; repository=False leaves the whole
    DISS_repository out, as older records do. dates=False leaves out both the agreement
    decision date and DISS_dates, so the record has no issue date.
    """
    attachment_xml = "".join(
        f"<DISS_attachment><DISS_file_name>{name}</DISS_file_name></DISS_attachment>"
        for name in attachments
    )
    acceptance_xml = "" if acceptance is None else f"<DISS_acceptance>{acceptance}</DISS_acceptance>"
    decision_xml = "<DISS_agreement_decision_date>2024-05-02 10:00:00</DISS_agreement_decision_date>" if dates else ""
    repository_xml = f"<DISS_repository>{acceptance_xml}{decision_xml}</DISS_repository>" if repository else ""
    dates_xml = "<DISS_dates><DISS_accept_date>05/01/2024</DISS_accept_date></DISS_dates>" if dates else ""
    return (
        f'{declaration}<DISS_submission embargo_code="{embargo}">'
        "<DISS_authorship><DISS_author><DISS_name><DISS_surname>Doe</DISS_surname>"
        "<DISS_fname>Jane</DISS_fname><DISS_middle>Q</DISS_middle></DISS_name></DISS_author>"
        "<DISS_author><DISS_name><DISS_surname>Roe</DISS_surname><DISS_fname>Rick</DISS_fname>"
        "</DISS_name></DISS_author></DISS_authorship>"
        f'<DISS_description type="doctoral"><DISS_title>{title}</DISS_title>{dates_xml}'
        "<DISS_degree>Ph.D.</DISS_degree>"
        "<DISS_institution><DISS_inst_contact>Physics</DISS_inst_contact></DISS_institution>"
        "<DISS_categorization><DISS_category><DISS_cat_desc>Physics</DISS_cat_desc></DISS_category>"
        "<DISS_keyword>quarks, gluons</DISS_keyword></DISS_categorization></DISS_description>"
        "<DISS_content><DISS_abstract><DISS_para>Abstract</DISS_para></DISS_abstract>"
        f'<DISS_binary type="PDF">{binary}</DISS_binary>{attachment_xml}</DISS_content>'
        f'<DISS_restriction><DISS_sales_restriction code="1" remove="{remove}"/></DISS_restriction>'
        f"{repository_xml}</DISS_submission>"
    )


def make_zip(directory: Path, name: str, files: dict[str, str | bytes]) -> Path:
    """Write <name>.zip in directory holding files (path in zip => contents)."""
    path = directory / f"{name}.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for member, data in files.items():
            archive.writestr(member, data.encode("utf-8") if isinstance(data, str) else data)
    return path


def make_existing_bag_zip(directory: Path, name: str) -> Path:
    """Write <name>.zip holding a valid bag (made with bagit) with one PDF."""
    source = directory / f".{name}_source"
    (source / "sub").mkdir(parents=True)
    (source / "sub" / "x.pdf").write_bytes(PDF)
    bagit.make_bag(str(source))
    path = directory / f"{name}.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for file in sorted(source.rglob("*")):
            if file.is_file():
                archive.write(file, file.relative_to(source).as_posix())
    return path


# Expected outcome of each standard zip, keyed by the folder name it is extracted to.
STANDARD_EXPECTED = {
    "etdadmin_upload_123": "created",
    "etdadmin_upload_124": "created",
    "etdadmin_upload_125": "created",
    "has_metadata_csv": "created",
    "stray_xml": "created",
    "etd_upload_128_x_y": "created",
    "existing_bag": "validated",
    "toplevel_attachment": "created",
    "etdadmin_upload_77_(1)": "created",
    "not_accepted": "unaccepted",
    "no_acceptance": "unaccepted",
    "empty_repository": "unaccepted",
    "acceptance_attribute": "unaccepted",
    "no_repository": "rejected",
    "broken": "rejected",
    "two_records": "rejected",
    "missing_pdf": "rejected",
    "conflicting_csv": "rejected",
    "old_csv_only": "rejected",
    "nothing": "rejected",
    "no_issue_date": "rejected",
    "etdadmin_upload_129": "failed",
}
# Zips that aren't extracted: zip file name => outcome.
STANDARD_NOT_EXTRACTED = {"unaccepted.zip": "failed"}
# "dup name.zip" and "dup_name.zip" share the folder dup_name: the first (in name order)
# is bagged and the second fails because that bag is already done. Not in
# STANDARD_EXPECTED, since the folder name ends up in two directories.
STANDARD_DUPLICATE = ("dup name.zip", "dup_name.zip")


def make_standard_zips(directory: Path) -> None:
    """Write the standard set of test zips (see STANDARD_EXPECTED) into directory."""
    directory.mkdir(parents=True, exist_ok=True)
    make_zip(directory, "etdadmin_upload_123", {
        RECORD_NAME: record(attachments=("data table.csv",), embargo="2"),
        PDF_NAME: PDF,
        "Doe_supp/data table.csv": "a,b\n1,2\n",
    })
    # Everything wrapped in one top-level folder, which is unwrapped on extraction.
    make_zip(directory, "etdadmin_upload_124", {
        f"wrapper/{RECORD_NAME}": record(), f"wrapper/{PDF_NAME}": PDF,
    })
    # Undeclared ISO-8859-1: not valid UTF-8, so read as Latin-1.
    make_zip(directory, "etdadmin_upload_125", {
        RECORD_NAME: record(title="Café", declaration="").encode("latin-1"), PDF_NAME: PDF,
    })
    make_zip(directory, "has_metadata_csv", {
        "metadata.csv": "source_identifier,model,title\nx,GraduateThesisOrDissertation,T\n",
        "a.pdf": PDF,
    })
    make_zip(directory, "stray_xml", {RECORD_NAME: record(), PDF_NAME: PDF, "notes.xml": "<notes/>"})
    # Every character Bulkrax can't handle, in the zip name, a folder and file names.
    make_zip(directory, "etd:upload;128|x y", {
        RECORD_NAME: record(attachments=("data table.csv",)),
        PDF_NAME: PDF,
        "Supp: Files/data table.csv": "a,b\n",
        "Supp: Files/data;table|v2.csv": "a,b\n",
        "Supp: Files/time 10:30.txt": "t",
    })
    make_existing_bag_zip(directory, "existing_bag")
    for name in STANDARD_DUPLICATE:
        make_zip(directory, name.removesuffix(".zip"), {
            "metadata.csv": "source_identifier,model,title\nd,GraduateThesisOrDissertation,D\n",
            "f.pdf": PDF,
        })
    # An attachment next to the PDF instead of in a subfolder.
    make_zip(directory, "toplevel_attachment", {
        RECORD_NAME: record(attachments=("data.csv",)), PDF_NAME: PDF, "data.csv": "a,b\n",
    })
    # A duplicate renamed by a browser: the folder name has "_" for the space, but the
    # file number still comes from the zip's name, as in main.py.
    make_zip(directory, "etdadmin_upload_77 (1)", {RECORD_NAME: record(), PDF_NAME: PDF})
    make_zip(directory, "not_accepted", {RECORD_NAME: record(acceptance="0"), PDF_NAME: PDF})
    make_zip(directory, "no_acceptance", {RECORD_NAME: record(acceptance=None), PDF_NAME: PDF})
    # <DISS_repository></DISS_repository>, which xmltodict reads as None.
    make_zip(directory, "empty_repository", {
        RECORD_NAME: record(acceptance=None, dates=False), PDF_NAME: PDF,
    })
    make_zip(directory, "acceptance_attribute", {
        RECORD_NAME: record().replace(
            "<DISS_acceptance>1</DISS_acceptance>", '<DISS_acceptance type="x">0</DISS_acceptance>'
        ),
        PDF_NAME: PDF,
    })
    make_zip(directory, "no_repository", {RECORD_NAME: record(repository=False), PDF_NAME: PDF})
    make_zip(directory, "broken", {RECORD_NAME: "<DISS_submission>", PDF_NAME: PDF})
    make_zip(directory, "two_records", {
        RECORD_NAME: record(), "Other_DATA.xml": record(), PDF_NAME: PDF,
    })
    make_zip(directory, "missing_pdf", {RECORD_NAME: record()})
    make_zip(directory, "conflicting_csv", {
        RECORD_NAME: record(), PDF_NAME: PDF, "sub/old_metadata.csv": "x\n",
    })
    make_zip(directory, "old_csv_only", {"dissertation_dataload.csv": "title\nT\n", "a.pdf": PDF})
    make_zip(directory, "nothing", {"a.pdf": PDF})
    make_zip(directory, "no_issue_date", {RECORD_NAME: record(dates=False), PDF_NAME: PDF})
    # Renaming "a;b.pdf" to "a_b.pdf" would overwrite the existing "a_b.pdf".
    make_zip(directory, "etdadmin_upload_129", {
        RECORD_NAME: record(), PDF_NAME: PDF, "a;b.pdf": b"one", "a_b.pdf": b"two",
    })
    # Its folder name is one of the output directories.
    make_zip(directory, "unaccepted", {RECORD_NAME: record(), PDF_NAME: PDF})


def make_big_zips(directory: Path, count: int, size_mb: int) -> None:
    """Write count ProQuest zips that bag successfully, each with a size_mb PDF of
    random (incompressible) bytes, so bagging them takes a while."""
    directory.mkdir(parents=True, exist_ok=True)
    pdf = os.urandom(size_mb * 1024 * 1024)
    for number in range(count):
        make_zip(directory, f"etdadmin_upload_{9000 + number}", {RECORD_NAME: record(), PDF_NAME: pdf})


def copy_zips(source: Path, destination: Path, link: bool = True) -> Path:
    """Put the zips in source into the new directory destination and return it.

    By default they are hard-linked, which is instant: bagit_builder only reads and
    renames zips, never writes to them. With link=False (or where linking isn't
    possible) they are copied, so later changes to source don't reach the copies.
    """
    destination.mkdir(parents=True)
    for zip_path in source.glob("*.zip"):
        target = destination / zip_path.name
        if link:
            try:
                os.link(zip_path, target)
                continue
            except OSError:
                pass  # e.g. on another file system: copy instead
        shutil.copyfile(zip_path, target)
    return destination


# --- Running bagit_builder --------------------------------------------------------------


def run_builder(
    parent: Path, *args: str, runner: Path = SCRIPT, **subprocess_options
) -> subprocess.CompletedProcess:
    """Run bagit_builder (or runner, which wraps it) on parent and return the result,
    with stdout and stderr combined in .stdout. subprocess_options (e.g. env,
    start_new_session) are passed on to subprocess.run."""
    return subprocess.run(
        [sys.executable, str(runner), str(parent), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=parent,
        timeout=subprocess_options.pop("timeout", 900),
        **subprocess_options,
    )


SUMMARY_PATTERN = re.compile(
    r"^(Done|Stopped)\. (?P<created>\d+) bag\(s\) created, (?P<validated>\d+) existing bag\(s\) "
    r"moved to \S+, (?P<failed>\d+) failed, (?P<rejected>\d+) rejected, "
    r"(?P<unaccepted>\d+) unaccepted, (?P<skipped>\d+) skipped\.$",
    re.MULTILINE,
)


def summary(output: str) -> dict[str, int]:
    """Return the counts in a run's summary line, keyed by outcome."""
    match = SUMMARY_PATTERN.search(output)
    assert match, f"no summary line in output:\n{output}"
    return {key: int(value) for key, value in match.groupdict().items()}


# --- Reading results --------------------------------------------------------------------


def output_dir_name(outcome: str) -> str:
    """Return the name of the directory a folder with outcome ('created', 'failed', ...)
    is moved into, as bagit_builder defines it."""
    return bagit_builder.OUTPUT_DIR_NAME_FOR[bagit_builder.Outcome(outcome)]


def log_name(outcome: str) -> str | None:
    """Return the name of the log a folder with outcome gets, or None."""
    return bagit_builder.LOG_NAME_FOR.get(bagit_builder.Outcome(outcome))


def outcome_for_dir(directory_name: str) -> str:
    """Return the outcome whose folders go into directory_name ('created' for
    processed/), the reverse of output_dir_name."""
    return next(
        outcome for outcome, name in bagit_builder.OUTPUT_DIR_NAME_FOR.items()
        if name == directory_name
    ).value


def where_folders_went(parent: Path) -> dict[str, str]:
    """Return {folder name: output directory name} for every folder bagit_builder sorted."""
    result = {}
    for directory_name in bagit_builder.OUTPUT_DIR_NAMES:
        directory = parent / directory_name
        if directory.is_dir():
            for folder in directory.iterdir():
                result[folder.name] = directory_name
    return result


def leftover_folders(parent: Path) -> list[str]:
    """Return the folders in parent that aren't output directories: extracted folders or
    temporary extraction directories a run left behind."""
    return sorted(
        path.name for path in parent.iterdir()
        if path.is_dir() and path.name not in bagit_builder.OUTPUT_DIR_NAMES
    )


def renamed_zips(parent: Path) -> list[str]:
    """Return the names (without .zip.processed) of the zips a run renamed."""
    suffix = ".zip" + bagit_builder.PROCESSED_SUFFIX
    return sorted(path.name.removesuffix(suffix) for path in parent.glob(f"*{suffix}"))


def assert_counts_match_disk(counts: dict[str, int], parent: Path) -> None:
    """Assert that a run's summary counts match the folders in the output directories."""
    went = list(where_folders_went(parent).values())
    for outcome in ("failed", "rejected", "unaccepted", "skipped"):
        assert counts[outcome] == went.count(output_dir_name(outcome)), outcome
    assert counts["created"] + counts["validated"] == went.count(output_dir_name("created"))


def read_metadata_row(bag: Path) -> dict[str, str]:
    """Return the one row of a bag's metadata.csv."""
    with (bag / bagit_builder.METADATA_CSV_NAME).open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    assert len(rows) == 1, f"{bag.name}'s metadata.csv has {len(rows)} rows"
    return rows[0]


def files_on_disk(parent: Path, include_logs: bool = True) -> list[str]:
    """Return every file under parent, relative and sorted. Logs contain timestamps, so
    with include_logs=False they are left out (their names are still compared by the
    caller if needed)."""
    return sorted(
        path.relative_to(parent).as_posix()
        for path in parent.rglob("*")
        if path.is_file() and (include_logs or not path.name.endswith(".log"))
    )


def zip_member_files(zip_path: Path) -> list[str]:
    """Return the file members of a zip, sorted."""
    with zipfile.ZipFile(zip_path) as archive:
        return sorted(info.filename for info in archive.infolist() if not info.is_dir())


def blank_aws_environment(**extra: str) -> dict[str, str]:
    """Return the environment with every AWS credential source blanked, so nothing in a
    test can reach a real bucket even if a stub isn't applied."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("AWS_")}
    env.update(
        AWS_SHARED_CREDENTIALS_FILE=os.devnull,
        AWS_CONFIG_FILE=os.devnull,
        AWS_EC2_METADATA_DISABLED="true",
        **extra,
    )
    return env
