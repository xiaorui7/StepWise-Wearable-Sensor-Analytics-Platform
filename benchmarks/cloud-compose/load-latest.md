# Synthetic asynchronous benchmark

Closed-loop clients; completion includes upload, queue wait, processing and polling delay; HTTP rate includes polling/artifact requests over entire scenario

| Clients | Jobs | Failed | Jobs/s | Submit p50/p95 ms | Completion p50/p95 ms |
|---:|---:|---:|---:|---:|---:|
| 1 | 10 | 0 | 0.723 | 101.4/260.2 | 1206.6/1802.8 |
| 5 | 10 | 0 | 0.832 | 879.0/1197.4 | 4471.1/6435.5 |

Environment: {"client_os": "Windows-11-10.0.26200-SP0", "client_python": "3.13.12", "logical_cpus": 16, "worker_count": 2, "api_replicas": 1, "deployment": "compose", "api_runtime": {"python": "3.12.15", "packages": {"fastapi": "0.143.3", "rq": "2.12.0", "redis": "5.3.1", "sqlalchemy": "2.1.4", "numpy": "2.5.4", "pandas": "2.3.3", "matplotlib": "3.11.2", "boto3": "1.43.114", "botocore": "1.43.114"}}}

Synthetic software-test data; no clinical or production scalability claim. Raw measurements and resource snapshots are in the JSON file.

| Clients | All HTTP p50/p95 ms | Queue wait proxy p50/p95 ms | HTTP errors | Extra attempts |
|---:|---:|---:|---:|---:|
| 1 | 14.8/94.7 | 490.7/800.5 | 0 | 0 |
| 5 | 78.4/341.6 | 2435.8/4476.9 | 0 | 0 |

For single-attempt jobs only: DB started_at minus created_at; includes input persistence and enqueue overhead, not pure Redis waiting. Retried jobs expose only their last start and are excluded from this distribution.
