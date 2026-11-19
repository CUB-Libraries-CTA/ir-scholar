"""Create a BagIt bag from every zip file within a parent directory.

Each ``<name>.zip`` in ``parent_dir`` is extracted into ``parent_dir/<name>`` (the zip
itself is never modified), turned into a bag for Bulkrax's BagIt importer, or only
validated if it already is one, and then moved into ``processed/``, ``failed/``,
``rejected/``, ``unaccepted/`` or ``skipped/`` inside ``parent_dir``. Each new bag gets a
``metadata.csv`` converted from its ProQuest record (see ``bulkrax_bag.py``).
Zips whose folder reaches ``processed/`` are renamed to ``<name>.zip.processed``.
Run with ``-h`` for the full behaviour, including how problems are logged and how to
retry a zip.

The zip extraction itself is in ``safe_unzip.py``, what Bulkrax needs from a bag (the
metadata conversion and safe file names) in ``bulkrax_bag.py``, which uses ``tocsv.py``
and ``xmltos3.py``, and the printing of progress messages in ``console.py``. They must
be kept in the same directory as this script.

Usage:
    python bagit_builder.py PARENT_DIR [--contact-name NAME]
        [--source-organization ORG] [--jobs N] [--processes N]
        [--upload-xml-to-s3 [--test]]

Requires Python 3.12 or newer.
"""

import sys

# Checked before the other imports so older versions get a clear message instead of an
# ImportError or a failure partway through a run.
if sys.version_info < (3, 12):
    sys.exit("Error: bagit_builder.py requires Python 3.12 or newer")

import argparse
import contextlib
import logging
import multiprocessing
import signal
import traceback
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

import bagit

# console.py and safe_unzip.py must be in the same directory as this script.
from console import escape_unencodable_output, print_status, warn
from safe_unzip import (
    extract_zip,
    is_windows_reserved,
    normalize_key,
    remove_tree,
    remove_tree_quietly,
)
# bulkrax_bag.py, tocsv.py and xmltos3.py must be in the same directory as this script.
from bulkrax_bag import (
    METADATA_CSV_NAME,
    RECORD_SUFFIX,
    BagMetadata,
    NotAcceptedError,
    add_record_url,
    build_bag_metadata,
    bulkrax_safe_name,
    replace_unsafe_characters,
    upload_record,
    write_csv,
)
from xmltos3 import key_prefix, test_key_prefix

ERROR_LOG_NAME = "bagit_errors.log"
SKIPPED_LOG_NAME = "bagit_skipped.log"
UNACCEPTED_LOG_NAME = "bagit_unaccepted.log"
PROCESSED_DIR_NAME = "processed"
SKIPPED_DIR_NAME = "skipped"
FAILED_DIR_NAME = "failed"
REJECTED_DIR_NAME = "rejected"
UNACCEPTED_DIR_NAME = "unaccepted"
OUTPUT_DIR_NAMES = (
    PROCESSED_DIR_NAME, SKIPPED_DIR_NAME, FAILED_DIR_NAME, REJECTED_DIR_NAME, UNACCEPTED_DIR_NAME
)
# Added to a zip's name once it has been processed, e.g. thesis.zip.processed.
PROCESSED_SUFFIX = ".processed"
RETRY_HINT = "Delete that folder to retry this zip."


class Outcome(StrEnum):
    """Result of processing one zip, counted for the summary.

    A zip whose folder is already in skipped/, failed/, rejected/ or unaccepted/ from
    an earlier run isn't extracted again and gets that folder's outcome.
    """

    # A new bag was created and moved to processed/.
    CREATED = "created"
    # The zip already held a bag, which was validated and moved to processed/.
    VALIDATED = "validated"
    # Bagging or validation went wrong, or the S3 upload failed, and the folder went to
    # failed/. Also a zip that couldn't be extracted at all.
    FAILED = "failed"
    # The ProQuest record couldn't be converted into the metadata CSV, and the folder
    # went to rejected/.
    REJECTED = "rejected"
    # The ProQuest record isn't accepted (DISS_acceptance is missing or 0), and the
    # folder went to unaccepted/.
    UNACCEPTED = "unaccepted"
    # The folder was already a bag that is in processed/, and went to skipped/.
    SKIPPED = "skipped"


