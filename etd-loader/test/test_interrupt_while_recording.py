"""Ctrl+C at moments too short to hit by pressing it during a real run.

Just after a bag is moved into processed/, before its zip is renamed and counted (see
interrupt_runner.py): that zip must still be finished and counted. Before this was
handled, the bag ended up in processed/ uncounted with its zip not renamed, so the next
run extracted the zip again and failed it as a duplicate.

A second Ctrl+C during the cleanup the first one started: it must be ignored. Before
this was handled, it stopped the cleanup, leaving the half-bagged folder behind to block
its zip on the next run.

Just after extraction: the extracted folder must be removed.

While recording fails: a Ctrl+C held off during recording must still stop the run.

Between two zips in a --jobs worker: the worker must not start its next zip.
"""

import os
import signal
import sys
import threading
from pathlib import Path

import pytest

from helpers import (
    PDF,
    PDF_NAME,
    RECORD_NAME,
    TEST_DIR,
    assert_counts_match_disk,
    bagit_builder,
    leftover_folders,
    make_zip,
    output_dir_name,
    record,
    renamed_zips,
    run_builder,
    summary,
    where_folders_went,
)

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="needs POSIX process groups")
RUNNER = TEST_DIR / "interrupt_runner.py"
PROCESSED = output_dir_name("created")
ZIP_COUNT = 6


def run_interrupted(tmp_path, interrupt_at, jobs):
    """Run bagit_builder on ZIP_COUNT good zips with Ctrl+C pressed at interrupt_at (see
    interrupt_runner.py) and return (parent directory, result)."""
    parent = tmp_path / "run"
    parent.mkdir()
    for number in range(ZIP_COUNT):
        make_zip(parent, f"etdadmin_upload_{number}", {RECORD_NAME: record(), PDF_NAME: PDF})
    result = run_builder(
        parent, "--jobs", jobs, runner=RUNNER, start_new_session=True,
        env={**os.environ, "INTERRUPT_AT": interrupt_at},
    )
    assert (tmp_path / "interrupt_sent").exists(), "the interrupt was never sent"
    assert result.returncode == 130, result.stdout
    assert "Traceback" not in result.stdout
    return parent, result


def assert_next_run_finishes_the_rest(parent, bags):
    """The next run picks up only the zips that weren't finished, and fails none."""
    rerun = run_builder(parent, "--jobs", "1")
    assert rerun.returncode == 0, rerun.stdout
    assert summary(rerun.stdout)["created"] == ZIP_COUNT - len(bags)


@pytest.mark.parametrize("jobs", ["1", "3"])
def test_bag_moved_into_processed_is_still_recorded(tmp_path, jobs):
    parent, result = run_interrupted(tmp_path, "recording", jobs)
    bags = sorted(name for name, directory in where_folders_went(parent).items() if directory == PROCESSED)
    assert bags, "no bag reached processed/"
    assert renamed_zips(parent) == bags
    assert summary(result.stdout)["created"] == len(bags)
    assert_next_run_finishes_the_rest(parent, bags)


@pytest.mark.parametrize("jobs", ["1", "3"])
def test_second_ctrl_c_during_cleanup_is_ignored(tmp_path, jobs):
    parent, result = run_interrupted(tmp_path, "cleanup", jobs)
    assert (tmp_path / "second_interrupt_sent").exists(), "the second interrupt was never sent"
    # The cleanup finished: no half-bagged folder is left, and the summary is complete.
    assert leftover_folders(parent) == []
    assert_counts_match_disk(summary(result.stdout), parent)
    bags = sorted(name for name, directory in where_folders_went(parent).items() if directory == PROCESSED)
    assert renamed_zips(parent) == bags
    assert_next_run_finishes_the_rest(parent, bags)


@pytest.fixture
def restore_ctrl_c():
    """Put the test process's own Ctrl+C handler back after a test that changes it."""
    previous = signal.getsignal(signal.SIGINT)
    yield
    signal.signal(signal.SIGINT, previous)


def test_ctrl_c_held_while_recording_still_stops_if_recording_fails(tmp_path, monkeypatch, restore_ctrl_c):
    """Ctrl+C is held off while the folder is moved; if the move then fails, the error
    is reported as usual and the run still stops, with the zip counted as failed."""
    zip_path = make_zip(tmp_path, "etdadmin_upload_8", {RECORD_NAME: record(), PDF_NAME: PDF})

    def ctrl_c_then_fail(*args):
        os.kill(os.getpid(), signal.SIGINT)  # held off: recorded, not raised
        raise OSError("can't move the folder")

    monkeypatch.setattr(bagit_builder, "sort_folder", ctrl_c_then_fail)
    settings = bagit_builder.Settings(parent_dir=tmp_path, metadata={}, processes=1, s3_prefix=None)
    with pytest.raises(bagit_builder.ZipInterrupted) as stopped:
        bagit_builder.process_zip(zip_path, settings)
    assert stopped.value.outcome is bagit_builder.Outcome.FAILED


def test_ctrl_c_between_zips_stops_a_worker(monkeypatch, restore_ctrl_c):
    """Ctrl+C reaching a --jobs worker just after it finished a zip, before the main
    process has asked the workers to stop: the worker must not start its next zip."""
    processed = []

    def fake_process_zip_safely(zip_path, settings):
        processed.append(zip_path.name)
        return bagit_builder.Outcome.CREATED

    real_signal = signal.signal

    def signal_then_ctrl_c(signum, handler):
        previous = real_signal(signum, handler)
        if handler is bagit_builder.request_stop and processed:
            # Ctrl+C the moment the worker is between zips.
            os.kill(os.getpid(), signal.SIGINT)
        return previous

    monkeypatch.setattr(bagit_builder, "process_zip_safely", fake_process_zip_safely)
    monkeypatch.setattr(bagit_builder, "stop_requested", threading.Event())
    monkeypatch.setattr(bagit_builder, "stop_pressed", False)
    monkeypatch.setattr(signal, "signal", signal_then_ctrl_c)
    outcomes = bagit_builder.process_zips([Path("first.zip"), Path("second.zip")], settings=None)
    assert processed == ["first.zip"]
    assert outcomes == [bagit_builder.Outcome.CREATED]


def test_ctrl_c_just_after_extraction_removes_the_folder(tmp_path, monkeypatch):
    """Ctrl+C after extract_zip has renamed the folder into place, before bagging starts:
    the folder is removed so it doesn't block the next run, and the zip is untouched."""
    zip_path = make_zip(tmp_path, "etdadmin_upload_7", {RECORD_NAME: record(), PDF_NAME: PDF})
    real_extract_zip = bagit_builder.extract_zip

    def extract_then_ctrl_c(*args):
        real_extract_zip(*args)
        raise KeyboardInterrupt

    monkeypatch.setattr(bagit_builder, "extract_zip", extract_then_ctrl_c)
    settings = bagit_builder.Settings(parent_dir=tmp_path, metadata={}, processes=1, s3_prefix=None)
    with pytest.raises(KeyboardInterrupt):
        bagit_builder.process_zip(zip_path, settings)
    assert sorted(path.name for path in tmp_path.iterdir()) == ["etdadmin_upload_7.zip"]
