"""The ``finsight`` command line.

Run as ``python -m finsight.cli.main <command>``.

**Operational commands print identifiers, states and counts and nothing else** — no
extracted text, no filename, no object key, no connection string, no part of a
document (CLAUDE.md §10). An operator running `corpus ingest` in a shared terminal, or
piping it into a log, must not thereby disclose the contents of a filing.

**`search` and `ask` are the exceptions, and they are so by design rather than by drift.**
Showing retrieved passages is their entire purpose, and §6.8 makes inspecting "exact pages,
spans, tables, and cells supporting an answer" a product capability rather than a leak. So the
rule is narrower than "never print document text": an *operational* command must not, and the
*evidence view* must. Two consequences follow, and both are enforced here rather than assumed:

* nothing either command prints is logged — it goes to stdout for the operator who typed the
  query, and no log line carries a passage, a question or a figure;
* passages and cited spans are truncated to a snippet unless `--full` is asked for, so a
  careless redirect spills a line rather than a filing.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from uuid import UUID

from finsight.chunking.service import build_chunking_service
from finsight.corpus.manifest import MANIFEST_PATH, CorpusError, Split, load_manifest
from finsight.corpus.service import (
    IngestionReport,
    build_corpus_chunking_service,
    build_corpus_indexing_service,
    build_corpus_ingestion_service,
)
from finsight.corpus.store import CorpusStore, digest_of, media_type_for
from finsight.domain.errors import DomainError
from finsight.embedding.port import EmbeddingError
from finsight.extraction.service import build_extraction_service
from finsight.generation.decision import AnswerDecision, ReleasedClaim
from finsight.generation.resolution import ResolvedCitation
from finsight.generation.service import AskedAnswer, build_ask_service
from finsight.indexing.pruning import REBUILD_HINT, build_pruning_service
from finsight.indexing.service import build_indexing_service
from finsight.object_store.port import ObjectStoreError
from finsight.persistence.repositories.source import ElementCounts
from finsight.reranking.port import RerankError
from finsight.retrieval.contracts import RetrievalFilters
from finsight.retrieval.pipeline import Result, build_retrieval_pipeline
from finsight.vector_index.port import Span, VectorIndexError

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

    index = subcommands.add_parser(
        "index",
        help="Embed and index a generation's chunks, then activate it.",
    )
    index.add_argument(
        "generation_id",
        type=UUID,
        help="Identifier of a shadow generation produced by chunking.",
    )
    index.add_argument(
        "--retry",
        action="store_true",
        help=(
            "Return a failed generation and its failed events to a retryable "
            "state first. Not needed after an outage, which leaves events pending."
        ),
    )
    index.set_defaults(handler=run_index)

    prune = subcommands.add_parser(
        "prune",
        help="Remove index points of generations no reader can see.",
    )
    prune.add_argument(
        "--confirm",
        action="store_true",
        help=(
            "Actually remove the points. Without this the command reports what it "
            "would remove and changes nothing."
        ),
    )
    prune.add_argument(
        "--include-failed",
        action="store_true",
        help=(
            "Also remove points of failed generations. Opt-in: a failed generation "
            "is retryable with 'index --retry', and this discards work a retry "
            "would otherwise skip."
        ),
    )
    prune.set_defaults(handler=run_prune)

    _add_search_command(subcommands)
    _add_ask_command(subcommands)
    _add_corpus_commands(subcommands)

    return parser


def _add_search_command(subcommands: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Attach ``search``: the evidence view (§6.8)."""
    search = subcommands.add_parser(
        "search", help="Retrieve passages from the indexed corpus."
    )
    search.add_argument("query", help="The question, in quotes.")
    search.add_argument(
        "--limit", type=int, default=5, help="Passages to return. Defaults to 5."
    )
    _add_filter_options(search)
    search.add_argument(
        "--no-rerank",
        action="store_true",
        help=(
            "Return the fused order. Reranking is 93%% of query latency, and §23.1 "
            "keeps it optional, so this makes the two orderings comparable."
        ),
    )
    search.add_argument(
        "--full",
        action="store_true",
        help="Print whole passages rather than a snippet.",
    )
    search.set_defaults(handler=run_search)