# The directory inside parent_dir that a folder with each outcome is moved into.
OUTPUT_DIR_NAME_FOR = {
    Outcome.CREATED: PROCESSED_DIR_NAME,
    Outcome.VALIDATED: PROCESSED_DIR_NAME,
    Outcome.FAILED: FAILED_DIR_NAME,
    Outcome.REJECTED: REJECTED_DIR_NAME,
    Outcome.UNACCEPTED: UNACCEPTED_DIR_NAME,
    Outcome.SKIPPED: SKIPPED_DIR_NAME,
}
# The log a folder (or, if it wasn't extracted, its zip) with each outcome gets.
LOG_NAME_FOR = {
    Outcome.FAILED: ERROR_LOG_NAME,
    Outcome.REJECTED: ERROR_LOG_NAME,
    Outcome.UNACCEPTED: UNACCEPTED_LOG_NAME,
    Outcome.SKIPPED: SKIPPED_LOG_NAME,
}
# Outcomes whose folder reaches processed/, after which the zip is renamed.
PROCESSED_OUTCOMES = (Outcome.CREATED, Outcome.VALIDATED)
# Outcomes that make the exit code 1 and are printed to stderr.
ERROR_OUTCOMES = (Outcome.FAILED, Outcome.REJECTED)


@dataclass(frozen=True)
class Settings:
    """The command-line settings every zip is processed with."""

    # Directory holding the zips and the output directories.
    parent_dir: Path
    # Fields added to each new bag's bag-info.txt.
    metadata: dict[str, str]
    # Number of processes used for checksums.
    processes: int
    # Where in the S3 bucket each ProQuest record is uploaded to for the 'replaces'
    # column (see --test), or None if it isn't uploaded (no --upload-xml-to-s3).
    s3_prefix: str | None

    @property
    def processed_dir(self) -> Path:
        return self.parent_dir / PROCESSED_DIR_NAME

    def output_dir(self, outcome: Outcome) -> Path:
        """Return the directory a folder with this outcome is moved into."""
        return self.parent_dir / OUTPUT_DIR_NAME_FOR[outcome]


class RestoreError(Exception):
    """Bagging failed and the folder couldn't be replaced with a fresh extraction."""


# --- Command line -----------------------------------------------------------------------


def positive_int(value: str) -> int:
    """argparse type that accepts only integers of 1 or more."""
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, got {number}")
    return number


