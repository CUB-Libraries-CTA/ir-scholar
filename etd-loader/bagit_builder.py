"""Create BagIt bags for every directory within a parent directory.

Each subdirectory of ``parent_dir`` is converted in place into a BagIt bag and
validated. Subdirectories that are already bags (contain ``bagit.txt``) are only
validated.
Afterwards each one is moved into one of these directories inside ``parent_dir``:

- ``completed/``: the bag was created and validated, or the directory was already
  a valid bag that wasn't in ``completed/`` yet.
- ``skipped/``: the directory was already a bag with the same name as one in
  ``completed/``. A ``bagit_skipped.log`` explaining why is added.
- ``failed/``: bagging or validation failed. A ``bagit_errors.log`` describing the
  error(s) is added. Folders that this script tried to bag are restored to their
  original contents first.

Usage:
    python bagit_builder.py PARENT_DIR [--contact-name NAME]
        [--source-organization ORG] [--processes N]
"""

import argparse
import os
import shutil
import stat
import sys
import tempfile
import traceback
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path

import bagit

ERROR_LOG_NAME = "bagit_errors.log"
SKIPPED_LOG_NAME = "bagit_skipped.log"
COMPLETED_DIR_NAME = "completed"
SKIPPED_DIR_NAME = "skipped"
FAILED_DIR_NAME = "failed"
# Prefix for the temporary backup directories created inside parent_dir.
BACKUP_PREFIX = ".bagit_backup_"


def positive_int(value: str) -> int:
    """argparse type that accepts only integers of 1 or more."""
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, got {number}")
    return number


