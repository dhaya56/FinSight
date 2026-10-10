# ENV-013 — Phase 12 text quality and the embedding cache, validated

**Date.** 2026-10-10. **Corpus.** Development split, three filings, 1,403 pages. **Before.**
Extraction configuration 2, 5,637 chunks, 4,969 children. **After.** Extraction configuration
3, 5,628 chunks, 4,859 children. **Model.** `nomic-embed-text`, 137M parameters, host-native
Ollama, 0% GPU offload. **Host.** Intel i7-1165G7, 4 cores / 8 threads, Iris Xe with 2 GB.

**573 passages that a normally-spelled query could not reach are now reachable, and the
number is 0.** The fix was decoding glyph-encoding artefacts as text leaves the extraction
producer; this record is the confirmation run, because until the corpus was re-extracted the
fix had a measured cause and no measured outcome.

The second half of the phase was the embedding cache, which exists so that this kind of
confirmation is affordable. The re-index it enables was measured here too.

---

## 1. The headline, before and after

Both extraction runs produced **61,150 elements carrying text** from the same bytes, so this
is a comparison of like with like rather than of two different corpora.

| Probe | Config 2 | Config 3 | |
|---|---|---|---|
| **Passages unreachable by a normally-spelled query** | **573** | **0** | **eliminated** |
| Retrievable chunks carrying a ligature | 441 of 5,637 | **0** of 5,628 | eliminated |
| Stored lexemes carrying a ligature | 441 | **0** | eliminated |
| Elements with a ligature | 1,561 | **0** | eliminated |
| Elements with a non-breaking space | 182 | **0** | eliminated |
| Elements with a thin or hair space | 32 | **0** | eliminated |
| Elements with a soft hyphen | 2 | **0** | eliminated |
| Elements with C0/C1 control characters | 127 | **0** | eliminated |
| A word split by a hyphen at a line break | 874 | **1** | see §2 |
| Elements over 40 chars with no ASCII space | 338 | **169** | halved, see §2 |
| Elements with a private-use glyph | 56 | 47 | **kept by design** |
| U+FFFD, mojibake | 0 | 0 | unchanged, as required |

The fifteen probe words were chosen before the fix and are ordinary financial vocabulary —
`financial`, `significant`, `benefit`, `efficiency`, `profit`. In configuration 2, 741
passages spelled one of them with a ligature and 573 of those could not be found by typing
the word normally. In configuration 3 there are no ligatured spellings left to be unreachable.

---

## 2. The three results that are not zero, and why each is correct

**169 elements over 40 characters with no ASCII space — halved from 338, not eliminated.**
The half that was fixed had its words separated by non-breaking, thin or hair spaces, which
are now spaces. The remaining 169 are 167 blocks and 2 cells of 41–195 characters with **no
space-like character of any kind** to decode — 11 of them look like URLs. There is nothing
for a normalisation table to map, so this is an extraction-level question rather than a
glyph-level one, and it is recorded as an open item for Phase 14 rather than claimed as
fixed. Each is still one enormous lexeme and therefore unreachable; 169 of 61,150 elements is
0.28%.

**One word still split by a hyphen at a line break — from 874.** The normaliser closes
`letter-\nletter` where only spaces or tabs surround the newline. The one survivor has a
**blank line** inside the break, which the pattern deliberately excludes: a blank line is a
paragraph boundary, and closing a word across it would join two paragraphs to repair one
word. Checked directly — of the cases that remain under a looser pattern, **zero** have a
letter following the break, so every genuinely split word was closed.

**47 private-use glyphs, deliberately unchanged.** Unmappable by definition. Deleting text is
a worse failure than rendering a box, and `normalise` leaves them alone on purpose. The count
moved from 56 to 47 incidentally, not because anything targeted them.

---

## 3. The reprocess behaved exactly as the generation model promises

| Stage | Wall time |
|---|---|
| Re-extract three filings under configuration 3 | **230.6 s** (3.8 min) |
| Re-chunk into three shadow generations | **28.7 s** |
| Embed, reconcile and activate | **2,360.8 s (39.3 min)** |
| **Total** | **~43.7 min** |

Observed mid-run, with one filing still indexing: **3 active, 1 shadow, 5 superseded.** That
is the §11.13 contract working — the configuration-2 generation for the unfinished filing was
still active and still answering queries while its replacement was being built as a shadow.
Nothing was unqueryable at any point, and a crash would have left the old index serving.

All three activated, zero failures. Reconciliation passed on all three, meaning PostgreSQL's
recorded count and Qdrant's point count agreed before any generation became visible.

---

## 4. The embedding cache, measured on the real corpus

### The cold run: 0% hit rate, as designed