def parse_args() -> argparse.Namespace:
    """Parse and return the command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Create a BagIt bag from each zip file within a parent directory.",
        epilog=(
            "Each <name>.zip is extracted into parent_dir/<name> without modifying the zip\n"
            "(spaces, \":\", \";\" and \"|\" in <name> are replaced with underscores).\n"
            "__MACOSX/ folders and .DS_Store files (except inside existing bags) are left\n"
            "out, and a single top-level folder in a zip is unwrapped so its contents are\n"
            "extracted into parent_dir/<name>.\n"
            "Extracted directories that already contain bagit.txt are only validated.\n"
            f"Other directories are bagged for Bulkrax's BagIt importer (set its metadata file\n"
            f"name to {METADATA_CSV_NAME}). The ProQuest record (*{RECORD_SUFFIX}) at the top of\n"
            f"the directory is converted into {METADATA_CSV_NAME}, and both go in the bag root,\n"
            "so data/ only holds the files to attach to the work. A directory without a\n"
            f"record needs a {METADATA_CSV_NAME} at its top level instead, which is moved to the\n"
            "bag root.\n"
            "Spaces, \":\", \";\" and \"|\" in the names of the files and folders that go in\n"
            "data/ are replaced with underscores, because Bulkrax can't attach a file with\n"
            "one of them in its path.\n"
            "Each extracted directory is moved into one of these directories inside parent_dir:\n"
            f"  {PROCESSED_DIR_NAME}/   the bag was created and validated (or was already a\n"
            f"                valid bag not yet in {PROCESSED_DIR_NAME}/)\n"
            f"  {FAILED_DIR_NAME}/      bagging or validation failed, e.g. a renamed file would\n"
            f"                overwrite another, a bag with the same name is already in\n"
            f"                {PROCESSED_DIR_NAME}/, or the --upload-xml-to-s3 upload failed\n"
            f"  {REJECTED_DIR_NAME}/    the ProQuest record couldn't be converted into\n"
            f"                {METADATA_CSV_NAME}: it can't be read or lacks required elements,\n"
            "                there is no record or more than one, or a file it lists is missing\n"
            f"  {UNACCEPTED_DIR_NAME}/  the ProQuest record isn't accepted (DISS_acceptance is\n"
            "                missing or 0)\n"
            f"  {SKIPPED_DIR_NAME}/     the directory was already a bag and is already in\n"
            f"                {PROCESSED_DIR_NAME}/\n"
            f"The reason is written into the directory: {ERROR_LOG_NAME} in {FAILED_DIR_NAME}/ and\n"
            f"{REJECTED_DIR_NAME}/, {UNACCEPTED_LOG_NAME} in {UNACCEPTED_DIR_NAME}/ and {SKIPPED_LOG_NAME} in\n"
            f"{SKIPPED_DIR_NAME}/. Directories in {FAILED_DIR_NAME}/ hold the zip's original contents (they\n"
            "are re-extracted from the zip if bagging changed them).\n"
            f"A zip whose directory reaches {PROCESSED_DIR_NAME}/ is renamed to "
            f"<name>.zip{PROCESSED_SUFFIX};\n"
            "other zips keep their name and are only retried once their directory is deleted\n"
            f"from {FAILED_DIR_NAME}/, {REJECTED_DIR_NAME}/, {UNACCEPTED_DIR_NAME}/ or {SKIPPED_DIR_NAME}/. A zip that can't be\n"
            "extracted or retried is left unchanged, with the reason in a log named\n"
            f"<name>.zip.{ERROR_LOG_NAME} (or .{UNACCEPTED_LOG_NAME} or .{SKIPPED_LOG_NAME}) next\n"
            "to it. The exit code is 1 if any zip failed or was rejected. Hidden files (names\n"
            "starting with \".\") are ignored."
        ),
        # Keep the epilog's line breaks and indentation instead of rewrapping it.
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("parent_dir", type=Path, help="Directory containing the zip files to bag")
    parser.add_argument("--contact-name", help="Value for the Contact-Name bag-info field")
    parser.add_argument(
        "--source-organization", help="Value for the Source-Organization bag-info field"
    )
    parser.add_argument(
        "--jobs",
        type=positive_int,
        default=4,
        help=(
            "Number of zips to process at the same time, each in its own process "
            "(default: %(default)s)"
        ),
    )
    parser.add_argument(
        "--processes",
        type=positive_int,
        default=1,
        help=(
            "Number of processes used for the checksums of each bag (default: "
            "%(default)s). bagit hashes each file in a single process, so this only helps "
            "bags with many files. With --jobs, up to JOBS x PROCESSES processes run at once."
        ),
    )
    parser.add_argument(
        "--upload-xml-to-s3",
        action="store_true",
        help=(
            "Upload each ProQuest record to S3 and put its URL in the CSV's replaces "
            "column (needs AWS credentials; a failed upload fails the folder); otherwise "
            "replaces is only the file number"
        ),
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help=(
            f"With --upload-xml-to-s3, upload to {test_key_prefix}/ instead of "
            f"{key_prefix}/"
        ),
    )
    args = parser.parse_args()
    if args.test and not args.upload_xml_to_s3:
        # Nothing goes to S3 without the upload, so --test alone would do nothing.
        parser.error("--test only applies with --upload-xml-to-s3")
    return args


def build_metadata(args: argparse.Namespace) -> dict[str, str]:
    """Return the bag-info.txt fields, including only the values given on the command line."""
    metadata: dict[str, str] = {}
    if args.contact_name:
        metadata["Contact-Name"] = args.contact_name
    if args.source_organization:
        metadata["Source-Organization"] = args.source_organization
    return metadata


# --- Logs -------------------------------------------------------------------------------


def format_error(error: BaseException) -> str:
    """Format an exception and its traceback as text for the error log.

    bagit's ``BagValidationError`` stores the individual validation failures in
    ``details``, so they are listed after the traceback when present.
    """
    message = "".join(traceback.format_exception(error))
    details = getattr(error, "details", None)
    if details:
        message += "\nDetails:\n" + "\n".join(f"  - {detail}" for detail in details)
    return message


def write_log(log_path: Path, outcome: Outcome, body: str) -> None:
    """Write a log file headed "BagIt processing <outcome> at <time>"."""
    # Explicit UTF-8 so non-ASCII file names in tracebacks are written the same on every OS.
    log_path.write_text(
        f"BagIt processing {outcome} at {datetime.now().isoformat()}\n\n{body}\n",
        encoding="utf-8",
    )


def zip_log_path(zip_path: Path, log_name: str) -> Path:
    """Return the path of a log written next to a zip, e.g. thesis.zip.bagit_errors.log."""
    return zip_path.with_name(f"{zip_path.name}.{log_name}")


def report_zip(zip_path: Path, outcome: Outcome, problem: str) -> Outcome:
    """Log why a zip wasn't extracted next to it and return outcome."""
    log_path = zip_log_path(zip_path, LOG_NAME_FOR[outcome])
    write_log(log_path, outcome, f"Zip: {zip_path}\n{problem}\nThe zip was left unchanged.")
    print_status(f"{outcome.name}: {zip_path} (see {log_path})", error=outcome in ERROR_OUTCOMES)
    return outcome


def fail_zip(zip_path: Path, problem: str) -> Outcome:
    """Log why a zip couldn't be processed next to it and return Outcome.FAILED."""
    return report_zip(zip_path, Outcome.FAILED, problem)


# --- Bagging an extracted folder --------------------------------------------------------


