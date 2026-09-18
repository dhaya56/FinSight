# ENV-003 — Object Storage and Schema Validation

- **Status:** recorded
- **Date:** 2026-09-18
- **Phase:** 3 — object storage, first schema, document intake
- **Method:** `docker compose up -d` and `docker compose ps` run by the developer; read-only `docker image inspect`, `docker stats` and `docker info` inspection; capability probes issued through the application's own S3 adapter against a throwaway container; Alembic applied, reversed and re-applied against the running database; the integration suite run against both services.
- **Scope:** measurements and observed status only. No parser, model, threshold, or experiment winner is selected here. Measured results are not written into `PROJECT_BLUEPRINT.md`.
- **Sanitization:** no user name, absolute path, credential, proxy setting, or internal network detail is recorded. The only addresses named are loopback literals already present in the committed `compose.yaml`.
- **Relationship to earlier records:** ENV-001 and ENV-002 are unchanged. The backend decision itself is recorded separately in [ADR-001](ADR-001-object-storage-backend-selection.md).

## Object store

| Property | Observed value |
|---|---|
| Image | `chrislusf/seaweedfs:4.47` |
| Resolved digest | `sha256:ce9e796f1fe6f06968f4c04bdaf8f678dad9c8acdfef3d244133d71bfa6bf882` |
| Binary version | `4.47 c50733600 linux amd64` |
| Registry | Docker Hub. **`ghcr.io/seaweedfs/seaweedfs` is a Helm chart, not an image** — verified by manifest media type `application/vnd.cncf.helm.config.v1+json` |
| Container | `finsight-objectstore-1`, running, healthy |
| Published address | IPv4 loopback only, port 8333 |
| Privilege | The image drops to a non-root `seaweed` user via `su-exec` |
| Health check | `curl -f http://localhost:8333/status` returns 200; `/healthz` also returns 200; `/` returns 403 because credentials are enforced |

### Credential model

SeaweedFS configures S3 identities through a JSON file passed to `-s3.config`. There is **no environment-variable path**. Its own help states that without that file "you can use any access key and secret key to access the S3 APIs", so the file is a security control rather than a convenience: `docker/seaweedfs/s3.json`, generated from `.env` and gitignored, with `s3.json.example` committed to document its shape.

The CI path differs by necessity: GitHub service containers cannot override a command, so the workflow runs the image's default command with no identities file. That was verified to work — health returns 200 and the adapter round-trips — and is acceptable for an ephemeral, isolated container that holds no real data.

## S3 capability verification

Probed through the application's own adapter, not through a separate client.

| Capability | Result |
|---|---|
| `create_bucket`, `head_bucket` | Works; `ensure_bucket` is idempotent |
| `put_object` without checksum | Works |
| `put_object` with `ChecksumAlgorithm=SHA256` | **Accepted** |
| `upload_fileobj` with `ChecksumAlgorithm=SHA256` | **Accepted** |
| `head_object` with `ChecksumMode=ENABLED` | **Returns `ChecksumSHA256`, and the value byte-matches our own digest** |
| `get_object` round trip | Digest preserved exactly |
| Conditional write `If-None-Match: *` | First succeeds; second returns **412 PreconditionFailed** |
| Multipart upload and retrieval (6 MB) | Digest preserved exactly |

Upstream issue #8911 reports SHA-256 checksums as unsupported. That did not reproduce here: AWS requires `ChecksumMode=ENABLED` on HEAD before checksum headers are returned, which is the most likely explanation for the report. `FINSIGHT_S3_SEND_CHECKSUM` therefore **defaults to true**, and remains configurable because S3 compatibility is a spectrum.

The authoritative integrity control remains our own streamed SHA-256, which *is* the object key, plus verification on read. Server-side checksums are corroboration, not the guarantee.

## Database and schema

| Property | Observed value |
|---|---|
| Server | PostgreSQL 18.6 (Debian 18.6-1.pgdg13+2) |
| `uuidv7()` | Available natively; `uuid_extract_version` returns 7 and a timestamp is extractable, confirming time-ordering |
| `gen_random_uuid()` | Available, and not needed |
| CREATE privilege | Held by the application role |
| Migration | `a2d5660001ae`, applied, reversed to base, and re-applied cleanly |
| Schema readiness | `is_schema_current()` returned `True` at head, `False` at base, and `True` again after restoring — verified against the live database, not only through test overrides |

## Measured resource use

Both services running, idle apart from the test suite.

| Measurement | Value |
|---|---|
| PostgreSQL container memory | 47.75 MiB |
| Object-store container memory | 165 MiB |
| Combined container memory | ~213 MiB |
| Docker Desktop VM allocation | 7.605 GiB, 8 CPUs |
| Host total memory | 15.70 GB (unchanged from ENV-001) |
| Host available memory | **1,551 MB** |

The containers are not what consumes the host: 213 MiB of container usage sits inside a VM sized at 7.6 GiB, alongside the developer's own tooling. Available host memory of roughly 1.5 GB is the figure that matters for later model selection, and it was measured without Ollama serving a model.

### Resource limits remain deferred

PROJECT_BLUEPRINT.md §38.11 requires container limits, and ENV-002 deferred them to "the phase where multiple services coexist" — which this is. They are still deferred, now with a measured reason rather than an absent one: these are **idle measurements**. A limit must be derived from peak usage under load, and the largest object exercised so far is 6 MB. Setting a bound from idle numbers would invent a threshold, which §36.12 forbids. Limits belong with the first realistic ingestion workload.

## Open items

| Item | Owner phase |
|---|---|
| Container resource limits — idle usage measured, peak under load not | First realistic ingestion workload |
| Usable memory with Ollama actively serving a model | Model validation phase |
| Readiness has no code-level deadline; its bound is addresses × connect timeout (ENV-002) | Observability phase |
| Object data has **no backup procedure**; §29.14 and §31.12 remain unimplemented for both stores | Backup and recovery phase |
| Connection-pool sizing left at SDK defaults for both PostgreSQL and S3 | When concurrency is introduced |
| Presigned URLs deferred; they bypass API authorization and need a TTL and audit decision | UI phase |
| Transfer values (timeouts, retries, multipart thresholds) are unmeasured defaults | Measure under load |
| Starlette deprecates `httpx` with its `TestClient` in favour of `httpx2` | When Starlette drops `httpx` |
| `defusedxml` is present only transitively; the blueprint requires it as a direct dependency | XML parsing phase |
| **Parser isolation is not yet enforced** (§11.7): only trusted development documents may be processed until the restricted parser container exists | Parser worker phase |
