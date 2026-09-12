# ENV-001 — Environment Validation

- **Status:** recorded
- **Date:** 2026-09-12
- **Phase:** 1 — project foundation and environment validation
- **Method:** `scripts\windows\check-environment.cmd` and `scripts\windows\verify-dependencies.cmd`, executed by the developer from the repository root with the project virtual environment activated. Both scripts are read-only: nothing was started, installed, upgraded, or downloaded.
- **Scope:** measurements and observed service status only. This record selects no parser, embedding model, reranker, generation model, or threshold. Measured results are not written into `PROJECT_BLUEPRINT.md`.
- **Sanitization:** no user name, absolute path, IP address, proxy setting, credential, or internal network detail is recorded.

## Measured host envelope

| Property | Measured value |
|---|---|
| Operating system | Windows 11 Enterprise, build 26200 |
| CPU | 11th Gen Intel Core i7-1165G7 @ 2.80 GHz |
| Physical cores | 4 |
| Logical processors | 8 |
| Total physical memory | 15.7 GB |
| Free space on project volume | 378.5 GB |
| Graphics adapter | Intel Iris Xe Graphics (integrated) |
| Dedicated accelerator | None present |

Usable memory *under the complete running stack* has not been measured. That measurement belongs to the Docker and model validation phases.

## Toolchain status

| Component | Observed status |
|---|---|
| Python | 3.12.10 |
| Virtual environment | Active and isolated |
| pip | 26.2.1 |
| Dependency consistency (`pip check`) | No broken requirements found |
| Known vulnerabilities (`pip-audit`) | No known vulnerabilities found |
| Package imports | Runtime imports and `finsight` 0.1.0 import succeeded |

## Service status at validation time

| Component | Observed status |
|---|---|
| Docker CLI | Version 29.7.2, build a7dcaa6, present on PATH |
| Docker daemon | **Not running or unavailable during validation.** Container backend OS type could not be reported. Not started or repaired in this phase. |
| Docker Compose | CLI version v5.5.0 present |
| WSL | Version 2.7.13.0, kernel 6.18.33.2-2, WSLg 1.0.73.2 |
| WSL distributions | `Ubuntu` (version 2, stopped), `docker-desktop` (version 2, stopped) |
| Ollama | Version 0.34.0; default local endpoint reachable and responded with its version |

### Notes

- The Docker daemon being unavailable is recorded as an observation, not a defect. Phase 1 requires no Docker service, and no attempt was made to start or fix it.
- Ollama emitted `failed to get console mode for stderr: The handle is invalid.` when run from a non-interactive console context. This is recorded as **non-blocking**: the version reported as 0.34.0 and the default local endpoint responded successfully.

## Implications for later phases

These follow from the measurements above and constrain, but do not decide, later selections.

1. **No dedicated accelerator.** Integrated graphics only, so embedding, reranking, and generation candidates must be evaluated as CPU-bound workloads on this host.
2. **15.7 GB total memory across 4 cores / 8 threads.** Ollama generation, embedding, cross-encoder reranking, layout-aware parsing, and the containerized infrastructure all compete for one shared envelope. Coexistence must be measured, not assumed — consistent with the blueprint's exclusion of heavyweight embedding models and 70B generation models for this target.
3. **Ample free disk (378.5 GB).** Corpus, vector index, and model cache growth are not constrained at this stage.
4. **Container backend unverified.** The WSL 2 Linux-container backend must be confirmed with a responding daemon before Compose topology work begins.

## Open items

| Item | Owner phase |
|---|---|
| Confirm Docker daemon responding and container backend OS type is `linux` | Docker infrastructure phase |
| Measure usable memory, ingestion and query latency, and peak resource use under the complete stack | Docker and model validation phases |
| Shortlist and record exact Ollama model identifiers, revisions, or digests | Environment and model validation phase (approval-gated) |