def bag_or_validate_folder(folder: Path, zip_path: Path, settings: Settings) -> Outcome:
    """Bag or validate a folder extracted from zip_path and return its outcome (see
    Outcome), logging the reason into the folder if it didn't reach processed. The folder
    is left where it is (see sort_folder). Raises RestoreError if bagging failed and
    couldn't be undone."""
    # bagit.txt marks a folder that is already a bag; bagging it again would nest bags.
    existing_bag = (folder / "bagit.txt").is_file()
    in_processed = (settings.processed_dir / folder.name).exists()

    if existing_bag and in_processed:
        # A duplicate of a bag that is already done.
        write_log(
            folder / SKIPPED_LOG_NAME,
            Outcome.SKIPPED,
            f"This folder is already a bag, and a bag named {folder.name} already exists in "
            f"{settings.processed_dir}. It was not validated or moved to {PROCESSED_DIR_NAME}/ "
            "so the existing bag is not overwritten.",
        )
        outcome = Outcome.SKIPPED
    elif in_processed:
        # The new bag couldn't be moved into processed, so don't create it.
        write_log(
            folder / ERROR_LOG_NAME,
            Outcome.FAILED,
            f"A bag named {folder.name} already exists in {settings.processed_dir}, so this "
            "folder was not bagged.",
        )
        outcome = Outcome.FAILED
    elif existing_bag:
        # Existing bags are validated rather than re-bagged.
        print_status(f"Validating existing bag {folder}")
        valid = validate_existing_bag(folder, settings.processes)
        outcome = Outcome.VALIDATED if valid else Outcome.FAILED
    else:
        print_status(f"Preparing the Bulkrax metadata for {folder}")
        metadata = prepare_bag_metadata(folder, zip_path, settings)
        if isinstance(metadata, Outcome):
            outcome = metadata
        else:
            print_status(f"Bagging {folder}")
            bagged = bag_folder(folder, zip_path, settings, metadata)
            outcome = Outcome.CREATED if bagged else Outcome.FAILED
    return outcome


def sort_folder(folder: Path, outcome: Outcome, settings: Settings) -> None:
    """Move folder into the output directory for outcome and report where it went."""
    destination = move_to(folder, settings.output_dir(outcome))
    if outcome is Outcome.CREATED:
        print_status(f"OK: {destination}")
    elif outcome is Outcome.VALIDATED:
        print_status(f"OK: {destination} (already a bag)")
    else:
        print_status(
            f"{outcome.name}: {destination} (see {destination / LOG_NAME_FOR[outcome]})",
            error=outcome in ERROR_OUTCOMES,
        )


def validate_existing_bag(folder: Path, processes: int) -> bool:
    """Validate a folder that is already a bag, writing the error log if it is invalid.

    Validation only reads the bag, so the folder never needs restoring.
    Returns True if the bag is valid.
    """
    try:
        bagit.Bag(str(folder)).validate(processes=processes)
    except Exception as error:
        write_log(
            folder / ERROR_LOG_NAME,
            Outcome.FAILED,
            "Existing bag failed validation:\n" + format_error(error),
        )
        return False
    return True


def prepare_bag_metadata(
    folder: Path, zip_path: Path, settings: Settings
) -> BagMetadata | Outcome:
    """Convert the ProQuest record of a folder extracted from zip_path that is about to
    be bagged (see bulkrax_bag.build_bag_metadata).

    If it can't be, the reason is logged into the folder and the folder's outcome is
    returned: UNACCEPTED for a record that isn't accepted, and REJECTED for any other
    problem with the record or the folder's contents. Nothing but that log is written
    into the folder, so it never needs restoring.
    """
    try:
        return build_bag_metadata(folder, zip_name=zip_path.name)
    except NotAcceptedError as error:
        write_log(
            folder / UNACCEPTED_LOG_NAME,
            Outcome.UNACCEPTED,
            f"{error} This folder was not bagged.",
        )
        return Outcome.UNACCEPTED
    except Exception as error:
        write_log(
            folder / ERROR_LOG_NAME,
            Outcome.REJECTED,
            "The Bulkrax metadata couldn't be prepared from this folder, so it was not "
            "bagged:\n" + format_error(error),
        )
        return Outcome.REJECTED


