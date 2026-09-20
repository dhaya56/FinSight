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
from uuid import UUID

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

    return parser


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
    except (DomainError, ObjectStoreError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILED
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
