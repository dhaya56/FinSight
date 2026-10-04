"""The ``finsight`` command line.

Run as ``python -m finsight.cli.main <command>``.

Output is deliberately dull: identifiers, states and counts. It never prints
extracted text, a filename, an object key, a connection string or any part of a
document (CLAUDE.md §10). An operator running this in a shared terminal, or
piping it into a log, must not thereby disclose the contents of a filing.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

from finsight.chunking.service import build_chunking_service
from finsight.corpus.manifest import MANIFEST_PATH, CorpusError, Split, load_manifest
from finsight.corpus.service import IngestionReport, build_corpus_ingestion_service
from finsight.corpus.store import CorpusStore, digest_of, media_type_for
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

    chunk = subcommands.add_parser(
        "chunk",
        help="Build retrieval chunks from a version's extracted blocks.",
    )
    chunk.add_argument(
        "document_version_id",
        type=UUID,
        help="Identifier of a document version that has been extracted.",
    )
    chunk.set_defaults(handler=run_chunk)

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

    ingest = commands.add_parser(
        "ingest", help="Put corpus documents through intake and extraction."
    )
    ingest.add_argument(
        "--split",
        type=Split,
        choices=list(Split),
        default=Split.DEVELOPMENT,
        help="Which split to ingest. Defaults to development; held-out is refused.",
    )
    ingest.add_argument(
        "--report", type=Path, default=None, help="Write a JSON report to this path."
    )
    ingest.add_argument(
        "--measure-memory",
        action="store_true",
        help="Record peak Python allocation. Slows extraction and distorts timings.",
    )
    ingest.set_defaults(handler=run_corpus_ingest)


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
    media_type = media_type_for(path)

    print("[[document]]")
    print('document_id       = "<issuer-type-period>"')
    print(f'split             = "{known}"')
    print(f'filename          = "{path.name}"')
    print(f"byte_size         = {byte_size}")
    print(f'sha256            = "{digest}"')
    print(f'format            = "{media_type or "<media type>"}"')
    print("# Complete the remaining fields listed in data/corpus/README.md.")
    print("# Record expected_challenges BEFORE running extraction.")
    if known == "<split>":
        print("# This file is not in a split directory; move it first.")
    if media_type is None:
        print("# Unrecognised extension; set format to the type intake will detect.")
    return EXIT_OK


def run_corpus_validate(_args: argparse.Namespace) -> int:
    """Validate the manifest. Any rule violation raises and is reported by main.

    Unrecorded fields are printed rather than ignored. The manifest permits
    honest absence — an estimate would be worse — but a gap that never appears
    anywhere stops being a known limitation and becomes an oversight.
    """
    manifest = load_manifest(MANIFEST_PATH)

    print(f"manifest valid: {len(manifest.entries)} document(s)")
    for split in Split:
        print(f"  {split.value:30} {len(manifest.for_split(split))}")

    incomplete = [
        (entry.document_id, entry.unrecorded_fields)
        for entry in manifest.entries
        if entry.unrecorded_fields
    ]
    if incomplete:
        print(f"  {len(incomplete)} of {len(manifest.entries)} entries have "
              "unrecorded fields:")
        for document_id, missing in incomplete:
            print(f"    {document_id:32} {', '.join(missing)}")
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


def run_corpus_ingest(args: argparse.Namespace) -> int:
    """Ingest a split and report what each document produced.

    The default split is ``development``, not every split: a command that
    processes documents must not reach frozen evidence because a flag was
    omitted. Held-out entries are refused by the store regardless.
    """
    entries = _selected(args)
    if not entries:
        print("no documents recorded for that selection")
        return EXIT_OK

    service = build_corpus_ingestion_service(_corpus_store())
    report = service.ingest(entries, measure_memory=args.measure_memory)

    for outcome in report.outcomes:
        if outcome.failure is not None:
            print(f"  {outcome.document_id:40} FAILED {outcome.failure}")
            continue
        counts = outcome.counts
        state = outcome.state.value if outcome.state else "unknown"
        detail = (
            f"pages={counts.pages} blocks={counts.blocks} gaps={counts.coverage_gaps}"
            if counts
            else ""
        )
        memory = (
            f" peak_python_mib={outcome.peak_python_mib:.1f}"
            if outcome.peak_python_mib is not None
            else ""
        )
        print(
            f"  {outcome.document_id:40} {state:10} {detail} "
            f"{outcome.seconds:.2f}s{memory}"
        )

    failed = len(report.failures)
    print(
        f"{len(report.outcomes) - failed} succeeded, {failed} failed, "
        f"{report.total_seconds:.2f}s total"
    )

    if args.report is not None:
        _write_report(args.report, report)
        print(f"report written to {args.report}")

    return EXIT_FAILED if failed else EXIT_OK


def _write_report(path: Path, report: IngestionReport) -> None:
    """Write per-document detail that is too long for a decision record.

    A real offer document produces more coverage detail than belongs in ENV
    prose. The file holds identifiers and counts only — never document content.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "total_seconds": round(report.total_seconds, 3),
        "documents": [
            {
                "document_id": outcome.document_id,
                "byte_size": outcome.byte_size,
                "state": outcome.state.value if outcome.state else None,
                "already_existed": outcome.already_existed,
                "pages": outcome.counts.pages if outcome.counts else None,
                "blocks": outcome.counts.blocks if outcome.counts else None,
                "elements": outcome.counts.total if outcome.counts else None,
                "coverage_gaps": (
                    outcome.counts.coverage_gaps if outcome.counts else None
                ),
                "seconds": round(outcome.seconds, 3),
                "peak_python_mib": outcome.peak_python_mib,
                "failure": outcome.failure,
            }
            for outcome in report.outcomes
        ],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


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


def run_chunk(args: argparse.Namespace) -> int:
    """Chunk one document version's extracted blocks and queue them for indexing.

    Re-running with an unchanged configuration is a no-op and says so. Building a
    second generation would re-embed every chunk for a byte-identical result.
    """
    result = build_chunking_service().chunk(args.document_version_id)

    outcome = "already recorded" if result.already_existed else "recorded"
    print(f"chunking {outcome}")
    print(f"  generation: {result.generation_id}")
    print(f"  version:    {result.document_version_id}")
    if not result.already_existed:
        print(f"  chunks:     {result.chunk_count}")
        print("  note:       queued for indexing; run 'index' to make them searchable")
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