def parse_args() -> argparse.Namespace:
    """Parse and return the command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Create a BagIt bag for each directory within a parent directory.",
        epilog=(
            "Directories that already contain bagit.txt are only validated, not bagged.\n"
            "Each processed directory is moved into one of these directories inside parent_dir:\n"
            f"  {COMPLETED_DIR_NAME}/  the bag was created and validated\n"
            f"              (or was already a valid bag not yet in {COMPLETED_DIR_NAME}/)\n"
            f"  {SKIPPED_DIR_NAME}/    the directory was already a bag and is already in\n"
            f"              {COMPLETED_DIR_NAME}/; the reason is written to {SKIPPED_LOG_NAME}\n"
            f"  {FAILED_DIR_NAME}/     bagging or validation failed; the error(s) are written\n"
            f"              to {ERROR_LOG_NAME} (folders this script tried to bag are\n"
            "              restored to their original files first)"
        ),
        # Keep the epilog's line breaks and indentation instead of rewrapping it.
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("parent_dir", type=Path, help="Directory containing the folders to bag")
    parser.add_argument("--contact-name", help="Value for the Contact-Name bag-info field")
    parser.add_argument(
        "--source-organization", help="Value for the Source-Organization bag-info field"
    )
    parser.add_argument(
        "--processes",
        type=positive_int,
        default=4,
        help="Number of processes used for checksums (default: %(default)s)",
    )
    return parser.parse_args()


def build_metadata(args: argparse.Namespace) -> dict[str, str]:
    """Return the bag-info.txt fields, including only the values given on the command line."""
    metadata: dict[str, str] = {}
    if args.contact_name:
        metadata["Contact-Name"] = args.contact_name
    if args.source_organization:
        metadata["Source-Organization"] = args.source_organization
    return metadata


def format_error(error: Exception) -> str:
    """Format an exception and its traceback as text for the error log.

    bagit's ``BagValidationError`` stores the individual validation failures in
    ``details``, so they are listed after the traceback when present.
    """
    message = "".join(traceback.format_exception(error))
    details = getattr(error, "details", None)
    if details:
        message += "\nDetails:\n" + "\n".join(f"  - {detail}" for detail in details)
    return message


def write_log(folder: Path, log_name: str, status: str, body: str) -> None:
    """Write a log file named log_name into folder.

    status completes the heading "BagIt processing <status> at <time>".
    """
    # Explicit UTF-8 so non-ASCII file names in tracebacks are written the same on every OS.
    (folder / log_name).write_text(
        f"BagIt processing {status} at {datetime.now().isoformat()}\n"
        f"Folder: {folder}\n\n"
        f"{body}\n",
        encoding="utf-8",
    )


def remove_old_logs(folder: Path) -> None:
    """Remove the error or skipped log left by a previous run, if there is one."""
    for log_name in (ERROR_LOG_NAME, SKIPPED_LOG_NAME):
        (folder / log_name).unlink(missing_ok=True)


def move_to(folder: Path, target_dir: Path) -> Path:
    """Move a folder into target_dir and return its new path.

    If a folder with the same name already exists there, the folder is left in place
    and its current path is returned.
    """
    destination = target_dir / folder.name
    if destination.exists():
        print(f"WARNING: {destination} already exists, leaving {folder} in place", file=sys.stderr)
        return folder
    target_dir.mkdir(exist_ok=True)
    shutil.move(folder, destination)
    return destination


def is_read_only_on_windows(path: str) -> bool:
    """Return True if path is a read-only file on Windows."""
    return os.name == "nt" and not os.stat(path).st_mode & stat.S_IWRITE


def link_or_copy(src: str, dst: str) -> None:
    """copytree copy_function that hard-links files, except read-only files on Windows.

    Windows only deletes a read-only file after its read-only flag is cleared, and that
    flag is shared by every hard link to the file. Copying those files keeps deleting
    one copy from changing the other.
    """
    if is_read_only_on_windows(src):
        shutil.copy2(src, dst)
    else:
        os.link(src, dst)


def clear_read_only_and_retry(
    func: Callable[[str], object], path: str, error: BaseException
) -> None:
    """shutil.rmtree onexc handler that deletes read-only files on Windows."""
    if not is_read_only_on_windows(path):
        raise error
    os.chmod(path, stat.S_IWRITE)
    func(path)


def back_up_folder(folder: Path, backup: Path) -> None:
    """Back up folder to backup, using hard links where the filesystem supports them.

    make_bag only renames the original files and adds new ones; it never edits them.
    Hard links therefore keep the originals safe without copying any file data.
    """
    try:
        shutil.copytree(folder, backup, symlinks=True, copy_function=link_or_copy)
    except OSError:
        # Hard links aren't supported here (e.g. FAT/exFAT drives or some network
        # shares), so copy instead.
        shutil.rmtree(backup, ignore_errors=True)
        shutil.copytree(folder, backup, symlinks=True)


def restore_folder(folder: Path, backup: Path) -> None:
    """Replace folder with its backup.

    The backup is on the same filesystem, so this is a rename rather than a copy.
    """
    shutil.rmtree(folder, onexc=clear_read_only_and_retry)
    backup.rename(folder)


def is_same_dir(folder: Path, dirs: Iterable[Path]) -> bool:
    """Return True if folder is the same directory as any existing directory in dirs.

    Compares the directories themselves rather than their paths, because on
    case-insensitive file systems (the macOS and Windows defaults) "Completed" and
    "completed" are the same directory.
    """
    return any(other.exists() and folder.samefile(other) for other in dirs)


def validate_existing_bag(folder: Path, processes: int) -> bool:
    """Validate a folder that is already a bag, writing the error log if it is invalid.

    Validation only reads the bag, so no backup or restore is needed.
    Returns True if the bag is valid.
    """
    remove_old_logs(folder)
    try:
        bagit.Bag(str(folder)).validate(processes=processes)
    except Exception as error:
        write_log(
            folder,
            ERROR_LOG_NAME,
            "failed",
            "Existing bag failed validation:\n" + format_error(error),
        )
        return False
    return True


def process_folder(folder: Path, metadata: dict[str, str], processes: int) -> bool:
    """Bag and validate a folder. On failure, restore the original contents and log the errors.

    Returns True if the bag was created and validated successfully.
    """
    # Remove logs from a previous run so they aren't included in the bag.
    remove_old_logs(folder)

    # make_bag rearranges the folder in place, so back it up first. The backup is kept
    # next to the folder so hard links and the restore rename stay on one filesystem.
    # The temporary directory (and the backup) is deleted when this block exits.
    with tempfile.TemporaryDirectory(
        dir=folder.parent, prefix=BACKUP_PREFIX, ignore_cleanup_errors=True
    ) as temp_dir:
        backup = Path(temp_dir) / "original"
        try:
            back_up_folder(folder, backup)
        except OSError as error:
            # Nothing has been modified yet, so the folder is still in its original state.
            write_log(
                folder,
                ERROR_LOG_NAME,
                "failed",
                "Failed to back up folder before bagging; bag was not created:\n"
                + format_error(error),
            )
            return False

        try:
            # Pass a copy of metadata because make_bag adds fields (e.g. Bagging-Date) to it.
            bag = bagit.make_bag(str(folder), dict(metadata), processes=processes)
            bag.validate(processes=processes)
        except Exception as error:
            errors = [format_error(error)]
        else:
            return True

        try:
            restore_folder(folder, backup)
        except OSError as error:
            errors.append("Failed to restore original folder contents:\n" + format_error(error))
            # Move the backup out of the temporary directory so the original files are not lost.
            preserved = folder.with_name(f"{folder.name}.original-{datetime.now():%Y%m%d%H%M%S}")
            backup.rename(preserved)
            errors.append(f"Original folder contents preserved at: {preserved}")

        # The folder may be missing if restoring failed after rmtree, so recreate it for the log.
        folder.mkdir(exist_ok=True)
        write_log(folder, ERROR_LOG_NAME, "failed", "\n\n".join(errors))
        return False


def main() -> int:
    """Bag each subdirectory of parent_dir and sort it into completed, skipped or failed.

    Returns the process exit code: 0 if no folder failed, otherwise 1.
    """
    args = parse_args()
    parent_dir: Path = args.parent_dir.resolve()
    if not parent_dir.is_dir():
        sys.exit(f"Error: {parent_dir} is not a directory")

    metadata = build_metadata(args)
    completed_dir = parent_dir / COMPLETED_DIR_NAME
    skipped_dir = parent_dir / SKIPPED_DIR_NAME
    failed_dir = parent_dir / FAILED_DIR_NAME
    output_dirs = {completed_dir, skipped_dir, failed_dir}
    succeeded, moved, failed, skipped = 0, 0, 0, 0

    # The listing is read once up front, so folders moved during the loop don't affect it.
    for folder in sorted(parent_dir.iterdir()):
        # Ignore plain files, this script's output directories and leftover backups.
        if (
            not folder.is_dir()
            or folder.name.startswith(BACKUP_PREFIX)
            or is_same_dir(folder, output_dirs)
        ):
            continue

        # bagit.txt marks a folder that is already a bag; bagging it again would nest bags.
        # An existing bag that is already in completed is a duplicate and is skipped.
        existing_bag = (folder / "bagit.txt").is_file()
        if existing_bag and (completed_dir / folder.name).exists():
            skipped += 1
            write_log(
                folder,
                SKIPPED_LOG_NAME,
                "skipped",
                f"This folder is already a bag, and a bag named {folder.name} already exists in "
                f"{completed_dir}. It was not validated or moved to {COMPLETED_DIR_NAME}/ so the "
                "existing bag is not overwritten.",
            )
            destination = move_to(folder, skipped_dir)
            print(f"SKIPPED: {destination} (already in {COMPLETED_DIR_NAME})")
            continue

        try:
            if existing_bag:
                # Existing bags are validated rather than re-bagged.
                print(f"Validating existing bag {folder}")
                succeeded_here = validate_existing_bag(folder, args.processes)
            else:
                print(f"Processing {folder}")
                succeeded_here = process_folder(folder, metadata, args.processes)
        except Exception:
            # Failure outside the bagging step (e.g. the error log could not be written).
            succeeded_here = False
            traceback.print_exc()

        if succeeded_here and existing_bag:
            moved += 1
            print(f"OK: {move_to(folder, completed_dir)} (already a bag)")
        elif succeeded_here:
            succeeded += 1
            print(f"OK: {move_to(folder, completed_dir)}")
        else:
            failed += 1
            destination = move_to(folder, failed_dir)
            log_path = destination / ERROR_LOG_NAME
            if log_path.exists():
                print(f"FAILED: {destination} (see {log_path})", file=sys.stderr)
            else:
                print(f"FAILED: {destination}", file=sys.stderr)

    print(
        f"Done. {succeeded} bag(s) created, {moved} existing bag(s) moved to "
        f"{COMPLETED_DIR_NAME}, {failed} failed, {skipped} skipped."
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
