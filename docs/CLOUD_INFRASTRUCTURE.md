# Optional object storage and local Kubernetes

This extension demonstrates infrastructure engineering using synthetic software-test recordings.
The gait rules and non-diagnostic scope have not changed. No paid cloud or public service is needed.

## Storage and publication contract

`StorageBackend` exposes `put`, `read`, `publish`, and `check`. The existing `ArtifactStore(Path)`
remains the default local implementation, including CLI/test compatibility. API and worker use
`create_store(Settings)`. The API returns artifact bytes through the same URLs and media types;
object URLs, bucket credentials, and internal storage keys are not returned to clients.

The optional `S3ArtifactStore` uses Boto3 with path-style requests, bounded timeouts, and one SDK
attempt. RQ owns the bounded processing retry policy. Connect/read failures, throttling and selected
server errors are transient; missing content, authorization/configuration failures and checksum
mismatch are terminal. HTTP 409 conditional-write conflicts are transient; HTTP 412 triggers a
read and exact-content comparison, accepting only an identical replay.

Input keys include analysis UUID, `inputs`, SHA-256 of the bytes, and a validated internal filename.
Generated keys include analysis UUID, execution-token UUID, and filename. Writes use
`If-None-Match: *`; existing content is never overwritten by the application. Each object records a
SHA-256 metadata value. Upload verification reads the bytes back, checks length and digest, and
compares them with the intended bytes. A whole-set second verification precedes DB finalization.
Downloads also verify content. This costs extra GETs and memory, acceptable for these small reports;
it is not an optimized large-object streaming design. ETag is not treated as a universal MD5 hash.

`publish` prepares storage; it does **not** make a result public. After every required object is
verified, `AnalysisRepository.finish` locks the RUNNING record and checks its execution token, then
commits all generated metadata, result JSON, and SUCCEEDED together. The API requires this state
and generated metadata. A stale worker can leave objects but cannot commit them. Buckets are private
and the application never offers object-listing or presigned-public URLs.

There is no S3 directory rename, atomic multi-object write, or transaction across PostgreSQL, Redis,
and S3. Administrative deletion/corruption after verification can still make a successful report
unavailable; retrieval returns a safe error. Storage immutability is an application conditional-write
contract, not S3 Object Lock or protection against an administrator with overwrite permissions.

### Orphans and reconciliation

- An interrupted partial publication may leave a subset of objects under its attempt UUID.
- Upload completion followed by DB commit failure can leave an entire unreferenced attempt.
- Recovery still conservatively fails expired RUNNING or lost QUEUED records. It does not silently
  enqueue them again. Retried processing uses a new execution token and new output namespace.
- No automatic object garbage collector is implemented. For manual cleanup, stop submissions and
  workers, reconcile expired jobs, export all `artifacts.storage_key` references, and review objects
  not referenced by PostgreSQL. Preserve referenced inputs and outputs. Remove only reviewed
  unreferenced keys, then resume. Do not apply a blanket bucket TTL to live reports.
- No distributed transaction, enqueue outbox, exactly-once computation, or automatic failover is claimed.

Storage backend/bucket/prefix is configuration for the whole deployment. Use a separate database
for the optional demo. Switching an existing local deployment to S3 does **not** migrate its stored
files or metadata. The implementation rejects keys belonging to another backend/namespace.

## Optional Compose demo

Requires Docker Compose supporting `!reset` and `!override` (2.24.4+). From the repository root:

```bash
docker compose -p stepwise-s3 -f docker-compose.yml -f compose.minio.yml --profile s3 up --build -d --scale worker=2
```

Open `http://localhost:5173` and click **Load demo data**. API documentation is at
`http://localhost:8000/docs`; optional MinIO console is at `http://localhost:9001`.
The overlay supplies explicitly development-only credentials, creates a private bucket through a
one-off provisioning container, and removes artifact volume mounts from API and workers. MinIO,
PostgreSQL and Redis retain their own named volumes. The separate Compose project isolates data
from the original mode. Stop the original stack first if its ports are occupied; do not delete volumes.

```bash
docker compose -p stepwise-s3 -f docker-compose.yml -f compose.minio.yml --profile s3 --profile test build tests
docker compose -p stepwise-s3 -f docker-compose.yml -f compose.minio.yml --profile s3 --profile test run --rm tests
docker compose -p stepwise-s3 -f docker-compose.yml -f compose.minio.yml --profile s3 exec api stepwise-reconcile
docker compose -p stepwise-s3 -f docker-compose.yml -f compose.minio.yml --profile s3 down
```

Default `docker compose up --build` continues to use local files. MinIO is absent from that mode.
Tests provision uniquely named MinIO buckets and PostgreSQL schemas and clean up only those test
namespaces. `.github/workflows/object-storage.yml` runs the real-store suite in an isolated Compose
project; the original CI remains available for the default backend.

