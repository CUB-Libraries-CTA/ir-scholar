"""pytest fixtures shared by the bagit_builder tests."""

from pathlib import Path

import pytest

from helpers import ETDS_DIR, copy_zips, make_standard_zips


@pytest.fixture(scope="session")
def standard_zips(tmp_path_factory) -> Path:
    """Directory holding the standard test zips (see helpers.STANDARD_EXPECTED).

    Built once; tests run bagit_builder on a copy (see helpers.copy_zips).
    """
    directory = tmp_path_factory.mktemp("standard_zips")
    make_standard_zips(directory)
    return directory


@pytest.fixture(scope="session")
def real_etds(tmp_path_factory) -> Path:
    """A snapshot of the zips in etd-loader/etds/, so files changing there during the
    run don't affect it. Tests using it are skipped when there are none."""
    zips = sorted(ETDS_DIR.glob("*.zip")) if ETDS_DIR.is_dir() else []
    if not zips:
        pytest.skip(f"no ETD zips in {ETDS_DIR}")
    return copy_zips(ETDS_DIR, tmp_path_factory.mktemp("etds") / "snapshot", link=False)
