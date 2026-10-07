"""The answer path, end to end (§26, §27).

retrieve → assemble evidence → build the prompt → generate → resolve citations → validate →
decide → record. Every stage is a module of its own and this one only orders them, so changing
how a numeral is checked does not reach into how a prompt is built.

**Losing the model costs prose, not the answer** (§26.10, §27.9). The three generation failures —
unreachable, prompt truncated, contract violated — each abstain with a flag naming the cause and
leave the evidence intact, because a reader given five cited passages and told no answer was
composed has more than a reader given a stack trace. The flags are distinct because the operator's
action differs: restart the model, lower the evidence budget, check that the runtime honours
schemas at all.

**No evidence skips the model.** A question whose retrieval returned nothing cannot produce a
supported claim, and measured on this host the model takes five seconds to say so. The decision is
still reached by :class:`AnswerDecision` so there remains one shape of outcome; only the call is
skipped.

**The record is written whether or not anything was released.** A refusal is the outcome most worth
being able to look up later, and §31 puts the question, the decision and its reasons on the audit
record regardless of which way it went.

**Model calls happen outside every transaction** (§29.7). Each database read here opens and closes
its own session, so a generation that takes sixteen seconds never holds one open.
"""

import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Final
from uuid import UUID

from sqlalchemy.orm import Session

from finsight.generation.contract import ANSWER_SCHEMA, GeneratedAnswer, parse_answer
from finsight.generation.decision import (
    REASON_NO_EVIDENCE,
    AnswerDecision,
    Decision,
    SupportBand,
    decide,
)
from finsight.generation.evidence import EvidenceSet, assemble_evidence
from finsight.generation.port import (
    GenerationRequest,
    GenerationResult,
    GenerationShapeError,
    GenerationTruncatedError,
    GenerationUnavailableError,
    Generator,
)
from finsight.generation.prompt import build_prompt
from finsight.generation.resolution import resolve_answer, source_element_ids
from finsight.generation.validation import validate_answer
from finsight.persistence.database import session_scope
from finsight.persistence.repositories.answers import AnswerRepository
from finsight.persistence.repositories.chunks import ChunkRepository
from finsight.persistence.repositories.source import SourceRepository
from finsight.retrieval.contracts import RetrievalFilters
from finsight.retrieval.pipeline import Result, RetrievalPipeline

__all__ = [
    "DEGRADED_GENERATION_CONTRACT",
    "DEGRADED_GENERATION_TRUNCATED",
    "DEGRADED_GENERATION_UNAVAILABLE",
    "AskService",
    "AskedAnswer",
    "build_ask_service",
]

DEGRADED_GENERATION_UNAVAILABLE: Final = "generation_unavailable"
"""The model could not be reached or did not answer in time (§27.9).

Retryable, and the evidence below the answer is unaffected — it was retrieved before the model
was called.
"""

DEGRADED_GENERATION_TRUNCATED: Final = "generation_prompt_truncated"
"""The prompt exceeded the context window, so the model did not read all of the evidence.

Reported rather than answered around. An answer composed from a silently truncated prompt cites
passages the model never saw, which looks fully supported and is not. Lower
``generation_evidence_budget_chars`` or raise ``generation_context_window``.
"""

DEGRADED_GENERATION_CONTRACT: Final = "generation_contract_violated"
"""The runtime returned something the schema forbids, so it is not constraining its output.

A configuration fault, not a bad question: retrying reproduces it. Named separately because the
answer path's grounding guarantees are stated against a typed response (§26.3).
"""


@dataclass(frozen=True, slots=True)
class AskedAnswer:
    """One answered, partial or abstained question, with everything behind it.

    The evidence is carried whatever the decision was. An abstention with five passages attached
    is useful — the reader can read them — and an abstention with none says something different,
    so the two must be distinguishable by the caller rather than collapsed into "no answer".
    """

    question: str
    decision: AnswerDecision
    evidence: EvidenceSet
    model: str

    answer_id: UUID | None = None
    """The audit record's identifier, or ``None`` when recording was not asked for."""

    timings_ms: dict[str, int] = field(default_factory=dict)
    """Stage latencies. Generation dominates, which is the reason they are separated."""

    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass(frozen=True, slots=True)
