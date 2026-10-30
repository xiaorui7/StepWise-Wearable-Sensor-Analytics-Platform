"""Opt-in real AWS bucket test; uses default SDK credentials and no custom endpoint."""
import hashlib
import os
from uuid import uuid4

import pytest

from stepwise.s3_storage import S3ArtifactStore
from stepwise.settings import Settings


@pytest.mark.skipif(not os.getenv("STEPWISE_TEST_AWS_BUCKET"), reason="Real AWS bucket not configured")
def test_real_aws_immutable_artifacts():
    # Leaves only uniquely prefixed synthetic objects; the instance role cannot delete objects.
    store = S3ArtifactStore(Settings(
        _env_file=None, storage_backend="s3", s3_endpoint_url=None,
        s3_bucket=os.environ["STEPWISE_TEST_AWS_BUCKET"],
        s3_region=os.getenv("AWS_REGION", "us-east-1"),
        s3_access_key="", s3_secret_key="", s3_prefix="aws-integration/" + uuid4().hex,
    ))
    store.check()
    identity, token = uuid4(), str(uuid4())
    content = b"Synthetic software integration test; no clinical data.\n"
    source = store.put(identity, "walking.synthetic.txt", content)
    assert store.read(source) == content
    bundle = {"report.html": ("text/html", content)}
    published = store.publish(identity, token, bundle)
    assert store.publish(identity, token, bundle) == published
    reference = published["report.html"][1]
    key = store._parse(reference)
    head = store.client.head_object(Bucket=store.bucket, Key=key)
    assert head["Metadata"]["sha256"] == hashlib.sha256(content).hexdigest()
    assert head["ContentLength"] == len(content)
    assert store.read(reference) == content
    with pytest.raises(OSError):
        store.publish(identity, token, {"report.html": ("text/html", b"different")})
