"""Measure the text-level damage in stored source elements and retrievable chunks.

**One script for before and after.** A measurement taken with one script and compared
against a number produced by another is not a comparison; it is two numbers that look
alike. Phase 12 changes extraction, and the only honest way to show the change helped is
to run this on the same corpus on both sides of it.

Three families are reported, because they fail differently:

* **search-breaking** — a ligature makes ``ﬁnancial`` a different word from ``financial``
  to PostgreSQL, so a passage spelling it that way cannot be found by typing the word
  normally. This is a retrieval defect wearing a typography costume, and it is the one
  Phase 12 exists to remove;
* **reading-breaking** — a word split across a line break, a run of column-gap spaces, a
  control character. These damage how text reads and how it tokenises;
* **clean signals** — replacement characters and mojibake, which are *expected to be
  zero*. A non-zero count here would mean a file-encoding fault rather than a glyph
  fault, and would change the diagnosis entirely.

The headline figure is the last section: for a list of ordinary financial words, how many
passages that spell the word with a ligature **cannot be reached** by a query spelling it
normally. Counting affected passages alone overstates the damage, because a long passage
often also spells the word correctly somewhere else and is reachable anyway.

Run from an activated prompt at the repository root, with PostgreSQL up:

    python scripts/measure_text_quality.py
    python scripts/measure_text_quality.py --json before.json
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import text

from finsight.persistence.database import session_scope

LIGATURES = "ﬀﬁﬂﬃﬄﬅﬆ"

# (label, SQL predicate over source_elements.text). Predicates rather than Python so
# PostgreSQL counts 104,601 rows instead of this process reading them all back.
SEARCH_BREAKING: tuple[tuple[str, str], ...] = (
    ("ligature fi/fl/ff/ffi/ffl", "text ~ '[ﬀ-ﬆ]'"),
    ("non-breaking space", "text LIKE '%' || chr(160) || '%'"),
    # Built from `chr()` rather than written literally: a thin space and a hair
    # space are invisible in source, leaving the predicate unreadable in review.
    ("thin / hair space", "text ~ ('[' || chr(8201) || chr(8202) || ']')"),
    ("soft hyphen", "text LIKE '%' || chr(173) || '%'"),
    ("no ASCII space, over 40 chars", "length(text) > 40 AND text NOT LIKE '% %'"),
)

READING_BREAKING: tuple[tuple[str, str], ...] = (
    ("hyphen + newline splits a word", "text ~ '[a-z]-\\s*\n\\s*[a-z]'"),
    ("embedded newline", "text LIKE '%' || chr(10) || '%'"),
    ("two or more spaces", "text ~ '  +'"),
    ("control characters", "text ~ '[\u0001-\u0008\u000B\u000C\u000E-\u001F]'"),
    ("private use area glyphs", "text ~ '[-]'"),
)

MUST_BE_ZERO: tuple[tuple[str, str], ...] = (
    ("U+FFFD replacement character", "text LIKE '%' || chr(65533) || '%'"),
    ("mojibake 'Ã'", "text LIKE '%Ã%'"),
    ("mojibake 'â€'", "text LIKE '%â€%'"),
)

# Ligatured spelling -> the word a reader would actually type.
PROBE_WORDS: tuple[tuple[str, str], ...] = (
    ("ﬁnancial", "financial"),
    ("ﬁnance", "finance"),
    ("artiﬁcial", "artificial"),
    ("proﬁt", "profit"),
    ("beneﬁt", "benefit"),
    ("conﬁdence", "confidence"),
    ("signiﬁcant", "significant"),
    ("speciﬁc", "specific"),
    ("classiﬁed", "classified"),
    ("identiﬁed", "identified"),
    ("ﬂow", "flow"),
    ("inﬂation", "inflation"),
    ("ofﬁce", "office"),
    ("efﬁciency", "efficiency"),
    ("conﬂict", "conflict"),
)

ACTIVE_CHUNKS = (
    " FROM chunks c JOIN document_versions v"
    " ON v.active_generation_id = c.generation_id"
)

CURRENT_ELEMENTS = (
    " FROM (SELECT e.text FROM source_elements e"
    "       JOIN document_versions v"
    "         ON v.current_extraction_run_id = e.extraction_run_id)"
    " AS current_elements"
)
"""Only the elements the current extraction run produced.

**Without this the script reports a successful fix as a no-op.** Extraction runs
accumulate — a superseded run's elements stay in the table, because an active
generation may still be citing them — so counting `source_elements` unfiltered sums
every run ever made. After the Phase 12 reprocess that read 1,561 ligature elements
both before and after, identical to the digit, while the current run contained
**zero**: the old config-2 rows were the entire count.

