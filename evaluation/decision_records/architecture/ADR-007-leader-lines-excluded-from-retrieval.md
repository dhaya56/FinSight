# ADR-007 — Typographic leader lines are excluded from the retrieval index

**Status.** Accepted, 2026-10-06. Reversible: the exclusion is one predicate and a
configuration version.

**Deviation.** PROJECT_BLUEPRINT.md §7 requires all successfully extracted filing
content to remain searchable through Narrative RAG. This withholds 258 blocks from the
index. The deviation is the point of this record.

---

## Decision

A source block is not admitted to chunking when it contains a run of **ten or more
consecutive `.` or `_`** *and* leader characters are **at least 30%** of its
non-whitespace characters.

Both conditions are required. The blocks remain in the source representation — verbatim,
citable, with coordinates — so §7's preservation requirements are untouched. Only the
retrieval clause is deviated from.

`CHUNKING_CONFIG_VERSION` moves 3 → 4.

---

## Problem

A table-of-contents line is a leader run with a section name attached. Indexed, it is
not merely untidy — it is a near-perfect match for a section-name query that contains
none of the answer.

### Scale, measured on the three development filings

| Population | Count | Share |
|---|---|---|
| Child chunks over 80% dots | 78 of 4,969 | 1.6% |
| Child chunks over 95% dots | 11 of 4,969 | 0.2% |
| Parent chunks over 80% dots | 13 of 790 | 1.6% |
| Largest offending parent | 1,515 tokens at 91% dots | — |
| Blocks matching the rule | 258 (of 264 with a run) | 0.3% of 80,952 |
| Characters withheld | 60,558 of 9,446,362 | 0.64% |

### Why they win rather than merely exist

They carry just enough lexemes to compete, and BM25 normalises by document length:

| | Avg lexemes | Avg tokens |
|---|---|---|
| Dot-heavy children | **5** (range 1–11) | 238 |
| Normal children | **60** (range 0–179) | 200 |
| Dot-heavy parents | 25 | 1,321 |
| Normal parents | 230 | 1,041 |

Against a measured average document length of 99.21 token positions, a 5-position
document is extremely short, and `b = 0.75` rewards that strongly. The lexemes that
survive are exactly the section names and page numbers:

```
302 304 b balanc c consolid loss profit sheet statement
2.1 2.2 234 237 asset equip goodwil intang plant properti
```

This is correct BM25 on a pathological document, not a BM25 defect. The document
genuinely has five terms and genuinely is about them.

### Measured displacement

Six section-name probes against the lexical path alone (dense excluded so the effect is
not confounded). **Two returned a contents line at rank 1:**

| Probe | Rank 1 | Real section at |
|---|---|---|
| `critical estimates and judgments` | contents line, 82% dots, rank 0.08333 | ranks 3, 4, 5 |
| `basis of preparation of financial statements` | contents line, 84% dots, rank 0.05833 | ranks 3, 4 |

The remaining four probes matched no contents line and ranked normally, so the harm is
targeted at exactly the queries a reader asks when they want a section.

---

## Alternatives rejected

**Demote in ranking (§18.8).** The blueprint-correct home for a ranking decision, and the
limitation register's own earlier suggestion. Rejected for now: it requires a weight, and
the only data to fit one against is the six probes above. That is enough to establish a
defect by inspection and far too little to calibrate a permanent scoring factor in the
production path. It also leaves a 1,515-token parent spending context budget on
decoration. Revisit once the §22.6 golden set exists — at that point demotion may be
strictly better than exclusion, because it discards nothing.

**Strip the leader run.** Forbidden. §14.4 keeps the chunker from rewriting text, and the
stored chunk must stay comparable with its source.

**Letters-ratio threshold.** The register's original framing, catching all 282 chunks
under 20% letters. Rejected on measurement: 141 of 142 matches at a stricter 10%
threshold contain digits, and the largest is a totals row carrying 335 digits of real
financial data. §17.8 requires that stay retrievable. A letters rule deletes evidence.

**Share without the run.** `.` is also a decimal point: 250 blocks of 80,688 exceed a 30%
leader share with no run at all, and they are dense decimal text.

**Run without the share.** Catches signature lines. The six lowest-share matches in the
corpus are `_____ Signed for and on behalf of …` at 0.234–0.236, which carry the
signatory. The 30% floor sits in the sparse gap between those and the contents
population (p05 0.384, median 0.852).

---

## What is lost

The **section-to-page mapping**, for 258 blocks across three filings.

Not lost: the sections themselves. In both displacement cases above the real content was
already being retrieved *behind* the contents line, so excluding it promotes rather than
hides. 148 of 264 matched blocks name a heading that exists verbatim in a `heading_path`;
the remaining 116 are multi-entry contents blocks whose individual entries are themselves
headings, which a whole-string comparison cannot match and which this record does not
claim to have verified entry by entry.

A question whose answer is a page number — "which page is the balance sheet on" — is no
longer answerable from the index. That is accepted: it is a navigational question about
the document, not a question about its financial content.

## Known limitation

The share guard is a proportion, so a signature line with a long rule and a **short**
signatory crosses the floor and is withheld. None of the six in this corpus is short
enough. Nothing guarantees the next filing's are, and the test
`test_a_short_signature_rule_is_excluded_and_that_is_a_known_limit` pins the behaviour so
the limit is visible rather than discovered as a missing signatory.

## Reversal

Set `leader_share_floor` to `1.0` and bump `CHUNKING_CONFIG_VERSION`. No data is
destroyed by this decision, so reversal costs one re-chunk and one re-index.
