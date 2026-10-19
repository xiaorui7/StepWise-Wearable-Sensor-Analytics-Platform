# Object storage and Kubernetes evidence

Work performed October 16-19, 2026. This report concerns software infrastructure only; StepWise's
domain rules, thresholds, non-diagnostic terminology and synthetic-data boundaries are unchanged.

## Changes and boundaries

Before: API/worker instantiated one filesystem store, and artifact downloads resolved a local path.
After: both use a storage protocol with the existing local implementation and an optional Boto3
S3-compatible implementation. Generated objects are attempt-specific, conditionally written and
checksum-verified before PostgreSQL locks and validates the ownership token and commits completion.
No schema change was required: existing private storage-key fields hold S3 references in the new,
isolated demo database. Local data is not automatically migrated.

The optional Compose overlay adds private MinIO and bucket initialization and removes application
artifact mounts. The optional kind manifests run two API and two worker replicas without shared
application filesystems, plus development PostgreSQL/Redis/MinIO with PVCs. API readiness includes
storage; liveness remains independent of dependencies. Worker readiness uses a Pod-qualified unique
RQ name and the RQ heartbeat TTL. A regression test covers records whose hostname metadata expired
during host suspension. A one-off Job handles migrations before application rollout.

The local default Compose definition, eight API operations, job states and analysis calculations
remain unchanged. The artifact response now returns verified bytes rather than a local FileResponse.
This buffers each report in memory; large-object streaming is not implemented.

## Executed verification

| Check | Observed result |
|---|---|
| Pre-change host baseline | 61 tests passed, 11 infrastructure/PostgreSQL tests deselected |
| Phase 1 host storage/core regression | 73 passed, 11 deselected |
| Phase 2 full Linux suite with MinIO/PostgreSQL/Redis | 98 passed, zero skipped |
| Expanded publication interruption and worker-readiness suite | 106 passed, zero skipped; see `benchmarks/cloud-verification.json` |
| Ruff and strict mypy | Passed locally |
| Backend/test/MinIO Docker builds | Executed and passed |
| Optional Compose synthetic workload | 20/20 timed jobs completed; raw results in `benchmarks/cloud-compose/` |
| kind one-command provisioning | `scripts/kind_demo.py up --kind-binary .tools/kind.exe` executed successfully |
| Cross-replica verification | Eight jobs; same idempotency key across API pods returned one ID; all five artifacts matched by SHA-256 across pods; both workers processed jobs |
| kind load workload | 30/30 timed jobs completed across 1, 5 and 10 clients; zero observed HTTP errors or extra attempts |
| Real local failure experiment | MinIO outage yielded safe 503s; queued job exhausted three attempts; no partial report was visible; explicit new-key submission succeeded after recovery |
| API replacement persistence | Original report remained available with identical SHA-256 after replacing API pods |

The test suite includes actual PostgreSQL arbitration, actual Redis/RQ child processes and actual
MinIO objects. It covers concurrent uploads, claims, two workers, immediate/delayed retries,
permanent failure, missing queue jobs, child/full-worker termination, partial publication, interrupted
publication, stale token fencing, DB finalization failure, corrupted object download, and migration
from an empty PostgreSQL schema. Test buckets and DB schemas are unique and isolated.

Commands actually executed from the repository root (Windows uses `.venv/Scripts/python.exe`):

```bash
python -m ruff check src tests scripts
python -m mypy src/stepwise
docker compose -p stepwise-s3 -f docker-compose.yml -f compose.minio.yml --profile s3 --profile test build
docker compose -p stepwise-s3 -f docker-compose.yml -f compose.minio.yml --profile s3 up -d --scale worker=2
docker compose -p stepwise-s3 -f docker-compose.yml -f compose.minio.yml --profile s3 --profile test run --rm tests pytest -q --cov=stepwise --cov-branch --cov-report=json:benchmarks/cloud-coverage.json --junitxml=benchmarks/cloud-tests.xml
python scripts/kind_demo.py up --kind-binary .tools/kind.exe
python scripts/kind_demo.py verify
python scripts/kind_demo.py benchmark
python scripts/failure_demo.py --run-local-failures
```