def _add_ask_command(subcommands: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Attach ``ask``: the answer view.

    The same filters as ``search`` and for the same reason. An answer is composed from
    retrieved passages, so a filter that narrows retrieval narrows the evidence the answer
    may rest on — and §20.2's filters being hard means an answer cannot quietly reach outside
    the scope the operator asked for.
    """
    ask = subcommands.add_parser(
        "ask", help="Answer a question from the indexed corpus, with citations."
    )
    ask.add_argument("question", help="The question, in quotes.")
    ask.add_argument(
        "--limit",
        type=int,
        default=8,
        help=(
            "Passages considered as evidence. Defaults to 8; the character budget may "
            "admit fewer."
        ),
    )
    _add_filter_options(ask)
    ask.add_argument(
        "--full",
        action="store_true",
        help="Print whole cited spans rather than a snippet.",
    )
    ask.set_defaults(handler=run_ask)


def _add_filter_options(parser: argparse.ArgumentParser) -> None:
    """The §20.2 hard filters, shared by ``search`` and ``ask``.

    Every filter is optional and every one is hard when given. Optional because a question
    spanning the corpus wants none, and hard because §7 forbids similarity overriding scope —
    so a filter that *is* given is never relaxed to find more results.
    """
    parser.add_argument(
        "--issuer", default=None, help="Restrict to one issuer, exactly as recorded."
    )
    parser.add_argument(
        "--document-type", default=None, help="annual_report, drhp, form_10k."
    )
    parser.add_argument(
        "--basis",
        default=None,
        help="consolidated, standalone, both or undetermined (§16.9).",
    )
    parser.add_argument(
        "--section", default=None, help="Restrict to one top-level heading."
    )
    parser.add_argument(
        "--year",
        type=int,
        default=None,
        help="Period ending in this year. Use --since for a range.",
    )
    parser.add_argument(
        "--since",
        type=int,
        default=None,
        help="Period ending in this year or later. Cannot be used with --year.",
    )
    parser.add_argument(
        "--evidence-type",
        choices=["narrative", "table_derived"],
        default=None,
        help="Restrict to one evidence type. Allocation covers both by default.",
    )


def _filters_from(args: argparse.Namespace) -> RetrievalFilters:
    """Build the filter set from parsed arguments.

    ``--year`` and ``--since`` land in the same field because a fiscal year is either an exact
    value or a bound, never both; the caller rejects the pair before reaching here.
    """
    return RetrievalFilters(
        issuer_name=args.issuer,
        document_type=args.document_type,
        reporting_basis=args.basis,
        section=args.section,
        evidence_type=args.evidence_type,
        fiscal_year=args.year
        if args.year is not None
        else (Span(low=args.since) if args.since is not None else None),
    )


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

    chunking = commands.add_parser(
        "chunk", help="Chunk every ingested document in a split."
    )
    chunking.add_argument(
        "--split",
        type=Split,
        choices=list(Split),
        default=Split.DEVELOPMENT,
        help="Which split to chunk. Defaults to development; held-out is refused.",
    )
    chunking.set_defaults(handler=run_corpus_chunk)

    indexing = commands.add_parser(
        "index", help="Index and activate the current generation of every document."
    )
    indexing.add_argument(
        "--split",
        type=Split,
        choices=list(Split),
        default=Split.DEVELOPMENT,
        help="Which split to index. Defaults to development; held-out is refused.",
    )
    indexing.add_argument(
        "--retry",
        action="store_true",
        help="Reopen failed generations and their failed events first.",
    )
    indexing.set_defaults(handler=run_corpus_index)


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
        memory = (
            f" peak_python_mib={outcome.peak_python_mib:.1f}"
            if outcome.peak_python_mib is not None
            else ""
        )
        state = outcome.state.value if outcome.state else "unknown"
        print(
            f"  {outcome.document_id:40} {state:10} {outcome.seconds:.2f}s{memory}"
        )
        # The version id is what every later stage is addressed by, so a run that
        # withheld it would leave an operator unable to chunk or index what it had
        # just ingested without querying the database by hand. An identifier is not
        # document content (§10).
        print(f"      version   {outcome.version_id}")
        if outcome.counts is not None:
            print(f"      elements  {_element_detail(outcome.counts)}")

    failed = len(report.failures)
    print(
        f"{len(report.outcomes) - failed} succeeded, {failed} failed, "
        f"{report.total_seconds:.2f}s total"
    )

    if args.report is not None:
        _write_report(args.report, report)
        print(f"report written to {args.report}")

    return EXIT_FAILED if failed else EXIT_OK


def _element_detail(counts: ElementCounts) -> str:
    """Every element type the producer can emit, including the zeroes.

    Printing only the non-zero types would make "no tables were detected" look
    identical to "tables are not reported here", which is the confusion that let a
    table-less extraction go unnoticed for a phase.
    """
    return (
        f"pages={counts.pages} blocks={counts.blocks} tables={counts.tables} "
        f"cells={counts.cells} footnotes={counts.footnotes} "
        f"gaps={counts.coverage_gaps}"
    )


def run_corpus_chunk(args: argparse.Namespace) -> int:
    """Chunk every ingested document in a split and report what each produced.

    Separate from ingest, and defaulting to development for the same reason: a
    command that processes documents must not reach frozen evidence because a flag
    was omitted.
    """
    entries = _selected(args)
    if not entries:
        print("no documents recorded for that selection")
        return EXIT_OK

    report = build_corpus_chunking_service(_corpus_store()).chunk(entries)

    for outcome in report.outcomes:
        if outcome.failure is not None:
            print(f"  {outcome.document_id:40} FAILED {outcome.failure}")
            continue
        state = "already recorded" if outcome.already_existed else "recorded"
        print(f"  {outcome.document_id:40} {state:16} {outcome.seconds:.2f}s")
        print(f"      generation {outcome.generation_id}")
        if not outcome.already_existed:
            print(f"      chunks     {outcome.chunk_count}")

    failed = len(report.failures)
    print(
        f"{len(report.outcomes) - failed} succeeded, {failed} failed, "
        f"{report.total_seconds:.2f}s total"
    )
    if failed == 0:
        print("queued for indexing; run 'index' to make the chunks searchable")
    return EXIT_FAILED if failed else EXIT_OK


def run_corpus_index(args: argparse.Namespace) -> int:
    """Index every chunked document in a split and activate what succeeds.

    The slow stage: measured at 1.68 texts/s against real enriched chunks, so the
    development split is roughly 47 minutes from cold. Each document activates as it
    finishes rather than at the end, so an interrupted run leaves the documents it
    completed queryable.
    """
    entries = _selected(args)
    if not entries:
        print("no documents recorded for that selection")
        return EXIT_OK

    report = build_corpus_indexing_service(_corpus_store()).index(
        entries, retry=args.retry
    )

    for outcome in report.outcomes:
        if outcome.failure is not None:
            print(f"  {outcome.document_id:40} FAILED {outcome.failure}")
            continue
        print(
            f"  {outcome.document_id:40} "
            f"{'activated' if outcome.activated else 'NOT ACTIVATED':16} "
            f"{outcome.seconds:.2f}s"
        )
        print(f"      generation {outcome.generation_id}")
        print(f"      indexed    {outcome.indexed}")
        if outcome.already_complete:
            print("      note       already indexed by an earlier run")

    failed = len(report.failures)
    print(
        f"{len(report.outcomes) - failed} succeeded, {failed} failed, "
        f"{report.total_seconds:.2f}s total"
    )
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
                "tables": outcome.counts.tables if outcome.counts else None,
                "cells": outcome.counts.cells if outcome.counts else None,
                "footnotes": outcome.counts.footnotes if outcome.counts else None,
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


def run_index(args: argparse.Namespace) -> int:
    """Index one generation and report whether it became queryable.

    Activation is the fact worth printing. Everything before it is work; only this
    makes the chunks visible to retrieval (§11.13), and a run that indexed
    everything and did not activate is a failure however healthy the counts look.
    """
    result = build_indexing_service().index(args.generation_id, retry=args.retry)

    print("indexing complete")
    print(f"  generation: {result.generation_id}")
    print(f"  collection: {result.collection}")
    print(f"  indexed:    {result.indexed}")
    if result.already_complete:
        print("  note:       every chunk was already indexed by an earlier run")
    print(f"  activated:  {result.activated}")
    return EXIT_OK


def run_prune(args: argparse.Namespace) -> int:
    """Report, and on ``--confirm`` remove, index points no reader can see.

    Prints the active totals alongside the prunable ones, because the number that
    matters to a reader deciding whether this is safe is the one that must *not*
    change. A report showing 4,867 active before and after is the assurance; the
    count removed is only the saving.
    """
    service = build_pruning_service()
    report = service.prune(
        include_failed=args.include_failed, confirm=args.confirm
    )

    print(f"generations prunable: {len(report.prunable)}")
    print(f"generations active:   {len(report.active)}")
    print(f"points in collection: {report.points_total_before}")
    print(f"  of which prunable:  {report.points_prunable}")
    print(f"  of which active:    {report.points_active_before}")

    if not report.prunable:
        print("nothing to prune")
        return EXIT_OK

    if not report.applied:
        print()
        print("reported only; nothing was removed. Pass --confirm to remove.")
        print(f"note: {REBUILD_HINT}")
        return EXIT_OK

    print()
    print(f"removed:              {report.removed}")
    print(f"points remaining:     {report.points_total_after}")
    print(
        f"active points:        {report.points_active_before} before, "
        f"{report.points_active_after} after"
    )
    return EXIT_OK


_SNIPPET = 220
_SPANS = 4
"""Cited spans printed per passage before the rest are counted rather than listed."""


def run_search(args: argparse.Namespace) -> int:
    """Retrieve passages and print them with the source regions behind each.

    The citations are the point. A passage with no resolvable source element is a
    retrieval representation and not evidence (§14.1, §14.7), so printing the text
    without the addresses would show something that looks citable and is not.
    """
    if args.year is not None and args.since is not None:
        print("error: use --year or --since, not both", file=sys.stderr)
        return EXIT_FAILED

    pipeline = build_retrieval_pipeline()
    if args.no_rerank:
        pipeline = replace(pipeline, reranker=None)

    result = pipeline.search(
        args.query, filters=_filters_from(args), limit=args.limit
    )

    if result.degraded:
        # First, not last. §20.12 makes degradation a property of the answer, and a
        # reader who stops at the first result must already know it is degraded.
        print(f"DEGRADED: {', '.join(result.degraded)}")
    if not result.candidates:
        print("no passages matched")
        _print_search_footer(result)
        return EXIT_OK

    for candidate in result.candidates:
        rerank = (
            f" rerank={candidate.rerank_score:+.4f}"
            if candidate.rerank_score is not None
            else ""
        )
        print(
            f"\n[{candidate.rank}] {candidate.issuer_name or 'unknown issuer'}"
            f"  {candidate.fiscal_period or 'unknown period'}"
            f"  {candidate.evidence_type}"
        )
        print(
            f"    pages {list(candidate.page_numbers)}"
            f"  fused={candidate.fused_score:.5f}{rerank}"
            f"  found_by={dict(candidate.contributions)}"
        )
        if candidate.heading_path:
            print(f"    section: {' > '.join(candidate.heading_path)}")
        body = " ".join(candidate.text.split())
        if not args.full and len(body) > _SNIPPET:
            body = f"{body[:_SNIPPET]}..."
        print(f"    {body}")
        print(f"    cites {len(candidate.citations)} source region(s):")
        for citation in candidate.citations[:4]:
            print(f"      {citation.locator}  {citation.source_element_id}")
        if len(candidate.citations) > 4:
            print(f"      ... and {len(candidate.citations) - 4} more")

    _print_search_footer(result)
    return EXIT_OK


def _print_search_footer(result: Result) -> None:
    """How the result was produced, which §20.13 wants recorded.

    Printed rather than persisted: the QueryTrace table is deferred, so this is the
    only place the path is currently visible. Said plainly so nobody mistakes it for
    §31.9 being satisfied.
    """
    print(
        f"\nretrieved {len(result.candidates)} of depth {result.depth}"
        f"  lexical={result.lexical_retriever}"
        f"  dense={'yes' if result.dense_used else 'no'}"
        f"  reranked={'yes' if result.reranked else 'no'}"
    )
    if result.reranker_model:
        print(f"reranker: {result.reranker_model}")
    if result.collapsed:
        print(f"collapsed {len(result.collapsed)} duplicate passage(s) (§20.9)")
    print(f"fusion config: {result.fusion_version}  (QueryTrace not persisted yet)")


def run_ask(args: argparse.Namespace) -> int:
    """Answer a question and print the answer, what was withheld, and the sources.

    **All three, always.** An answer without its citations is prose a reader cannot check, and
    an answer that silently omits what the Evidence Gate removed reads as complete when it is
    not — §27.11 gives the answer one decision, and this prints that decision rather than
    inferring one from whether any text appeared.

    Exit code 0 for an abstention. Declining to answer is a correct outcome (§26.1), not a
    command failure; an operator scripting this should distinguish them by the decision line,
    not by whether the process succeeded.
    """
    if args.year is not None and args.since is not None:
        print("error: use --year or --since, not both", file=sys.stderr)
        return EXIT_FAILED

    answer = build_ask_service().ask(
        args.question, filters=_filters_from(args), limit=args.limit
    )
    decision = answer.decision

    if decision.degraded:
        # First, as in `search`. A reader who stops at the first line must already know.
        print(f"DEGRADED: {', '.join(decision.degraded)}")
    print(
        f"decision: {decision.decision.value}"
        f"   support: {decision.support_band.value}"
        f"   model: {answer.model}"
    )
    if decision.reason_codes:
        print(f"reasons:  {', '.join(decision.reason_codes)}")

    _print_claims(decision)
    _print_withheld(decision)
    _print_sources(answer, full=args.full)
    _print_ask_footer(answer)
    return EXIT_OK


def _print_claims(decision: AnswerDecision) -> None:
    """The released claims, each followed by the passage ids it rests on.

    Inline ``[n]`` rather than a footnote list, because the reference has to sit beside the
    sentence it supports: a claim whose citations are three lines away is one a reader checks
    by assuming rather than by looking.
    """
    if not decision.released:
        print("\n(no claim was released)")
        return

    print()
    for claim in decision.released:
        marks = "".join(f"[{identifier}]" for identifier in _cited_ids(claim))
        print(f"{claim.text} {marks}")
        for finding in claim.disclosures:
            # Shown with the claim, not collected at the end. §27.8 requires a disclosed
            # conflict to appear beside what it qualifies.
            print(f"    disclosed: {finding.code} — {finding.detail}")


def _print_withheld(decision: AnswerDecision) -> None:
    """What the Gate removed, and why.

    Printed rather than dropped: a reader who cannot see what was removed cannot judge
    whether what remains is complete.
    """
    if not decision.withheld:
        return

    total = len(decision.released) + len(decision.withheld)
    print(f"\nwithheld {len(decision.withheld)} of {total} claim(s):")
    for claim in decision.withheld:
        print(f"  - {claim.text}")
        for finding in claim.findings:
            print(f"      {finding.code} — {finding.detail}")


def _print_sources(answer: AskedAnswer, *, full: bool) -> None:
    """Every passage a released claim cites, with the source regions behind it.

    The spans are the point. A citation a reader cannot follow to stored text is a reference
    rather than evidence (§14.9), so the locator and the source element id are printed with
    it — and the span's own text, which is what the numeral in the claim was checked against.
    """
    spans: dict[int, list[ResolvedCitation]] = {}
    for claim in answer.decision.released:
        for citation in claim.citations:
            seen = spans.setdefault(citation.passage_id, [])
            if all(
                existing.source_element_id != citation.source_element_id
                for existing in seen
            ):
                seen.append(citation)

    if not spans:
        return

    print("\nsources:")
    for identifier in sorted(spans):
        passage = answer.evidence.by_id(identifier)
        heading = passage.heading_path if passage is not None else ()
        print(
            f"  [{identifier}] "
            f"{(passage.issuer_name if passage else None) or 'unknown issuer'}"
            f"  {(passage.fiscal_period if passage else None) or 'unknown period'}"
            f"  {(passage.reporting_basis if passage else None) or 'basis unknown'}"
            f"  pages {list(passage.page_numbers) if passage else []}"
        )
        if heading:
            print(f"        section: {' > '.join(heading)}")

        # Bounded as `search` bounds its own, and for a sharper reason: a chunk spanning a
        # table resolves to one element per row, so an unbounded list buried the two spans
        # that carried the claim under fourteen that carried "2025 2024". The count is
        # printed so a reader knows the list was cut rather than that the rest do not exist.
        shown = spans[identifier] if full else spans[identifier][:_SPANS]
        for citation in shown:
            print(f"        {citation.locator}  {citation.source_element_id}")
            body = " ".join(citation.text.split())
            if not full and len(body) > _SNIPPET:
                body = f"{body[:_SNIPPET]}..."
            print(f"          {body}")
        remaining = len(spans[identifier]) - len(shown)
        if remaining > 0:
            print(f"        ... and {remaining} further span(s) in this passage")

    uncited = len(answer.evidence.passages) - len(spans)
    if uncited > 0:
        print(f"  ({uncited} further passage(s) were supplied as evidence but not cited)")


def _print_ask_footer(answer: AskedAnswer) -> None:
    """What the evidence set cost and where the time went.

    Separated by stage because generation dominates — and an operator seeing one total cannot
    tell a slow index from a cold model.
    """
    evidence = answer.evidence
    print(
        f"\nevidence: {len(evidence.passages)} passage(s) of {evidence.considered} "
        f"considered, {evidence.used_chars} of {evidence.budget_chars} chars"
        f", {evidence.merged} merged, {evidence.expanded} expanded"
        f", {evidence.dropped_for_budget} dropped to budget"
    )
    timings = ", ".join(
        f"{name.removesuffix('_ms')} {value} ms"
        for name, value in answer.timings_ms.items()
    )
    print(f"timings:  {timings}")
    print(
        f"tokens:   {answer.prompt_tokens} prompt, "
        f"{answer.completion_tokens} completion"
    )
    if answer.answer_id is not None:
        print(f"recorded: {answer.answer_id}")


def _cited_ids(claim: ReleasedClaim) -> list[int]:
    """The passage ids a claim cites, ascending and without repeats."""
    return sorted({citation.passage_id for citation in claim.citations})


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

    The embedding and vector-index families are caught too, and they are not
    defects: Ollama being down or Qdrant being unreachable is an ordinary
    operational state for a host-native model and a derived index, and printing a
    stack trace for it would suggest otherwise.
    """
    args = build_parser().parse_args(argv)
    try:
        exit_code: int = args.handler(args)
    except (
        DomainError,
        ObjectStoreError,
        CorpusError,
        EmbeddingError,
        VectorIndexError,
        RerankError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILED
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