### MinIO image provenance

Old Docker Hub/Quay image pulls failed during this implementation and the pinned upstream binary
download returned HTTP 410. `deploy/minio/Dockerfile` instead builds upstream community source at
commit `07c3a429bfed433e49018cb0f78a52145d4bedeb` (release 2025-09-07), with Go 1.24.7.
The upstream AGPL license is included in the image. This is a pinned local demonstration dependency,
not a claim that this historical release is suitable for production or has current security support.
First build requires network access and several minutes; subsequent builds use Docker's cache.
Application dependency ranges are resolved at build time; exact measured versions are in benchmark
JSON. Builds are operationally reproducible, not claimed to be bit-for-bit deterministic.

## kind: two API pods and two workers

Install kind, kubectl, Docker, and the Python project with `pip install -e ".[dev,s3]"`.
Tested kind version: 0.33.0; node image: `kindest/node:v1.35.0`. Use a dedicated local cluster named
`stepwise`; all script operations explicitly select `kind-stepwise` and namespace `stepwise`.

```bash
python scripts/kind_demo.py up
python scripts/kind_demo.py verify
python scripts/kind_demo.py benchmark
python scripts/failure_demo.py --run-local-failures
```

On Windows a locally downloaded executable can be supplied as `--kind-binary .tools/kind.exe`.
`up` builds and loads images, applies development dependencies, waits for them, runs one migration
and bucket-provisioning Job, then starts API and workers. Migrations are not raced by API replicas.
The single-platform image archive avoids containerd import errors with Docker manifest lists.

All services are ClusterIP. No Ingress, NodePort, external LoadBalancer, or public deployment is
created. For manual API use:

```bash
kubectl --context kind-stepwise -n stepwise port-forward service/api 18000:8000 --address 127.0.0.1
```

Open `http://127.0.0.1:18000/docs`. Port-forwarding a service selects one pod; it does not measure
load balancing across replicas. Verification and benchmark scripts explicitly open one local
port-forward per API pod. The benchmark alternates HTTP requests across those two connections.

`verify` checks that application pods have no shared volumes, replays an idempotency key through a
different API pod, submits eight synthetic jobs, compares all five artifact hashes across replicas,
and checks both worker logs contain processed job IDs. It writes `benchmarks/cloud-kind/verification.json`.

API liveness checks only `/health`; readiness checks DB, Redis and storage. Worker readiness checks
its RQ registration/heartbeat. It is not a liveness probe: external dependency failures should not
cause restart storms. Worker process exit still restarts its container; that does not guarantee
automatic recovery of the interrupted analysis. Use the explicit reconciler when needed.

API and workers have bounded CPU/memory requests and limits. PostgreSQL, Redis and MinIO each have
one development-only instance and PVC. This is a **single-node local cluster**, not multi-node high
availability. PVCs survive pod replacement, not deletion of the kind cluster. Secret examples are
public demo credentials, not encrypted secret management. Authentication remains outside scope.

## Measurements and failure experiment

`scripts/load_benchmark.py` records all-HTTP, submission, and completion p50/p95, completed workflows
per second, errors, observed attempts, and a queue-wait proxy. The latter is DB `started_at - created_at`
for single-attempt jobs only, so it includes input storage/enqueue overhead. It must not be presented
as pure Redis waiting. Completion includes client polling delay; full workload throughput also
includes fetching every artifact. One warmup is excluded. Workload is closed-loop and synthetic.

```bash
python scripts/load_benchmark.py --concurrency 1 5 --jobs 10 --workers 2 --compose-project stepwise-s3 --sample-resources --output-dir benchmarks/cloud-compose
```

kind uses kubelet stats-summary CPU/memory samples, without installing a monitoring stack. These
are coarse snapshots, not true peak measurements. Samples and exact package versions are saved in
JSON. `failure_demo.py` temporarily scales only this local demo's workers/MinIO, checks storage outage
503s and hidden partial results, observes bounded attempts, restores services in `finally`, then
replaces API pods and checks report SHA-256 persistence. It records actual terminal state rather
than promising every outage recovers automatically. Process interruption and stale execution are
also covered by the real RQ/PostgreSQL/MinIO integration suite.

See [CLOUD_EVIDENCE.md](CLOUD_EVIDENCE.md) for executed results and limitations.

## Primary implementation references

- [Amazon S3 conditional writes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html): `If-None-Match`, 412/409 behavior. AWS itself was not used or tested.
- [kind quick start](https://kind.sigs.k8s.io/docs/user/quick-start/): local clusters and loading images.
- [Pinned MinIO source](https://github.com/minio/minio/tree/07c3a429bfed433e49018cb0f78a52145d4bedeb).
