"""Local-only kind provisioning and cross-replica synthetic verification."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
CONTEXT = "kind-stepwise"


def command(*args: str) -> str:
    result = subprocess.run(args, cwd=ROOT, text=True, capture_output=True, check=True)
    return result.stdout


def kube(*args: str) -> str:
    return command("kubectl", "--context", CONTEXT, "-n", "stepwise", *args)


def pods(app: str) -> list[str]:
    items = json.loads(kube("get", "pods", "-l", f"app={app}", "-o", "json"))["items"]
    return [item["metadata"]["name"] for item in items if not item["metadata"].get("deletionTimestamp")]


@contextmanager
def forward(pod: str, port: int):
    process = subprocess.Popen(
        ["kubectl", "--context", CONTEXT, "-n", "stepwise", "port-forward", f"pod/{pod}",
         f"{port}:8000", "--address", "127.0.0.1"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 20
        while True:
            try:
                httpx.get(f"http://127.0.0.1:{port}/health", timeout=1).raise_for_status()
                break
            except httpx.HTTPError:
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("Local port-forward failed") from None
                time.sleep(0.2)
        yield f"http://127.0.0.1:{port}"
    finally:
        process.terminate()
        process.wait(timeout=10)


def upload(client: httpx.Client, key: str | None = None) -> httpx.Response:
    return client.post("/api/v1/analyses", headers={"Idempotency-Key": key or uuid4().hex}, files={
        "walking_file": ("normal_like.synthetic.txt", (ROOT / "fixtures/synthetic/normal_like.synthetic.txt").read_bytes(), "text/plain"),
        "standing_file": ("standing.synthetic.txt", (ROOT / "fixtures/synthetic/standing_neutral.synthetic.txt").read_bytes(), "text/plain"),
    })


def wait_job(client: httpx.Client, identity: str, timeout: int = 120) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/analyses/{identity}")
        response.raise_for_status()
        record = response.json()
        if record["status"] in {"SUCCEEDED", "FAILED"}:
            return record
        time.sleep(0.2)
    raise TimeoutError("Job did not reach a terminal state")


def verify() -> dict:
    api_pods, worker_pods = pods("api"), pods("worker")
    assert len(api_pods) == len(worker_pods) == 2
    for deployment in ("api", "worker"):
        spec = json.loads(kube("get", "deploy", deployment, "-o", "json"))["spec"]["template"]["spec"]
        assert not spec.get("volumes"), "Application must not share artifact filesystems"
    with ExitStack() as stack:
        urls = [stack.enter_context(forward(p, 18081 + i)) for i, p in enumerate(api_pods)]
        clients = [stack.enter_context(httpx.Client(base_url=url, timeout=30)) for url in urls]
        key = "kind-verify-" + uuid4().hex
        first, replay = upload(clients[0], key), upload(clients[1], key)
        assert first.status_code == replay.status_code == 202
        identity = first.json()["analysis_id"]
        assert replay.json()["analysis_id"] == identity
        identities = [identity]
        for index in range(7):
            response = upload(clients[index % 2])
            response.raise_for_status()
            identities.append(response.json()["analysis_id"])
        hashes = {}
        for identity in identities:
            record = wait_job(clients[1], identity)
            assert record["status"] == "SUCCEEDED" and record["attempts"] == 1
            result = clients[1].get(f"/api/v1/analyses/{identity}/result").json()
            assert len(result["artifact_urls"]) == 5
            for path in result["artifact_urls"].values():
                a, b = clients[0].get(path), clients[1].get(path)
                assert a.status_code == b.status_code == 200 and a.content == b.content and a.content
                hashes[path] = hashlib.sha256(a.content).hexdigest()
        worker_evidence = {}
        for pod in worker_pods:
            logs = kube("logs", pod)
            seen = [identity for identity in identities if identity in logs]
            worker_evidence[pod] = seen
        assert all(worker_evidence.values()), "Both worker pods must process benchmark jobs"
    return {"generated_at": datetime.now(UTC).isoformat(), "synthetic_data": True,
            "api_pods": api_pods, "worker_jobs": worker_evidence, "jobs": len(identities),
            "cross_replica_idempotency": True, "identical_artifact_sha256": hashes,
            "shared_application_volumes": False}


def provision(kind_binary: str) -> None:
    if "stepwise" not in command(kind_binary, "get", "clusters").split():
        command(kind_binary, "create", "cluster", "--name", "stepwise", "--image", "kindest/node:v1.35.0", "--wait", "120s")
    command("docker", "build", "-f", "backend/Dockerfile", "-t", "stepwise:kind", ".")
    command("docker", "build", "-f", "deploy/minio/Dockerfile", "-t", "stepwise-minio:local", ".")
    for image in ("postgres:16-alpine", "redis:7-alpine"):
        command("docker", "pull", image)
    # Single-platform export avoids missing multi-arch/attestation blobs in containerd imports.
    architecture = command("docker", "info", "--format", "{{.Architecture}}").strip()
    platform = "linux/arm64" if architecture in {"aarch64", "arm64"} else "linux/amd64"
    with tempfile.TemporaryDirectory() as directory:
        archive = str(Path(directory) / "images.tar")
        command("docker", "image", "save", "--platform", platform, "-o", archive,
                "stepwise:kind", "stepwise-minio:local", "postgres:16-alpine", "redis:7-alpine")
        command(kind_binary, "load", "image-archive", archive, "--name", "stepwise")
    for file in ("namespace.yaml", "config.yaml", "secrets.example.yaml", "dependencies.yaml"):
        kube("apply", "-f", f"deploy/kind/{file}")
    for deployment in ("postgres", "redis", "minio"):
        kube("rollout", "status", f"deployment/{deployment}", "--timeout=180s")
    kube("delete", "job", "stepwise-bootstrap", "--ignore-not-found=true", "--wait=true")
    kube("apply", "-f", "deploy/kind/bootstrap.yaml")
    kube("wait", "--for=condition=complete", "job/stepwise-bootstrap", "--timeout=180s")
    kube("apply", "-f", "deploy/kind/application.yaml")
    for deployment in ("api", "worker"):
        kube("rollout", "restart", f"deployment/{deployment}")
        kube("rollout", "status", f"deployment/{deployment}", "--timeout=180s")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["up", "verify", "benchmark"])
    parser.add_argument("--kind-binary", default="kind")
    args = parser.parse_args()
    if args.action == "up":
        provision(args.kind_binary)
        print("Local kind stack ready. Run this script with verify next.")
    elif args.action == "verify":
        result = verify()
        target = ROOT / "benchmarks/cloud-kind/verification.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(f"Verified {result['jobs']} synthetic jobs across two API and two worker replicas.")
    else:
        with ExitStack() as stack:
            api_pods = pods("api")
            assert len(api_pods) == 2
            urls = [stack.enter_context(forward(p, 18081 + i)) for i, p in enumerate(api_pods)]
            subprocess.run([
                sys.executable, "scripts/load_benchmark.py", "--base-url", urls[0],
                "--alternate-base-url", urls[1], "--deployment", "kind", "--api-replicas", "2",
                "--workers", "2", "--concurrency", "1", "5", "10", "--jobs", "10",
                "--sample-resources", "--output-dir", "benchmarks/cloud-kind",
            ], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