class AskService:
    """Answers a question from the indexed corpus."""

    pipeline: RetrievalPipeline
    generator: Generator
    evidence_budget_chars: int
    expand_below_chars: int
    session_scope_factory: Callable[[], AbstractContextManager[Session]] = session_scope

    def ask(
        self,
        question: str,
        *,
        filters: RetrievalFilters | None = None,
        limit: int = 8,
        record: bool = True,
    ) -> AskedAnswer:
        """Answer one question.

        ``limit`` bounds the candidates considered for the evidence set, not the claims
        released: merging and the character budget both reduce it further, and the Gate may
        remove claims after that.

        ``record=False`` exists for measurement and for probes that must not leave rows behind.
        An answer shown to a reader is always recorded — a decision nobody can reference is one
        nobody can question.

        Raises:
            ValueError: the question is blank. Left to raise rather than abstained, because an
                empty question is a caller fault and not a property of the corpus.
        """
        if not question.strip():
            raise ValueError("question must not be blank")

        timings: dict[str, int] = {}
        started = time.perf_counter()
        retrieved = self.pipeline.search(question, filters=filters, limit=limit)
        timings["retrieval_ms"] = _since(started)

        evidence = self._assemble(retrieved)
        degraded = tuple(retrieved.degraded)

        if evidence.is_empty:
            timings["total_ms"] = _since(started)
            return self._finish(
                question,
                _abstention(degraded, reason_codes=(REASON_NO_EVIDENCE,)),
                evidence,
                timings,
                record=record,
            )

        prompt = build_prompt(question, evidence)
        generation_started = time.perf_counter()
        try:
            result = self.generator.generate(
                GenerationRequest(prompt=prompt, schema=ANSWER_SCHEMA)
            )
            answer = parse_answer(result.payload)
        except GenerationUnavailableError:
            flag = DEGRADED_GENERATION_UNAVAILABLE
        except GenerationTruncatedError:
            flag = DEGRADED_GENERATION_TRUNCATED
        except GenerationShapeError:
            flag = DEGRADED_GENERATION_CONTRACT
        else:
            timings["generation_ms"] = _since(generation_started)
            return self._verify(
                question, answer, result, evidence, degraded, timings, started, record=record
            )

        timings["generation_ms"] = _since(generation_started)
        timings["total_ms"] = _since(started)
        return self._finish(
            question,
            _abstention((*degraded, flag)),
            evidence,
            timings,
            record=record,
        )

    def _assemble(self, retrieved: Result) -> EvidenceSet:
        """Expand, merge, budget and order the retrieved candidates (§20.8).

        Parents and the parents' own citations are read in one session each: a round trip per
        candidate, on a path already dominated by model latency, buys nothing.
        """
        if not retrieved.candidates:
            # Still an EvidenceSet rather than None, so the empty case has the same shape as
            # every other and the caller is not asked to check for two kinds of nothing.
            return assemble_evidence(
                (), parents={}, budget_chars=self.evidence_budget_chars
            )

        with self.session_scope_factory() as session:
            repository = ChunkRepository(session)
            parents = repository.parents_of(
                chunk_ids=[candidate.chunk_id for candidate in retrieved.candidates]
            )
            parent_citations = repository.citations_for(
                chunk_ids=[parent.chunk_id for parent in parents.values()]
            )

        return assemble_evidence(
            retrieved.candidates,
            parents=parents,
            citations=parent_citations,
            budget_chars=self.evidence_budget_chars,
            expand_below_chars=self.expand_below_chars,
        )

    def _verify(
        self,
        question: str,
        answer: GeneratedAnswer,
        result: GenerationResult,
        evidence: EvidenceSet,
        degraded: tuple[str, ...],
        timings: dict[str, int],
        started: float,
        *,
        record: bool,
    ) -> AskedAnswer:
        """Resolve the citations, run the Gate, and reach one decision."""
        verification_started = time.perf_counter()
        with self.session_scope_factory() as session:
            element_text = SourceRepository(session).text_for(
                element_ids=source_element_ids(answer, evidence)
            )
        resolved = resolve_answer(answer, evidence, element_text=element_text)
        findings = validate_answer(resolved, evidence)
        decision = decide(
            resolved, findings, degraded=(*degraded, *result.degraded)
        )
        timings["verification_ms"] = _since(verification_started)
        timings["total_ms"] = _since(started)

        return self._finish(
            question,
            decision,
            evidence,
            timings,
            record=record,
            model=result.model,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
        )

    def _finish(
        self,
        question: str,
        decision: AnswerDecision,
        evidence: EvidenceSet,
        timings: dict[str, int],
        *,
        record: bool,
        model: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> AskedAnswer:
        """Record the decision, then return it.

        ``model`` falls back to the configured identifier when no call completed, so a refusal
        still records which model would have answered — the alternative is a row whose model is
        blank and which §22's comparison therefore cannot place.
        """
        resolved_model = model or self.generator.model
        answer_id: UUID | None = None
        if record:
            with self.session_scope_factory() as session:
                answer_id = AnswerRepository(session).record(
                    question=question,
                    decision=decision,
                    model=resolved_model,
                    evidence_passages=len(evidence.passages),
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    elapsed_ms=timings.get("total_ms", 0),
                )

        return AskedAnswer(
            question=question,
            decision=decision,
            evidence=evidence,
            model=resolved_model,
            answer_id=answer_id,
            timings_ms=timings,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )


def _abstention(
    degraded: tuple[str, ...], *, reason_codes: tuple[str, ...] = ()
) -> AnswerDecision:
    """An abstention reached without the model having produced claims.

    Built here rather than by :func:`decide`, which decides *from claims*. There are none to
    decide from: either nothing was retrieved or the generation call never returned one. The
    shape is identical so the caller and the audit record see one kind of outcome.
    """
    return AnswerDecision(
        decision=Decision.ABSTAINED,
        reason_codes=reason_codes,
        support_band=SupportBand.NONE,
        released=(),
        withheld=(),
        degraded=degraded,
    )


def _since(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def build_ask_service() -> AskService:
    """Wire the answer path from configuration."""
    from finsight.config.settings import get_settings
    from finsight.generation.ollama_generator import build_generator
    from finsight.retrieval.pipeline import build_retrieval_pipeline

    settings = get_settings()
    return AskService(
        pipeline=build_retrieval_pipeline(),
        generator=build_generator(settings),
        evidence_budget_chars=settings.generation_evidence_budget_chars,
        expand_below_chars=settings.generation_expand_below_chars,
    )
