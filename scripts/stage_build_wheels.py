"""Fetch the wheels an image cannot download for itself into the build context.

**Why any wheel needs staging.** Every pin in ``requirements.txt`` resolves from PyPI
except ``torch``, whose CPU-only build is published on a separate index. Measured from
inside the build container: ``pypi.org`` presents its own certificate chain and
verifies, while the PyTorch index presents a chain this host's network re-issues and
the container has no way to verify. The host verifies it — its trust store carries the
issuing root — so the download works there and fails one layer down.

**Three ways to fix that, and why this is the one.**

* Disable certificate verification for that index. Forbidden outright (CLAUDE.md §10),
  and it would turn a network control into a hole in the build.
* Install the issuing root into the image. It makes the build depend on this
  particular network, embeds an organisation's certificate into a distributable
  artefact, and leaves the running container trusting a CA it has no reason to.
* **Fetch the wheel where verification succeeds and carry it into the context.** The
  wheel is a pinned, hash-verifiable artefact; a build that reads it needs no network
  for that package at all. This is the ordinary approach for restricted or air-gapped
  builds and it costs 196 MB of build context.

The digest is printed and re-checked, so a staged wheel is verifiable rather than
merely present — the difference between a reproducible build input and a file someone
once put in a directory.

Run before building the API image, and again after changing the ``torch`` pin:

    python scripts/stage_build_wheels.py

Idempotent. A wheel already staged with a matching digest is left alone.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LOCK = REPO / "requirements.txt"
DESTINATION = REPO / "docker" / "api" / "wheels"

PYTORCH_INDEX = "https://download.pytorch.org/whl/cpu"

# The image is linux/amd64 on CPython 3.12. Stated rather than inferred from this
# host, which is Windows: `pip download` cross-targets, and a wheel fetched for the
# wrong platform installs nowhere and fails at the build's last step.
PLATFORM = "manylinux_2_28_x86_64"
PYTHON_VERSION = "3.12"

STAGED_FROM_PYTORCH = ("torch",)
"""Distributions fetched from the PyTorch index rather than PyPI.

``torchvision`` is deliberately absent: it is a Docling dependency and the
sequence-classification head the API reranks with does not use it.
"""


def pinned(distribution: str) -> str:
    """The exact pin for ``distribution`` from the lock file.

    Read rather than passed in, so this script cannot drift from the one pinned
    file that CLAUDE.md §10 makes the version authority.
    """
    pattern = re.compile(rf"^{re.escape(distribution)}==(\S+)\s*$", re.IGNORECASE)
    for line in LOCK.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line.strip())
        if match:
            return f"{distribution}=={match.group(1)}"
    raise SystemExit(f"{distribution} is not pinned in {LOCK}")


def digest_of(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def already_staged(requirement: str) -> Path | None:
    """An existing wheel for this exact pin, if one is there.

    Matched on the normalised version, so ``torch==2.14.1+cpu`` recognises
    ``torch-2.14.1+cpu-cp312-...whl`` and does not recognise a 2.14.0 left behind by
    an earlier pin.
    """
    name, _, version = requirement.partition("==")
    prefix = f"{name.replace('-', '_')}-{version}-"
    for candidate in DESTINATION.glob("*.whl"):
        if candidate.name.startswith(prefix):
            return candidate
    return None


def fetch(requirement: str) -> Path:
    DESTINATION.mkdir(parents=True, exist_ok=True)
    print(f"  fetching {requirement} for {PLATFORM}, cp{PYTHON_VERSION.replace('.', '')}")
    subprocess.run(
        [
            sys.executable, "-m", "pip", "download", requirement,
            "--no-deps",                 # dependencies resolve from PyPI inside the build
            "--only-binary", ":all:",    # required when cross-targeting a platform
            "--platform", PLATFORM,
            "--python-version", PYTHON_VERSION,
            "--implementation", "cp",
            "--extra-index-url", PYTORCH_INDEX,
            "--dest", str(DESTINATION),
        ],
        check=True,
    )
    staged = already_staged(requirement)
    if staged is None:
        raise SystemExit(
            f"pip reported success but no wheel for {requirement} is in {DESTINATION}"
        )
    return staged


def stage(*, verify_only: bool) -> int:
    print(f"lock file : {LOCK}")
    print(f"target    : {DESTINATION}")
    print(f"index     : {PYTORCH_INDEX}\n")

    missing = 0
    for distribution in STAGED_FROM_PYTORCH:
        requirement = pinned(distribution)
        staged = already_staged(requirement)

        if staged is None:
            if verify_only:
                print(f"  {requirement}: NOT STAGED")
                missing += 1
                continue
            staged = fetch(requirement)

        size = staged.stat().st_size / 1024 / 1024
        print(f"  {requirement}")
        print(f"    file   : {staged.name}")
        print(f"    size   : {size:.1f} MiB")
        print(f"    sha256 : {digest_of(staged)}")

    if verify_only:
        return 1 if missing else 0

    print("\ndocker/api/wheels is git-ignored: a build input, not source")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Check that every wheel is staged for its current pin, and fetch nothing.",
    )
    return stage(verify_only=parser.parse_args().verify)


if __name__ == "__main__":
    sys.exit(main())