Raw coverage/JUnit outputs are generated locally and ignored; the sanitized counters are recorded
in `benchmarks/cloud-verification.json`. Parameterized test counts are not distinct clinical scenarios.
Final suite coverage: 1172/1342 statements (87.33%), 221/300 branches (73.67%), combined 84.84%.
JUnit duration was 72.654 seconds. These categories must not be interchanged.
The test harness emitted Starlette TestClient and RQ fork-from-multithreaded-process deprecation
warnings. Child-process execution is not fully represented in parent-process coverage.

## Measured workload and environment

October 16 kind run: two API pods, two one-task-at-a-time RQ workers, single kind node, one instance
each of PostgreSQL, Redis, MinIO. Client alternated HTTP requests across two loopback Pod
port-forwards; this did not test an Ingress or external load balancer. Ten synthetic jobs per
concurrency, one excluded warmup, 200 ms polling. Each completed workflow retrieved all five outputs.

| Clients | Jobs/s | POST p50/p95 ms | All HTTP p50/p95 ms | Completion p50/p95 ms | Queue-wait proxy p50/p95 ms |
|---:|---:|---:|---:|---:|---:|
| 1 | 0.719 | 95.6 / 105.0 | 15.2 / 93.5 | 1219.6 / 1696.6 | 498.8 / 919.2 |
| 5 | 2.130 | 194.9 / 314.6 | 16.0 / 124.1 | 1797.1 / 2598.0 | 1073.7 / 1805.9 |
| 10 | 1.963 | 290.9 / 310.4 | 20.3 / 81.7 | 3193.5 / 4885.4 | 2372.9 / 4121.0 |

Completion includes submission, queueing, processing, persistence and polling delay. Throughput
also includes report retrieval. Queue-wait proxy is DB creation to first worker start for
single-attempt jobs; it includes input persistence/enqueue, not just Redis waiting. All-request
percentiles mix uploads, polls and downloads and must not be presented as POST latency.

Hardware: Intel Core Ultra 9 285H, 16 logical CPUs, 33,752,977,408 bytes physical RAM.
Docker memory limit: 16,470,933,504 bytes. Host: Windows 11 build 26200; client Python 3.13.12.
Docker Engine 29.8.1, Compose 5.5.1, kind 0.33.0, Kubernetes node 1.35.0, kubectl 1.36.1.
Containers: Python 3.12.15; PostgreSQL 16; Redis 7; pinned MinIO source commit
`07c3a429bfed433e49018cb0f78a52145d4bedeb`. Exact application package versions and fixture hashes
are included in load JSON. API CPU limit is one core and memory limit 1 GiB per pod; worker limits
are the same. Requests and dependency limits are in `deploy/kind/`.

Twelve coarse kubelet resource snapshots were captured. Maximum sampled per-Pod CPU was
413,924,457 nanocores (0.414 cores); maximum sampled working set was 229,433,344 bytes. These are
sampled values, not true workload peaks; kubelet timestamps can lag the client sampling time.
The Compose stack remained running during kind measurement. The earlier Compose workload overlapped
kind setup/image loading, so those timings are exploratory and are not an A/B comparison.

The outage experiment lasted 22.08 seconds from storage removal through restoration readiness.
The affected worker job ended FAILED after three attempts, as bounded retries allow. A failed
submission-key replay remained FAILED; only a new-key submission created new work. This is evidence
of conservative failure handling, not automatic recovery or an availability percentage.

## Limitations and unverified claims

- No AWS account or paid cloud service was used. S3 protocol behavior was tested against MinIO,
  with selected error responses tested through Botocore stubs; AWS S3 itself was not exercised.
- kind is one local node. Two replicas demonstrate cross-process object access, not multi-node
  failover, regional redundancy, internet-scale capacity or production availability.
- Object/DB/queue transitions do not share a transaction. No outbox, automatic orphan collector,
  distributed exactly-once computation, or guaranteed automatic resubmission is implemented.
- Root object credentials and Kubernetes Secret examples are explicitly local demo values.
  Production auth, per-user isolation, least-privilege IAM and secret management remain outside scope.
- MinIO is built from fixed historical upstream source because old binary/image downloads failed.
  This is not a current security-support claim; dependency ranges also prevent bit-reproducible builds.
- The new GitHub Actions workflow is configured; local equivalent commands passed. Hosted CI status
  must be checked separately after pushing. No frontend behavior was changed or new medical claims added.

Use these observations as infrastructure interview evidence. Do not replace earlier benchmark
numbers with these without retaining workload definitions, and do not claim an improvement percentage.
