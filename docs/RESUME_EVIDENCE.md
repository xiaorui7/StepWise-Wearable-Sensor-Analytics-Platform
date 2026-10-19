# Resume evidence and interview preparation

**Infrastructure extension:** The October 16-19 object-storage/kind work is recorded separately in
[CLOUD_EVIDENCE.md](CLOUD_EVIDENCE.md), with commands in [CLOUD_INFRASTRUCTURE.md](CLOUD_INFRASTRUCTURE.md).
The counts and performance below describe the earlier October 13 baseline; do not mix them with the
new deployment measurements. The extension adds tested MinIO storage and a local two-API/two-worker
kind demo, not paid cloud deployment, multi-node availability, or exactly-once processing.

This file describes implemented software behavior and executed measurements. StepWise remains a
non-diagnostic four-pressure-channel/IMU prototype. Clinical rules and thresholds were not upgraded
into medical claims.

## A. Verified engineering contributions

| Problem | Implementation and exact code | Automated evidence | Trade-off |
|---|---|---|---|
| Duplicate submissions from concurrent callers | `api/main.py::_create_analysis_job` fingerprints effective config plus input bytes; `repository.py::create_submission` arbitrates hashed keys with a DB unique constraint; `20261013_0002_reliability.py` migrates the schema | `test_submission.py::test_api_idempotency_replay_and_conflict`; `test_infrastructure.py::test_concurrent_uploads_and_idempotency_use_postgres_constraint` | Exact-byte request identity; globally scoped keys in an unauthenticated local application |
| Duplicate workers and obsolete executions | `repository.py::mark_running` conditionally increments attempts; `owned`/`finish` lock and fence completion with an execution token | `test_postgres_concurrent_claim_has_one_winner`; `test_claim_is_atomic_and_terminal_execution_is_noop`; `test_reconcile_expired_execution_fences_old_worker` | Prevents duplicate published results, not exactly-once computation |
| Transient failures without infinite retries | `jobs.py::is_retryable`, `execute_analysis_job`, `process_analysis_job`, `RQJobQueue.enqueue_analysis`; configurable RQ Retry and scheduler in `worker.py` | `test_retry_classification.py`; `test_retry_budget_is_bounded`; `test_real_rq_retries_only_transient_processing_failure`; `test_delayed_retry_enters_scheduled_registry_then_completes`; `test_real_rq_permanent_failure_is_not_retried` | PostgreSQL and Redis transitions are separate; reconciliation closes detectable gaps |
| Partially visible reports | `storage.py::put` writes/replaces atomically; `publish` renames a complete attempt directory; `repository.py::finish` commits metadata/result together; artifact endpoint requires SUCCEEDED | `test_publication_failure_leaves_no_partial_directory`; `test_database_commit_failure_exposes_no_generated_rows`; `test_transient_failure_then_success_has_one_complete_artifact_set` | A successful rename followed by failed DB commit can leave an inaccessible orphan directory |
| Interrupted worker states | `jobs.py::handle_workhorse_killed`; `recovery.py::reconcile` and `stepwise-reconcile` CLI | `test_workhorse_abrupt_exit_is_persisted_as_failed`; `test_whole_worker_termination_is_recovered_after_lease`; `test_missing_redis_job_is_reconciled` | Recovery is an operator command; it marks an ambiguous job FAILED rather than silently re-running it |
| Malformed or oversized uploads | `api/limits.py::BodyLimitMiddleware`, `_read_upload`, `core/parser.py::parse_sensor_text`; safe path names and strict Pydantic config | `test_submission.py`, `test_storage.py`, `test_parser.py` | Body buffering is bounded but no global concurrent-upload quota exists |
| Request-to-worker diagnostics | `api/main.py::request_context`, persisted request ID, structured `analysis_state`/`analysis_failed` logs | `test_request_id_is_returned_and_persisted` | Basic logs, no distributed tracing system |
| Reproducible backend verification | Linux `tests` Compose profile, unique PostgreSQL schemas, real Redis queues and forked workers; CI supplies both dependencies | `test_infrastructure.py`, `test_migrations.py`, `.github/workflows/ci.yml` | Small local correctness workloads, not a production traffic model |

