"""Reading and advancing generations within a caller-owned transaction.

The only interesting operation here is :meth:`GenerationRepository.activate`,
because it is the moment evidence becomes visible to a reader. §11.13 requires the
switch to happen only after indexing and validation succeed, and requires a failed
shadow run never to replace active evidence.

Activation is three writes that must not be separable: supersede the generation
currently active, activate the new one, and move the pointer on the document
version. Any two of those without the third leaves the system claiming something
untrue — two live generations, or a pointer at a superseded one. They are issued
inside the caller's transaction for that reason, and the partial unique index is
what makes a half-applied sequence fail rather than persist.

``tables.source`` is imported for its side effect only. ``generations`` carries a
foreign key to ``extraction_runs``, and SQLAlchemy resolves that string reference
against whatever is registered on the shared ``MetaData`` — so without the import
the mapper fails at first use rather than at import time. The same reason
``migrations/env.py`` imports every table module.
"""

import datetime
from uuid import UUID

from sqlalchemy import func as sql_func
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from finsight.persistence.tables import source as _source  # noqa: F401
from finsight.persistence.tables.documents import DocumentVersion
from finsight.persistence.tables.generations import (
    STATE_ACTIVE,
    STATE_FAILED,
    STATE_SHADOW,
    STATE_SUPERSEDED,
    Generation,
)


class GenerationActiveError(RuntimeError):
    """An operation was attempted that is only valid on a non-active generation.

    Separate from a domain error because it reports a caller mistake rather than a
    condition of the data: the generation is fine, and the request was not.
    """


