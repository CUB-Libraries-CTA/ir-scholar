"""Safely extract a zip file into a new directory.

``extract_zip`` is the entry point. It never modifies the zip, never leaves a partially
extracted directory behind, refuses zips whose files would overwrite each other or that
use Windows device names, and leaves out macOS Finder metadata (``__MACOSX/`` and
``.DS_Store``). A zip that wraps everything in a single top-level folder is unwrapped.

``remove_tree`` and ``remove_tree_quietly`` delete directories, including read-only
files on Windows.

Warnings are printed with ``console.py``, which must be kept in the same directory.

Requires Python 3.12 or newer.
"""

import ntpath
import os
import posixpath
import shutil
import secrets
import stat
import unicodedata
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from console import warn

# Prefix for the temporary directories zips are extracted into before being renamed.
EXTRACT_PREFIX = ".bagit_extract_"
# Characters zipfile replaces with "_" when extracting on Windows.
WINDOWS_ILLEGAL_CHARS = ':<>|"?*'
ILLEGAL_CHARS_TO_UNDERSCORES = str.maketrans(
    WINDOWS_ILLEGAL_CHARS, "_" * len(WINDOWS_ILLEGAL_CHARS)
)
# Device names Windows reserves in every directory, with or without an extension.
WINDOWS_RESERVED_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"{port}{number}" for port in ("COM", "LPT") for number in "123456789¹²³"]
)


class PathKeys(NamedTuple):
    """Where a zip member is extracted to, under Windows rules and under POSIX rules.

    Both are passed through normalize_key, so two members with an equal key would be
    extracted to the same file.
    """

    windows: str
    posix: str


# --- Extracting -------------------------------------------------------------------------


def extract_zip(zip_path: Path, folder: Path) -> None:
    """Extract zip_path into the new directory folder, leaving the zip file unchanged.

    Finder metadata is left out (see select_members) and a single top-level folder is
    unwrapped (see unwrap_single_folder), so the payload and any existing bagit.txt end
    up at the top of folder. Raises FileExistsError if folder already exists, and
    ValueError if the members can't all be extracted safely (see check_member_names).

    The zip is extracted into a temporary directory first and then renamed into place,
    so a corrupt zip, a full disk or Ctrl+C never leaves a partially extracted folder or
    the temporary directory behind. (If Ctrl+C arrives after the rename, folder is
    complete; the caller decides whether to keep it.) zipfile strips absolute paths and
    ".." from member names, so nothing is written outside the temporary directory.
    """
    # Checked up front because the final rename doesn't always refuse: on macOS and
    # Linux it silently replaces an existing empty directory.
    if folder.exists(follow_symlinks=False):
        raise FileExistsError(f"{folder} already exists, so {zip_path.name} was not extracted")
    # Named before it is created, and created inside the try, so the finally removes it
    # even if Ctrl+C arrives the moment it exists. (tempfile.mkdtemp would create it
    # before the try could protect it.) mkdir refuses an existing name.
    temp_dir = folder.parent / f"{EXTRACT_PREFIX}{secrets.token_hex(8)}"
    try:
        temp_dir.mkdir(mode=0o700)
        contents = temp_dir / "contents"
        # Created up front so an empty zip still produces a folder.
        contents.mkdir()
        with zipfile.ZipFile(zip_path) as archive:
            members = select_members(archive.infolist())
            check_member_names(members)
            archive.extractall(contents, members)
        unwrap_single_folder(contents).rename(folder)
    finally:
        if temp_dir.exists():
            remove_tree_despite_interrupt(temp_dir)


