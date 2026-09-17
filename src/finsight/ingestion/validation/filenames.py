"""Filename handling.

Filenames are metadata and nothing more (PROJECT_BLUEPRINT.md §30.9). They never
reach an object key, which is derived from the content hash alone, and they never
reach a filesystem path. What survives here is a display label, stripped of
directory components and control characters so that storing or echoing it later
cannot smuggle a path or a terminal escape.
"""

import re
from typing import Final

MAX_FILENAME_LENGTH: Final = 255
"""Trim point for a stored label. Long enough for real filings, bounded for storage."""

_CONTROL_CHARACTERS: Final = re.compile(r"[\x00-\x1f\x7f]")


def sanitize_filename(raw: str | None) -> str | None:
    """Reduce an untrusted filename to a safe display label, or None.

    Never raises: a filename cannot fail an upload, because nothing depends on
    it. A name that sanitizes to nothing simply becomes None.
    """
    if raw is None:
        return None

    without_directories = raw.replace("\\", "/").rsplit("/", maxsplit=1)[-1]
    cleaned = _CONTROL_CHARACTERS.sub("", without_directories).strip()
    cleaned = cleaned.strip(".")

    if not cleaned:
        return None
    return cleaned[:MAX_FILENAME_LENGTH]
