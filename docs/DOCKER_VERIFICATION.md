# Docker Compose Verification

This document records a local runtime verification of the containerized StepWise application. It
describes commands that were actually executed and observed results. The test used only deterministic
synthetic software-test data; it does not provide clinical validation or production-load evidence.

## Environment

- Verification date: 2026-09-29
- Host: Windows 11, 64-bit, WSL 2 backend
- Docker Engine: 29.8.1
- Docker Compose: 5.5.1
- Compose services: `web`, `api`, `worker`, `postgres`, and `redis`
- Persistent volumes: `postgres_data`, `redis_data`, and `artifact_data`

## Build and startup

The stack was built and started from a clean local checkout with:

```bash
docker compose up --build -d
docker compose ps
```

Docker successfully built the React/Nginx web image and the shared Python API/worker images. It also
pulled PostgreSQL 16 and Redis 7 images. All five containers started; the configured health checks for
PostgreSQL, Redis, and the API reached `healthy`.

The following endpoints were then requested successfully:

| Check | Observed result |
|---|---|
| `GET http://localhost:8000/health` | HTTP success with `{"status":"ok"}` |
| `GET http://localhost:8000/ready` | HTTP success with `{"status":"ready"}` |
| `GET http://localhost:5173` | HTTP 200 |

The API startup command ran `alembic upgrade head`, so this startup also exercised migration of an
empty PostgreSQL database before the application became healthy.

## Asynchronous analysis

A multipart request submitted these checked-in fixtures:

- `fixtures/synthetic/normal_like.synthetic.txt`
- `fixtures/synthetic/standing_neutral.synthetic.txt`

The API initially returned `QUEUED`. The RQ worker processed the job, and the persisted state reached
`SUCCEEDED` on the next two-second poll. The result endpoint exposed these generated artifacts:

- `report.html`
- `pressure.png`
- `orientation.png`
- `steps.csv`
- `result.json`

`report.html` was retrieved through the artifact endpoint with HTTP 200. This verifies the local path
from upload to PostgreSQL job state, Redis/RQ dispatch, core analysis, persistent artifact storage,
and API retrieval.

## Restart persistence

The API container was restarted after the successful analysis:

```bash
docker compose restart api
```

The health endpoint recovered, the same completed result remained retrievable, and the history
endpoint still reported the stored analysis. This verifies persistence across an API container
restart for the exercised workflow. It is not a backup, failover, or durability benchmark.

Recent logs for the five services were also searched for `ERROR`, `Traceback`, `FATAL`, and `panic`;
none were found during this verification run.

## Reproduce

```bash
docker compose up --build -d
docker compose ps
```

Open `http://localhost:5173`, select **Load demo data**, and submit the bundled synthetic example.
The OpenAPI interface is available at `http://localhost:8000/docs`.

Stop the stack while preserving its data:

```bash
docker compose down
```

Avoid `docker compose down -v` unless deleting the database and artifact volumes is intentional.

## Scope

This run verifies one local five-container happy path and restart persistence. It does not establish
multi-user security, high availability, internet-scale throughput, clinical accuracy, or production
reliability.
