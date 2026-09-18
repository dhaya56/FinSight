# ADR-001 — Object-Storage Backend Selection

- **Status:** accepted
- **Date:** 2026-09-18
- **Phase:** 3 — object storage, first schema, document intake
- **Supersedes:** the object-storage backend named in PROJECT_BLUEPRINT.md §9.5 and CLAUDE.md §6
- **Decision:** replace MinIO with **SeaweedFS** as the local S3-compatible object-storage backend.

## Context

The blueprint selected MinIO under an assumption stated in §29.4 and §41.12: an actively maintained, locally deployable community distribution, accessed through an abstraction that keeps it replaceable. Phase 3 halted when `docker compose up -d` could not pull the image.

Investigation established that the assumption no longer holds. This is an activation-condition failure, not a preference change.

| Fact | Evidence |
|---|---|
| Community repository archived | GitHub API reports `archived: true`, last push 2026-04-24, AGPL-3.0 |
| Maintenance mode announced | 2025-12-03 — no features, no pull requests, security fixes case-by-case |
| Declared unmaintained | February 2026; repository became read-only |
| Archived | 2026-04-25 |
| **Container publishing stopped** | **October 2025** — every pullable community image is frozen and unpatched |
| Docker Hub repositories deleted | `minio/minio` and `minio/mc` removed around 2026-09-11 to 2026-09-16 |
| Newest image on quay | `RELEASE.2025-09-07T16-13-09Z`, roughly twelve months old |

A related finding worth recording: the tag first chosen, `RELEASE.2025-10-15T17-29-55Z`, exists as a GitHub *source release* but was never published as a container image — quay returns `"tags": []` for it. A release tag is not an image tag, and the two diverged once publishing stopped.

## Options considered

| Option | Assessment |
|---|---|
| **SeaweedFS** | Apache-2.0, actively developed, supports object versioning, all-in-one single-node mode. **Chosen.** |
| versitygw | Apache-2.0, active, lightest footprint. Strong runner-up; a filesystem gateway rather than a storage system, so its semantics sit further from AWS. |
| Garage | Active, purpose-built for small self-hosted clusters, but AGPL-3.0, **no object versioning**, and needs a cluster-layout step even single-node. |
| Ceph RGW | Highest S3 fidelity, but mon/mgr/osd/rgw and multiple gigabytes — disproportionate on a laptop shared with a model runtime. |
| RustFS | Apache-2.0 MinIO drop-in, but young; §8.9 and §8.11 argue against an unproven component on the evidence path. |
| pgsty/minio fork | Single-maintainer fork of an abandoned codebase; a bridge for those with migration costs. We had none, being pre-commit. |
| Hosted R2 / B2 | Rejected: §5.7 and §8.11 require locally deployable components. |
| No local server, filesystem adapter only | Rejected: never exercises the S3 wire protocol, where multipart, checksums and error semantics live. |

## Decision

SeaweedFS, pinned to `chrislusf/seaweedfs:4.47`, chosen on four grounds: it was the most actively maintained viable option (repository pushed the day it was assessed); Apache-2.0 avoids the licensing-and-business exposure that stranded the previous choice; it supports object versioning, keeping the versioning and object-lock immutability controls available as future work; and its all-in-one `mini` mode keeps local operational complexity at one container.

**Registry note.** `ghcr.io/seaweedfs/seaweedfs` was initially proposed and is **wrong**: that package is a Helm chart (`application/vnd.cncf.helm.config.v1+json`), not a container image. The image is published on Docker Hub. This was caught by pulling and inspecting rather than by reading a package listing.

## Consequences

**Application code was unaffected.** The port, content-addressed keys, filesystem adapter, and every unit test are backend-agnostic, because the adapter was named for the protocol it speaks rather than the vendor it points at. The swap touched `compose.yaml`, `.env.example`, one settings flag, and one conditional in the adapter. Had the module been named `minio_store.py`, this would have meant renaming a module, its tests, and its settings mid-phase.

**One operational difference.** SeaweedFS configures S3 credentials through a JSON identities file passed to `-s3.config`, not through environment variables. Without that file it accepts **any** access key and secret. The file is generated locally from `.env` and gitignored; a committed `s3.json.example` documents its shape.

**Documents require amendment.** MinIO is named in 14 places across 13 blueprint sections and 3 places in CLAUDE.md. §29.3's abstraction requirement and §29.4/§41.12's portability warnings should be strengthened rather than softened — they predicted this failure, and the abstraction is why it cost six files instead of a rewrite.

## What would trigger revisiting

SeaweedFS entering maintenance mode or ceasing image publication; a material S3-compatibility gap appearing in a path FinSight depends on; or a move to a managed object store, which the abstraction already accommodates as a configuration change.