def bag_folder(folder: Path, zip_path: Path, settings: Settings, metadata: BagMetadata) -> bool:
    """Bag and validate a folder extracted from zip_path, putting the metadata's tag
    files and CSV in the bag root. With --upload-xml-to-s3, the ProQuest record is then
    uploaded and its URL added to the CSV.

    On failure (including a failed upload) the half-bagged folder is replaced with a
    fresh extraction of the zip and the error is logged into it. Returns True if the bag
    was created and validated. Raises RestoreError if the folder can't be re-extracted.
    """
    try:
        replace_unsafe_characters(folder, keep=metadata.tag_files)
        # Pass a copy of metadata because make_bag adds fields (e.g. Bagging-Date) to it.
        bag = bagit.make_bag(str(folder), dict(settings.metadata), processes=settings.processes)
        # make_bag moved everything into data/, which Bulkrax attaches to the work.
        for name in metadata.tag_files:
            (folder / "data" / name).rename(folder / name)
        if metadata.row is not None:
            write_csv(folder / METADATA_CSV_NAME, metadata.row)
        # Rewrites the payload manifests and Payload-Oxum without the files moved out of
        # data/, and the tag manifests with the new tag files.
        save_bag(bag, settings.processes, manifests=True)
        bag.validate(processes=settings.processes)
        if settings.s3_prefix is not None and metadata.record is not None:
            # Only now that the folder is bagged, so a folder that ends up in failed/ or
            # rejected/ is never uploaded. Only metadata.csv changes, so only the tag
            # manifests need rewriting.
            url = upload_record(folder, metadata.record, settings.s3_prefix, zip_path.name)
            write_csv(folder / METADATA_CSV_NAME, add_record_url(metadata.row, url))
            save_bag(bag, settings.processes, manifests=False)
    except Exception as error:
        message = format_error(error)
        # make_bag rearranges the folder in place; the unchanged zip has the original files.
        try:
            remove_tree(folder)
            extract_zip(zip_path, folder)
        except Exception as restore_error:
            raise RestoreError(
                f"Bagging failed:\n{message}\n"
                f"Restoring {folder} from the zip also failed:\n{format_error(restore_error)}"
            ) from restore_error
        write_log(folder / ERROR_LOG_NAME, Outcome.FAILED, message)
        return False
    return True


def save_bag(bag: bagit.Bag, processes: int, manifests: bool) -> None:
    """Regenerate a bag's tag manifests and bag-info.txt, and with manifests=True also
    its payload manifests and Payload-Oxum.

    Bag.save changes into the bag directory and only changes back if it succeeds. Left
    there after a failure, the working directory would be deleted when the folder is
    re-extracted, and make_bag, which reads it, would fail for every later zip.
    """
    # contextlib.chdir changes back on exit, whatever happens.
    with contextlib.chdir(Path.cwd()):
        bag.save(processes=processes, manifests=manifests)


def move_to(folder: Path, target_dir: Path) -> Path:
    """Move a folder into target_dir and return its new path.

    If a folder with the same name already exists there, the folder is left in place
    and its current path is returned.
    """
    destination = target_dir / folder.name
    if destination.exists():
        warn(f"{destination} already exists, leaving {folder} in place")
        return folder
    target_dir.mkdir(exist_ok=True)
    # target_dir is always inside parent_dir, so a rename works. Unlike shutil.move, it
    # never falls back to copying and deleting, which could leave a partial copy.
    folder.rename(destination)
    return destination


# --- Processing a zip -------------------------------------------------------------------


def process_zip(zip_path: Path, settings: Settings) -> Outcome:
    """Extract zip_path into parent_dir/<name>, bag or validate the extracted folder and
    move it into the output directory for its outcome (see Outcome).

    The zip itself is never modified. It is renamed to <name>.zip.processed only if its
    folder reached processed (CREATED or VALIDATED). If the zip isn't extracted at all,
    the reason is logged next to it (see check_before_extracting).
    """
    folder = settings.parent_dir / folder_name(zip_path)
    # Remove the logs from a previous run that didn't extract this zip.
    for log_name in set(LOG_NAME_FOR.values()):
        zip_log_path(zip_path, log_name).unlink(missing_ok=True)

    blocked = check_before_extracting(zip_path, folder, settings)
    if blocked is not None:
        return blocked

    print_status(f"Extracting {zip_path}")
    try:
        try:
            extract_zip(zip_path, folder)
        except Exception as error:
            return fail_zip(zip_path, "Failed to extract the zip:\n" + format_error(error))
        outcome = bag_or_validate_folder(folder, zip_path, settings)
        # From here the outcome is decided. Moving the folder, renaming the zip and
        # counting the outcome must all happen, so Ctrl+C is held off until they have:
        # otherwise a finished bag could end up in processed/ uncounted, with its zip not
        # renamed, and the next run would fail that zip as a duplicate.
        hold = InterruptHold()
    except RestoreError as error:
        return fail_zip(zip_path, str(error))
    except KeyboardInterrupt:
        # The zip is unchanged, so delete the extracted copy rather than leave a folder
        # that would block the next run. Nothing was at that path before (see
        # check_before_extracting), so this is safe even if Ctrl+C arrived just as
        # extract_zip renamed the folder into place.
        if folder.exists():
            remove_tree_quietly(folder)
        raise
    try:
        sort_folder(folder, outcome, settings)
        if outcome in PROCESSED_OUTCOMES:
            mark_zip_processed(zip_path, folder)
    except Exception:
        if hold.release():
            # Ctrl+C was pressed as well: report the error as process_zip_safely would,
            # and still stop the run.
            report_unexpected_error(zip_path)
            raise ZipInterrupted(Outcome.FAILED) from None
        raise
    if hold.release():
        raise ZipInterrupted(outcome)
    return outcome


