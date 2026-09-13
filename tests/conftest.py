"""Shared pytest fixtures.

Unit and contract tests run with a clean settings environment: ambient
``FINSIGHT_*`` variables are removed, the working directory is a temporary path so
a developer's local ``.env`` cannot leak in, and the settings cache is cleared
before and after.

Integration tests are exempt. They exist to exercise the real infrastructure, so
they must resolve the real configuration that points at it.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from finsight.config.settings import get_settings


@pytest.fixture(autouse=True)
def isolated_settings_environment(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[None]:
    """Isolate settings resolution from the developer's environment."""
    if request.node.get_closest_marker("integration") is not None:
        yield
        return

    for key in [name for name in os.environ if name.startswith("FINSIGHT_")]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
