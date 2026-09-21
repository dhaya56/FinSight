"""Locating corpus documents, checking they are intact, and keeping held-out evidence closed.

Two operations that look similar are deliberately governed differently.

**Verification is allowed on every split, including held-out.** It reads the
bytes, but only to compute a digest, and a digest discloses nothing about the
document. §34.6 forbids *tuning* against held-out data; it does not forbid
checking that held-out data is still the data that was frozen. A corpus whose
integrity cannot be confirmed is not a corpus.

**Content access is refused for held-out entries.** Anything that would hand the
bytes to the pipeline or to a person raises :class:`HeldOutAccessError`. §34.10's
freeze is only meaningful if it is enforced rather than remembered, and the
realistic failure is not malice — it is a `--split` flag typed without thinking
at the end of a long day.

That distinction is the whole purpose of this module. The guard lives here,
below the CLI, so that no future command can forget to apply it.
"""

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Final

from finsight.corpus.manifest import CORPUS_ROOT, CorpusEntry, CorpusError

_CHUNK_BYTES: Final = 1024 * 1024


class DocumentMissingError(CorpusError):
    """The manifest records a document that is not present locally.

    Expected rather than exceptional: documents are gitignored, so a fresh clone
    has a complete manifest and no bytes at all. The remedy is acquisition, not
    repair.
    """


class ChecksumMismatchError(CorpusError):
    """The local bytes are not the bytes the manifest recorded.

    §32.7 makes this a new candidate version rather than a correction: the file
    on disk is a different document, and the manifest entry it no longer matches
    must not be edited to agree with it.
    """


class HeldOutAccessError(CorpusError):
    """Something tried to read a frozen document's content (§34.6, §34.10)."""


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """What checking one entry against local storage found."""

    entry: CorpusEntry
    present: bool
    digest_matches: bool
    actual_byte_size: int | None

    @property
    def is_intact(self) -> bool:
        return self.present and self.digest_matches


class CorpusStore:
    """Resolves manifest entries to local files and guards held-out ones."""

    def __init__(self, root: Path = CORPUS_ROOT) -> None:
        self._root = root

    def path_for(self, entry: CorpusEntry) -> Path:
        """Where this entry's bytes should be.

        Matches ``CorpusEntry.relative_path`` under the default root; the root is
        configurable so tests can exercise a temporary corpus without touching
        the developer's own.
        """
        return self._root / entry.split.value / entry.filename

    def verify(self, entry: CorpusEntry) -> VerificationResult:
        """Check that the local file exists and still hashes to its recorded digest.

        Permitted for every split. Reading bytes to hash them is not reading a
        document: nothing is returned but a comparison.
        """
        path = self.path_for(entry)
        if not path.is_file():
            return VerificationResult(
                entry=entry, present=False, digest_matches=False, actual_byte_size=None
            )

        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()

        return VerificationResult(
            entry=entry,
            present=True,
            digest_matches=digest == entry.sha256,
            actual_byte_size=path.stat().st_size,
        )

    def require_intact(self, entry: CorpusEntry) -> Path:
        """Return the path, or raise describing exactly what is wrong.

        Raises:
            DocumentMissingError: the file is not present locally.
            ChecksumMismatchError: the bytes are not the recorded bytes.
        """
        result = self.verify(entry)
        if not result.present:
            raise DocumentMissingError(
                f"{entry.document_id}: not present locally; acquire it into the "
                f"{entry.split.value} split"
            )
        if not result.digest_matches:
            raise ChecksumMismatchError(
                f"{entry.document_id}: local bytes do not match the recorded "
                "checksum; a changed download is a new candidate version (§32.7)"
            )
        return self.path_for(entry)

    @contextmanager
    def open_content(self, entry: CorpusEntry) -> Iterator[IO[bytes]]:
        """Open a document for processing, after confirming it is intact.

        Raises:
            HeldOutAccessError: the entry belongs to a frozen split.
            DocumentMissingError, ChecksumMismatchError: see ``require_intact``.
        """
        self.ensure_readable(entry)
        path = self.require_intact(entry)
        with path.open("rb") as handle:
            yield handle

    def ensure_readable(self, entry: CorpusEntry) -> None:
        """Refuse any content access to a frozen document.

        Separate from ``open_content`` so a caller that reads by another route
        still has one obvious call to make, and so the refusal can be asserted
        without opening anything.
        """
        if entry.split.is_held_out:
            raise HeldOutAccessError(
                f"{entry.document_id} is in the frozen {entry.split.value} split; "
                "its content may not be read (§34.6). Checksum verification is "
                "permitted and does not require this."
            )


def digest_of(path: Path) -> tuple[str, int]:
    """Return a file's SHA-256 digest and size.

    Used by the checksum command to produce a manifest entry for a document that
    has just been downloaded and therefore has no entry yet.
    """
    with path.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    return digest, path.stat().st_size
