"""The ``finsight`` command line.

Run as ``python -m finsight.cli.main <command>``.

Output is deliberately dull: identifiers, states and counts. It never prints
extracted text, a filename, an object key, a connection string or any part of a
document (CLAUDE.md §10). An operator running this in a shared terminal, or
piping it into a log, must not thereby disclose the contents of a filing.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

from finsight.corpus.manifest import MANIFEST_PATH, CorpusError, Split, load_manifest
from finsight.corpus.store import CorpusStore, digest_of
from finsight.domain.errors import DomainError
from finsight.extraction.service import build_extraction_service
from finsight.object_store.port import ObjectStoreError

EXIT_OK = 0
EXIT_FAILED = 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="finsight",
        description="FinSight operator commands.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    extract = subcommands.add_parser(
        "extract",
        help="Extract the source representation of a stored document version.",
    )
    extract.add_argument(
        "document_version_id",
        type=UUID,
        help="Identifier of a document version already recorded by intake.",
    )
    extract.set_defaults(handler=run_extract)

    _add_corpus_commands(subcommands)

    return parser


def _add_corpus_commands(subcommands: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Attach the corpus group.

    A nested group rather than flat ``corpus-verify`` style commands, so the
    boundary between operating on documents and operating on the corpus record
    stays visible in the help output.
    """
    corpus = subcommands.add_parser("corpus", help="Inspect and verify the corpus.")
    commands = corpus.add_subparsers(dest="corpus_command", required=True)

    checksum = commands.add_parser(
        "checksum",
        help="Print a manifest entry for a document that has just been acquired.",
    )
    checksum.add_argument("path", type=Path, help="Path to the downloaded document.")
    checksum.set_defaults(handler=run_corpus_checksum)

    validate = commands.add_parser(
        "validate", help="Check the manifest against the corpus rules."
    )
    validate.set_defaults(handler=run_corpus_validate)

    verify = commands.add_parser(
        "verify", help="Check local documents against their recorded checksums."
    )
    _add_split_option(verify)
    verify.set_defaults(handler=run_corpus_verify)

    listing = commands.add_parser("list", help="List recorded documents.")
    _add_split_option(listing)
    listing.set_defaults(handler=run_corpus_list)


def _add_split_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--split",
        type=Split,
        choices=list(Split),
        default=None,
        help="Restrict to one split. Defaults to every split.",
    )


def _corpus_store() -> CorpusStore:
    """The corpus is wherever its manifest is — one source of truth, not two."""
    return CorpusStore(root=MANIFEST_PATH.parent)


def _selected(args: argparse.Namespace) -> list:  # type: ignore[type-arg]
    manifest = load_manifest(MANIFEST_PATH)
    if args.split is None:
        return list(manifest.entries)
    return list(manifest.for_split(args.split))


def run_corpus_checksum(args: argparse.Namespace) -> int:
    """Print a paste-ready manifest block for a freshly downloaded document.

    The command prints rather than writes. The manifest is a reviewed artefact,
    and a tool that edited it would make the record something generated instead
    of something approved.
    """
    path: Path = args.path
    if not path.is_file():
        print(f"error: no file at '{path}'", file=sys.stderr)
        return EXIT_FAILED

    digest, byte_size = digest_of(path)
    split = path.parent.name
    known = split if split in {member.value for member in Split} else "<split>"

    print("[[document]]")
    print('document_id       = "<issuer-type-period>"')
    print(f'split             = "{known}"')
    print(f'filename          = "{path.name}"')
    print(f"byte_size         = {byte_size}")
    print(f'sha256            = "{digest}"')
    print('format            = "application/pdf"')
    print("# Complete the remaining fields listed in data/corpus/README.md.")
    print("# Record expected_challenges BEFORE running extraction.")
    if known == "<split>":
        print("# This file is not in a split directory; move it first.")
    return EXIT_OK


def run_corpus_validate(_args: argparse.Namespace) -> int:
    """Validate the manifest. Any rule violation raises and is reported by main."""
    manifest = load_manifest(MANIFEST_PATH)

    print(f"manifest valid: {len(manifest.entries)} document(s)")
    for split in Split:
        print(f"  {split.value:30} {len(manifest.for_split(split))}")
    return EXIT_OK


def run_corpus_verify(args: argparse.Namespace) -> int:
    """Confirm local documents still match their recorded checksums.

    Runs across held-out splits too. Hashing bytes discloses nothing, and a
    frozen document whose integrity is never checked is not meaningfully frozen.
    """
    store = _corpus_store()
    entries = _selected(args)
    if not entries:
        print("no documents recorded for that selection")
        return EXIT_OK

    failed = 0
    for entry in entries:
        result = store.verify(entry)
        if result.is_intact:
            status = "ok"
        elif not result.present:
            status = "MISSING"
        else:
            status = "CHECKSUM MISMATCH"
        if not result.is_intact:
            failed += 1
        print(f"  {entry.document_id:40} {entry.split.value:30} {status}")

    print(f"{len(entries) - failed} intact, {failed} failed")
    return EXIT_FAILED if failed else EXIT_OK


def run_corpus_list(args: argparse.Namespace) -> int:
    """List recorded documents. Prints the record, never the content."""
    entries = _selected(args)
    if not entries:
        print("no documents recorded for that selection")
        return EXIT_OK

    for entry in entries:
        frozen = " frozen" if entry.split.is_held_out else ""
        print(
            f"  {entry.document_id:40} {entry.split.value:30} "
            f"{entry.document_type.value:18} {entry.fiscal_period}{frozen}"
        )
    return EXIT_OK


def run_extract(args: argparse.Namespace) -> int:
    """Extract one document version and report what was recorded.

    Re-running against an already-extracted version is a no-op and says so,
    rather than failing: idempotence is the expected operating mode, not an
    error condition.
    """
    service = build_extraction_service()
    result = service.extract(args.document_version_id)

    outcome = "already recorded" if result.already_existed else "recorded"
    print(f"extraction {outcome}")
    print(f"  run:      {result.run_id}")
    print(f"  version:  {result.document_version_id}")
    print(f"  state:    {result.state.value}")
    print(f"  elements: {result.element_count}")

    if result.state.value == "partial":
        print("  note:     some regions were not extracted; see failure reasons")

    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments, run the command, and turn a known failure into an exit code.

    Only errors this project defines are caught. An unexpected exception keeps
    its traceback, because a swallowed stack trace is how a real defect gets
    mistaken for a bad document.
    """
    args = build_parser().parse_args(argv)
    try:
        exit_code: int = args.handler(args)
    except (DomainError, ObjectStoreError, CorpusError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILED
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
