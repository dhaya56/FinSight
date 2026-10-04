"""Stage Docling's model artifacts into a local, project-controlled cache.

Docling's wheel contains inference code, not trained weights. The weights live in
separate HuggingFace repositories and, left to itself, Docling fetches them at the
first ``convert()`` call — which PROJECT_BLUEPRINT.md §20.6 forbids, which the
restricted parser worker (§11.7) would fail closed on, and which once already left
this project with a working parser that could not be reproduced after the
user-level cache was cleared.

So the artifacts are staged here, deliberately, before any parse. Two properties
make this more than a download helper:

**Pinned to commits, not branches.** Docling asks for ``main`` for the layout
model. A branch is not a revision, and a model that silently moves underneath a
measurement invalidates it. ``PIN`` records the commit each artifact was verified
at; the script downloads that commit and refuses a mismatch.

**The cache makes a parse-time download impossible, not merely unlikely.** With
``artifacts_path`` set, Docling raises ``FileNotFoundError`` for a missing model
instead of reaching for the network. Today's guarantee comes from the network
being unreachable, which is not a guarantee.

TLS verification is never disabled. ``truststore`` is used so Python trusts the
host's own certificate store, which is what makes this work on a network whose
issuer is absent from the bundled CA list. There is deliberately no flag to skip
verification.

Run from an activated prompt at the repository root:

    python scripts/stage_docling_models.py

Re-running is safe: an artifact already present at the right commit is left alone.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path
from typing import Final, NamedTuple

os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
"""Force the classic HTTP download path, before huggingface_hub is imported.

Its chunked-transfer backend corrupts these downloads on this host, failing with
``Byte range not sequential`` partway through the layout model. ENV-007 hit it
during the feasibility spike and resolved it the same way; the fix lived only in
that session, so it was hit again on the first attempt to restage. It is set here
so it cannot be rediscovered a third time.

``setdefault`` rather than assignment, so an operator who needs the other backend
can still choose it from the environment. Must precede the import: the flag is
read when the library loads.
"""


class Artifact(NamedTuple):
    """One model repository Docling needs, and the commit we verified it at."""

    repo_id: str
    revision: str
    """What Docling itself asks for, kept so a drift is visible."""

    pin: str
    """The commit the revision resolved to when it was verified."""

    purpose: str

    @property
    def folder(self) -> str:
        """Docling locates a staged model by this name, derived from the repo id.

        Both the layout model and TableFormer resolve
        ``artifacts_path / repo_id.replace("/", "--")``, so the layout is theirs
        rather than ours and must not be prettified.
        """
        return self.repo_id.replace("/", "--")


REQUIRED: Final[tuple[Artifact, ...]] = (
    Artifact(
        repo_id="docling-project/docling-layout-heron",
        revision="main",
        pin="8f39ad3c0b4c58e9c2d2c84a38465abf757272d8",
        purpose="page layout and table region detection",
    ),
    Artifact(
        repo_id="docling-project/docling-models",
        revision="v2.3.0",
        pin="fc0f2d45e2218ea24bce5045f58a389aed16dc23",
        purpose="TableFormer table structure recognition",
    ),
)
"""Exactly what the configured pipeline needs, and nothing else.

Docling 2.133.0 references roughly sixty model repositories across its optional
VLM, OCR and figure-classification paths. OCR is disabled (§12.11) and no VLM
pipeline is used, so staging more would download gigabytes to satisfy capability
this project has not adopted. If this list is wrong, the verification step fails
loudly rather than quietly fetching the remainder.
"""

DEFAULT_CACHE: Final = Path("model_cache/docling")
"""Under the ``model_cache/`` path .gitignore already reserves for exactly this.

Project-controlled rather than the user-level default: the user cache sits outside
the repository, nothing here owns it, and its disappearance is what made a working
parser unreproducible once already.
"""


def _trust_host_certificates() -> None:
    """Make Python trust the host's own certificate store.

    Verification stays on. This exists because the bundled CA list does not
    contain every issuer a corporate network presents, and the alternative people
    reach for in that situation is disabling verification, which §10 forbids and
    which this script will not do.
    """
    import truststore

    truststore.inject_into_ssl()


def _stage(artifact: Artifact, cache: Path, *, force: bool) -> tuple[bool, str]:
    """Download one artifact at its pinned commit. Returns (changed, detail)."""
    from huggingface_hub import snapshot_download

    target = cache / artifact.folder
    if target.exists() and not force:
        return False, f"already staged at {target}"

    if target.exists():
        shutil.rmtree(target)

    path = snapshot_download(
        repo_id=artifact.repo_id,
        revision=artifact.pin,
        local_dir=str(target),
    )
    return True, f"staged to {path}"


def _verify(artifact: Artifact, cache: Path) -> str | None:
    """Confirm the staged artifact is present and non-empty.

    Returns a problem description, or None when it is sound. The commit is not
    re-derived from the files: ``snapshot_download`` was given the pin, so a wrong
    commit would have failed there rather than arrived quietly.
    """
    target = cache / artifact.folder
    if not target.is_dir():
        return f"missing: {target}"
    if not any(target.rglob("*")):
        return f"empty: {target}"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache",
        type=Path,
        default=DEFAULT_CACHE,
        help=f"where to stage the artifacts (default: {DEFAULT_CACHE})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-download even if an artifact is already present",
    )
    args = parser.parse_args(argv)

    cache: Path = args.cache
    cache.mkdir(parents=True, exist_ok=True)

    _trust_host_certificates()

    print(f"staging {len(REQUIRED)} artifact(s) into {cache.resolve()}\n")
    for artifact in REQUIRED:
        print(f"  {artifact.repo_id}@{artifact.pin[:12]}  ({artifact.purpose})")
        try:
            changed, detail = _stage(artifact, cache, force=args.force)
        except Exception as error:
            print(f"    FAILED: {type(error).__name__}: {error}", file=sys.stderr)
            return 1
        print(f"    {'downloaded' if changed else 'present'} - {detail}\n")

    problems = [p for a in REQUIRED if (p := _verify(a, cache)) is not None]
    if problems:
        for problem in problems:
            print(f"VERIFY FAILED: {problem}", file=sys.stderr)
        return 1

    total = sum(f.stat().st_size for f in cache.rglob("*") if f.is_file())
    print(f"all artifacts verified. cache size: {total / 1e6:.0f} MB")
    print(
        "\nSet FINSIGHT_DOCLING_ARTIFACTS_PATH to this directory, or pass"
        "\nartifacts_path to DoclingTableDetector, so a missing model fails"
        "\nloudly instead of being downloaded during a parse."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
