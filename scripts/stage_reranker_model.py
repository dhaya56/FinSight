"""Copy the cross-encoder out of the developer's HuggingFace cache into the build context.

**Why this exists.** The reranker loads with ``local_files_only=True`` (ADR-006, and
ENV-007 records the reason: an implicit download inside a request path is a network
call the architecture does not permit). So the model has to be inside the image, and
Docker can only copy from the build context — which the cache, living under the user
profile, is not.

**Why not download it during the build.** A build that reaches the network is not
reproducible, and it would make the image depend on HuggingFace being up. The model is
already local and already approved; copying it is deterministic and offline.

**Why the cache layout is preserved rather than flattened.** ``from_pretrained`` accepts
a plain directory, which would be simpler — but then ``reranker.model`` returns a
filesystem path, and that value is recorded on every retrieval result as
``reranker_model`` (``retrieval/pipeline.py``) and shown in the query trace. Provenance
would read ``/opt/models/...`` instead of ``cross-encoder/ms-marco-MiniLM-L-6-v2``.
Keeping the hub layout and pointing ``HF_HOME`` at it means the model keeps its name.

Run before building the API image:

    python scripts/stage_reranker_model.py

Idempotent. Re-running verifies the staged copy rather than copying again.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DESTINATION = REPO / "docker" / "api" / "model"

MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
CACHE_DIRECTORY = "models--cross-encoder--ms-marco-MiniLM-L-6-v2"

REQUIRED = (
    "config.json",
    "model.safetensors",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "vocab.txt",
)
"""Files the tokenizer and the sequence-classification head both need.

Checked by name rather than trusting the directory, because a partially-populated
cache copies without error and then fails at the first reranked query — in the one
code path that degrades silently by design (§23.8 returns fused order and a flag).
"""


def hub_root() -> Path:
    """Where HuggingFace keeps its hub cache on this machine."""
    from huggingface_hub import constants

    return Path(constants.HF_HUB_CACHE)


def snapshot_of(model_root: Path) -> Path:
    """The revision directory to copy, resolved through the cache's own ref.

    ``refs/main`` records the commit the cache resolved, so the staged copy is
    pinned to a revision rather than to whichever directory sorts first.
    """
    ref = model_root / "refs" / "main"
    if ref.is_file():
        revision = ref.read_text(encoding="utf-8").strip()
        candidate = model_root / "snapshots" / revision
        if candidate.is_dir():
            return candidate

    snapshots = sorted((model_root / "snapshots").glob("*"))
    if not snapshots:
        raise SystemExit(f"no snapshot found under {model_root / 'snapshots'}")
    if len(snapshots) > 1:
        raise SystemExit(
            f"{len(snapshots)} snapshots under {model_root / 'snapshots'} and no "
            "refs/main to choose between them; the staged revision must be explicit"
        )
    return snapshots[0]


def digest_of(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def stage(*, verify_only: bool) -> int:
    model_root = hub_root() / CACHE_DIRECTORY
    if not model_root.is_dir():
        raise SystemExit(
            f"{MODEL} is not in the local cache at {model_root}.\n"
            "The reranker has never run on this machine, or the cache moved."
        )

    source = snapshot_of(model_root)
    revision = source.name
    missing = [name for name in REQUIRED if not (source / name).is_file()]
    if missing:
        raise SystemExit(
            f"the cached snapshot {revision} is missing {', '.join(missing)}; "
            "it is incomplete and would fail at the first reranked query"
        )

    target = DESTINATION / CACHE_DIRECTORY / "snapshots" / revision
    print(f"model    : {MODEL}")
    print(f"revision : {revision}")
    print(f"source   : {source}")
    print(f"target   : {target}")

    if verify_only:
        staged = [name for name in REQUIRED if (target / name).is_file()]
        print(f"\nstaged   : {len(staged)} of {len(REQUIRED)} required files")
        return 0 if len(staged) == len(REQUIRED) else 1

    target.mkdir(parents=True, exist_ok=True)
    total = 0
    for name in REQUIRED:
        origin, destination = source / name, target / name
        if destination.is_file() and digest_of(destination) == digest_of(origin):
            print(f"  = {name}")
        else:
            shutil.copy2(origin, destination)
            print(f"  + {name}")
        total += destination.stat().st_size

    # The ref is what makes the staged copy self-describing: the Dockerfile copies a
    # hub cache, and a hub cache without refs/main has no resolvable revision.
    ref = DESTINATION / CACHE_DIRECTORY / "refs" / "main"
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_text(revision, encoding="utf-8")

    print(f"\nstaged {total / 1024 / 1024:.1f} MiB into the build context")
    print("docker/api/model is git-ignored: it is a build input, not source")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Check that the model is already staged and copy nothing.",
    )
    arguments = parser.parse_args()
    return stage(verify_only=arguments.verify)


if __name__ == "__main__":
    sys.exit(main())