A subquery exposing only `text` rather than a join with an alias, so the probe
predicates stay written against a bare `text` column. They contain thin spaces,
private-use glyphs and ligatures that cannot be reviewed once qualified and retyped.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, default=None, help="Also write results here.")
    args = parser.parse_args()

    report: dict[str, Any] = {}

    with session_scope() as session:
        total = session.execute(
            text(f"SELECT count(*){CURRENT_ELEMENTS} WHERE text IS NOT NULL")
        ).scalar_one()
        active = session.execute(text(f"SELECT count(*){ACTIVE_CHUNKS}")).scalar_one()

        print(f"source elements carrying text : {total:,}  (current runs only)")
        print(f"chunks in active generations  : {active:,}\n")
        report["source_elements"] = total
        report["active_chunks"] = active

        for heading, probes in (
            ("SEARCH-BREAKING  (a query cannot reach this text)", SEARCH_BREAKING),
            ("READING-BREAKING (the text renders or tokenises wrongly)", READING_BREAKING),
            (
                "EXPECTED ZERO    (non-zero means an encoding fault, not a glyph fault)",
                MUST_BE_ZERO,
            ),
        ):
            print(f"--- {heading} ---")
            print(f"{'probe':<34}{'elements':>10}{'share':>9}")
            section: dict[str, int] = {}
            for label, predicate in probes:
                count = session.execute(
                    text(
                        f"SELECT count(*){CURRENT_ELEMENTS}"
                        f" WHERE text IS NOT NULL AND {predicate}"
                    )
                ).scalar_one()
                section[label] = count
                share = f"{count / total * 100:.2f}%" if total else "—"
                flag = "  <<<" if count else ""
                print(f"{label:<34}{count:>10}{share:>9}{flag}")
            report[heading.split()[0].lower()] = section
            print()

        chunks_hit = session.execute(
            text(f"SELECT count(*){ACTIVE_CHUNKS} WHERE c.text ~ '[ﬀ-ﬆ]'")
        ).scalar_one()
        lexemes_hit = session.execute(
            text(f"SELECT count(*){ACTIVE_CHUNKS} WHERE c.lexemes::text ~ '[ﬀ-ﬆ]'")
        ).scalar_one()
        print("--- retrievable chunks carrying a ligature ---")
        print(f"  chunk text    : {chunks_hit:,} of {active:,}")
        print(f"  stored lexemes: {lexemes_hit:,} of {active:,}\n")
        report["chunks_with_ligature"] = chunks_hit
        report["lexemes_with_ligature"] = lexemes_hit

        print("--- THE HEADLINE: passages a normally-spelled query cannot reach ---")
        print(f"{'word':<14}{'ligatured':>11}{'reachable':>11}{'UNREACHABLE':>13}")
        totals = [0, 0, 0]
        per_word: dict[str, dict[str, int]] = {}
        for ligatured, clean in PROBE_WORDS:
            row = session.execute(
                text(
                    "SELECT count(*) AS ligatured,"
                    "  count(*) FILTER ("
                    "    WHERE c.lexemes @@ plainto_tsquery('english', :clean)"
                    "  ) AS reachable"
                    f"{ACTIVE_CHUNKS} WHERE c.text LIKE '%' || :lig || '%'"
                ).bindparams(lig=ligatured, clean=clean)
            ).one()
            unreachable = row.ligatured - row.reachable
            totals = [
                totals[0] + row.ligatured,
                totals[1] + row.reachable,
                totals[2] + unreachable,
            ]
            per_word[clean] = {
                "ligatured": row.ligatured,
                "reachable": row.reachable,
                "unreachable": unreachable,
            }
            flag = "  <<<" if unreachable else ""
            print(f"{clean:<14}{row.ligatured:>11}{row.reachable:>11}{unreachable:>13}{flag}")
        print(f"{'TOTAL':<14}{totals[0]:>11}{totals[1]:>11}{totals[2]:>13}")
        report["probe_words"] = per_word
        report["probe_totals"] = {
            "ligatured": totals[0],
            "reachable": totals[1],
            "unreachable": totals[2],
        }

        print("\n--- non-ASCII characters actually present (sampled) ---")
        rows = session.execute(
            text(
                f"SELECT text{CURRENT_ELEMENTS}"
                " WHERE text IS NOT NULL AND text ~ '[^\\x00-\\x7F]' LIMIT 4000"
            )
        ).all()
        counter: Counter[str] = Counter()
        for row in rows:
            counter.update(char for char in row.text if ord(char) > 127)
        census: dict[str, int] = {}
        for char, count in counter.most_common(20):
            try:
                name = unicodedata.name(char)
            except ValueError:
                name = "<unnamed>"
            census[f"U+{ord(char):04X}"] = count
            print(f"  U+{ord(char):04X}  {count:>7}  {name}")
        report["non_ascii_census"] = census

    if args.json:
        args.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nwritten: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
