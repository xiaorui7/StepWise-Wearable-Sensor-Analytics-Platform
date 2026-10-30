# StepWise Project Guide

This guide describes the request flow and module responsibilities. Deployment commands and
measured results are linked below.

## Request Flow

1. A client submits walking TXT, optional standing calibration and sensor configuration.
2. FastAPI validates the request and creates or reuses a PostgreSQL analysis record.
3. The storage backend persists inputs; Redis/RQ receives the analysis identifier.
4. A worker conditionally claims the job and obtains an execution token.
5. The shared Python pipeline parses, segments, extracts features and generates reports.
6. The worker publishes artifacts through the configured storage backend.
7. PostgreSQL verifies execution ownership before committing results and completion state.
8. Clients poll status and retrieve completed results and artifacts through the API.

Database, queue and storage operations do not share a distributed transaction. See
[Reliability](docs/RELIABILITY.md) for failure windows and operator reconciliation.

## Module Responsibilities

| Module | Responsibility |
|---|---|
| `src/stepwise/core/` | Parsing, calibration, contact detection, segmentation, features, quality and screening rules |
| `src/stepwise/pipeline.py` | Return a typed analysis bundle and artifact bytes without choosing storage |
| `src/stepwise/reports/` | Generate HTML and charts |
| `src/stepwise/api/` | Validate submissions, return job state and serve completed artifacts |
| `src/stepwise/jobs.py`, `repository.py` | Queue processing, bounded retries and fenced database transitions |
| `src/stepwise/storage.py`, `s3_storage.py` | Local filesystem and optional S3-compatible storage |
| `web/` | React upload, processing, result and history views |
| `miniprogram/` | Historical mobile client using text submissions |

The pipeline produces `result.json`, `steps.csv`, `pressure.png`, `orientation.png` and
`report.html`. Separating artifact generation from storage lets the CLI, worker and tests
use the same calculations.

## Storage and Deployment

Default Docker Compose uses a persistent local artifact volume shared by API and worker.
The optional MinIO profile and kind deployment use object storage instead of a shared application
filesystem. Attempt-specific immutable keys, content verification and database ownership checks
prevent incomplete or stale attempts from publishing a completed report.

The kind demo runs two API replicas and two workers on one local node. It does not establish
multi-node availability or production capacity. Failed publication can leave unreferenced objects;
automatic orphan collection is not implemented.

## Verification and Limitations

Tests cover parsing, mapping, segmentation, rule behavior, JSON serialization, storage integrity,
concurrent submissions and claims, retries, process interruption and end-to-end report retrieval.
Deterministic synthetic fixtures verify software behavior, not clinical accuracy.

StepWise remains non-diagnostic. Four pressure points and IMU readings support engineering
screening, not clinical diagnosis. Authentication, multi-user isolation and a durable enqueue outbox
are not implemented.

## Technical Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Reliability and recovery](docs/RELIABILITY.md)
- [Benchmark methodology](docs/BENCHMARK.md)
- [Object storage and local Kubernetes](docs/CLOUD_INFRASTRUCTURE.md)
- [Cloud infrastructure verification](docs/CLOUD_EVIDENCE.md)
