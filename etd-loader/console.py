"""Print progress messages and warnings without ever failing.

``print_status`` and ``warn`` print messages that are only informational, so a closed
or broken output stream never stops the work they report on.
``escape_unencodable_output`` stops characters the console can't encode from crashing
the program.
"""

import io
import os
import sys
from typing import TextIO


def print_status(message: str, *, error: bool = False) -> None:
    """Print a progress message to stdout, or to stderr if error is True.

    Messages are only informational (the logs and output directories record every
    result), so if the stream can't be written to, e.g. because it is piped into
    ``head`` or ``less`` and the reader has exited, it is sent to the null device and the
    run carries on. Each message is flushed straight away, which also leaves nothing
    buffered for multiprocessing to fail on when bagit starts its worker processes.
    """
    stream = sys.stderr if error else sys.stdout
    try:
        print(message, file=stream, flush=True)
    except OSError:
        discard_stream(stream)


def warn(message: str) -> None:
    """Print a warning to stderr."""
    print_status(f"WARNING: {message}", error=True)


def discard_stream(stream: TextIO) -> None:
    """Point stream at the null device, so its buffered output and all later writes and
    flushes (including multiprocessing's and the one at exit) succeed instead of failing."""
    try:
        null_device = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(null_device, stream.fileno())
        finally:
            os.close(null_device)
    except (OSError, ValueError):
        # ValueError: the stream has no usable file descriptor.
        pass


def escape_unencodable_output() -> None:
    """Make stdout and stderr escape characters they can't encode instead of crashing.

    Before Python 3.15, Windows writes redirected output in its ANSI code page, which
    can't encode every file name.
    """
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(errors="backslashreplace")