```
embedding cache:      2 hit(s), 4836 miss(es) (0.0% hit rate)
  vectors stored:     4836
```

The cache was empty and every text was new, so the run paid full price and populated the
cache. The **2 hits** are duplicate enriched texts inside the corpus — which corrects an
earlier figure worth recording: 127 chunks share identical *chunk* text, but only **22**
share identical *enriched* text, because the deterministic context header carries the heading
path and makes near-duplicates distinct. Intra-run deduplication is worth even less than it
first appeared.

### The warm run: the whole corpus, not a sample

Every enriched child text reconstructed from the active generations and sent again, which is
exactly what a re-index of unchanged text does:

| | Measured |
|---|---|
| Child chunks | 4,859 (4,837 distinct) |
| Cold, full corpus | **2,360.77 s** |
| Warm, full corpus | **2.91 s** |
| Warm rate | **1,669 texts/s** |
| Hit rate | **100.00%** — 4,837 hits, 0 misses |
| Vectors stored on the warm pass | **0** |
| Cache faults | 0 read, 0 write |
| **Speedup on unchanged text** | **811×** |

The 100% hit rate is the load-bearing number twice over. It is the throughput result, and it
is also the proof that the reconstruction is byte-exact — a single character of drift between
what the indexer embedded and what the probe rebuilt would have shown up as a miss.

### What a real re-index costs now

At the measured 84.2% share of chunk text that survives a re-extraction unchanged:

| | |
|---|---|
| Re-index, no cache | **39.3 min** |
| Re-index, 84.2% hits | **6.3 min** |
| Saving | **~33 min, 6.3×** |

**Limitation.** The 6.3 min figure combines two measured numbers — the warm rate and the cold
rate, mixed at a measured hit share — and is not itself a stopwatch reading. The next
reprocess produces that reading. The 84.2% share was measured against the configuration-2 to
configuration-3 change specifically; a change that rewrites more text hits less.

### A hit is bit-identical, measured

200 real chunks embedded cold, then served warm: **200 of 200 vectors identical**, largest
difference in any single component across 153,600 values **exactly 0.0**. This is why the
column is `double precision` and not `real`, and why the cache stores the input text and
compares it on every hit rather than trusting the digest.

### One shortcut refused

The cache could have been back-filled from Qdrant — 4,867 vectors were already there, paired
with their text, so the first reprocess could have run in ~6 minutes instead of 39. **Qdrant
stores float32.** Back-filling would have loaded rounded values into a double-precision cache
and then served them as hits, converting a measured guarantee into an approximation. The
33-minute saving was declined; the first run pays full price exactly once.

---

## 5. Faults found while validating

| Fault | Found by |
|---|---|
| **The measurement script counted every extraction run, not the current one.** Elements accumulate — a superseded run's rows stay in the table because an active generation may still cite them — so the "after" report read 1,561 ligature elements, identical to the before run to the digit, while the current run contained zero. A complete fix presented as a total no-op. Now filtered to `current_extraction_run_id` | Noticing that three counts were byte-identical across a reprocess, which is not how measurements behave |
| A new CHECK-constraint test probed an invalid `kind` with an 18-character string against a `varchar(16)` column, so PostgreSQL rejected it for length and the CHECK was never consulted — a passing test proving nothing | The integration run failing for the wrong reason |
| `migrations/env.py` never imported `tables/answers.py`, so `answers` and `answer_claims` were invisible to `Base.metadata` and autogenerate would have proposed dropping them. Masked because the parity test kept a second, more complete copy of the same list | Adding a third copy and asking why there were two |

The first is the one worth keeping. A measurement that cannot see a change is worse than no
measurement, because it is read as evidence.

---

## 6. What this record does not claim

- **No retrieval-quality claim.** "573 passages are now reachable" is a defect count against a
  stated invariant — a query spelling a word normally should find text spelling it with a
  ligature. It is not evidence that answers improved, and §22.6's metrics still need the
  Phase 13 golden question set.
- **169 elements remain unreachable** for want of any word separator, and that is stated
  rather than rounded away.
- **The 6.3-minute re-index is a mix of two measurements**, not a stopwatch reading.
- **The cache's quality guarantee is measured on 200 chunks and 4,837 corpus-wide hits**, both
  on one model and one host.

## 7. Open items

| Item | Owning phase |
|---|---|
| 169 elements over 40 chars with no separator of any kind — an extraction defect, not a glyph one | 14 |
| A stopwatch reading for the 84.2%-mix re-index | the next reprocess |
| Footnote association, which changes extracted output and needs its own reprocess | 12c |
| Investment-advice guardrail and cross-claim scope disclosure — generation-time, no reprocess | 12c |
| Retrieval and answer quality, which needs the golden question set | 13 |
