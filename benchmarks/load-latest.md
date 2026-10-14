# Synthetic asynchronous benchmark

Closed-loop clients; completion includes upload, queue wait, processing and polling delay; HTTP rate includes polling/artifact requests over entire scenario

| Clients | Jobs | Failed | Jobs/s | Submit p50/p95 ms | Completion p50/p95 ms |
|---:|---:|---:|---:|---:|---:|
| 1 | 20 | 0 | 0.979 | 101.8/135.9 | 981.1/1046.9 |
| 5 | 20 | 0 | 3.185 | 117.0/303.8 | 1311.6/1780.4 |
| 10 | 20 | 0 | 2.894 | 318.3/529.7 | 2864.3/3660.9 |
| 20 | 20 | 0 | 2.760 | 1068.6/1182.8 | 4241.2/7123.1 |

Environment: {"client_os": "Windows-11-10.0.26200-SP0", "client_python": "3.13.12", "logical_cpus": 16, "worker_count": 2, "api_runtime": {"python": "3.12.15", "packages": {"fastapi": "0.143.0", "rq": "2.12.0", "redis": "5.3.1", "sqlalchemy": "2.1.4", "numpy": "2.5.4", "pandas": "2.3.3", "matplotlib": "3.11.2"}}}

Synthetic software-test data; no clinical or production scalability claim. Raw measurements and resource snapshots are in the JSON file.
