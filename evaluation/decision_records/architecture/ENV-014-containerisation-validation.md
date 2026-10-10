# ENV-014 — The API and UI containerised, validated

**Date.** 2026-10-11. **Scope.** Blueprint §9 step 4, partially: the API, the UI and a
one-shot migrator. **Base image.** `python:3.12-slim`, Python 3.12.15, Debian trixie,
pinned by digest `sha256:a6e34c59…`. **Host.** Intel i7-1165G7, 4 physical cores / 8
threads, 15.7 GB, no accelerator. Docker Desktop, WSL 2, Linux containers.

**The stack starts, migrates, answers and reranks with nothing on the host but Docker
and Ollama.** `docker compose up -d` brings PostgreSQL, Qdrant and object storage to
healthy, runs `alembic upgrade head` once to completion, starts the API and waits for
its readiness probe, then starts the UI. Measured end to end on this host.

**Workers are deliberately absent.** §9 orders containerisation of the parser and
processing workers *after* their local paths work, and neither a worker loop nor an
upload route exists. Containerising them now would ship scaffolding.

---

## 1. Images

| | Before the layer fix | After | Change |
|---|---|---|---|
| `finsight-api:local` | 3.15 GB | **1.66 GB** | **−47%** |
| `finsight-ui:local` | 1.19 GB | **683 MB** | **−43%** |

### The layer fix, which was a measurement rather than a tidy-up

The first build of each image ended with `RUN chown -R root:root … && chmod -R a-w …`
over the application and the virtual environment. `docker history` on the API image:

| Layer | Size |
|---|---|
| `RUN chown -R root:root /app /opt/…` | **1.14 GB** |
| `COPY /opt/venv /opt/venv` | 1.04 GB |
| `COPY docker/api/model/` | 91.9 MB |
| Debian base | 87.7 MB |
| `COPY src/` | 1.53 MB |

A recursive mode change rewrites every file it touches, and a rewritten file is a new
layer entry — so the environment was stored **twice**, the second copy differing only
in a permission bit. It also secured nothing that was not already secured: `COPY`
lands root-owned files that group and other cannot write, the process runs as an
unprivileged uid, and Compose mounts the root filesystem read-only. Removing both
recursive calls removed 1.14 GB from one image and 507 MB from the other.

### Why the API image is 1.66 GB and not smaller

| Component | Size |
|---|---|
| Virtual environment (torch dominates) | ~1.04 GB |
| Cross-encoder weights | 91.9 MB |
| Debian base | 87.7 MB |
| Application source, migrations, metadata | ~1.6 MB |

Installing the root `requirements.txt` instead would produce roughly 4 GB: it is a
187-pin development superset carrying Docling, OpenCV, SciPy, pandas, reportlab,
python-docx, python-pptx and pytest. The API installs **57** packages — the set
derived by importing `create_app()` under a module tracer, plus three it needs
lazily (`psycopg`, `uvicorn`, `torch`/`transformers`). `torchvision` is excluded: it
is a Docling dependency and the sequence-classification head does not use it.

Trimming torch's headers and test data is the remaining lever and is **unmeasured**.

---

## 2. The reranker thread count, measured rather than guessed

The image first shipped `OMP_NUM_THREADS=2` as a cautious bound. Left unset, torch
sizes its pool from the host's CPU count, ignores the container's limit, and every
surplus thread contends with host-native Ollama — the process ADR-004 records an
indexing run being lost to.

Reranking 25 candidates on the same query, three samples per setting, after a warm-up
call to exclude the model load:

| `OMP_NUM_THREADS` | `torch.get_num_threads()` | rerank, ms |
|---|---|---|
| 1 | 1 | 6,003 / 5,094 / 4,462 |
| 2 | 2 | 2,544 / 2,870 / 2,648 |
| **4** | **4** | **2,307 / 2,248 / 2,189** |
| 8 | **4** | 2,210 / 2,363 / 2,300 |

**Four is the knee, and 8 is the same run.** torch reports 4 threads when asked for
8, clamping to the physical cores of this 4-core / 8-thread host — so the two rows
differ only by noise. The shipped value of 2 was costing about 18% of rerank latency
for nothing.

Set to 4 in the image, overridable through `API_OMP_THREADS` for a host with a
different core count.