class GenerationRepository:
    """Record and advance generations. Opens no transaction of its own."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def open(
        self,
        *,
        document_version_id: UUID,
        extraction_run_id: UUID | None = None,
        chunking_config_version: str | None = None,
    ) -> UUID:
        """Create a shadow generation and return its identifier.

        Shadow is the only state a generation may be born in (§11.12). It becomes
        queryable through :meth:`activate` and by no other route.
        """
        generation = Generation(
            document_version_id=document_version_id,
            state=STATE_SHADOW,
            extraction_run_id=extraction_run_id,
            chunking_config_version=chunking_config_version,
        )
        self._session.add(generation)
        self._session.flush()
        return generation.id

    def attach_extraction_run(self, *, generation_id: UUID, run_id: UUID) -> None:
        """Record the extraction component of a generation (§11.12).

        Separate from :meth:`open` because a generation is a container that fills
        as its stages complete, not a row written once at the end.
        """
        self._session.execute(
            update(Generation)
            .where(Generation.id == generation_id)
            .values(extraction_run_id=run_id)
        )

    def activate(self, *, generation_id: UUID) -> None:
        """Make a generation queryable, superseding whichever one was.

        The order matters and is not an implementation detail: the outgoing
        generation is superseded **before** the incoming one is activated, because
        the partial unique index permits only one active generation per document
        version. Reversing the two would make the database reject a correct
        activation.

        ``clock_timestamp()`` rather than ``now()``: ``now()`` returns the
        transaction's start time, which can precede rows written moments earlier
        and make the activation look as though it happened before the work it
        depends on. The same reasoning is recorded against ``complete_run`` in
        ``repositories/source.py``.
        """
        version_id = self._session.execute(
            select(Generation.document_version_id).where(Generation.id == generation_id)
        ).scalar_one()

        self._session.execute(
            update(Generation)
            .where(
                Generation.document_version_id == version_id,
                Generation.state == STATE_ACTIVE,
                Generation.id != generation_id,
            )
            .values(state=STATE_SUPERSEDED, activated_at=None)
        )
        self._session.execute(
            update(Generation)
            .where(Generation.id == generation_id)
            .values(state=STATE_ACTIVE, activated_at=sql_func.clock_timestamp())
        )
        self._session.execute(
            update(DocumentVersion)
            .where(DocumentVersion.id == version_id)
            .values(active_generation_id=generation_id)
        )

    def fail(self, *, generation_id: UUID) -> None:
        """Abandon a generation without disturbing whatever is active.

        §11.13: a failed shadow run never replaces active evidence. Nothing here
        touches the pointer, which is the whole point — the previous generation
        stays queryable through a failure.
        """
        self._session.execute(
            update(Generation)
            .where(Generation.id == generation_id)
            .values(state=STATE_FAILED, activated_at=None)
        )

    def reopen(self, *, generation_id: UUID) -> None:
        """Return a failed generation to shadow so indexing can be retried.

        Guarded to the failed state, and the guard is the point. A failed
        generation was never queryable, so reopening it takes nothing from a
        reader; an *active* one is being served right now, and returning it to
        shadow would make a reader's evidence vanish mid-session while the
        reprocessing §11.13 requires — build a new generation, switch atomically —
        was never performed.

        A generation that is not failed is left exactly as it is, and the caller
        sees no error: asking to reopen a shadow generation is a harmless no-op,
        which is what makes ``index --retry`` safe to pass habitually.

        Raises:
            GenerationActiveError: the generation is active.
        """
        state = self.state_of(generation_id=generation_id)
        if state == STATE_ACTIVE:
            raise GenerationActiveError(
                f"generation {generation_id} is active and is being served; "
                "reprocessing builds a new generation rather than reopening this "
                "one (§11.13)"
            )
        if state != STATE_FAILED:
            return
        self._session.execute(
            update(Generation)
            .where(Generation.id == generation_id, Generation.state == STATE_FAILED)
            .values(state=STATE_SHADOW, activated_at=None)
        )

    def prunable_ids(self, *, include_failed: bool = False) -> list[UUID]:
        """Generations whose index points are safe to remove, newest first.

        **Superseded only by default, and the exclusions are the substance.** A shadow
        generation may be mid-build right now and its points are the work in progress; an
        active one is being served. Both are selected *out* by naming the states that may
        go, never by naming the states that may not — an inverted predicate turns a
        missing state constant into "prune everything".

        ``include_failed`` is opt-in because a failed generation is retryable through
        ``index --retry``: removing its points is harmless, since the point identifiers are
        derived (§29.9) and a retry rewrites them, but it discards work that a retry would
        otherwise skip. Deliberate, not habitual.

        Returns identifiers only. The caller removes points from the derived index; nothing
        here deletes a row, because PostgreSQL holds the audit trail a superseded generation
        exists for, and §29.12 reserves removal for tombstoning.
        """
        states = [STATE_SUPERSEDED, *( [STATE_FAILED] if include_failed else [] )]
        rows = self._session.execute(
            select(Generation.id)
            .where(Generation.state.in_(states))
            .order_by(Generation.id.desc())
        )
        return list(rows.scalars().all())

    def for_configuration(
        self,
        *,
        document_version_id: UUID,
        extraction_run_id: UUID,
        chunking_config_version: str,
    ) -> UUID | None:
        """A non-failed generation already built from exactly this configuration.

        What makes re-chunking an unchanged document a no-op. Failed generations
        are excluded so a failure stays retryable, matching the partial unique
        index that enforces the same rule in the database.
        """
        return self._session.execute(
            select(Generation.id).where(
                Generation.document_version_id == document_version_id,
                Generation.extraction_run_id == extraction_run_id,
                Generation.chunking_config_version == chunking_config_version,
                Generation.state != STATE_FAILED,
            )
        ).scalar_one_or_none()

    def for_current_run(
        self, *, document_version_id: UUID, extraction_run_id: UUID
    ) -> UUID | None:
        """The generation built from this extraction run, whatever its state.

        Keyed on the run rather than the chunking configuration, because the caller
        is a stage driver asking "which generation should I index for this
        document?" — and the answer must not change when the chunking configuration
        does. A re-chunk under a new configuration supersedes the old generation
        through the configuration index, so at most one non-failed generation exists
        per run.

        Failed generations are excluded, so a document whose indexing failed reports
        as unchunked rather than silently retrying a generation that needs
        ``--retry`` to be reopened. Ordered newest first so that a run holding both
        a superseded and a current generation yields the current one.
        """
        return self._session.execute(
            select(Generation.id)
            .where(
                Generation.document_version_id == document_version_id,
                Generation.extraction_run_id == extraction_run_id,
                Generation.state != STATE_FAILED,
            )
            .order_by(Generation.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()

    def active_for(self, *, document_version_id: UUID) -> UUID | None:
        """The generation retrieval may see for this version, or None.

        Read from ``generations`` rather than from the pointer on the version, so
        that a pointer left stale by a defect shows up as a disagreement instead of
        being reported as truth.
        """
        return self._session.execute(
            select(Generation.id).where(
                Generation.document_version_id == document_version_id,
                Generation.state == STATE_ACTIVE,
            )
        ).scalar_one_or_none()

    def document_version_of(self, *, generation_id: UUID) -> UUID | None:
        """Which version a generation belongs to, or None if it does not exist."""
        return self._session.execute(
            select(Generation.document_version_id).where(
                Generation.id == generation_id
            )
        ).scalar_one_or_none()

    def active_ids(self) -> list[UUID]:
        """Every generation a reader may currently see (§20.2, §11.13).

        Read from ``generations.state`` rather than from the pointers on
        ``document_versions``, for the reason :meth:`active_for` gives: a pointer left
        stale by a defect would silently widen what retrieval is allowed to return,
        and the state column is what the partial unique index actually constrains.

        An empty list is a real answer — nothing has been indexed and activated yet —
        and callers must treat it as "nothing may be returned" rather than as "do not
        filter".
        """
        return list(
            self._session.execute(
                select(Generation.id)
                .where(Generation.state == STATE_ACTIVE)
                .order_by(Generation.activated_at)
            ).scalars()
        )

    def state_of(self, *, generation_id: UUID) -> str | None:
        return self._session.execute(
            select(Generation.state).where(Generation.id == generation_id)
        ).scalar_one_or_none()

    def activated_at(self, *, generation_id: UUID) -> datetime.datetime | None:
        return self._session.execute(
            select(Generation.activated_at).where(Generation.id == generation_id)
        ).scalar_one_or_none()