## B. Measured results

The complete Linux test suite passed **72 tests, zero failures and zero skips**. It ran with Python
3.12.15 against PostgreSQL 16 and Redis 7. The command and coverage counters are stored in
`benchmarks/verification.json`.

Coverage over `src/stepwise` was:

- Statements: 1053 / 1194 = **88.19%**.
- Branches: 191 / 256 = **74.61%**.
- Combined coverage.py value: **85.79%**.
- Domain `core/` statements: 354 / 379 = **93.40%**; branches: 102 / 128 = **79.69%**.

The combined coverage value must not be described as branch coverage. RQ child-process correctness
tests passed, although parent-process coverage does not include every line executed in a forked
child. Seven dependency/test-harness deprecation warnings were emitted: one Starlette TestClient
warning and six RQ fork-from-multithreaded-process warnings.

The real database/queue tests prove eight simultaneous identical-key uploads create one analysis and
one queued job; six simultaneous DB claims produce one winner. Two real workers complete four
jobs with overlapping execution intervals and one expected artifact set per job. Failure injection
checks immediate and scheduled transient retries, permanent errors, exhausted attempts, atomic
publication, a child exit, full worker termination and recovery.

The final HTTP workload completed **80 / 80 timed synthetic jobs** at 1, 5, 10 and 20 concurrent
clients, with two workers and no observed missing reports or duplicate IDs. At five clients it
measured **3.185 completed workflows/s**, POST p50/p95 **117.0/303.8 ms**, and completion p50/p95
**1311.6/1780.4 ms**, including queue wait and polling. At 20 clients, completion p95 increased to
**7123.1 ms**. These are measured workload results, not a sustained-capacity or availability promise.

The core/report pipeline separately measured **532.21 ms median** and **714.07 ms p95** over 20 runs
after warmup on Windows Python 3.13.12. It excludes queue/HTTP/database work. Twelve coarse Docker
resource snapshots are retained with the HTTP benchmark; they are not a true peak-memory profile.
See `docs/BENCHMARK.md` for definitions and the executable commands.

## C. Resume bullet suggestions

**StepWise - Wearable Sensor Analytics Platform | Python, FastAPI, PostgreSQL, Redis/RQ, Docker**

- Engineered an asynchronous analytics backend with eight typed REST operations, PostgreSQL persistence, Redis/RQ workers, and Docker Compose, supporting file submission, status polling, and generation of five report artifacts.
- Implemented database-constrained idempotency, bounded transient-failure retries, and execution-token fencing; published complete report sets atomically and reconciled interrupted worker states without exposing incomplete results.
- Validated the backend with 72 automated tests, including real PostgreSQL/Redis worker and concurrency tests; completed 80 synthetic jobs across 1-20 concurrent clients with no observed workflow failures.

An optional performance-focused replacement for the third bullet is:

- Benchmarked two background workers across 1-20 concurrent clients, completing 80 synthetic analyses; measured 3.185 workflows/s and 1.78-second p95 completion latency at five clients, including queue waiting.

These statements do not imply public users, clinical accuracy, production scale or an optimization
percentage. Test count is a parameterized pytest count, not 72 independent real-world scenarios.

## D. Technical interview preparation

### 1. Why a modular monolith with a background worker?

The same Python package contains HTTP handling, persistence and analytics, while pure domain modules
avoid web/database dependencies. FastAPI and RQ run as separate processes because chart generation
and calculation should not block an HTTP request. Keeping them in one codebase simplifies local
deployment and interfaces. Independent microservices would require separate contracts/deployments
without a current ownership or scaling need. See `pipeline.py`, `worker.py` and ADR 0001.

### 2. What does the API return before analysis completes?

