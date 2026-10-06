# ENV-011 — Phase 10 chunking hygiene, validated

**Date.** 2026-10-06. **Corpus.** Development split, three filings, 1,403 pages,
40,476 narrative blocks. **Configuration.** Chunking 3 → 4.

**One of three planned fixes shipped.** The other two were measured and refused, a third
defect was found that belongs to another phase, and four recorded figures — three of them
mine — were corrected. That distribution is the main result of this record.

---

## 1. What shipped: typographic leader lines withheld from the index

ADR-007 carries the decision and the §7 deviation. Measured outcome after re-chunking and
re-indexing:

| | config 3 | config 4 |
|---|---|---|
| Chunks | 5,759 | 5,637 |
| Children | 4,969 | 4,867 |
| Parents | 790 | 770 |
| Children over 50% dots | 91 | **0** |
| Children over 80% dots | 78 | **0** |
| Parents over 50% dots | 16 | **0** |
| Parents over 80% dots | 13, worst 1,515 tokens | **0** |
| Children under the 48-token floor | 1,212 | 1,211 |

Every dot-dominated chunk is gone and nothing fragmented in their place.

### The defect was retrieval displacement, not untidiness

Six section-name probes against the lexical path alone, before and after. Dense retrieval
is excluded so the effect is not confounded.

| Probe | Before, rank 1 | After, rank 1 |
|---|---|---|
| `critical estimates and judgments` | contents line, **82% dots**, real section at ranks 3–5 | `1.4 Critical accounting estimates and judgment`, 1% dots — promoted from rank 3 |
| `basis of preparation of financial statements` | contents line, **84% dots**, real section at ranks 3–4 | the section itself at ranks 2–3 |
| `balance sheet`, `contingent liabilities`, `property plant and equipment`, `related party transactions` | no contents line matched | unchanged |

Maximum dot share anywhere in the returned sets fell from **0.84 to 0.02**.

The mechanism was correct BM25 on a pathological document: a contents line carries five
lexemes in 238 tokens against a measured average document length of 99.21, and `b = 0.75`
rewards a short document. A table of contents is a list of section names, so it matches a
section-name query almost perfectly while containing none of the answer.

### Every block is accounted for

The check that matters is not the chunk count but whether a block stopped reaching a chunk.

| | Blocks |
|---|---|
| Carrying text, active runs | 40,476 |
| Whitespace only, dropped (unchanged behaviour) | 1,557 |
| Leader lines, newly withheld | 129 |
| Reaching a chunk | 38,790 |

The three sum exactly to 40,476. Config 3 placed 38,919 blocks and config 4 placed 38,790 —
a difference of exactly 129, the leader set, and nothing else.

The 102 lost children decompose as: **91** config-3 children whose every block was a leader
line and which therefore vanish correctly, **25** that held leader content alongside real
text and survive shortened, and **11** that merged into a sibling once shortened. The 20
lost parents follow from windows holding fewer children, which makes the
parent-identical-to-its-only-child rule fire more often.

---

## 2. Refused on measurement: a table's text kept whole (problem 5)

`SourceBlock.region_id` made per-table grouping possible for the first time. Both forms
were measured end to end with the region mapping held identical on both sides.

| | Baseline | Run per table | Child boundary per table |
|---|---|---|---|
| Children under the 48-token floor | 1,212 | **1,399** | 1,210 |
| Regions split across chunks | 165 | **184** | 163 |
| Regions split across parents | 11 | 11 | 11 |
| Chunks holding two or more tables | 50 | — | 39 |

A run per table is harmful: 803 regions become 803 runs, each taking a parent window, and
because merging across runs is refused a small table becomes a fragment that cannot grow.
It also breaks a §20.8 property — a table interleaved with prose lands under two parents, so
expanding from one child recovers half the table.

A child boundary per table is harmless and not useful: two fewer split regions and eleven
fewer mixed chunks out of 1,254 holding table text, with the sign inconsistent across
documents — one of three filings got worse on both counts. At this corpus size that is
noise, and a production path should not carry a threshold interaction for noise.

**128 of the 805 regions with text exceed the child budget outright** (median 734 tokens,
max 3,801). That is the dominant cause of the recorded 19% and no grouping rule reunites it.

Kept from the attempt: `region_id` itself, and `region_containing` replacing
`mark_table_derived`. The latter is an independent correctness fix — the old `any(...)` took
whichever region matched first, so a block's table depended on detector emission order
rather than geometry. It now resolves by greatest overlap, then smallest region for the
nested case, then identifier. `table_derived` is bit-identical, so this changed no output.

