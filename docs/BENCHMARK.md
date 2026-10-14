# Benchmark methodology and measured results

Measured locally on 2026-10-13 (America/New_York). The JSON timestamps use UTC. All inputs are
checked-in deterministic synthetic software-test fixtures, not clinical recordings.

## Environment and reproduction

Windows 11 / WSL 2, 16 logical host CPUs, client Python 3.13.12, two Linux RQ workers, PostgreSQL 16
and Redis 7. API/worker Python is 3.12.15. Runtime package versions are recorded by the load script
in `benchmarks/load-latest.json`. Docker reports about 15.34 GiB available to the containers. This is
one development machine; the measurements are not a hardware-independent capacity guarantee.

```bash
docker compose up --build -d --scale worker=2
pip install -e ".[dev]"
python scripts/load_benchmark.py --concurrency 1 5 10 20 --jobs 20 --workers 2 --sample-resources
python scripts/benchmark.py --runs 20 --output-dir benchmarks/core
```

`--workers` records the configured number; Compose actually starts the workers. Run from the
repository root for optional Docker resource sampling. The command creates one warmup job and 80
timed jobs in application history. It does not delete previous user sessions. Core-only benchmarking
does not need the database or queue.

## Workload and measurement window

Each concurrency scenario runs 20 workflows. A client slot is held from the start of submission until
result and artifact verification finishes; waiting for a free client slot is outside per-job latency.
Workload elapsed time covers the first submission through the last completed artifact verification.
The script uses `perf_counter`, reuses HTTP connections and polls every 200 ms. A complete warmup
workflow is excluded from the timed scenarios.

- Submission latency: multipart POST start to successful HTTP response, including validation and
  storage/queue dispatch. It excludes background analysis.
- Completion latency: POST start to observation of SUCCEEDED, including upload, queue waiting,
  computation, chart/report generation, persistence, and polling delay.
- Completed jobs/s: successful workflows divided by the full scenario window, including result and
  five-artifact retrieval.
- HTTP requests/s: all submission, status-poll and artifact/result requests divided by that same
  window. This is not the platform's independent POST capacity.
- Failure rate: failed workflows divided by the 20 attempted workflows. A timeout, HTTP failure,
  terminal job state, missing/empty artifact or invalid JSON causes workflow failure.
- p50/p95: NumPy percentiles of the measured observations; failed completions are excluded from the
  completion distribution and separately counted. A twenty-sample p95 is a small-sample estimate.

Walking data is `normal_like.synthetic.txt`, with `standing_neutral.synthetic.txt` for calibration.
Input byte sizes and the walking SHA-256 are stored with the results. Each benchmark job has a unique
idempotency key; deliberate repeated keys are covered by integration tests instead.

## Latest asynchronous run

| Concurrent clients | Completed / submitted | Jobs/s | POST p50 / p95 (ms) | Completion p50 / p95 (ms) | HTTP requests/s |
|---:|---:|---:|---:|---:|---:|
| 1 | 20 / 20 | 0.979 | 101.8 / 135.9 | 981.1 / 1046.9 | 11.500 |
| 5 | 20 / 20 | 3.185 | 117.0 / 303.8 | 1311.6 / 1780.4 | 42.357 |
| 10 | 20 / 20 | 2.894 | 318.3 / 529.7 | 2864.3 / 3660.9 | 53.538 |
| 20 | 20 / 20 | 2.760 | 1068.6 / 1182.8 | 4241.2 / 7123.1 | 60.317 |

All 80 timed jobs completed with zero observed workflow failures or duplicate logical IDs. The
script checked that each result exposed the five expected artifacts, every artifact was non-empty
and retrievable, and JSON contained no NaN/Infinity constants. This is observed behavior for this
workload, not a general zero-failure guarantee.

Raw per-job durations, request counts, failure categories, and scenario windows are in
[`../benchmarks/load-latest.json`](../benchmarks/load-latest.json). The generated short table is in
[`../benchmarks/load-latest.md`](../benchmarks/load-latest.md).

## Resources and bottlenecks

The final run collected 12 Docker CPU/memory snapshots. The largest sampled per-container CPU value
was 107.63%; Docker expresses usage relative to one CPU, so this is not 107% of the entire host.
Raw memory strings and per-service CPU samples are retained in JSON. Sampling is coarse, overlaps
the workload, and does not establish a true peak or steady-state memory requirement.

Increasing client concurrency above five did not increase completed throughput in this run. The
two worker processes remained the bounded processing pool, while queue waiting and API submission
latency grew. Each job also starts an RQ child process and renders charts. These are plausible
contributors; no profiler measurement isolates their individual costs. There is no claim of a
measured optimization or scalability improvement over the previous version.

## Direct pipeline run

The freshly measured Windows direct pipeline result is 532.21 ms median and 714.07 ms p95 over 20
runs after one warmup. It includes parsing, calculations, charts, CSV/JSON and HTML generation. See
`benchmarks/core/latest.json` and `.md`. It excludes HTTP, database work and queue waiting, and uses
a different Python environment than the container workload.

The older `benchmark-results/latest.*` files remain historical September measurements (323.54 ms
median). Different dates/environments/workload contexts are not an A/B experiment, so these numbers
must not be used to claim a percentage performance improvement or regression.

## Limits

Only 80 small synthetic jobs, one machine and two workers were measured. There is no sustained
soak test, public internet test, per-user isolation, cross-host storage, availability measurement or
clinical accuracy test. The benchmark's warmup and polling interval affect observed timings.
