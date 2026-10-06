"""Constrained generation: the model writes prose, never numbers.

§26.3 has the model return a typed intermediate rather than an answer. Numerals enter
through placeholders that code binds to evidence and substitutes deterministically (§26.5,
§26.7), and §27.3 removes any factual numeral that arrived another way. The model's
contribution is wording; every value in a released answer came from a source region.

The boundary is :mod:`finsight.generation.port`. Nothing above it knows which runtime
answers, and nothing below it knows what a claim means.
"""
