import errno
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from stepwise.core.models import AnalysisConfig
from stepwise.db import Base
from stepwise.db_models import ArtifactRecord
from stepwise.jobs import PermanentAnalysisError, RetryableAnalysisError, execute_analysis_job
from stepwise.recovery import reconcile
from stepwise.repository import AnalysisRepository, IdempotencyConflict, LostExecutionClaim
from stepwise.settings import Settings
from stepwise.storage import ArtifactStore

FIXTURES = Path(__file__).parents[2] / "fixtures" / "synthetic"


@pytest.fixture
def setup_job(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'jobs.sqlite3'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    store = ArtifactStore(tmp_path / "artifacts")
    with sessions() as session:
        repo = AnalysisRepository(session)
        record = repo.create(AnalysisConfig(), {})
        key = store.put(record.id, "walking-input.txt", (FIXTURES / "normal_like.synthetic.txt").read_bytes())
        repo.add_artifact(record, "walking-input.txt", "text/plain", key, "input")
        identity = record.id
    yield sessions, store, identity
    engine.dispose()


def test_claim_is_atomic_and_terminal_execution_is_noop(setup_job):
    sessions, store, identity = setup_job
    execute_analysis_job(str(identity), sessions, store)
    execute_analysis_job(str(identity), sessions, store)
    with sessions() as session:
        record = AnalysisRepository(session).get(identity)
        assert record.status == "SUCCEEDED" and record.attempts == 1
        generated = [a for a in record.artifacts if a.artifact_type == "generated"]
        assert len(generated) == 5


def test_transient_failure_then_success_has_one_complete_artifact_set(setup_job, monkeypatch):
    sessions, store, identity = setup_job
    real_publish = store.publish

    def fail(*args):
        raise OSError(errno.EBUSY, "private path must not leak")

    monkeypatch.setattr(store, "publish", fail)
    with pytest.raises(RetryableAnalysisError):
        execute_analysis_job(str(identity), sessions, store)
    with sessions() as session:
        record = AnalysisRepository(session).get(identity)
        assert record.status == "QUEUED" and record.attempts == 1
        assert "private" not in record.error_message
        assert not any(a.artifact_type == "generated" for a in record.artifacts)
    monkeypatch.setattr(store, "publish", real_publish)
    execute_analysis_job(str(identity), sessions, store)
    with sessions() as session:
        record = AnalysisRepository(session).get(identity)
        assert record.status == "SUCCEEDED" and record.attempts == 2
        assert record.error_code is None
        assert len([a for a in record.artifacts if a.artifact_type == "generated"]) == 5


def test_retry_budget_is_bounded(setup_job, monkeypatch):
    sessions, store, identity = setup_job
    monkeypatch.setattr("stepwise.jobs.run_analysis", lambda *args: (_ for _ in ()).throw(TimeoutError()))
    settings = Settings(max_attempts=2)
    with pytest.raises(RetryableAnalysisError):
        execute_analysis_job(str(identity), sessions, store, settings)
    with pytest.raises(PermanentAnalysisError):
        execute_analysis_job(str(identity), sessions, store, settings)
    with sessions() as session:
        record = AnalysisRepository(session).get(identity)
        assert record.status == "FAILED" and record.attempts == 2


def test_invalid_input_is_not_retryable(setup_job):
    sessions, store, identity = setup_job
    store.put(identity, "walking-input.txt", b"invalid input")
    with pytest.raises(PermanentAnalysisError):
        execute_analysis_job(str(identity), sessions, store)
    with sessions() as session:
        record = AnalysisRepository(session).get(identity)
        assert record.status == "FAILED" and record.attempts == 1
        assert record.error_code == "INVALID_SENSOR_DATA"


def test_idempotency_matches_content_and_rejects_conflict(setup_job):
    sessions, _, _ = setup_job
    with sessions() as session:
        repo = AnalysisRepository(session)
        first, created = repo.create_submission(AnalysisConfig(), {}, "key", "fingerprint")
        second, duplicate = repo.create_submission(AnalysisConfig(), {}, "key", "fingerprint")
        assert created and not duplicate and first.id == second.id
        with pytest.raises(IdempotencyConflict):
            repo.create_submission(AnalysisConfig(), {}, "key", "different")


def test_reconcile_expired_execution_fences_old_worker(setup_job):
    sessions, _, identity = setup_job
    with sessions() as session:
        repo = AnalysisRepository(session)
        record = repo.get(identity)
        assert repo.mark_running(record)
        token = record.execution_token
        record.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    assert reconcile(sessions, lambda _: None)["failed"] == 1
    with sessions() as session:
        with pytest.raises(LostExecutionClaim):
            AnalysisRepository(session).finish(identity, token, {}, {})
        record = AnalysisRepository(session).get(identity)
        assert record.status == "FAILED" and record.error_code == "EXECUTION_INTERRUPTED"


@pytest.mark.parametrize(
    "rq_state,expected", [(None, 1), ("failed", 1), ("finished", 1), ("queued", 0), ("scheduled", 0)]
)
def test_reconcile_queued_states(setup_job, rq_state, expected):
    sessions, _, identity = setup_job
    with sessions() as session:
        record = AnalysisRepository(session).get(identity)
        record.created_at = datetime.now(UTC) - timedelta(minutes=10)
        session.commit()
    assert reconcile(sessions, lambda _: rq_state)["failed"] == expected


def test_reconciliation_preserves_state_when_redis_is_unavailable(setup_job):
    sessions, _, _ = setup_job

    def unavailable(_):
        raise ConnectionError()

    result = reconcile(sessions, unavailable, now=datetime.now(UTC) + timedelta(hours=1))
    assert result["queue_unavailable"] == 1 and result["failed"] == 0


def test_publication_failure_leaves_no_partial_directory(tmp_path):
    store = ArtifactStore(tmp_path)
    identity = uuid4()
    with pytest.raises(ValueError):
        store.publish(
            identity, str(uuid4()), {"report.html": ("text/html", b"ok"), "../bad": ("text/plain", b"bad")}
        )
    assert list((tmp_path / str(identity)).iterdir()) == []


def test_database_commit_failure_exposes_no_generated_rows(setup_job, monkeypatch):
    sessions, store, identity = setup_job
    monkeypatch.setattr(
        AnalysisRepository, "mark_succeeded", lambda *args: (_ for _ in ()).throw(RuntimeError("injected"))
    )
    with pytest.raises(PermanentAnalysisError):
        execute_analysis_job(str(identity), sessions, store)
    with sessions() as session:
        record = AnalysisRepository(session).get(identity)
        assert record.status == "FAILED" and record.result is None
        assert not list(
            session.scalars(select(ArtifactRecord).where(ArtifactRecord.artifact_type == "generated"))
        )


def test_legacy_running_record_without_lease_can_be_recovered(setup_job):
    sessions, _, identity = setup_job
    with sessions() as session:
        record = AnalysisRepository(session).get(identity)
        record.status = "RUNNING"
        record.started_at = datetime.now(UTC) - timedelta(hours=1)
        record.lease_expires_at = None
        session.commit()
    assert reconcile(sessions, lambda _: "started")["failed"] == 1


def test_worker_finishing_after_recovery_is_terminal_failure(setup_job, monkeypatch):
    sessions, store, identity = setup_job
    from stepwise.jobs import run_analysis

    def recovered_during_processing(*args):
        with sessions() as session:
            record = AnalysisRepository(session).get(identity)
            record.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
            session.commit()
        assert reconcile(sessions, lambda _: "started")["failed"] == 1
        return run_analysis(*args)

    monkeypatch.setattr("stepwise.jobs.run_analysis", recovered_during_processing)
    with pytest.raises(PermanentAnalysisError):
        execute_analysis_job(str(identity), sessions, store)
    with sessions() as session:
        record = AnalysisRepository(session).get(identity)
        assert record.status == "FAILED" and record.result is None
        assert not any(a.artifact_type == "generated" for a in record.artifacts)
