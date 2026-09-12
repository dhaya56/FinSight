"""Shared pytest fixtures.

Every test runs with a clean settings environment: ambient ``FINSIGHT_*`` variables
are removed, the working directory is a temporary path so a developer's local
``.env`` cannot leak in, and the settings cache is cleared before and after.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from finsight.config.settings import get_settings


@pytest.fixture(autouse=True)
def isolated_settings_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[None]:
    """Isolate settings resolution from the developer's environment."""
    for key in [name for name in os.environ if name.startswith("FINSIGHT_")]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