def unwrap_single_folder(contents: Path) -> Path:
    """Return the directory whose contents become the extracted folder.

    That is the only entry in contents if it is a folder, as when a folder is zipped on
    macOS or Windows, and otherwise contents itself.
    """
    entries = list(contents.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return contents


# --- Choosing which members to extract --------------------------------------------------


def select_members(members: list[zipfile.ZipInfo]) -> list[zipfile.ZipInfo]:
    """Return the members to extract, leaving out macOS Finder metadata.

    The __MACOSX/ folder is always left out. .DS_Store files are left out too, except
    inside an existing bag, whose manifests decide which files belong in it.
    """
    members = [member for member in members if not is_macos_metadata(member)]
    bag_root = find_bag_root(members)
    return [
        member for member in members
        if not is_ds_store(member) or is_inside_bag(member, bag_root)
    ]


def is_macos_metadata(member: zipfile.ZipInfo) -> bool:
    """Return True for members of the __MACOSX/ folder that the macOS Finder adds to zips.

    It only holds Finder metadata ("._" files) for the real files, so it isn't payload.
    """
    return any(key.split("/")[0] == "__macosx" for key in path_keys(member.filename))


def is_ds_store(member: zipfile.ZipInfo) -> bool:
    """Return True for the .DS_Store files macOS creates to store Finder view settings."""
    return not member.is_dir() and any(
        key.rsplit("/", 1)[-1] == ".ds_store" for key in path_keys(member.filename)
    )


def find_bag_root(members: list[zipfile.ZipInfo]) -> str | None:
    """Return where the existing bag in members starts, or None if they aren't a bag.

    The result is "" if bagit.txt is at the top of the zip, or "<folder>/" if it is in a
    single top-level folder that will be unwrapped. .DS_Store files are ignored when
    looking for that folder, since outside a bag they are left out. Use is_inside_bag to
    test a member against the result.
    """
    paths = {path_keys(member.filename).posix for member in members if not is_ds_store(member)}
    paths.discard("")
    if "bagit.txt" in paths:
        return ""
    top_level = {path.split("/")[0] for path in paths}
    if len(top_level) == 1:
        folder = top_level.pop()
        if f"{folder}/bagit.txt" in paths:
            return f"{folder}/"
    return None


def is_inside_bag(member: zipfile.ZipInfo, bag_root: str | None) -> bool:
    """Return True if member is inside the bag that starts at bag_root (see find_bag_root).

    A bag at the top of the zip has the root "", which is falsy, so None is checked
    explicitly.
    """
    return bag_root is not None and path_keys(member.filename).posix.startswith(bag_root)


# --- Checking member names --------------------------------------------------------------


def check_member_names(members: list[zipfile.ZipInfo]) -> None:
    """Raise ValueError if members can't all be extracted safely on every OS.

    That is when two of them would be extracted to the same path, which would make the
    later file silently overwrite the earlier one, or when a name is a Windows device
    name.
    """
    # The first member seen for each (rules, key) pair, e.g. ("posix", "thesis/a.pdf").
    seen: dict[tuple[str, str], str] = {}
    for member in members:
        keys = path_keys(member.filename)
        if any(is_windows_reserved(part) for key in keys for part in key.split("/")):
            raise ValueError(
                f"zip contains {member.filename!r}, which uses a reserved device name on Windows"
            )
        if member.is_dir():
            continue
        for rules, key in keys._asdict().items():
            if (rules, key) in seen:
                raise ValueError(
                    f"zip contains files that would overwrite each other: "
                    f"{seen[rules, key]!r} and {member.filename!r}"
                )
            seen[rules, key] = member.filename


def is_windows_reserved(name: str) -> bool:
    """Return True if name is a Windows device name such as NUL or com1.txt.

    Windows sends data written to these names to the device instead of a file, so a
    file with that name would silently go missing.
    """
    # Windows ignores trailing dots and spaces, and everything from the first dot on, so
    # "nul", "NUL.txt" and "com1 .log" are all device names.
    base_name = name.rstrip(" .").split(".")[0].strip()
    return base_name.upper() in WINDOWS_RESERVED_NAMES


def path_keys(member_name: str) -> PathKeys:
    """Return keys that are equal for zip member names extracted to the same file.

    zipfile cleans up member names differently on Windows and POSIX, so there is one key
    for each. Both are checked on every OS so a zip is handled the same everywhere.
    """
    return PathKeys(
        windows=normalize_key(windows_path(member_name)),
        posix=normalize_key(posix_path(member_name)),
    )


def windows_path(member_name: str) -> str:
    r"""Return where zipfile extracts member_name on Windows, relative to the target.

    zipfile drops the root (a drive letter, \\server\share or leading slashes) and "."
    and ".." parts, treats backslashes as separators, replaces illegal characters with
    "_" and strips trailing dots and spaces.
    """
    relative = ntpath.splitroot(member_name)[2].replace("\\", "/")
    parts = (
        part.translate(ILLEGAL_CHARS_TO_UNDERSCORES).rstrip(" .")
        for part in usable_parts(relative)
    )
    # Parts that were only dots and spaces are now empty, and are dropped too.
    return "/".join(part for part in parts if part)


def posix_path(member_name: str) -> str:
    """Return where zipfile extracts member_name on macOS and Linux, relative to the target.

    zipfile drops leading slashes and "." and ".." parts.
    """
    return "/".join(usable_parts(posixpath.splitroot(member_name)[2]))


def usable_parts(path: str) -> list[str]:
    """Split a "/"-separated path into its parts, dropping empty, "." and ".." parts."""
    return [part for part in path.split("/") if part not in ("", ".", "..")]


def normalize_key(path: str) -> str:
    """Make path compare equal regardless of case and Unicode normalization, which macOS
    and Windows ignore in file names."""
    return unicodedata.normalize("NFC", path).casefold()


# --- Deleting directories --------------------------------------------------------------


def remove_tree(path: Path) -> None:
    """Delete a directory tree, including read-only files on Windows."""
    shutil.rmtree(path, onexc=clear_read_only_and_retry)


def remove_tree_quietly(path: Path) -> None:
    """Delete a directory tree, warning instead of failing if it can't be removed."""
    try:
        remove_tree(path)
    except OSError as error:
        warn(f"could not remove {path}: {error}")


def remove_tree_despite_interrupt(path: Path) -> None:
    """remove_tree_quietly, finishing the removal even if Ctrl+C interrupts it, and then
    passing the KeyboardInterrupt on."""
    try:
        remove_tree_quietly(path)
    except KeyboardInterrupt:
        remove_tree_quietly(path)
        raise


def clear_read_only_and_retry(
    func: Callable[[str], object], path: str, error: BaseException
) -> None:
    """shutil.rmtree onexc handler that deletes read-only files on Windows.

    Windows refuses to delete a read-only file until its read-only flag is cleared.
    """
    if os.name != "nt" or os.lstat(path).st_mode & stat.S_IWRITE:
        raise error
    os.chmod(path, stat.S_IWRITE)
    func(path)