def interrupt_once(signum, frame) -> None:
    """SIGINT handler: raise KeyboardInterrupt for the first Ctrl+C and ignore any more.

    Without this, a second Ctrl+C could interrupt the cleanup and counting the first one
    started: leave an extracted folder that blocks the zip on the next run, or skip
    counting zips that were finished. Ctrl+\\ (SIGQUIT) still stops a run at once.
    """
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    raise KeyboardInterrupt


class InterruptHold:
    """Holds off Ctrl+C from when it is created until release(), which says whether
    Ctrl+C was pressed in the meantime."""

    def __init__(self) -> None:
        self.interrupted = False
        self.previous_handler = signal.signal(signal.SIGINT, self.record)

    def record(self, signum, frame) -> None:
        self.interrupted = True

    def release(self) -> bool:
        # Once Ctrl+C has been pressed, any more presses are ignored (see interrupt_once).
        signal.signal(signal.SIGINT, signal.SIG_IGN if self.interrupted else self.previous_handler)
        return self.interrupted


class ZipInterrupted(KeyboardInterrupt):
    """Ctrl+C was pressed while a zip's outcome was being recorded. The zip was finished
    anyway, and its outcome is counted before the run stops."""

    def __init__(self, outcome: Outcome) -> None:
        super().__init__()
        self.outcome = outcome


def folder_name(zip_path: Path) -> str:
    """Return the name of the folder zip_path is extracted into.

    The bag directory's name is part of every file path Bulkrax reads, so it gets the
    same replacements as the files in it (see bulkrax_safe_name).
    """
    return bulkrax_safe_name(zip_path.stem)


def check_before_extracting(zip_path: Path, folder: Path, settings: Settings) -> Outcome | None:
    """Return the zip's outcome, after logging why next to the zip, if zip_path must not
    be extracted into folder, or None if it can be.

    A zip whose folder name is already in skipped/, failed/, rejected/ or unaccepted/
    from an earlier run is not extracted again but gets that folder's outcome, so it
    stays reported until that folder is deleted.
    """
    name = folder.name
    if name in {".", ".."} or is_windows_reserved(name):
        return fail_zip(
            zip_path,
            f"{zip_path.name} doesn't have a usable folder name (\".\", \"..\" and Windows "
            "device names such as NUL can't be used).",
        )
    # Compared case-insensitively because "Processed" and "processed" are the same
    # directory on the default macOS and Windows file systems.
    if name.casefold() in OUTPUT_DIR_NAMES:
        return fail_zip(zip_path, f"{name} is the name of one of this script's output directories.")

    # Every outcome with a log has its own directory: all but processed/.
    for outcome in LOG_NAME_FOR:
        earlier = settings.output_dir(outcome) / name
        if earlier.exists():
            # Retrying would most likely end the same way, and the new folder couldn't
            # be moved into that directory anyway.
            return report_zip(
                zip_path,
                outcome,
                f"{earlier} already exists from an earlier run (see its "
                f"{LOG_NAME_FOR[outcome]}). {RETRY_HINT}",
            )

    if folder.exists(follow_symlinks=False):
        # Never extract over an existing folder, e.g. one left behind by an earlier run.
        return fail_zip(
            zip_path,
            f"{folder} already exists (perhaps left by an earlier run that stopped "
            f"unexpectedly), so the zip was not extracted. {RETRY_HINT}",
        )
    return None


def mark_zip_processed(zip_path: Path, folder: Path) -> None:
    """Rename zip_path to <name>.zip.processed once its folder is in processed.

    The zip is left as it is if its folder is still in parent_dir (it couldn't be moved
    into processed), so the leftover folder is reported on the next run.
    """
    processed = zip_path.with_name(zip_path.name + PROCESSED_SUFFIX)
    if folder.exists():
        warn(f"{folder} was not moved, so {zip_path.name} was not renamed")
    elif processed.exists():
        warn(f"{processed} already exists, so {zip_path.name} was not renamed")
    else:
        try:
            zip_path.rename(processed)
        except OSError as error:
            # The bag is already in processed, so only warn.
            warn(f"could not rename {zip_path.name}: {error}")


# --- Running ----------------------------------------------------------------------------


