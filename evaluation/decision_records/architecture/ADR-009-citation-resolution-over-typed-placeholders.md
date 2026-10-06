# ADR-009 — Grounding by citation resolution, not typed placeholders

**Status.** Accepted, 2026-10-06. **Amends PROJECT_BLUEPRINT.md** §26.3, §26.5, §26.6,
§26.7, §27.3, §27.4, §24.4, §30.11 and the §4 summary. The blueprint has been edited; this
record is why.

**Decision.** The model emits claims with **citation references**. Code resolves each
reference to the stored source span. A numeral may be released only if it appears in a span
its own claim cites. There is no placeholder vocabulary and no substitution engine.

---

## What was specified, and the problem with it

§26.5 and §26.7 specified numeric placeholders bound to authoritative values, substituted
deterministically by code. §27.3 then removed any numeral that did not arrive through an
approved placeholder.

The mechanism prevents one failure and introduces a worse one.

**Prevented:** transcription corruption — the model writing `145,0001` for `145,000`. Real,
and worth preventing.

**Introduced:** wrong binding. The model emits `{{fact:3}}` where it meant `{{fact:7}}`.
Substitution produces a numeral that genuinely came from a source region, genuinely matches
a chunk in the evidence set, and is wrong. §27.3 passes. §27.6 passes. §27.7 passes, because
chunk 3's issuer, period and basis are all perfectly valid — they are simply not the ones
the claim is about. **No step in the specified design could detect it.**

So the design traded a failure that an exact string comparison catches for one that nothing
catches. That is the documented hazard of constraining output: structural validity is not
semantic correctness, and a team that constrains and stops measuring trades a visible
failure mode for an invisible one.

A second cost: the harder the output is constrained, the less capacity the model has for the
content. That matters more here than usual — an 8B model on CPU has the least to spare.

---

## What replaces it

Anthropic's Citations API is a shipped production feature solving this exact problem, and
its design answers it better than either option considered:

1. documents are chunked to sentence granularity, defining the smallest citable unit;
2. the model returns text blocks, each a **claim plus the citations supporting it**;
3. citations are **location references**;
4. the service resolves them into `cited_text`, which *"does not count toward output
   tokens"* because the model emits a standardised reference rather than the quotation.

**The model never writes the quoted text.** Corruption of a quoted value is therefore not
caught — it is impossible. And wrong binding becomes detectable, because the resolved span
is the one the claim actually points at, so a numeral the claim asserts either appears there
or does not.

Our implementation:

| Step | Owner |
|---|---|
Assign short opaque ids `[1]`…`[n]` to the evidence set | code |
Emit claims with citation references | model, schema-constrained |
Resolve references to stored spans | code, from PostgreSQL |
Verify each numeral appears in a span its claim cites | code, deterministic |
Strip unsupported claims; abstain if none survive | code |

---

## Why the Fact Ledger is no longer a prerequisite

§27.4 checked numeric claims against "facts or derived calculations", which made generation
depend on a Fact Ledger that does not exist. Amended: where the ledger exists for a concept
it is the stronger authority and a claim is checked against the fact and its dimensions;
where it does not, **the cited span is the authority** and no normalisation, comparison or
arithmetic is performed.

This follows §7 as already written — a non-ledger number may be reproduced when precisely
source-bound, and may not be normalised, compared or calculated without an approved
structured path. Reproduction is exactly what a cited span supports.

The consequence is stated rather than hidden: a question requiring arithmetic over
non-ledger values is **abstained from**. Asked how much profit grew year on year, the system
reports both figures with their periods and declines the subtraction.

**The ledger is not abandoned.** The financial data industry builds normalised structured
fundamentals databases through extraction plus human QA, and analysts query those rather
than the filings. That is decades of revealed preference saying financial figures belong in
a typed record. The ledger remains the right destination for comparison and calculation; it
is no longer a gate on answering narrative questions.

---

## Measured basis

Schema-constrained decoding, against the running service, with an invented prompt:

| Call | Time | Output |
|---|---|---|
Free-form, includes model load | 37.3 s | prose, 70 tokens |
Schema-constrained | 17.2 s | valid JSON, correct shape |
Schema-constrained, warm | 16.0 s | valid JSON, correct shape |

So the contract is enforceable at the runtime rather than requested in the prompt, which is
what makes a reference-based contract safe to depend on. ADR-008 carries the model decision.

---

## Audit of three research documents, and what changed because of it

Reviewed 2026-10-06. Confirmed, and adopted: hard token budget rather than filling the
window; highest-scoring passages at the beginning **and end** against lost-in-the-middle;
short deterministic ids; per-passage metadata as structured blocks; explicit permission to
refuse; schema-enforced output; post-generation numeral cross-checking against cited spans.

Rejected or deferred, with reasons:

| Proposal | Decision |
|---|---|
`[Source #2, Page 45]` style citations | **Rejected.** A compound citation gives the model a second thing to get wrong, and the page is derivable from the id |
Binary block of a failed answer | **Rejected.** Strip the claim, return partial; abstain only if nothing survives. All-or-nothing blocking is useless on the common case |
Re-prompt the model when verification fails | **Rejected.** Optimising against your own verifier produces answers that satisfy the check without being truer |
"Faithfulness > 0.98" as a target | **Rejected.** LLM-judged and noisy; §27.13 forbids presenting such a score as calibrated correctness |
NLI or LLM-critic entailment per claim | **Deferred.** A new model dependency (§4) and per-claim inference on a host already paying 16 s. Deterministic numeral checking first, measured, then decide |
Context compression (LLMLingua) | **Rejected.** It rewrites text; §14.9 makes citations character offsets into stored strings, so compression invalidates every one |
Fetch the unified table container and re-inject headers | **Inapplicable.** ADR-003 refuses a table detector that bounded 2 of 6 regions correctly, and our chunks are text blocks with no markup. Parent expansion is the available approximation |
RAGAS / DeepEval metrics | **Deferred.** They need an evaluation set, which §22.6 does not yet have |
Streaming with a citation manifest | **Deferred, and a trade-off recorded below** |

### Streaming versus structured output

A schema-constrained JSON object cannot be streamed as progressive prose: there is one
complete body. Inline `[n]` markers in free text stream well and give no per-claim structure
to verify or strip.

Structured output is chosen, because per-claim granularity is what allows one unsupported
claim to be removed instead of the whole answer being rejected. A streaming interface would
require inline markers and a different verification shape, and that is a decision for the
interface phase rather than one to pre-empt here.

---

## What this does not claim

- **No hallucination freedom.** The Gate bounds what a claim may assert about numbers and
  sources. Wording remains the model's, and a fluent, well-cited, subtly misleading summary
  is not prevented by any of this.
- **No prompt-injection immunity.** Document text enters the prompt. Delimiting content as
  data is a mitigation; §30.11 now says so explicitly.
- **No measured answer quality.** §22.6's golden set does not exist, so the phase can show
  that an unbound numeral never reaches a reader and cannot show that answers are good.
  ENV-012 will distinguish the two.
