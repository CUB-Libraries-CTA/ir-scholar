"""What a ProQuest ETD bag needs for Bulkrax 9.1.0's BagIt importer to load it.

The importer finds one metadata CSV per bag by its file name, anywhere in the bag, and
attaches every payload file (everything under data/) to the work in each of its rows.
So bagit_builder.py puts the CSV and the ProQuest *_DATA.xml record in the bag root as
tag files, leaving only the PDF and attachments in data/, and a bag holds one record.
build_bag_metadata prepares the record and its CSV row; with --upload-xml-to-s3,
bagit_builder.py uploads the record once the folder is bagged (see upload_record).

The columns are the ones app/services/proquest_bulkrax_csv.rb writes for zipped CSV
imports: work property names, numbered columns for multiple values, a model column
(the importer only attaches files to rows that have one) and no admin_set_id, so the
admin set chosen on the importer is used. The values come from tocsv.transform, the
same as the old loader's.

The importer also can't open a file whose path contains certain characters, so
replace_unsafe_characters renames the files before bagging (see bulkrax_safe_name).

Requires Python 3.12 or newer.
"""

import contextlib
import csv
import io
from dataclasses import dataclass
from pathlib import Path

from console import print_status
from tocsv import csv_divider, defaults, isAccepted, readSubmission, transform
from xmltos3 import xmltos3

# Enter this as the metadata file name when creating the Bulkrax importer.
METADATA_CSV_NAME = "metadata.csv"
# ProQuest names each package's record <author>_<school>_<id>_DATA.xml.
RECORD_SUFFIX = "_DATA.xml"
MODEL = "GraduateThesisOrDissertation"
# Characters replaced with "_" in folder and file names (see bulkrax_safe_name).
UNSAFE_TO_UNDERSCORES = str.maketrans(" :;|", "____")

# transform() column => work property, as in ProquestBulkraxCsv::PROPERTIES. Left out:
# the columns transform() always leaves blank, 'source' (Bulkrax stores the source
# identifier there) and 'admin_set_id'.
PROPERTIES = {
    "title": "title",
    "resource type": "resource_type",
    "creator": "creator",
    "keyword": "keyword",
    "rights statement": "rights_statement",
    "publisher": "publisher",
    "subject": "subject",
    "language": "language",
    "abstract": "abstract",
    "academic_affiliation": "academic_affiliation",
    "contributor_advisor": "contributor_advisor",
    "contributor_committeemember": "contributor_committeemember",
    "date_issued": "date_issued",
    "degree_grantors": "degree_grantors",
    "degree_level": "degree_level",
    "graduation_year": "graduation_year",
    "degree_name": "degree_name",
    "replaces": "replaces",
    "visibility": "visibility",
    "embargo_release_date": "embargo_release_date",
}
# Properties transform() joins with csv_divider. They are written as numbered columns
# (creator_1, creator_2, ...), which Bulkrax reads as one multi-value field.
MULTI_VALUED = {"creator", "keyword", "subject", "contributor_advisor", "contributor_committeemember"}
# Values for properties that would otherwise be empty: transform()'s defaults, keyed by
# work property, without admin_set_id.
DEFAULTS = {
    PROPERTIES.get(column, column): value
    for column, value in defaults.items()
    if value and column != "admin_set_id"
}


class MetadataError(Exception):
    """The folder can't be given the metadata the Bulkrax importer needs."""


class NotAcceptedError(MetadataError):
    """The ProQuest record isn't accepted (DISS_acceptance is missing or 0)."""


class UploadError(Exception):
    """Uploading the ProQuest record to S3 failed. Not a problem with the record, so
    it isn't a MetadataError."""


@dataclass(frozen=True)
class BagMetadata:
    """What goes in a bag's root next to the BagIt tag files."""

    # Files from the top of the folder to move out of data/ once it is bagged.
    tag_files: list[str]
    # The row for METADATA_CSV_NAME, or None if the folder already has that file.
    row: dict[str, str] | None
    # The ProQuest record the row was converted from (one of tag_files), or None.
    record: str | None


