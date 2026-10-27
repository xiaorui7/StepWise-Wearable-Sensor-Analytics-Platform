"""Opt-in failure experiment confined to the local kind-stepwise/stepwise demo."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import UTC, datetime
from uuid import uuid4

import httpx
from kind_demo import ROOT, forward, kube, pods, upload, wait_job


def scale(name: str, replicas: int) -> None:
    kube("scale", f"deploy/{name}", f"--replicas={replicas}")
    if replicas:
        kube("rollout", "status", f"deploy/{name}", "--timeout=120s")
    else:
        kube("wait", "--for=delete", "pod", "-l", f"app={name}", "--timeout=120s")


def run() -> dict:
    result = {"generated_at": datetime.now(UTC).isoformat(), "synthetic_data": True,
              "context": "kind-stepwise", "namespace": "stepwise", "events": []}
    with forward(pods("api")[0], 18083) as url, httpx.Client(base_url=url, timeout=30) as client:
        baseline = upload(client)
        baseline.raise_for_status()
        baseline_id = baseline.json()["analysis_id"]
        assert wait_job(client, baseline_id)["status"] == "SUCCEEDED"
        report_path = f"/api/v1/analyses/{baseline_id}/artifacts/report.html"
        original = client.get(report_path)
        original.raise_for_status()
        original_hash = hashlib.sha256(original.content).hexdigest()
        try:
            scale("worker", 0)
            submitted = upload(client)
            submitted.raise_for_status()
            identity = submitted.json()["analysis_id"]
            scale("minio", 0)
            outage_started = time.perf_counter()
            assert client.get("/health").status_code == 200
            assert client.get("/ready").status_code == 503
            failed_key = "outage-" + uuid4().hex
            failed_upload = upload(client, failed_key)
            assert failed_upload.status_code == 503
            assert client.get(report_path).status_code == 503
            result["events"].append({"scenario": "object-store unavailable", "upload_http": 503,
                                     "download_http": 503, "ready_http": 503, "health_http": 200})
            # Workers can claim from PostgreSQL/Redis while object reads fail.
            scale("worker", 2)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                record = client.get(f"/api/v1/analyses/{identity}").json()
                if record["attempts"] >= 1:
                    break
                time.sleep(0.2)
            assert record["attempts"] >= 1
            assert client.get(f"/api/v1/analyses/{identity}/artifacts/report.html").status_code == 409
            scale("minio", 1)
            result["outage_wall_s"] = time.perf_counter() - outage_started
            terminal = wait_job(client, identity)
            assert terminal["status"] in {"SUCCEEDED", "FAILED"} and 1 <= terminal["attempts"] <= 3
            result["events"].append({"scenario": "bounded worker attempts after outage",
                                     "state": terminal["status"], "attempts": terminal["attempts"],
                                     "error_code": terminal["error_code"], "analysis_id": identity})
            replay = upload(client, failed_key)
            assert replay.status_code == 202 and replay.json()["status"] == "FAILED"
            result["events"].append({"scenario": "failed submission key replay",
                                     "state": "FAILED", "automatically_resubmitted": False})
            replacement = upload(client)
            replacement.raise_for_status()
            assert wait_job(client, replacement.json()["analysis_id"])["status"] == "SUCCEEDED"
            result["events"].append({"scenario": "new-key submission after storage recovery", "state": "SUCCEEDED"})
        finally:
            scale("minio", 1)
            scale("worker", 2)
    kube("rollout", "restart", "deployment/api")
    kube("rollout", "status", "deployment/api", "--timeout=120s")
    with forward(pods("api")[0], 18083) as url:
        response = httpx.get(url + report_path, timeout=30)
        assert response.status_code == 200
        assert hashlib.sha256(response.content).hexdigest() == original_hash
    result["events"].append({"scenario": "API replacement preserves report", "sha256": original_hash})
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-local-failures", action="store_true",
                        help="Temporarily stop only the kind-stepwise demo workers and MinIO")
    args = parser.parse_args()
    if not args.run_local_failures:
        parser.error("Explicit --run-local-failures is required")
    result = run()
    path = ROOT / "benchmarks/cloud-kind/failures.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
