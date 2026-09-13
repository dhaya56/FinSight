# ENV-002 — Docker and PostgreSQL Validation

- **Status:** recorded
- **Date:** 2026-09-14
- **Phase:** 2 — PostgreSQL, connectivity, and readiness surface
- **Method:** `docker compose config`, `docker compose up -d`, and `docker compose ps` run by the developer; read-only `docker info` and `docker compose ps` inspection, plus timed probes and the integration suite, run against the live container.
- **Scope:** measurements and observed status only. No parser, model, threshold, or experiment winner is selected here. Measured results are not written into `PROJECT_BLUEPRINT.md`.
- **Sanitization:** no user name, absolute path, credential, proxy setting, or internal network detail is recorded. The only addresses named are loopback literals, which are already present in the committed `compose.yaml`.
- **Relationship to [ENV-001](ENV-001-environment-validation.md):** ENV-001 is unchanged. It recorded, correctly for its date, that the Docker daemon was not responding. This record supersedes nothing; it closes that open item with new evidence.

## Docker

| Property | Observed value |
|---|---|
| Daemon | Responding |
| Container backend OS type | `linux` — **closes the ENV-001 open item** |
| Docker server version | 29.7.2 |
| Compose validation | `docker compose config` passed |

## PostgreSQL container

| Property | Observed value |
|---|---|
| Image (pinned tag) | `postgres:18.6` |
| Resolved digest | `sha256:4ef4dbc939d61acea57712655ddb4b4ab27419c913f94cca0cd57cb3ea3c2280` |
| Container | `finsight-postgres-1` |
| State | running, healthy |
| Published address | IPv4 loopback only, port 5432 |
| Server version | 18.6 (Debian 18.6-1.pgdg13+2) |
| Server encoding | UTF8 |
| Data volume | `finsight-postgres-data` mounted at `/var/lib/postgresql` |

The digest is recorded for reproducibility. It is **not** pinned in `compose.yaml`: a digest identifies one platform-specific manifest, and multi-platform compatibility has not been verified. Pinning by digest remains a separate decision.

PostgreSQL 18 moved `PGDATA` to `/var/lib/postgresql/<major>/docker` and declares its volume at `/var/lib/postgresql`. The pre-18 mount path `/var/lib/postgresql/data` would appear to work while failing to persist data across container recreation. Verified against the official image documentation before `compose.yaml` was written.

## Measured behavior

| Scenario | Measurement |
|---|---|
| `GET /health/live`, database stopped | 200 in 0.05 s |
| `GET /health/ready`, database running | 200 in 0.312 s (first call, includes engine creation) |
| `GET /health/ready`, database stopped, host `localhost`, 5 s connect timeout | 503 in 10.31 s |
| Reachability probe, database stopped, host `127.0.0.1`, 5 s connect timeout | False in 5.22 s |
| Reachability probe, database stopped, host `localhost`, 2 s connect timeout | False in 4.38 s |
| Integration suite against the live container | 7 tests passed |

### Address families multiply the connect timeout

The worst-case wait for an unreachable database is **number of resolved address families × connect timeout**, not the connect timeout. The name `localhost` resolves to both `::1` and `127.0.0.1`; with the container stopped, Docker Desktop's networking drops packets rather than refusing the connection, so each address is tried until it times out.

`.env.example` therefore uses the IPv4 loopback address rather than the name, which halves the worst case and matches `compose.yaml`, where the service is published on IPv4 loopback only. This is a configuration mitigation, not a bound: readiness still has no end-to-end deadline in code.

## Open items

| Item | Owner phase |
|---|---|
| Readiness has no code-level deadline; its bound is addresses × connect timeout | Observability phase, alongside operating modes |
| Readiness checks reachability only. §31.2 also requires schema compatibility | First-migration phase |
| Pool size, max overflow, recycle interval, and connect timeout are unmeasured initial defaults, configurable through the environment | Measure under the complete stack |
| Container resource limits absent (§38.11) because stack memory is unmeasured | Phase where multiple services coexist |
| Usable memory, ingestion and query latency under the complete stack still unmeasured (carried from ENV-001) | Docker and model validation phases |
| Starlette 1.6.0 deprecates using `httpx` with its `TestClient` and asks for `httpx2`. Nothing is broken; no dependency was added and the warning was not filtered | Revisit when Starlette drops `httpx` support |
| `defusedxml` is present only as a transitive dependency of `pip-audit`; the blueprint requires it as a direct XML-safety dependency (carried from Phase 1) | XML parsing phase |
