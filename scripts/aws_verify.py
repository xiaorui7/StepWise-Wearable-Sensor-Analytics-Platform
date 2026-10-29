"""Opt-in real S3/API check using bundled synthetic software-demo recordings."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import boto3
import httpx
from botocore.config import Config

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = {"report.html", "pressure.png", "orientation.png", "steps.csv", "result.json"}


def verify(client: httpx.Client, s3, bucket: str, prefix: str, timeout: float) -> dict:
    response = client.post("/api/v1/analyses", headers={"Idempotency-Key": uuid4().hex}, files={
        "walking_file": ("normal_like.synthetic.txt",
                         (ROOT / "fixtures/synthetic/normal_like.synthetic.txt").read_bytes(), "text/plain"),
        "standing_file": ("standing_neutral.synthetic.txt",
                          (ROOT / "fixtures/synthetic/standing_neutral.synthetic.txt").read_bytes(), "text/plain"),
    })
    response.raise_for_status()
    identity = str(UUID(response.json()["analysis_id"]))
    deadline = time.monotonic() + timeout
    while True:
        response = client.get(f"/api/v1/analyses/{identity}")
        response.raise_for_status()
        state = response.json()["status"]
        if state == "SUCCEEDED":
            break
        if state == "FAILED":
            raise RuntimeError("Analysis failed; inspect server logs")
        if time.monotonic() >= deadline:
            raise TimeoutError("Analysis did not finish within the polling timeout")
        time.sleep(1)
    response = client.get(f"/api/v1/analyses/{identity}/result")
    response.raise_for_status()
    urls = response.json()["artifact_urls"]
    if set(urls) != ARTIFACTS:
        raise ValueError("Unexpected artifact set")
    downloaded = {}
    for name, path in urls.items():
        expected = f"/api/v1/analyses/{identity}/artifacts/{name}"
        if path != expected:
            raise ValueError("Unexpected artifact URL")
        response = client.get(path)
        response.raise_for_status()
        downloaded[name] = response.content

    # Public API intentionally hides private object keys. Find this new job's complete attempt
    # by comparing all API bytes to one attempt's objects, never mixing orphan attempt prefixes.
    namespace = "/".join(part for part in (prefix.strip("/"), identity) if part) + "/"
    attempts: dict[str, dict[str, str]] = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=namespace):
        for item in page.get("Contents", []):
            parts = item["Key"][len(namespace):].split("/")
            if len(parts) == 2 and parts[1] in ARTIFACTS:
                attempts.setdefault(parts[0], {})[parts[1]] = item["Key"]
    matched = []
    for keys in attempts.values():
        if set(keys) != ARTIFACTS:
            continue
        checks = []
        for name in sorted(ARTIFACTS):
            obj = s3.get_object(Bucket=bucket, Key=keys[name])
            try:
                content = obj["Body"].read()
            finally:
                obj["Body"].close()
            digest = hashlib.sha256(content).hexdigest()
            head = s3.head_object(Bucket=bucket, Key=keys[name])
            matches = (
                content == downloaded[name] and len(content) == obj["ContentLength"]
                and len(content) == head["ContentLength"]
                and digest == obj.get("Metadata", {}).get("sha256")
                and digest == head.get("Metadata", {}).get("sha256")
            )
            checks.append({"artifact": name, "sha256": digest, "size_bytes": len(content),
                           "metadata_matches": matches})
        if all(check["metadata_matches"] for check in checks):
            matched.append(checks)
    if len(matched) != 1:
        raise ValueError("Expected exactly one complete attempt matching API bytes and S3 SHA-256 metadata")
    return {"analysis_id": identity, "status": state, "artifacts": matched[0]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--bucket", default=os.getenv("AWS_BUCKET"))
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "us-east-1"))
    parser.add_argument("--prefix", default="artifacts")
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--output", type=Path, default=Path("aws-verification.json"))
    args = parser.parse_args()
    if not args.bucket or args.timeout <= 0:
        parser.error("Provide --bucket or AWS_BUCKET, and a positive timeout")
    evidence = {"generated_at": datetime.now(UTC).isoformat(), "synthetic_data": True, "passed": False}
    try:
        s3 = boto3.client("s3", region_name=args.region,
                          config=Config(connect_timeout=5, read_timeout=15, retries={"max_attempts": 2}))
        with httpx.Client(base_url=args.api_url, timeout=30) as client:
            evidence.update(verify(client, s3, args.bucket, args.prefix, args.timeout))
        evidence["passed"] = True
    except Exception as exc:
        # Avoid writing SDK messages containing account details, bucket names or credential context.
        evidence["error_type"] = type(exc).__name__
        raise SystemExit(f"Verification failed ({type(exc).__name__}); inspect API/storage configuration") from None
    finally:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(evidence, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(f"Verified five synthetic report artifacts; evidence written to {args.output.name}")


if __name__ == "__main__":
    main()
