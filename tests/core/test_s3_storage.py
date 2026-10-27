import hashlib
import io
from uuid import uuid4

import pytest
from botocore.exceptions import EndpointConnectionError
from botocore.response import StreamingBody
from botocore.stub import Stubber

from stepwise.jobs import is_retryable
from stepwise.s3_storage import ObjectIntegrityError, ObjectStoreUnavailable, S3ArtifactStore
from stepwise.settings import Settings
from stepwise.storage import ArtifactNotFoundError, ArtifactStore, create_store


@pytest.fixture
def store():
    return S3ArtifactStore(Settings(s3_access_key="test", s3_secret_key="test"))


def response(content=b"ok", digest=None):
    return {
        "Body": StreamingBody(io.BytesIO(content), len(content)),
        "ContentLength": len(content),
        "Metadata": {"sha256": digest or hashlib.sha256(content).hexdigest()},
    }


def test_factory_preserves_local_default(tmp_path):
    assert isinstance(create_store(Settings(artifact_root=tmp_path)), ArtifactStore)


def test_read_checks_digest(store):
    with Stubber(store.client) as stub:
        stub.add_response("get_object", response(), {"Bucket": "stepwise", "Key": "artifacts/test"})
        assert store.read("s3://stepwise/artifacts/test") == b"ok"
        stub.add_response("get_object", response(digest="bad"))
        with pytest.raises(ObjectIntegrityError):
            store.read("s3://stepwise/artifacts/test")


@pytest.mark.parametrize("status,code,error,retry", [
    (503, "SlowDown", ObjectStoreUnavailable, True),
    (409, "ConditionalRequestConflict", ObjectStoreUnavailable, True),
    (403, "AccessDenied", OSError, False),
    (404, "NoSuchKey", ArtifactNotFoundError, False),
])
def test_errors_are_safe_and_classified(store, status, code, error, retry):
    with Stubber(store.client) as stub:
        stub.add_client_error("get_object", code, "private message", http_status_code=status)
        with pytest.raises(error) as caught:
            store.read("s3://stepwise/artifacts/test")
        assert "private" not in str(caught.value)
        assert is_retryable(caught.value) == retry


def test_connection_failure_is_retryable(store, monkeypatch):
    def fail(**kwargs):
        raise EndpointConnectionError(endpoint_url="http://private")

    monkeypatch.setattr(store.client, "get_object", fail)
    with pytest.raises(ObjectStoreUnavailable) as caught:
        store.read("s3://stepwise/artifacts/test")
    assert is_retryable(caught.value)
    assert "private" not in str(caught.value)


@pytest.mark.parametrize("reference", [
    "../secret", "s3://elsewhere/artifacts/x", "s3://stepwise/other/x",
    "s3://stepwise/artifacts/../x", "s3://stepwise/artifacts/x\\y",
])
def test_keys_cannot_escape_namespace(store, reference):
    with pytest.raises(ValueError):
        store.read(reference)


def test_conditional_write_accepts_identical_replay_but_rejects_mutation(store):
    identity, token = uuid4(), str(uuid4())
    key = f"artifacts/{identity}/{token}/report.html"
    for content, succeeds in [(b"ok", True), (b"changed", False)]:
        with Stubber(store.client) as stub:
            stub.add_client_error("put_object", "PreconditionFailed", http_status_code=412,
                                  expected_params={"Bucket": "stepwise", "Key": key, "Body": content,
                                                   "ContentType": "text/html", "IfNoneMatch": "*",
                                                   "Metadata": {"sha256": hashlib.sha256(content).hexdigest()}})
            stub.add_response("get_object", response())
            if succeeds:
                stub.add_response("get_object", response())
                assert store.publish(identity, token, {"report.html": ("text/html", content)})
            else:
                with pytest.raises(ObjectIntegrityError):
                    store.publish(identity, token, {"report.html": ("text/html", content)})
