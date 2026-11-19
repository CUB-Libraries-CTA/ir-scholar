"""Run bagit_builder.py and press Ctrl+C at a moment too short to hit by hand (used by
test_interrupt_while_recording.py).

Usage: INTERRUPT_AT=recording|cleanup python interrupt_runner.py PARENT_DIR [options]

  recording  Ctrl+C right after the first bag lands in processed/, before its zip is
             renamed and counted.
  cleanup    Ctrl+C when bagging starts, then a second Ctrl+C while the half-bagged
             folder is being removed.

Each Ctrl+C goes to the whole process group, as from a terminal, so run this in its own
session. The wrappers are installed when this file is imported, so --jobs workers (which
import it as __mp_main__) get them too; marker files next to PARENT_DIR make sure each
Ctrl+C is sent only once, by whichever process gets there first.
"""

import os
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bagit_builder  # noqa: E402

MARKERS = Path(sys.argv[1]).parent
MODE = os.environ["INTERRUPT_AT"]


def press_ctrl_c_once(marker_name: str) -> None:
    """Send SIGINT to the process group, unless marker_name says it was sent already."""
    try:
        os.close(os.open(MARKERS / marker_name, os.O_CREAT | os.O_EXCL))
    except FileExistsError:
        return
    os.killpg(os.getpgrp(), signal.SIGINT)


def wrap(name, before=None, after=None):
    """Replace bagit_builder.<name> with a version that calls before/after around it."""
    original = getattr(bagit_builder, name)

    def wrapper(*args, **kwargs):
        if before:
            before(*args)
        result = original(*args, **kwargs)
        if after:
            after(*args)
        return result

    setattr(bagit_builder, name, wrapper)


if MODE == "recording":
    def after_move(folder, target_dir):
        if target_dir.name == bagit_builder.PROCESSED_DIR_NAME:
            press_ctrl_c_once("interrupt_sent")

    wrap("move_to", after=after_move)
elif MODE == "cleanup":
    wrap("bag_folder", before=lambda *args: press_ctrl_c_once("interrupt_sent"))
    wrap("remove_tree_quietly", before=lambda *args: press_ctrl_c_once("second_interrupt_sent"))
else:
    sys.exit(f"unknown INTERRUPT_AT: {MODE}")

if __name__ == "__main__":
    sys.exit(bagit_builder.main())