def build_bag_metadata(folder: Path, zip_name: str) -> BagMetadata:
    """Return the metadata for a folder about to be bagged, converting its ProQuest
    record into a Bulkrax CSV row. Nothing in the folder is changed.

    zip_name is the zip the folder was extracted from. A folder without a record must
    already have METADATA_CSV_NAME at its top level. Raises NotAcceptedError if the
    record isn't accepted, MetadataError for other problems with the folder, or any
    error from parsing or converting the record.
    """
    records = sorted(
        path.name for path in folder.iterdir()
        if path.name.endswith(RECORD_SUFFIX) and not path.name.startswith(".") and path.is_file()
    )
    if len(records) > 1:
        raise MetadataError(
            f"The folder has several ProQuest records ({', '.join(records)}). Bulkrax's BagIt "
            "importer attaches every file in a bag to each of its works, so a bag can only "
            "hold one record."
        )
    # The importer reads the first file whose name ends with the metadata file name,
    # so there must be exactly one.
    metadata_csvs = [
        path for path in folder.rglob(f"*{METADATA_CSV_NAME}")
        if path.is_file() and not any(part.startswith(".") for part in path.relative_to(folder).parts)
    ]

    if records:
        if metadata_csvs:
            raise MetadataError(
                f"{metadata_csvs[0].relative_to(folder)} would be read by the Bulkrax importer "
                f"instead of the {METADATA_CSV_NAME} converted from {records[0]}."
            )
        return BagMetadata(
            tag_files=records, row=build_row(folder, records[0], zip_name), record=records[0]
        )

    if metadata_csvs == [folder / METADATA_CSV_NAME]:
        return BagMetadata(tag_files=[METADATA_CSV_NAME], row=None, record=None)
    if metadata_csvs:
        raise MetadataError(
            f"The folder has no ProQuest record (*{RECORD_SUFFIX}), and "
            f"{metadata_csvs[0].relative_to(folder)} isn't a {METADATA_CSV_NAME} at its top "
            "level."
        )
    raise MetadataError(
        f"The folder has no ProQuest record (*{RECORD_SUFFIX}) or {METADATA_CSV_NAME} at its "
        "top level."
    )


def build_row(folder: Path, record_name: str, zip_name: str) -> dict[str, str]:
    """Convert the ProQuest record record_name in folder into a Bulkrax CSV row.

    Its replaces column holds the file number; add the record's S3 URL with
    add_record_url once it has been uploaded.
    """
    submission = readSubmission(str(folder / record_name))
    if not isAccepted(submission):
        raise NotAcceptedError(f"{record_name} is not accepted (DISS_acceptance is missing or 0).")
    check_files_present(folder, record_name, submission)
    # The same file number main.py takes from the zip's name (the folder's name may have
    # had characters replaced). The files column isn't used here, so transform() isn't
    # asked to work out the attachment paths.
    file_number = Path(zip_name).stem.split("_")[-1]
    record = transform(
        submission, file_number, str(folder) + "/", record_name, folder.name,
        upload_xml=False, list_files=False,
    )
    return bulkrax_row(record, source_identifier=record_name.removesuffix(".xml"))


def add_record_url(row: dict[str, str], url: str) -> dict[str, str]:
    """Return row with the record's S3 URL added to its replaces column, which then
    reads "<file number>|<url>" like the old loader's."""
    return {**row, "replaces": f"{row.get('replaces', '')}|{url}"}


