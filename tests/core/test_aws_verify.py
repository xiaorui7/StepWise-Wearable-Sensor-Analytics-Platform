"""Check verification failures without AWS credentials or network access."""
import hashlib
import io
import runpy
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

script = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/aws_verify.py"))
ARTIFACTS, verify = script["ARTIFACTS"], script["verify"]


@pytest.mark.parametrize("fault", [None, "metadata", "content", "partial"])
def test_verification_requires_complete_matching_artifacts(fault):
    identity = str(uuid4())
    namespace = f"artifacts/{identity}/"
    payloads = {name: ("synthetic " + name).encode() for name in ARTIFACTS}

    def http(request):
        if request.method == "POST":
            return httpx.Response(202, json={"analysis_id": identity})
        if request.url.path.endswith("/result"):
            return httpx.Response(200, json={"artifact_urls": {
                name: f"/api/v1/analyses/{identity}/artifacts/{name}" for name in ARTIFACTS
            }})
        if "/artifacts/" in request.url.path:
            return httpx.Response(200, content=payloads[request.url.path.split("/")[-1]])
        return httpx.Response(200, json={"status": "SUCCEEDED"})

    class Objects:
        def get_paginator(self, operation):
            assert operation == "list_objects_v2"
            return self

        def paginate(self, *, Bucket, Prefix):
            assert Bucket == "test-bucket" and Prefix == namespace
            names = ARTIFACTS - {"report.html"} if fault == "partial" else ARTIFACTS
            yield {"Contents": [{"Key": namespace + "attempt/" + name} for name in names]}

        def head_object(self, *, Bucket, Key):
            data = payloads[Key.split("/")[-1]]
            digest = "bad" if fault == "metadata" else hashlib.sha256(data).hexdigest()
            return {"ContentLength": len(data), "Metadata": {"sha256": digest}}

        def get_object(self, **kwargs):
            metadata = self.head_object(**kwargs)
            data = payloads[kwargs["Key"].split("/")[-1]]
            return {**metadata, "Body": io.BytesIO(b"changed" if fault == "content" else data)}

    with httpx.Client(base_url="http://test", transport=httpx.MockTransport(http)) as client:
        if fault:
            with pytest.raises(ValueError, match="complete attempt"):
                verify(client, Objects(), "test-bucket", "artifacts", 1)
        else:
            result = verify(client, Objects(), "test-bucket", "artifacts", 1)
            assert result["status"] == "SUCCEEDED"
            assert len(result["artifacts"]) == 5
            assert all(item["metadata_matches"] for item in result["artifacts"])
