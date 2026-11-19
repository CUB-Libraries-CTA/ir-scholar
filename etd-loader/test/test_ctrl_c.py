"""Ctrl+C partway through a run, sent to the whole process group as a terminal does.

Whenever it lands, the run must stop with exit code 130, clean up the zips it was working
on, start no more, print no tracebacks and report a summary that matches what is on
disk. macOS and Linux only (process groups).
"""

import os
import signal
import subprocess
import sys
import time

import bagit
import pytest

from helpers import (
    PDF,
    PDF_NAME,
    RECORD_NAME,
    SCRIPT,
    assert_counts_match_disk,
    copy_zips,
    leftover_folders,
    make_big_zips,
    make_zip,
    output_dir_name,
    record,
    renamed_zips,
    summary,
    where_folders_went,
)

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(sys.platform == "win32", reason="needs POSIX process groups"),
]
PROCESSED = output_dir_name("created")


@pytest.fixture(scope="module")
def zips_to_interrupt(tmp_path_factory):
    """Zips that take long enough to bag that a run can be interrupted partway, plus a
    rejected and an unaccepted one."""
    directory = tmp_path_factory.mktemp("big_zips")
    make_big_zips(directory, count=24, size_mb=40)
    make_zip(directory, "etdadmin_upload_1", {RECORD_NAME: record(repository=False), PDF_NAME: PDF})
    make_zip(directory, "etdadmin_upload_2", {RECORD_NAME: record(acceptance="0"), PDF_NAME: PDF})
    return directory


@pytest.mark.parametrize("jobs", ["1", "3"])
@pytest.mark.parametrize("delay", [0.5, 1.0, 1.5])
def test_ctrl_c_stops_cleanly(zips_to_interrupt, tmp_path, jobs, delay):
    parent = copy_zips(zips_to_interrupt, tmp_path / "run")
    zip_count = len(list(parent.glob("*.zip")))
    process = subprocess.Popen(
        [sys.executable, str(SCRIPT), str(parent), "--jobs", jobs],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=parent,
        start_new_session=True,
    )
    time.sleep(delay)
    if process.poll() is not None:
        process.communicate()
        pytest.skip(f"the run finished within {delay}s, before it could be interrupted")
    os.killpg(process.pid, signal.SIGINT)
    output, _ = process.communicate(timeout=300)

    if process.returncode == -signal.SIGINT:
        if "\nDone. " in output:
            # The run had finished and Python was exiting when Ctrl+C arrived.
            pytest.skip(f"the run finished just before the interrupt at {delay}s")
        # Interrupted while Python was still starting the script, before main() ran:
        # Python's own KeyboardInterrupt, and nothing may have been touched.
        assert sorted(path.name for path in parent.iterdir()) == sorted(
            path.name for path in zips_to_interrupt.glob("*.zip")
        )
        return

    assert process.returncode == 130, output
    assert "Interrupted by user." in output
    assert "Traceback" not in output
    assert "worker process stopped" not in output

    # The summary matches what is on disk.
    counts = summary(output)
    assert_counts_match_disk(counts, parent)
    assert counts["failed"] == 0

    # Finished bags are valid and only their zips were renamed.
    bag_names = sorted(name for name, directory in where_folders_went(parent).items() if directory == PROCESSED)
    for name in bag_names:
        bagit.Bag(str(parent / PROCESSED / name)).validate()
    assert renamed_zips(parent) == bag_names
    assert len(list(parent.glob("*.zip"))) + len(bag_names) == zip_count

    # Nothing half-done is left: no extracted folders or temporary extraction directories
    # (which are hidden folders in parent too).
    assert leftover_folders(parent) == []

    # Every process of the run has exited.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.2)
    else:
        pytest.fail("processes of the interrupted run are still running")
