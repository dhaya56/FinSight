# ADR-008 — llama3.1:8b adopted provisionally for generation

**Status.** Accepted as **provisional**, 2026-10-06. Not a selection.

**Follows** ADR-002 (PyMuPDF), ADR-004 (Nomic), ADR-006 (MiniLM reranker). Same reasoning
in each: §22 reserves the choice for recorded evidence, no comparison has been run, and the
work cannot proceed without *a* model. Recording the adoption as provisional is what keeps
"it is what we are using" from becoming "it is what we chose".

---

## Decision

Generation uses `llama3.1:8b` through host-native Ollama, with the output constrained by a
JSON schema the runtime enforces, temperature 0, and an 8,192-token context window.

No alternative was evaluated.

---

## Why this one, honestly

| Reason | Weight |
|---|---|
| Already pulled on this host, 4.9 GB | Operational, not quality |
| Runs on CPU within the measured window | Necessary |
| Supports Ollama's schema-constrained decoding | **Load-bearing — see below** |
| Instruction-tuned, so it follows an output contract | Assumed, not measured |

None of these is a quality argument. The model's answer quality is **unmeasured** and
cannot be measured: §22.6's golden question set does not exist, so there is nothing to
score against. ENV-012 will record mechanical properties — whether an unbound numeral ever
reaches a reader — which is a different claim from whether the answers are good.

---

## The schema constraint is the reason this architecture is viable

§26.3 requires the model to return a typed intermediate rather than prose. Measured against
the running service, three calls with an invented prompt:

| Call | Time | Output |
|---|---|---|
| Free-form, includes model load | 37.3 s | prose, 70 tokens |
| Schema-constrained | 17.2 s | **valid JSON, correct shape** |
| Schema-constrained, warm | 16.0 s | **valid JSON, correct shape** |

Passing a JSON schema as `format` constrains decoding, so the contract is enforced at the
boundary rather than requested and repaired. Without this, an 8B model asked for JSON needs
retries and a repair path, and every repair is an opportunity to accept something that is
*nearly* the contract — which is how a claim's citations go unread and its numerals reach a
reader unchecked.

This is a property of the runtime, not of the model, so it survives a model change. A
candidate that Ollama cannot constrain is disqualified for a structural reason rather than
a quality one.

---

## Measured cost

> **Superseded by [ENV-012](ENV-012-generation-validation.md) §1–2.** The figures below were
> taken against an *invented* prompt of a few hundred characters, before the answer path
> existed. They are left in place because the schema-constraint finding above rests on them,
> but the conclusion drawn from them was wrong and is corrected here.

| | Value |
|---|---|
| Warm latency, 2-claim answer, 62 completion tokens | **16.0 s** |
| Throughput | ~3.9 tokens/s |
| First call | adds a 4.9 GB model load |

**"An answer is tens of seconds" was wrong.** It generalised from a short prompt, and the cost
is dominated by *reading* the evidence rather than writing the answer. Measured end to end over
real passages, one answer took **302 s**, of which **287 s** was the model: prompt evaluation
runs at 27.3 tokens per second against decode's 3.0, and no GPU offload is available on this
host. ENV-012 §1 has the split.

Two consequences followed. The configured `generation_timeout_seconds` of 300 s was **below the
worst case the other bounds permit** (510 s) and was raised to 600 s — the old value would have
abandoned a working generation and reported it as the model being unreachable. And the reasoning
that the answer surface should be the CLI "until the latency is known" was satisfied rather than
contradicted: the latency is now known, and the UI is built around it with evidence rendered
first while the answer composes.

---

## What this does not claim

- **No quality comparison.** No other model was run. Mistral, Qwen and the larger Llama
  variants are untested here.
- **No reproducibility guarantee.** Temperature 0 asks for the least variance available;
  batching and floating-point order can still move a token, so nothing asserts identical
  output across runs.
- **No prompt-injection immunity.** Document text enters the prompt. §26.2's delimiting of
  content as data is a mitigation and is not a defence, and §10 forbids claiming otherwise.
- **No judgement of financial reasoning.** The model contributes wording. Every numeral in a
  released answer is verified against a span its own claim cites (§26.5, §27.3, as amended by
  ADR-009 — this record originally said "a placeholder bound to a source region", which the
  design no longer uses), and arithmetic is forbidden to it outright (§7: financial arithmetic
  never uses model arithmetic).

---

## Selection criteria, for whoever runs the comparison

Recorded now so the eventual evaluation is not designed around whatever this model happens
to do well:

1. **Contract compliance** — share of responses that satisfy the schema, and whether
   constrained decoding is supported at all.
2. **Unsupported-numeral rate** — how often the model writes a figure that appears in none
   of the spans the claim itself cites. This is the failure the Gate exists to catch, so a
   model that does it rarely reduces refusals.
3. **Citation discipline** — how often it cites a passage outside the evidence set.
4. **Abstention behaviour** — whether it declines when the evidence does not answer the
   question, rather than composing something plausible.
5. **Latency against the above**, since quality that costs a minute per answer is a
   different product.

Production admission requires recorded evidence on a golden set plus developer approval
(§8). Until then this model is in use and is not chosen.
