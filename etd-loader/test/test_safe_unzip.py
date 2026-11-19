"""safe_unzip.extract_zip on its own."""

import os
import zipfile

import pytest

import safe_unzip
from helpers import make_zip
from safe_unzip import EXTRACT_PREFIX, extract_zip


@pytest.fixture
def small_zip(tmp_path):
    return make_zip(tmp_path, "small", {"record.xml": "<x/>", "thesis.pdf": b"pdf"})


def temporary_dirs(directory):
    return [path for path in directory.iterdir() if path.name.startswith(EXTRACT_PREFIX)]


def test_extracts_into_a_new_folder(small_zip, tmp_path):
    folder = tmp_path / "out"
    extract_zip(small_zip, folder)
    assert sorted(path.name for path in folder.iterdir()) == ["record.xml", "thesis.pdf"]
    assert temporary_dirs(tmp_path) == []


@pytest.mark.parametrize("existing", ["empty folder", "folder with a file", "file", "dangling symlink"])
def test_refuses_to_extract_over_anything_that_exists(small_zip, tmp_path, existing):
    target = tmp_path / "target"
    if existing == "empty folder":
        target.mkdir()
    elif existing == "folder with a file":
        target.mkdir()
        (target / "keep.txt").write_text("keep")
    elif existing == "file":
        target.write_text("file")
    else:
        os.symlink(tmp_path / "nowhere", target)
    before = sorted(path.name for path in target.iterdir()) if target.is_dir() else None

    with pytest.raises(FileExistsError, match="already exists"):
        extract_zip(small_zip, target)

    # Nothing was touched or left behind. (On macOS and Linux a plain rename would have
    # silently replaced the empty folder.)
    after = sorted(path.name for path in target.iterdir()) if target.is_dir() else None
    assert after == before
    assert temporary_dirs(tmp_path) == []


def test_unwraps_a_single_top_level_folder(tmp_path):
    zip_path = make_zip(tmp_path, "wrapped", {"wrapper/a.txt": "a", "wrapper/sub/b.txt": "b"})
    extract_zip(zip_path, tmp_path / "out")
    assert (tmp_path / "out" / "a.txt").is_file() and (tmp_path / "out" / "sub" / "b.txt").is_file()


def test_leaves_out_finder_metadata(tmp_path):
    zip_path = make_zip(tmp_path, "mac", {
        "a.txt": "a", ".DS_Store": "x", "__MACOSX/._a.txt": "x", "sub/.DS_Store": "x",
    })
    extract_zip(zip_path, tmp_path / "out")
    assert sorted(p.relative_to(tmp_path / "out").as_posix() for p in (tmp_path / "out").rglob("*")) == ["a.txt"]


# --- Ctrl+C at the worst moments --------------------------------------------------------
# Simulated by raising KeyboardInterrupt where Python would raise it for Ctrl+C.


def test_ctrl_c_the_moment_the_temporary_folder_exists(small_zip, tmp_path, monkeypatch):
    real_mkdir = os.mkdir

    def mkdir_then_ctrl_c(path, *args, **kwargs):
        real_mkdir(path, *args, **kwargs)
        if os.path.basename(path).startswith(EXTRACT_PREFIX):
            raise KeyboardInterrupt

    monkeypatch.setattr(os, "mkdir", mkdir_then_ctrl_c)
    with pytest.raises(KeyboardInterrupt):
        extract_zip(small_zip, tmp_path / "out")
    monkeypatch.undo()
    assert temporary_dirs(tmp_path) == []
    assert not (tmp_path / "out").exists()


def test_ctrl_c_during_extraction(small_zip, tmp_path, monkeypatch):
    def extractall_ctrl_c(self, *args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(zipfile.ZipFile, "extractall", extractall_ctrl_c)
    with pytest.raises(KeyboardInterrupt):
        extract_zip(small_zip, tmp_path / "out")
    assert temporary_dirs(tmp_path) == []
    assert not (tmp_path / "out").exists()


def test_ctrl_c_during_the_cleanup_still_cleans_up(small_zip, tmp_path, monkeypatch):
    real_remove_tree = safe_unzip.remove_tree
    calls = []

    def remove_tree_ctrl_c_once(path):
        calls.append(path)
        if len(calls) == 1:
            raise KeyboardInterrupt
        real_remove_tree(path)

    monkeypatch.setattr(safe_unzip, "remove_tree", remove_tree_ctrl_c_once)
    with pytest.raises(KeyboardInterrupt):
        extract_zip(small_zip, tmp_path / "out")
    assert temporary_dirs(tmp_path) == []
    # The extraction itself had finished; the caller decides whether to keep it.
    assert (tmp_path / "out" / "thesis.pdf").is_file()


def test_refuses_names_that_would_overwrite_each_other(tmp_path):
    zip_path = make_zip(tmp_path, "clash", {"Thesis.pdf": b"1", "thesis.pdf": b"2"})
    with pytest.raises(ValueError, match="overwrite each other"):
        extract_zip(zip_path, tmp_path / "out")
    assert not (tmp_path / "out").exists()
    assert temporary_dirs(tmp_path) == []