**Limitation.** One host, one query, one depth. The figure is a thread-count
comparison, not a reranker latency benchmark — ADR-006 owns that, and its in-pipeline
rate of ~95 ms per candidate predicts ~2,375 ms at depth 25, which this sits just
inside. No cross-host claim is made.

---

## 3. Request latency through the container

Three warm samples, depth 25, after the cross-encoder is resident:

| Stage | ms |
|---|---|
| Lexical (BM25, Qdrant) | 55–68 |
| Dense (Ollama embed + Qdrant) | 115–121 |
| Fusion | 0 |
| **Rerank** | **2,189–2,307** |
| Resolve chunk text | 9–16 |
| **Total** | **~2,250–2,460** |

Cold, first request in a fresh container: **19,592 ms**, of which 13,213 ms is the
cross-encoder loading and 6,176 ms is Ollama's first embed.

**Reranking is 92% of a warm request**, and the rest of the path costs about 207 ms —
against ENV-010's host-native 337 ms for the same stages, which excluded reranking.
The container is not the cost; the cross-encoder is.

`reranker_model` on the response reads `cross-encoder/ms-marco-MiniLM-L-6-v2`, not a
filesystem path. That is why the model is staged in HuggingFace cache layout rather
than as a plain directory: `from_pretrained` accepts both, but a path would have been
recorded as the model's identity on every retrieval result and in every query trace.

`degraded: []` on every request — dense retrieval reached host Ollama and the
cross-encoder loaded offline from the baked weights on a read-only filesystem.

---

## 4. Memory, and the limits set from it

| Service | Start-up | After five searches | Limit | Ratio |
|---|---|---|---|---|
| `api` | 128.2 MiB | **493.1 MiB** | 2 GiB | 4.1× |
| `ui` | 54.6 MiB | **55.0 MiB** | 512 MiB | 9.3× |

The API's jump is torch loading lazily: the model is not resident until the first
reranked query, so an idle reading would have understated the requirement fourfold.
2 GiB matches the ratio PostgreSQL carries here, and the headroom is deliberate on a
host that has already run out of memory once.

The UI's ratio is looser than every other service's on purpose. 55 MiB is **one
browser session**, and Streamlit holds per-session state, so the ceiling scales with
concurrent viewers rather than with the workload. That ceiling is unmeasured, and
tightening the limit needs a multi-session measurement rather than a smaller number.

---

## 5. Security properties, verified rather than asserted

| Property | How it was checked | Result |
|---|---|---|
| Non-root | `os.getuid()` in each container | api **10001**, ui **10002** |
| Read-only root filesystem | `touch /app/probe` | `Read-only file system`; `/tmp` writable |
| Capabilities | `cap_drop: ALL`, `no-new-privileges` | set on both |
| Published ports | `docker compose config` | **every** port bound `127.0.0.1` |
| Auth enforced | unauthenticated, wrong-token and valid-token requests | **401 / 401 / 200** |
| Health unauthenticated | `/health/live`, `/health/ready` | 200 / 200 |
| **UI cannot reach a datastore** | socket connect from the ui container | `postgres`, `qdrant`, `objectstore` **unresolvable**; `api` reachable |
| API can reach its dependencies | socket connect from the api container | `postgres`, `qdrant`, `host.docker.internal:11434` all reachable |

The UI's isolation is topology, not configuration: it is attached only to the `edge`
network, so the datastores are not resolvable from it, and its image contains no
database driver, no object-store client, no vector-store client and no ORM —
`psycopg`, `boto3`, `qdrant_client`, `sqlalchemy`, `torch` and `alembic` are all
absent. Withholding a credential depends on someone remembering the policy;
withholding the route and the library does not.

### Migrations are a service, not a start-up step

`alembic upgrade head` runs in a one-shot `migrate` service that the API waits on with
`condition: service_completed_successfully`. Two API containers starting together
would otherwise both migrate one database, and Alembic's version table is not a lock —
the race resolves to whichever transaction commits first, with the loser failing
partway through DDL. Observed: `migrate` exited 0, then the API started and reported
healthy, which is itself the schema check, because readiness compares the database
revision against the shipped `migrations/` directory.

---

## 6. Two obstacles that shaped the build

### The PyTorch index is not verifiable from inside the build

Measured from the build container: `pypi.org` presents its own certificate chain and
verifies, while the index publishing the CPU-only torch build presents a chain this
host's network re-issues, which the container cannot verify. The host verifies it —
its trust store carries the issuing root — so the same `pip install` succeeds on the
host and fails one layer down with `ResolutionImpossible`.

