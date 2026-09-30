# StepWise Project Guide

This guide is for preparing to explain StepWise in a software engineering interview. Start with the
end-to-end request flow, then learn each module well enough to explain why it exists and what can
fail.

## The Short Explanation

StepWise accepts a text recording from a pressure-sensor insole and IMU. A FastAPI endpoint validates
and stores the input, creates a database record, and sends the record ID to an RQ worker. The worker
runs a deterministic Python pipeline, stores the generated report files, and updates the database.
The React client polls job status and displays the result.

The application is non-diagnostic. Its software architecture is the main engineering contribution;
the sensor rules provide the domain context.

## Request Flow

1. `POST /api/v1/analyses` receives walking TXT, optional standing TXT, and sensor mapping.
2. `api/main.py` checks type, size, UTF-8 encoding, and non-empty content.
3. `AnalysisRepository.create()` stores configuration and a `QUEUED` record.
4. `ArtifactStore.put()` stores input files under the analysis UUID.
5. `RQJobQueue.enqueue_analysis()` queues only the UUID, not a large file payload.
6. `execute_analysis_job()` loads the record and changes it to `RUNNING`.
7. `run_analysis()` parses the files, extracts features, evaluates rules, and creates artifacts.
8. The worker stores results and changes the record to `SUCCEEDED` or `FAILED`.
9. The client polls status, fetches the result, and requests report artifacts by name.

## Core Analysis

### Parser

**File:** `src/stepwise/core/parser.py`

The parser converts whitespace-delimited sensor rows into a DataFrame with normalized time. It
rejects recordings that contain no valid rows instead of passing empty data deeper into the system.

Example: a synthetic file with 960 valid rows becomes a DataFrame whose first time is `0.0 s`.

Interview question: **Why use a DataFrame?**

Answer: The analysis is column-oriented and relies on rolling filters, ratios, time differences, and
per-stance aggregation. pandas makes those operations explicit and matches the existing sensor-data
format. For a high-throughput service I would measure whether NumPy or another representation is
needed before changing it.

### Contact Detection and Segmentation

**Files:** `core/contact.py`, `core/segmentation.py`

The contact threshold is adaptive to the recording's observed pressure. Hysteresis uses separate
enter and exit thresholds so values near one threshold do not repeatedly toggle foot contact.
Segmentation turns the Boolean contact signal into stance intervals and removes intervals shorter
than the configured minimum duration.

Interview question: **Why hysteresis?**

Answer: A single threshold is sensitive to sensor noise. Separate on/off thresholds preserve state
around the boundary and make event detection more stable without introducing a complex model.

### Features, Quality, and Rules

**Files:** `core/features.py`, `core/quality.py`, `core/rules.py`

Feature extraction calculates pressure-region ratios, coarse pressure-center proxies, stance and
stride timing, orientation ranges, and calibration-relative angles. Quality checks detect short
trials, low sample rate, repeated timestamps, and zero pressure channels. Rules consume explicit
metrics and return user-facing cards.

Pressure and IMU disagreement is handled conservatively. The code can report a loading bias without
claiming a confirmed foot posture. Project thresholds are engineering choices, not clinical cutoffs.

Interview question: **Why rules instead of machine learning?**

Answer: The project has limited labeled data and no clinical validation set. Explainable rules make
the evidence and limitations visible and avoid claiming accuracy that was never measured.

## Pipeline and Reports

**Files:** `pipeline.py`, `reports/charts.py`, `reports/generator.py`

`run_analysis()` is the shared application pipeline. It takes text and configuration and returns an
`AnalysisBundle` containing a typed result and in-memory artifact bytes. It does not decide where
files are stored, so the CLI, worker, tests, and benchmark call the same function.

Artifacts are `result.json`, `steps.csv`, two PNG charts, and `report.html`.

Interview question: **Why return bytes instead of writing files inside the pipeline?**

Answer: Keeping storage out of the calculation makes the pipeline deterministic and easy to test.
The caller decides whether artifacts go to a local directory, worker-managed storage, or elsewhere.

## API

**Files:** `api/main.py`, `api/schemas.py`

FastAPI provides typed multipart and JSON inputs, OpenAPI documentation, pagination, status polling,
result retrieval, and artifact downloads. A shared helper performs record creation, input storage,
and queue submission for both upload formats.

Failure cases include unsupported content types, oversized or empty files, invalid configuration,
queue unavailability, unknown IDs, incomplete jobs, and missing artifacts. Unexpected exceptions are
logged while clients receive a safe error message rather than a traceback.

Interview question: **Why return 202 instead of 200?**

Answer: The request accepts work but does not finish the analysis. `202 Accepted` communicates that
the client must poll the status resource for completion.

## Jobs and Persistence

**Files:** `jobs.py`, `repository.py`, `db_models.py`, `alembic/`

RQ transports analysis IDs to a worker. PostgreSQL stores the durable state, timestamps,
configuration, summary, result, and artifact metadata. The repository centralizes state updates and
commits. Alembic owns schema changes.

Interview question: **Why not store status only in Redis?**

Answer: Redis is the queue dependency and can be cleared or restarted. User-visible job history and
results belong in the relational database with the rest of the application state.

Interview question: **Why not microservices or Kafka?**

Answer: There is one small domain and no streaming or independent-team requirement. A modular
monolith with a separate worker gives request/compute isolation without unnecessary deployment and
debugging complexity.

## Artifact Storage

**File:** `storage.py`

Artifacts are stored under `<artifact root>/<analysis UUID>/<safe filename>`. The implementation
rejects path separators and verifies resolved paths remain below the configured root. Docker Compose
mounts the same persistent volume into the API and worker.

Interview question: **Why filesystem storage instead of S3?**

Answer: A shared persistent volume is enough for the local portfolio deployment. Object storage would
be appropriate for multiple hosts or independent scaling, but adding it now would increase setup
without solving a current requirement.

## Frontend

**Files:** `web/src/App.tsx`, `web/src/api.ts`

The React client has four states: upload, processing, result, and history. It submits multipart data,
polls every 800 ms while work is queued or running, and loads structured results plus report images.

The WeChat Mini Program is retained as an optional historical client. It uses the JSON text endpoint
because its file-transfer environment differs from a browser.

## Tests

The most important tests protect these failures:

| Test area | Failure protected against |
|---|---|
| Parser | empty or malformed files silently entering analysis |
| Contact/segmentation | noisy threshold transitions or incorrect stance boundaries |
| Features/rules | reversed sensor mapping or unsupported posture conclusions |
| Quality | short trials and broken channels receiving confident results |
| JSON | NaN or infinity producing invalid API JSON |
| Storage | path traversal or missing artifact content |
| API | returning results before completion or hiding queue failure |
| E2E | upload, worker, persistence, result, history, and report drifting apart |

The public fixtures are generated with fixed seeds. They make software tests reproducible but cannot
measure clinical accuracy.

## What Is Intentionally Missing

- Authentication and multi-user isolation
- Automatic RQ retries and idempotency guarantees
- S3-compatible object storage
- Concurrent load-test results
- Medical diagnostic or accuracy claims

These are honest boundaries, not hidden completed features. In an interview, explain what requirement
would justify adding each one instead of saying the project is production-ready.
