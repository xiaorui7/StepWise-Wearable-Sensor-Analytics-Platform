from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from stepwise.api.main import create_app
from stepwise.db import get_db
from stepwise.jobs import PermanentAnalysisError, RetryableAnalysisError, execute_analysis_job
from stepwise.recovery import reconcile
from stepwise.repository import AnalysisRepository
from stepwise.s3_storage import ObjectIntegrityError, ObjectStoreUnavailable, S3ArtifactStore

FIXTURE = Path(__file__).parents[2] / "fixtures/synthetic/normal_like.synthetic.txt"


@pytest.fixture
def object_job(s3_settings, postgres_env):
    sessions, _, _ = postgres_env

    class Queue:
        def enqueue_analysis(self, analysis_id):
            pass

    app = create_app(settings=s3_settings, queue=Queue())

    def db():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = db
    with TestClient(app) as client:
        response = client.post("/api/v1/analyses", files={
            "walking_file": ("demo.synthetic.txt", FIXTURE.read_bytes(), "text/plain"),
        })
        assert response.status_code == 202
        yield client, sessions, S3ArtifactStore(s3_settings), UUID(response.json()["analysis_id"])


def test_real_objects_are_immutable_and_checksum_checked(s3_settings):
    store = S3ArtifactStore(s3_settings)
    identity, token = uuid4(), str(uuid4())
    outputs = {"report.html": ("text/html", b"original")}
    keys = store.publish(identity, token, outputs)
    assert store.publish(identity, token, outputs) == keys
    with pytest.raises(ObjectIntegrityError):
        store.publish(identity, token, {"report.html": ("text/html", b"changed")})
    assert store.read(keys["report.html"][1]) == b"original"
    key = store._parse(keys["report.html"][1])
    store.client.put_object(Bucket=store.bucket, Key=key, Body=b"corrupt", Metadata={"sha256": "wrong"})
    with pytest.raises(ObjectIntegrityError):
        store.read(keys["report.html"][1])


def test_partial_upload_is_hidden_then_new_attempt_succeeds(object_job, monkeypatch):
    client, sessions, store, identity = object_job
    original = store._put
    written = []

    def partial(key, content, media):
        if len(written) == 1:
            raise ObjectStoreUnavailable()
        reference = original(key, content, media)
        written.append(reference)
        return reference

    monkeypatch.setattr(store, "_put", partial)
    with pytest.raises(RetryableAnalysisError):
        execute_analysis_job(str(identity), sessions, store)
    assert store.read(written[0])
    assert client.get(f"/api/v1/analyses/{identity}/artifacts/report.html").status_code == 409
    with sessions() as session:
        record = AnalysisRepository(session).get(identity)
        assert record.status == "QUEUED" and record.result is None
        assert not any(a.artifact_type == "generated" for a in record.artifacts)
    monkeypatch.setattr(store, "_put", original)
    execute_analysis_job(str(identity), sessions, store)
    assert client.get(f"/api/v1/analyses/{identity}/artifacts/report.html").status_code == 200
    with sessions() as session:
        record = AnalysisRepository(session).get(identity)
        assert record.attempts == 2
        assert written[0] not in [a.storage_key for a in record.artifacts]


def test_stale_attempt_objects_cannot_publish(object_job, monkeypatch):
    client, sessions, store, identity = object_job
    publish = store.publish

    def revoke_after_objects(*args):
        keys = publish(*args)
        with sessions() as session:
            record = AnalysisRepository(session).get(identity)
            record.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
            session.commit()
        assert reconcile(sessions, lambda _: "started")["failed"] == 1
        return keys

    monkeypatch.setattr(store, "publish", revoke_after_objects)
    with pytest.raises(PermanentAnalysisError):
        execute_analysis_job(str(identity), sessions, store)
    assert client.get(f"/api/v1/analyses/{identity}/result").status_code == 409
    assert client.get(f"/api/v1/analyses/{identity}/artifacts/report.html").status_code == 409


def test_actual_unreachable_object_endpoint_is_bounded_and_safe(object_job, s3_settings):
    client, sessions, _, identity = object_job
    unavailable = S3ArtifactStore(s3_settings.model_copy(update={"s3_endpoint_url": "http://127.0.0.1:1"}))
    with pytest.raises(RetryableAnalysisError):
        execute_analysis_job(str(identity), sessions, unavailable)
    with sessions() as session:
        record = AnalysisRepository(session).get(identity)
        assert record.status == "QUEUED" and record.attempts == 1
    client.app.state.store = unavailable
    response = client.post("/api/v1/analyses", files={
        "walking_file": ("demo.txt", FIXTURE.read_bytes(), "text/plain"),
    })
    assert response.status_code == 503
    assert "127.0.0.1" not in response.text


def test_database_failure_after_objects_keeps_report_hidden(object_job, monkeypatch):
    client, sessions, store, identity = object_job

    def fail(*args):
        raise RuntimeError("injected database finalization failure")

    monkeypatch.setattr(AnalysisRepository, "mark_succeeded", fail)
    with pytest.raises(PermanentAnalysisError):
        execute_analysis_job(str(identity), sessions, store)
    with sessions() as session:
        record = AnalysisRepository(session).get(identity)
        assert record.status == "FAILED"
        assert not any(a.artifact_type == "generated" for a in record.artifacts)
    assert client.get(f"/api/v1/analyses/{identity}/artifacts/report.html").status_code == 409


def test_corrupted_completed_content_is_not_served(object_job):
    client, sessions, store, identity = object_job
    execute_analysis_job(str(identity), sessions, store)
    with sessions() as session:
        artifact = AnalysisRepository(session).find_artifact(identity, "report.html")
        key = store._parse(artifact.storage_key)
    store.client.put_object(Bucket=store.bucket, Key=key, Body=b"tampered", Metadata={"sha256": "wrong"})
    response = client.get(f"/api/v1/analyses/{identity}/artifacts/report.html")
    assert response.status_code == 503 and "tampered" not in response.text
