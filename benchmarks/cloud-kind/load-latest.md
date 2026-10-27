# Synthetic asynchronous benchmark

Closed-loop clients; completion includes upload, queue wait, processing and polling delay; HTTP rate includes polling/artifact requests over entire scenario

| Clients | Jobs | Failed | Jobs/s | Submit p50/p95 ms | Completion p50/p95 ms |
|---:|---:|---:|---:|---:|---:|
| 1 | 10 | 0 | 0.719 | 95.6/105.0 | 1219.6/1696.6 |
| 5 | 10 | 0 | 2.130 | 194.9/314.6 | 1797.1/2598.0 |
| 10 | 10 | 0 | 1.963 | 290.9/310.4 | 3193.5/4885.4 |

Environment: {"client_os": "Windows-11-10.0.26200-SP0", "client_python": "3.13.12", "logical_cpus": 16, "worker_count": 2, "api_replicas": 2, "deployment": "kind", "routing": "Alternating requests across two pod port-forwards", "api_runtime": {"python": "3.12.15", "packages": {"fastapi": "0.143.3", "rq": "2.12.0", "redis": "5.3.1", "sqlalchemy": "2.1.4", "numpy": "2.5.4", "pandas": "2.3.3", "matplotlib": "3.11.2", "boto3": "1.43.114", "botocore": "1.43.114"}}}

Synthetic software-test data; no clinical or production scalability claim. Raw measurements and resource snapshots are in the JSON file.

| Clients | All HTTP p50/p95 ms | Queue wait proxy p50/p95 ms | HTTP errors | Extra attempts |
|---:|---:|---:|---:|---:|
| 1 | 15.2/93.5 | 498.8/919.2 | 0 | 0 |
| 5 | 16.0/124.1 | 1073.7/1805.9 | 0 | 0 |
| 10 | 20.3/81.7 | 2372.9/4121.0 | 0 | 0 |

For single-attempt jobs only: DB started_at minus created_at; includes input persistence and enqueue overhead, not pure Redis waiting. Retried jobs expose only their last start and are excluded from this distribution.