---

## 3. Refused on measurement: footnotes indexed (problem 9)

The recorded problem read "36 exist … and footnotes are not indexed". The second half is
true of the footnote *elements* and the first is false of their *text*.

| | Measured |
|---|---|
| Bound footnote elements | 36, across 30 tables |
| Whose text also exists as a `block` element | **36 of 36** |
| Whose text is findable inside an indexed chunk | **34 of 36** |
| Blocks opening with a footnote-style marker | 780 `(n)`, 1,142 `(a)`, 348 `*`, 62 `#` |

`adjacency.py` creates a footnote element *in addition to* the block it read, so the text
reaches the index by that route. Indexing the elements would add 36 chunks whose text is
already present — duplicate evidence that §20.9 cannot collapse, because the copies have
different source elements.

What is missing is the association, and it has no consumer: a table chunk does not carry the
qualifier that modifies its figures. That produces a wrong answer only once something
composes one. Register §24 records why the admissible fix — footnote text as enrichment
context, never as chunk body — belongs to Phase 8.

---

## 4. Found, not fixed: superscript markers merge into values (problem 11)

Prompted by developer research and measured against the PDFs. Register §23 carries it.
`get_text("blocks")` discards font size, so a superscript footnote marker is
indistinguishable from a value digit by the time anything sees it.

| Filing | Pages | Small marker spans | Superscript digit after a digit | Stored glued | Stored separated |
|---|---|---|---|---|---|
| Infosys AR | 369 | 153 | 0 | 0 | 0 |
| HDFC Bank AR | 590 | 398 | 2 | **2** | 0 |
| Ola Electric DRHP | 444 | 211 | 1 | **1** | 0 |

Three of three glued; one turns a three-digit figure into a four-digit one. Three is a
floor, not a count: the detector requires a span under 0.80 of the page median. The fix is
span-level extraction, which invalidates every stored element, so it belongs with
table-aware reading order in the extraction phase.

---

## 5. Cost

| | Before | After |
|---|---|---|
| Re-chunk | — | 36.4 s for three filings |
| Re-index | — | **2,439 s (40.7 min)** for 4,867 chunks |
| Throughput | 1.93 chunks/s recorded | **2.00 chunks/s** |
| PostgreSQL database | 127 MB | 142 MB |
| `chunks` table | 39 MB | 49 MB |
| `chunk_sources` | 19 MB | 23 MB |
| Qdrant points | 4,969 | 9,836 |
| Container peak, qdrant | — | 242 MiB of 1 GiB |
| Container peak, postgres | — | 165 MiB of 1 GiB |

Lexemes still cost more than the text they index: 7,266 kB against 5,446 kB for config 4,
confirming the ENV-010 finding at the new configuration.

**Superseded points are not pruned.** Config 3's 4,969 points remain, so the collection
holds 9,836 of which half are never served — retrieval binds to active generations, so this
is a storage cost rather than a correctness risk. No prune command exists.

---

## 6. Corrections recorded

| Claim | Status |
|---|---|
| "38 regions recoverable by per-table grouping" | **Wrong.** Derived by subtracting two figures from a region mapping no longer in use. Measured end to end: 2 |
| "No region is split across parents" | **Never measured.** The counter was initialised and never incremented. True baseline: 11 |
| "258 blocks excluded, 60,558 characters" | **Double.** Counted over every stored extraction run; the database holds two per document. Correct: 129 blocks, 29,838 characters |
| Register §4's "No superscript digits appear anywhere in the corpus" | **True of the stored bytes, false of the documents.** Corrected in place and labelled, because it read as reassurance |
| README row 9, footnote unreachability | **Premise wrong.** Rewritten |

---

## 7. What this does not show

- **No retrieval-quality claim.** The displacement probes show a specific defect removed on
  six hand-chosen queries. There is no golden question set (§22.6), so whether retrieval is
  better *overall* is unmeasured and cannot be asserted.
- **The probes are lexical only.** Dense retrieval and reranking were excluded to isolate
  the effect. The production path fuses and reranks, so the end-user ordering differs.
- **One corpus, three filings, one jurisdiction.** The leader-share separation that the 0.30
  floor sits inside is a property of these documents.
- **The 0.30 floor is a threshold, not a measurement.** It was placed in a measured gap, and
  a filing with a short signatory under a long rule would lose it — pinned as a test rather
  than left to be discovered.