def main() -> int:
    """Extract and bag each zip in parent_dir and sort the results into the output
    directories (see Outcome).

    Returns the process exit code: 0 if no zip failed or was rejected, 1 if any was and
    130 if interrupted with Ctrl+C.
    """
    escape_unencodable_output()
    hide_bagit_interrupt_logs()
    signal.signal(signal.SIGINT, interrupt_once)
    args = parse_args()
    parent_dir: Path = args.parent_dir.resolve()
    check_parent_dir(parent_dir)
    zip_paths = list_zips(parent_dir)
    settings = Settings(
        parent_dir=parent_dir,
        metadata=build_metadata(args),
        processes=args.processes,
        s3_prefix=(
            (test_key_prefix if args.test else key_prefix) if args.upload_xml_to_s3 else None
        ),
    )

    counts: Counter[Outcome] = Counter()
    interrupted = False
    try:
        # A single zip gains nothing from a worker process, which takes a while to start.
        if args.jobs == 1 or len(zip_paths) < 2:
            process_sequentially(zip_paths, settings, counts)
        else:
            process_in_parallel(zip_paths, settings, args.jobs, counts)
    except KeyboardInterrupt:
        interrupted = True
        print_status("Interrupted by user.", error=True)

    print_status(
        f"{'Stopped' if interrupted else 'Done'}. {counts[Outcome.CREATED]} bag(s) created, "
        f"{counts[Outcome.VALIDATED]} existing bag(s) moved to {PROCESSED_DIR_NAME}, "
        f"{counts[Outcome.FAILED]} failed, {counts[Outcome.REJECTED]} rejected, "
        f"{counts[Outcome.UNACCEPTED]} unaccepted, {counts[Outcome.SKIPPED]} skipped."
    )
    if interrupted:
        return 130
    return 1 if any(counts[outcome] for outcome in ERROR_OUTCOMES) else 0


def hide_bagit_interrupt_logs() -> None:
    """Stop bagit logging a Ctrl+C as an error.

    When Ctrl+C stops it creating or validating a bag, bagit logs "An error occurred
    creating a bag" or "Unable to calculate file hashes" with a traceback, which reads
    like a failure. The interrupt is reported and cleaned up anyway (see process_zip).
    """

    def is_not_interrupt(record: logging.LogRecord) -> bool:
        return not (record.exc_info and isinstance(record.exc_info[1], KeyboardInterrupt))

    bagit.LOGGER.addFilter(is_not_interrupt)


def process_sequentially(
    zip_paths: list[Path], settings: Settings, counts: Counter[Outcome]
) -> None:
    """Process the zips one after another, adding each outcome to counts."""
    for zip_path in zip_paths:
        try:
            counts[process_zip_safely(zip_path, settings)] += 1
        except ZipInterrupted as interrupt:
            counts[interrupt.outcome] += 1
            raise


def process_zip_safely(zip_path: Path, settings: Settings) -> Outcome:
    """Process one zip (see process_zip), reporting an unexpected error, e.g. a failed
    move or log write, as FAILED instead of stopping the run.

    After an unexpected error the zip and any extracted folder are left where they are.
    """
    try:
        return process_zip(zip_path, settings)
    except Exception:
        report_unexpected_error(zip_path)
        return Outcome.FAILED


def report_unexpected_error(zip_path: Path) -> None:
    """Print the error being handled (call from an except block) as unexpected while
    processing zip_path."""
    print_status(f"FAILED: {zip_path} (unexpected error, see below)", error=True)
    print_status(traceback.format_exc().rstrip("\n"), error=True)


def check_parent_dir(parent_dir: Path) -> None:
    """Exit with an error message if parent_dir can't be used."""
    if not parent_dir.is_dir():
        sys.exit(f"Error: {parent_dir} is not a directory")
    # Extracting and bagging folders inside a bag would corrupt it.
    if (parent_dir / "bagit.txt").exists():
        sys.exit(
            f"Error: {parent_dir} is itself a bag; pass the directory that contains the "
            "zip files to bag instead"
        )
    for name in OUTPUT_DIR_NAMES:
        output_dir = parent_dir / name
        if output_dir.exists() and not output_dir.is_dir():
            sys.exit(f"Error: {output_dir} exists but is not a directory")


def list_zips(parent_dir: Path) -> list[Path]:
    """Return the zip files in parent_dir sorted by name, or exit if it can't be listed.

    Subdirectories (including the output directories) are ignored, and so are hidden
    files such as the "._<name>.zip" metadata files macOS leaves on network and external
    drives. The list is read once up front, so zips renamed during the run don't affect it.
    """
    try:
        return sorted(
            path for path in parent_dir.iterdir()
            if path.suffix.casefold() == ".zip"
            and not path.name.startswith(".")
            and path.is_file()
        )
    except OSError as error:
        sys.exit(f"Error: cannot list {parent_dir}: {error}")


# --- Running zips in parallel (--jobs) ----------------------------------------------------

# Set in each worker process by init_worker. Once it is set, workers start no more zips.
stop_requested = None
# Set in a worker by request_stop when Ctrl+C arrives between zips.
stop_pressed = False