Submission validates and stores the files, commits a PostgreSQL row and sends its UUID to RQ. It
returns HTTP 202 and an ID/status. Clients poll PostgreSQL-backed status, then request the structured
result and report URLs after SUCCEEDED. RQ may expire old result metadata; PostgreSQL still stores
completed results. API file validation/persistence runs in a threadpool; domain analysis runs in an
RQ child process. See `api/main.py::_create_analysis_job` and `jobs.py::process_analysis_job`.

### 3. How do you handle two simultaneous submissions with the same key?

The API hashes the key and fingerprints canonical effective configuration plus exact input bytes.
The insert is protected by a PostgreSQL unique constraint. One request creates the row; the loser
rolls back its failed insert and reads the winner. Matching fingerprint returns its ID; a mismatch
returns 409. No-key submissions remain independent. The mechanism is global to the local app and
has no TTL until the analysis is removed. The concurrency test uses eight caller threads against
real PostgreSQL and checks one row plus one real Redis queue job.

### 4. How do retries distinguish a temporary failure from bad input?

`is_retryable` uses explicit exception types, temporary errno values, and structured DB failure
evidence. A malformed recording is not retried. The owning attempt records QUEUED with a safe
transient category, releases its token and raises a safe exception that activates RQ Retry. The
worker scheduler handles delayed retries. PostgreSQL counts claims; RQ independently bounds dispatch
retries. Permanent failures disable further RQ retries and persist FAILED. Tests inject a one-time
timeout and an always-failing timeout, and inspect both PostgreSQL and actual RQ statuses.

### 5. How do multiple workers avoid writing duplicate reports?

`mark_running` performs a conditional update where status is QUEUED and attempts remain below the
configured limit. Only one winner receives an execution token. The worker writes into a unique
token directory. Completion locks the row and requires that token still own RUNNING. Artifact names
are also unique per analysis. Recovered/stale workers cannot publish DB metadata even if they finish
computation. This prevents duplicate logical results, but it is not exactly-once execution across
all failure windows. Six concurrent DB claims are explicitly tested.

### 6. What happens if the worker dies or Redis loses the job?

If only the RQ child dies, its surviving parent records a safe interrupted failure and stops
automatic retry of that unclassified process exit. If the whole worker is killed, a callback cannot
run. `stepwise-reconcile` later revokes an expired execution token and marks FAILED. For old queued
records it checks RQ: live queued/started/scheduled work is preserved, while missing/terminal
mismatches fail conservatively. Redis-read failures leave queued rows unchanged. Recovery is
operator-run and requires a new key for explicit resubmission. Both forms of termination are tested.

### 7. How are filesystem writes consistent with database transactions?

All reports are generated before publication, written in an attempt staging directory, and renamed
on the same filesystem. A single DB transaction inserts all metadata and marks SUCCEEDED. The API
only serves generated artifacts for SUCCEEDED rows. Filesystem and DB do not share a transaction;
rename-before-commit can leave an orphan, while a lost commit acknowledgement can hide successful
completion from the worker. Token checks prevent a failure handler from overwriting an already
committed result. The design leaves unreferenced directories for future retention-aware cleanup.

### 8. What did your load measurements show, and what would you optimize next?

Two workers completed all 80 measured synthetic workflows. Throughput was highest at five clients
in the recorded run and did not continue increasing at 10/20; queue wait increased, reaching a
7.12-second completion p95 at 20 clients. Submission timings are measured separately from full
completion, and HTTP request throughput includes polling. I would profile parsing, RQ process startup
and chart rendering before changing the worker count or architecture. No A/B optimization or
internet-scale capacity claim is supported. Resource snapshots are coarse; larger and longer trials
would be needed for sustained capacity estimates.

## E. Remaining limits

- No authentication, per-user isolation, rate limits, or multi-host deployment.
- No distributed DB/Redis/filesystem transaction or durable submission outbox.
- Recovery requires an operator command; abandoned output cleanup/retention is not automated.
- Small synthetic workload measurements do not establish clinical accuracy or production reliability.
- Dependency warnings remain; measured branch coverage is lower than combined coverage.
- The optional WeChat client is preserved, but this upgrade was not tested on a physical phone.
