"""Small closed-loop HTTP/worker benchmark using only synthetic fixtures."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import platform
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import numpy as np


def distribution(values: list[float]) -> dict[str, float | None]:
    return {
        "p50_ms": float(np.percentile(values, 50)) if values else None,
        "p95_ms": float(np.percentile(values, 95)) if values else None,
    }


async def workload(
    base_url: str,
    concurrency: int,
    jobs: int,
    timeout_s: float,
    walking: bytes,
    standing: bytes,
    poll_s: float,
    alternate_base_url: str | None = None,
) -> dict[str, object]:
    semaphore = asyncio.Semaphore(concurrency)
    measurements: list[dict[str, object]] = []
    request_count = 0
    request_latencies: list[float] = []
    http_errors = 0
    run_key = uuid4().hex
    async with httpx.AsyncClient(
        base_url=base_url, timeout=30, limits=httpx.Limits(max_connections=concurrency * 2)
    ) as client:

        async def request(method: str, path: str, **kwargs):
            nonlocal request_count, http_errors
            request_count += 1
            request_started = time.perf_counter()
            try:
                target = alternate_base_url + path if alternate_base_url and request_count % 2 == 0 else path
                response = await client.request(method, target, **kwargs)
            except httpx.HTTPError:
                http_errors += 1
                raise
            finally:
                request_latencies.append((time.perf_counter() - request_started) * 1000)
            if response.is_error:
                http_errors += 1
            response.raise_for_status()
            return response

        async def one(index: int) -> None:
            async with semaphore:
                started = time.perf_counter()
                item: dict[str, object] = {"index": index, "success": False}
                try:
                    created = await request(
                        "POST",
                        "/api/v1/analyses",
                        headers={"Idempotency-Key": f"benchmark-{run_key}-{index}"},
                        files={
                            "walking_file": ("normal_like.synthetic.txt", walking, "text/plain"),
                            "standing_file": ("standing.synthetic.txt", standing, "text/plain"),
                        },
                    )
                    item["submission_ms"] = (time.perf_counter() - started) * 1000
                    identity = created.json()["analysis_id"]
                    item["analysis_id"] = identity
                    while True:
                        status = (await request("GET", f"/api/v1/analyses/{identity}")).json()
                        item["attempts"] = status.get("attempts", 0)
                        if status.get("started_at") and status.get("created_at"):
                            item["admission_to_last_start_ms"] = (
                                datetime.fromisoformat(status["started_at"])
                                - datetime.fromisoformat(status["created_at"])
                            ).total_seconds() * 1000
                        if status["status"] == "FAILED":
                            raise RuntimeError(status["error_code"])
                        if status["status"] == "SUCCEEDED":
                            break
                        if time.perf_counter() - started > timeout_s:
                            raise TimeoutError("job completion deadline exceeded")
                        await asyncio.sleep(poll_s)
                    item["completion_ms"] = (time.perf_counter() - started) * 1000
                    result = (await request("GET", f"/api/v1/analyses/{identity}/result")).json()
                    paths = result["artifact_urls"]
                    expected = {"report.html", "pressure.png", "orientation.png", "steps.csv", "result.json"}
                    if set(paths) != expected:
                        raise RuntimeError("missing or unexpected artifacts")
                    for name, path in paths.items():
                        artifact = await request("GET", path)
                        if not artifact.content:
                            raise RuntimeError("empty artifact")
                        if name == "result.json":

                            def invalid_constant(value):
                                raise ValueError("non-standard JSON constant")

                            json.loads(artifact.content, parse_constant=invalid_constant)
                    item["success"] = True
                except Exception as exc:
                    item["failure_category"] = type(exc).__name__
                item["workflow_ms"] = (time.perf_counter() - started) * 1000
                measurements.append(item)

        started = time.perf_counter()
        await asyncio.gather(*(one(index) for index in range(jobs)))
        window_s = time.perf_counter() - started
    successful = [item for item in measurements if item["success"]]
    identities = [item["analysis_id"] for item in measurements if "analysis_id" in item]
    return {
        "concurrency": concurrency,
        "jobs": jobs,
        "completed": len(successful),
        "failure_rate": (jobs - len(successful)) / jobs,
        "duplicate_logical_jobs": len(identities) - len(set(identities)),
        "measurement_window_s": window_s,
        "http_requests": request_count,
        "http_errors": http_errors,
        "http_latency": distribution(request_latencies),
        "extra_attempts": sum(max(0, int(item.get("attempts", 0)) - 1) for item in measurements),
        "queue_wait_proxy": distribution([
            float(item["admission_to_last_start_ms"]) for item in measurements
            if item.get("attempts") == 1 and "admission_to_last_start_ms" in item
        ]),
        "http_requests_per_s": request_count / window_s,
        "completed_jobs_per_s": len(successful) / window_s,
        "submission": distribution(
            [float(item["submission_ms"]) for item in measurements if "submission_ms" in item]
        ),
        "completion_including_queue": distribution([float(item["completion_ms"]) for item in successful]),
        "measurements": sorted(measurements, key=lambda item: int(item["index"])),
    }


async def resources(stop: asyncio.Event, snapshots: list[dict[str, object]], args) -> None:
    while not stop.is_set():
        try:
            if args.deployment == "kind":
                process = await asyncio.create_subprocess_exec(
                    "kubectl", "--context", args.kube_context, "get", "--raw",
                    f"/api/v1/nodes/{args.kind_node}/proxy/stats/summary",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                output, _ = await process.communicate()
                stats = json.loads(output)
                snapshots.append({
                    "at": datetime.now(UTC).isoformat(),
                    "pods": [
                        {"name": p["podRef"]["name"], "cpu": p.get("cpu"), "memory": p.get("memory")}
                        for p in stats.get("pods", []) if p["podRef"]["namespace"] == "stepwise"
                    ],
                })
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=2)
                continue
            process = await asyncio.create_subprocess_exec(
                "docker", "compose", "-p", args.compose_project,
                "ps",
                "-q",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            output, _ = await process.communicate()
            identities = output.decode().split()
            if identities:
                process = await asyncio.create_subprocess_exec(
                    "docker",
                    "stats",
                    "--no-stream",
                    "--format",
                    "{{json .}}",
                    *identities,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                output, _ = await process.communicate()
                rows = [json.loads(line) for line in output.decode().splitlines() if line.strip()]
                snapshots.append(
                    {
                        "at": datetime.now(UTC).isoformat(),
                        "containers": [
                            {
                                "service": row["Name"].split("-")[-2],
                                "cpu_percent": row["CPUPerc"],
                                "memory": row["MemUsage"],
                            }
                            for row in rows
                        ],
                    }
                )
        except (OSError, ValueError):
            snapshots.append({"error": "resource sampling unavailable"})
            return
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=1)


async def run(args) -> dict[str, object]:
    walking = (args.fixtures / "normal_like.synthetic.txt").read_bytes()
    standing = (args.fixtures / "standing_neutral.synthetic.txt").read_bytes()
    # One complete warmup workflow is excluded from timed scenarios.
    warmup = await workload(args.base_url, 1, 1, args.timeout_s, walking, standing, args.poll_s, args.alternate_base_url)
    if warmup["completed"] != 1:
        raise RuntimeError("Warmup analysis failed; verify readiness and workers")
    snapshots: list[dict[str, object]] = []
    runtime = None
    if args.sample_resources:
        command = (
            ["kubectl", "--context", args.kube_context, "-n", "stepwise", "exec", "deploy/api", "--"]
            if args.deployment == "kind" else
            ["docker", "compose", "-p", args.compose_project, "exec", "-T", "api"]
        )
        probe = await asyncio.create_subprocess_exec(
            *command,
            "python",
            "-c",
            "import json,platform,importlib.metadata as m; print(json.dumps({'python':platform.python_version(),'packages':{n:m.version(n) for n in ['fastapi','rq','redis','sqlalchemy','numpy','pandas','matplotlib','boto3','botocore']}}))",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        probe_output, _ = await probe.communicate()
        if probe.returncode == 0:
            runtime = json.loads(probe_output)
    stop = asyncio.Event()
    sampler = asyncio.create_task(resources(stop, snapshots, args)) if args.sample_resources else None
    scenarios = []
    try:
        for concurrency in args.concurrency:
            scenario = await workload(
                args.base_url, concurrency, args.jobs, args.timeout_s, walking, standing, args.poll_s, args.alternate_base_url
            )
            scenarios.append(scenario)
            print(
                f"clients={concurrency} completed={scenario['completed']}/{args.jobs} jobs/s={scenario['completed_jobs_per_s']:.3f}",
                flush=True,
            )
    finally:
        stop.set()
        if sampler:
            await sampler
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "synthetic_data": True,
        "environment": {
            "client_os": platform.platform(),
            "client_python": platform.python_version(),
            "logical_cpus": __import__("os").cpu_count(),
            "worker_count": args.workers,
            "api_replicas": args.api_replicas,
            "deployment": args.deployment,
            "routing": "Alternating requests across two pod port-forwards" if args.alternate_base_url else "Single base URL",
            "api_runtime": runtime,
        },
        "fixture_sha256": hashlib.sha256(walking).hexdigest(),
        "walking_bytes": len(walking),
        "standing_bytes": len(standing),
        "warmup_jobs": 1,
        "poll_interval_s": args.poll_s,
        "method": "Closed-loop clients; completion includes upload, queue wait, processing and polling delay; HTTP rate includes polling/artifact requests over entire scenario",
        "queue_wait_definition": "For single-attempt jobs only: DB started_at minus created_at; includes input persistence and enqueue overhead, not pure Redis waiting. Retried jobs expose only their last start and are excluded from this distribution.",
        "scenarios": scenarios,
        "resource_snapshots": snapshots,
    }


def markdown(result: dict[str, object]) -> str:
    def number(value):
        return f"{value:.1f}" if value is not None else "unavailable"

    rows = [
        "# Synthetic asynchronous benchmark",
        "",
        str(result["method"]),
        "",
        "| Clients | Jobs | Failed | Jobs/s | Submit p50/p95 ms | Completion p50/p95 ms |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for item in result["scenarios"]:
        submit, complete = item["submission"], item["completion_including_queue"]
        rows.append(
            f"| {item['concurrency']} | {item['jobs']} | {item['jobs'] - item['completed']} | {item['completed_jobs_per_s']:.3f} | {number(submit['p50_ms'])}/{number(submit['p95_ms'])} | {number(complete['p50_ms'])}/{number(complete['p95_ms'])} |"
        )
    rows.extend(
        [
            "",
            "Environment: " + json.dumps(result["environment"]),
            "",
            "Synthetic software-test data; no clinical or production scalability claim. Raw measurements and resource snapshots are in the JSON file.",
        ]
    )
    rows.extend(["", "| Clients | All HTTP p50/p95 ms | Queue wait proxy p50/p95 ms | HTTP errors | Extra attempts |",
                 "|---:|---:|---:|---:|---:|"])
    for item in result["scenarios"]:
        http, queue = item["http_latency"], item["queue_wait_proxy"]
        rows.append(f"| {item['concurrency']} | {number(http['p50_ms'])}/{number(http['p95_ms'])} | {number(queue['p50_ms'])}/{number(queue['p95_ms'])} | {item['http_errors']} | {item['extra_attempts']} |")
    rows.extend(["", str(result["queue_wait_definition"])])
    return "\n".join(rows) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--alternate-base-url", help="Alternate requests across a second API replica")
    parser.add_argument("--concurrency", type=int, nargs="+", default=[1, 5, 10, 20])
    parser.add_argument("--jobs", type=int, default=20)
    parser.add_argument("--workers", type=int, default=1, help="Record actual configured worker count")
    parser.add_argument("--poll-s", type=float, default=0.2)
    parser.add_argument("--timeout-s", type=float, default=120)
    parser.add_argument("--fixtures", type=Path, default=Path("fixtures/synthetic"))
    parser.add_argument("--output-dir", type=Path, default=Path("benchmarks"))
    parser.add_argument("--sample-resources", action="store_true")
    parser.add_argument("--deployment", choices=["compose", "kind"], default="compose")
    parser.add_argument("--compose-project", default="stepwise-bench")
    parser.add_argument("--api-replicas", type=int, default=1, help="Record actual replica count")
    parser.add_argument("--kube-context", default="kind-stepwise")
    parser.add_argument("--kind-node", default="stepwise-control-plane")
    args = parser.parse_args()
    if (
        args.jobs < 1
        or any(c < 1 or c > 100 for c in args.concurrency)
        or args.poll_s <= 0
        or args.timeout_s <= 0
        or args.workers < 1
    ):
        parser.error("Use positive job/worker counts and timeouts; concurrency must be 1-100")
    result = asyncio.run(run(args))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "load-latest.json").write_text(
        json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    (args.output_dir / "load-latest.md").write_text(markdown(result), encoding="utf-8")
    if any(item["failure_rate"] for item in result["scenarios"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