def process_in_parallel(
    zip_paths: list[Path], settings: Settings, jobs: int, counts: Counter[Outcome]
) -> None:
    """Process the zips in jobs worker processes, adding each outcome to counts.

    Zips that would be extracted into the same folder are processed by one worker, one
    after another in name order, so they never race for it and end up as they would in
    a sequential run. On Ctrl+C the zips in progress are cleaned up as in a sequential
    run (see process_zip), no more are started, and KeyboardInterrupt is raised once
    the workers have stopped, with the zips they finished counted.
    """
    groups: dict[str, list[Path]] = {}
    for zip_path in zip_paths:
        # Names macOS and Windows treat as the same folder go in the same group.
        groups.setdefault(normalize_key(folder_name(zip_path)), []).append(zip_path)

    context = multiprocessing.get_context()
    stop_event = context.Event()
    # Workers are started with Ctrl+C ignored, which they inherit. A spawned worker
    # imports this script and its dependencies before init_worker runs, and Ctrl+C then
    # would kill it and break the whole pool. The submit calls start every worker the
    # pool will use, so it is only ignored here for that long.
    previous_handler = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        executor = ProcessPoolExecutor(
            max_workers=jobs, mp_context=context, initializer=init_worker, initargs=(stop_event,)
        )
        futures = {
            executor.submit(process_zips, group, settings): group for group in groups.values()
        }
    except BaseException:
        signal.signal(signal.SIGINT, previous_handler)
        raise

    try:
        # Ctrl+C is handled from here on, inside the try.
        signal.signal(signal.SIGINT, previous_handler)
        wait(futures)
        # From here Ctrl+C is held off: stopping the pool and counting every result must
        # finish, or the summary could miss zips that are on disk.
        hold = InterruptHold()
        interrupted = False
    except KeyboardInterrupt:
        # Workers start no more zips; the ones in progress clean up after themselves.
        # Ctrl+C is ignored now (see interrupt_once).
        stop_event.set()
        hold = InterruptHold()
        interrupted = True
    try:
        executor.shutdown(wait=True, cancel_futures=True)
        for future, group in futures.items():
            if future.cancelled():
                continue
            try:
                outcomes = future.result()
            except BaseException as error:
                if interrupted:
                    # Stopped by the interrupt before finishing, so not counted, like the
                    # zip in progress in a sequential run.
                    continue
                # A worker process died (e.g. it was killed), which stops the whole pool,
                # so every group not finished by then ends up here. Their zips may not
                # have been touched; any without a folder in an output directory are
                # processed on the next run.
                counts[Outcome.FAILED] += len(group)
                names = ", ".join(zip_path.name for zip_path in group)
                print_status(f"FAILED: {names} (worker process stopped: {error!r})", error=True)
                continue
            counts.update(outcomes)
    finally:
        interrupted = hold.release() or interrupted
    if interrupted:
        raise KeyboardInterrupt


def init_worker(stop_event) -> None:
    """Set up a worker process for process_in_parallel."""
    global stop_requested
    stop_requested = stop_event
    # A spawned worker starts with new output streams and logging set up.
    escape_unencodable_output()
    hide_bagit_interrupt_logs()
    # Outside a zip, Ctrl+C only stops the worker before its next zip (see process_zips),
    # so an idle worker isn't killed by it and doesn't print a traceback.
    signal.signal(signal.SIGINT, request_stop)


def request_stop(signum, frame) -> None:
    """Worker SIGINT handler while no zip is being processed: start no more zips.

    Sets a plain flag rather than stop_requested: an Event uses a lock, which the worker
    might be holding (checking the Event) when the signal arrives.
    """
    global stop_pressed
    stop_pressed = True


def process_zips(zip_paths: list[Path], settings: Settings) -> list[Outcome]:
    """Process zip_paths one after another in a worker process and return their outcomes.

    Stops early if the run is interrupted. The zip that was in progress then isn't
    counted, as in a sequential run, unless it was already being recorded (see
    ZipInterrupted).
    """
    outcomes = []
    for zip_path in zip_paths:
        try:
            signal.signal(signal.SIGINT, interrupt_once)
            # Checked once Ctrl+C is handled for this zip, so a press just before it,
            # which request_stop recorded, isn't missed.
            if stop_pressed or stop_requested.is_set():
                break
            outcomes.append(process_zip_safely(zip_path, settings))
        except KeyboardInterrupt as interrupt:
            if isinstance(interrupt, ZipInterrupted):
                outcomes.append(interrupt.outcome)
            # Set here as well as in the main process, so no worker starts another zip
            # in the meantime.
            stop_requested.set()
            break
        finally:
            signal.signal(signal.SIGINT, request_stop)
    return outcomes


if __name__ == "__main__":
    sys.exit(main())
