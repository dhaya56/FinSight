"""Constrained generation: the model writes prose and cites, code checks and decides.

§26.3 has the model return a typed intermediate rather than an answer. It emits claims with
citation references; code resolves each reference to the stored source span, and §27.3 removes
any factual numeral that does not appear in a span its own claim cites. The model's contribution
is wording and attribution; every value in a released answer was verified against a source
region.

ADR-009 replaced the typed placeholders and deterministic substitution §26.5 and §26.7
originally specified, because a model that emits the wrong placeholder produces a numeral that
is genuinely source-bound and genuinely wrong — undetectable by construction. A citation
reference is checkable: the resolved span either contains the asserted numeral or it does not.

The boundary is :mod:`finsight.generation.port`. Nothing above it knows which runtime answers,
and nothing below it knows what a claim means.
"""
