import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHOP = ROOT / "examples" / "buggy-shop"
ATTEMPTS = ROOT / "examples" / "mock-agent" / "attempts"


@pytest.fixture
def shop(tmp_path):
    """Fresh (before, after) copies of the buggy example project."""
    before, after = tmp_path / "before", tmp_path / "after"
    shutil.copytree(SHOP, before)
    shutil.copytree(SHOP, after)
    return before, after
