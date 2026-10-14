# Engineering audit

Audit date: 2026-10-13. Baseline commit: `1f8499c`.

## Baseline evidence

The repository is a modular monolith: pure sensor calculations in `core/`, in-memory analysis and
report orchestration in `pipeline.py`, FastAPI in `api/`, SQLAlchemy persistence, Redis/RQ, filesystem
artifacts, React, and an optional WeChat client. There are eight explicit HTTP operations including
health, readiness, and the mobile text endpoint. Both clients submit and poll jobs. The domain
screening thresholds are explicitly engineering thresholds; they are not changed by this upgrade.

`analyses` stores four statuses, timestamps, configuration and JSON results. `artifacts` stores
metadata and private storage keys. The initial Alembic migration creates these tables. Every legacy
repository method commits separately, including every generated artifact row. Docker Compose
provides five services and named volumes; the API runs migrations before startup.

The baseline test suite was executed using the existing `.venv`: **29 passed, one PostgreSQL test
skipped, 86% combined statement/branch coverage**. The first attempt with the base Miniconda Python
failed collection because that environment lacked the project and dependencies; it is not a code
regression. The PostgreSQL test calls a worker function synchronously; the RQ adapter test uses
fakeredis. Neither proves real concurrent RQ worker execution.

Historical September Docker evidence records a successful synthetic job and API restart. Baseline
CI runs Ruff, mypy, migrations, tests with PostgreSQL/Redis services, and frontend/backend image
builds. The checked-in benchmark measured direct sequential pipeline calls, including charts and
reports: 20 runs, 323.54 ms median and 406.70 ms p95. It excludes HTTP and queue waiting. The supplied
resume figures (30 passing tests, 83%, 558 ms) are not supported by this checkout's fresh baseline.

## Gaps and implementation plan

| Area | Baseline gap | Modules to change |
|---|---|---|
| Lifecycle | Unconditional RUNNING update; duplicate execution can overwrite a terminal result | `repository.py`, `db_models.py`, migration |
| Retries | Exceptions are swallowed, so RQ can report success when PostgreSQL reports failure | `jobs.py`, `worker.py`, `settings.py` |
| Recovery | Dead workers leave RUNNING forever; no queued-job reconciliation | new `recovery.py`, CLI, reliability documentation |
| Submission | No idempotency key or transactional concurrency constraint | `api/main.py`, repository, migration |
| Publication | Files written directly; each artifact committed independently | `storage.py`, worker, repository |
| Validation | UTF-8/size/MIME checks exist; malformed numeric rows and infinity need stronger checks | parser and API tests |
| Diagnostics | Request IDs logged separately but not linked to jobs; no retry/attempt context | API and worker logging |
| Evidence | No concurrent load benchmark; real Redis/RQ coverage missing | tests, benchmark script, CI |

Implementation order: database claims and key uniqueness; bounded RQ retry policy; attempt-specific
atomic artifact publication and fenced completion; reconciliation; real database/queue and concurrent
tests; measured small load workload; documentation and final verification. No new framework or
clinical rules are introduced.

## Upgrade results

Final changes and fresh measurements are recorded in `RELIABILITY.md`, `BENCHMARK.md`, and
`RESUME_EVIDENCE.md`. Baseline numbers above remain historical, rather than being silently replaced
with a claimed performance improvement.
