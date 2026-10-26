# Backend upgrade implementation report

## Before and after

Baseline `1f8499c` already supplied a modular domain package, eight HTTP operations, PostgreSQL,
Redis/RQ, React, Docker and tests. Its queue handler swallowed exceptions, unconditionally claimed
jobs, committed each output separately, and had no retry/key/recovery policy. Baseline behavior and
measurements were audited. See `ENGINEERING_AUDIT.md` for the initial findings.

The same architecture now includes:

- Optional database-constrained submission idempotency and content conflict detection.
- Conditional claims, attempt counts, execution tokens and bounded RQ transient retries.
- Scheduler-enabled workers and interruption handling/reconciliation.
- Atomic attempt-directory publication plus transactional artifact/result metadata.
- Body/file limits, finite data validation, safe names and request/job correlation.
- Isolated real PostgreSQL/Redis integration tests, failure injection and concurrent load tooling.

The API's four status names and eight operation paths remain compatible. The additive status field
is `attempts`. Intentional validation changes: malformed numbered rows and non-finite values now
fail rather than being skipped/processed; invalid uploads are rejected before DB job creation;
non-TXT filenames are rejected. No domain interpretation thresholds changed. Generated artifact
retrieval now requires SUCCEEDED even when historical partial metadata exists.

## Commands actually executed

| Command / workflow | Observed result |
|---|---|
| Baseline `.venv` pytest with coverage | 29 passed, one infrastructure-dependent test skipped |
| `ruff check src tests scripts` | Passed on host virtual environment and Linux test container |
| `mypy src/stepwise` | Passed on host virtual environment and Linux test container |
| `docker compose --profile test build tests` | Test image built |
| Complete test profile with pytest/branch coverage/JUnit | 72 passed, zero skipped; counters in `benchmarks/verification.json` |
| Fresh SQLite `alembic upgrade head` | Both migrations applied |
| PostgreSQL migration integration test | Empty initialization, existing-row preservation, downgrade and re-upgrade passed in an isolated schema |
| `docker compose up --build -d --scale worker=2` | Application images built and six runtime containers started (two instances of the worker service) |
| Frontend build-stage image; `pnpm lint`, `pnpm build` | ESLint, TypeScript and Vite passed |
| `docker compose config --quiet` | Valid Compose configuration |
| `docker compose exec api stepwise-reconcile` | Executed; no outstanding failed/missing jobs in the live application |
| `python scripts/load_benchmark.py --concurrency 1 5 10 20 --jobs 20 --workers 2 --sample-resources` | 80 timed jobs succeeded; all five artifacts retrievable |
| `python scripts/benchmark.py --runs 20 --output-dir benchmarks/core` | 20 measured direct pipeline runs completed |

The base Miniconda Python initially could not collect tests because it lacked the project and
dependencies; the existing virtual environment and Docker test runner were used successfully.
The new migration test found and fixed Alembic percent interpolation for encoded database URLs.

## Measurement interpretation

Statement coverage was 88.19%, branch coverage 74.61%, and combined coverage 85.79%. These are
different quantities. The tests exercise real forked RQ processes, but not every child-process line
is included in the parent coverage report. Dependency/test-harness deprecation warnings remain.

With two workers, the final five-client scenario measured 3.185 completed workflows/s and 1780.4 ms
p95 completion, including queue waiting and polling. The 20-client p95 was 7123.1 ms. Results are
small local workloads; there is no before/after optimization claim. See `BENCHMARK.md` and the raw
JSON for resource snapshots, definitions and environment versions.

## Limits and review

There is no distributed transaction, enqueue outbox, automated orphan garbage collection,
authentication, user isolation, or high-availability test. Recovery is operator-run. Generated
synthetic data demonstrates software behavior only. No real sensor data was requested or added.

Review `RELIABILITY.md` for consistency windows, `ARCHITECTURE.md` for control/data flow, and
`BENCHMARK.md` for measurement methodology and results.
The existing WeChat client was preserved but not tested on a physical phone during this upgrade.