def upload_record(folder: Path, record_name: str, s3_prefix: str, zip_name: str) -> str:
    """Upload the record to S3 under s3_prefix and return its URL. Raises UploadError if
    the upload fails.

    xmltos3 prints with plain print(), which can't say which zip it is about when
    several run at once (--jobs), so its messages are collected and printed again,
    prefixed with zip_name, even if the upload fails.
    """
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output):
            url = xmltos3(str(folder) + "/", record_name, region_fallback=True, prefix=s3_prefix)
    except Exception as error:
        raise UploadError(f"Uploading {record_name} to S3 failed: {error}") from error
    finally:
        for line in output.getvalue().splitlines():
            print_status(f"{zip_name}: {line.removeprefix('- ')}")
    if not url:
        # xmltos3 returns False for a ClientError it doesn't raise.
        raise UploadError(f"Uploading {record_name} to S3 failed (see the error above)")
    return url


def check_files_present(folder: Path, record_name: str, submission: dict) -> None:
    """Raise MetadataError if a file the record lists isn't anywhere in folder."""
    content = submission["DISS_content"]
    names = [content["DISS_binary"]["#text"]]
    attachments = content.get("DISS_attachment") or []
    if not isinstance(attachments, list):
        attachments = [attachments]
    names += [attachment["DISS_file_name"] for attachment in attachments]

    present = {path.name for path in folder.rglob("*") if path.is_file()}
    missing = [name for name in names if name not in present]
    if missing:
        raise MetadataError(
            f"Listed in {record_name} but missing from the package: {', '.join(missing)}"
        )


def bulkrax_row(record: dict, source_identifier: str) -> dict[str, str]:
    """Turn a transform() row into a Bulkrax CSV row (ProquestBulkraxCsv#bulkrax_row)."""
    row = {"source_identifier": source_identifier, "model": MODEL}
    for column, prop in PROPERTIES.items():
        value = "" if record[column] is None else str(record[column])
        if prop in MULTI_VALUED:
            values = [part for part in value.split(csv_divider) if part.strip()]
            for number, part in enumerate(values, start=1):
                row[f"{prop}_{number}"] = part
        elif value.strip():
            row[prop] = value
    for prop, default in DEFAULTS.items():
        if not row.get(prop, "").strip():
            row[prop] = default
    # Hyrax only applies an embargo when it is told what to show during and after it.
    if row.get("visibility") == "embargo":
        row["visibility_during_embargo"] = "restricted"
        row["visibility_after_embargo"] = "open"
    else:
        row.pop("embargo_release_date", None)
    return row


def write_csv(path: Path, row: dict[str, str]) -> None:
    """Write row to path as a UTF-8 CSV with a header of its columns."""
    # newline="" stops Windows turning the csv module's \r\n into \r\r\n.
    with path.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(output_file, row)
        writer.writeheader()
        writer.writerow(row)


def bulkrax_safe_name(name: str) -> str:
    """Return name with the characters Bulkrax can't handle in a file path replaced with
    underscores.

    The importer joins each bag's file paths with "|", splits them again on ":", ";"
    and "|" (its default multi_value_element_split_on), and replaces the spaces in each
    path with underscores before opening it. So a file whose path contains any of those
    characters couldn't be attached.
    """
    return name.translate(UNSAFE_TO_UNDERSCORES)


def replace_unsafe_characters(folder: Path, keep: list[str]) -> None:
    """Rename the files and folders inside folder to their bulkrax_safe_name, except for
    the top-level names in keep.

    Raises FileExistsError if a new name is already taken, instead of letting the
    rename overwrite that file.
    """
    # Deepest first, so renaming a folder doesn't move paths still waiting to be renamed.
    paths = sorted(
        (path for path in folder.rglob("*") if bulkrax_safe_name(path.name) != path.name),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for path in paths:
        if path.parent == folder and path.name in keep:
            continue
        target = path.with_name(bulkrax_safe_name(path.name))
        if target.exists(follow_symlinks=False):
            raise FileExistsError(
                f"can't rename {path.relative_to(folder)} to {target.name}, which already exists"
            )
        path.rename(target)
        print_status(f"Renamed {path} to {target.name}")