Three ways out, and why the third was taken:

* **Stop verifying certificates for that index.** Forbidden outright (CLAUDE.md §10),
  and it converts a network control into a hole in the build.
* **Install the issuing root into the image.** Embeds an organisation's certificate
  into a distributable artefact, leaves the running container trusting a CA it has no
  reason to, and makes the build depend on one particular network.
* **Fetch the wheel where verification succeeds and carry it in.** `pip download`
  cross-targets `manylinux_2_28_x86_64` / cp312 from the Windows host, the wheel is
  recorded with its SHA-256, and the build resolves it through `--find-links` and
  reaches no second index at all. 187.2 MiB of build context, and the build needs no
  network for that package.

The pin is read out of `requirements.txt` rather than restated, so the staging script
cannot drift from the one file §10 makes the version authority. Confirmed in the build
log: `torch-2.14.1+cpu` installed, **no `nvidia-*` packages**.

### The migrations path is derived from `__file__`

`persistence/database.py` resolves `MIGRATIONS_PATH` as its own fourth parent plus
`migrations`. Under the source layout that is the repository root and correct;
installed into site-packages it becomes `<venv>/lib/python3.12/migrations`, which does
not exist — and readiness compares the database revision against that directory, so a
pip-installed API would report itself permanently un-ready.

So the images ship the source tree with `PYTHONPATH=/app/src` rather than installing
a wheel. That keeps the derivation true and keeps the image layout identical to the
one the tests run against. **Making the path configurable is the better fix** and is
an open item below; it would let the image install a wheel and drop `PYTHONPATH`.

---

## 7. Container logs were unbounded

`docker inspect` reported `json-file` with an empty options map on every service —
Docker's default, which has **no** `max-size` and **no** `max-file`. A long-running
stack writes until the disk fills, and the first symptom is normally something
unrelated failing to write. uvicorn logs a line per request and PostgreSQL logs more
than that, so "it is only a local stack" is not a bound.

Now 10 MiB across 3 files per service: 30 MiB each, 180 MiB for the stack. Enough to
diagnose a failure from minutes ago, which is what these logs are for. Real retention
needs a shipper, and §9 keeps observability services disabled by default.

Found by asking what the defaults were rather than by anything failing.

## 8. A latent crash found in the existing configuration

`.env.example` documented `FINSIGHT_QDRANT_PORT`, which is **not** a field on
`Settings`. Measured: an unknown `FINSIGHT_*` *environment variable* is ignored, but
an unknown key *in `.env`* raises `extra_forbidden` and the application does not
start. Uncommenting that line therefore broke the entire application rather than
changing a port.

Host port mappings now live in a `PUBLISH_*` namespace that cannot collide with the
settings namespace, and the variable has been removed from `.env.example` with the
reason recorded in its place.

---

## 9. Port allocation

Another project runs containers on this host publishing on `0.0.0.0` across
13000–19090. FinSight's application tier takes a reserved `87xx` block — **8701**
(API) and **8702** (UI) — chosen to clear that range and the defaults a developer is
likely to want elsewhere (8000, 8080, 8501, 3000), and verified free before use.
Compose project isolation does the rest: separate project names, separate networks,
and `docker compose down` here touches only FinSight's containers.

---

## 10. What this record does not claim

- **No cross-host performance claim.** Every figure is this host, this day.
- **No image-size claim beyond this dependency set.** 1.66 GB is what a PyTorch
  runtime costs; the torch trim is unmeasured.
- **No production-readiness claim.** There is no CI build of these images, no
  vulnerability scan, no CD, no secret manager, no multi-user auth, and no worker
  tier. §9 defers continuous delivery and this record does not advance it.
- **The UI memory ceiling is unmeasured** beyond a single session.

## 11. Open items

| Item | Owning phase |
|---|---|
| Make `MIGRATIONS_PATH` configurable, so the image can install a wheel and drop `PYTHONPATH` | 16b |
| Container image vulnerability scanning — a new tool, needing approval | 16b |
| Build the images in CI, with the staged wheel and model as cached inputs | 16b |
| Parser and processing workers, and the upload route they serve | 16b |
| Trim torch headers and test data from the runtime layer | unscheduled |
| Multi-session UI memory ceiling | unscheduled |
| TLS termination and a reverse proxy, at which point `--proxy-headers` becomes correct | 17 |
