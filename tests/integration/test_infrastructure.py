import multiprocessing
import os
import signal
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from redis import Redis
from rq import Queue, Worker
from rq.job import Job
from sqlalchemy import select

from stepwise.api.main import create_app
from stepwise.db import get_db
from stepwise.db_models import AnalysisRecord
from stepwise.jobs import RQJobQueue, handle_workhorse_killed
from stepwise.recovery import reconcile
from stepwise.settings import Settings

FIXTURES = Path(__file__).parents[2] / "fixtures" / "synthetic"
pytestmark = pytest.mark.infrastructure


def run_worker(queue_name, connection, engine, barrier=None):
    engine.dispose(close=False)
    if barrier:
        barrier.wait(timeout=20)
    Worker([queue_name], connection=connection, work_horse_killed_handler=handle_workhorse_killed).work(
        burst=True, logging_level="WARNING"
    )


@pytest.fixture(params=["local", "s3"])
def environment(postgres_env, redis_env, tmp_path, monkeypatch, request):
    if os.name != "posix":
        pytest.skip("Forking RQ worker test requires Linux; run the Docker test profile")
    sessions, url, engine = postgres_env
    queue_name = "test_" + uuid4().hex
    queue = RQJobQueue(settings=Settings(redis_url=os.environ["STEPWISE_TEST_REDIS_URL"], retry_delay_s=0))
    queue.queue = Queue(queue_name, connection=redis_env)
    settings = Settings(database_url=url, artifact_root=tmp_path / "artifacts", retry_delay_s=0)
    if request.param == "s3":
        settings = request.getfixturevalue("s3_settings").model_copy(update={
            "database_url": url, "retry_delay_s": 0,
        })
    monkeypatch.setattr("stepwise.jobs.SessionLocal", sessions)
    monkeypatch.setattr("stepwise.jobs.get_settings", lambda: settings)
    app = create_app(settings=settings, queue=queue)

    def db():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = db
    client = TestClient(app)
    yield client, sessions, queue, engine, settings
    client.close()
    with sessions() as session:
        identities = list(session.scalars(select(AnalysisRecord.id)))
    from rq.exceptions import NoSuchJobError

    for identity in identities:
        with suppress(NoSuchJobError):
            Job.fetch(str(identity), connection=redis_env).delete()
    for job_id in queue.queue.get_job_ids():
        job = Job.fetch(job_id, connection=redis_env)
        job.delete()
    queue.queue.delete(delete_jobs=True)
    for key in redis_env.scan_iter(match=f"rq:*{queue_name}*"):
        redis_env.delete(key)


def upload(client, key=None):
    return client.post(
        "/api/v1/analyses",
        headers={"Idempotency-Key": key} if key else {},
        files={
            "walking_file": (
                "normal_like.synthetic.txt",
                (FIXTURES / "normal_like.synthetic.txt").read_bytes(),
                "text/plain",
            ),
            "standing_file": (
                "standing.synthetic.txt",
                (FIXTURES / "standing_neutral.synthetic.txt").read_bytes(),
                "text/plain",
            ),
        },
    )


def test_concurrent_uploads_and_idempotency_use_postgres_constraint(environment):
    client, sessions, queue, _, _ = environment
    barrier = Barrier(8)

    def concurrent(_):
        barrier.wait(timeout=20)
        return upload(client, "concurrent-key")

    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(concurrent, range(8)))
    assert all(response.status_code == 202 for response in responses)
    assert len({response.json()["analysis_id"] for response in responses}) == 1
    assert queue.queue.count == 1
    with sessions() as session:
        assert len(list(session.scalars(select(AnalysisRecord)))) == 1
    with ThreadPoolExecutor(max_workers=4) as pool:
        independent = list(pool.map(lambda _: upload(client), range(4)))
    assert len({r.json()["analysis_id"] for r in independent}) == 4
    assert queue.queue.count == 5


def test_two_real_workers_complete_concurrent_jobs_without_missing_reports(environment, monkeypatch):
    client, sessions, queue, engine, _ = environment
    from stepwise.jobs import run_analysis

    def slower(*args):
        time.sleep(0.5)
        return run_analysis(*args)

    monkeypatch.setattr("stepwise.jobs.run_analysis", slower)
    identities = [upload(client).json()["analysis_id"] for _ in range(4)]
    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(2)
    workers = [
        context.Process(target=run_worker, args=(queue.queue.name, queue.queue.connection, engine, barrier))
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    try:
        for worker in workers:
            worker.join(timeout=40)
            assert worker.exitcode == 0
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.kill()
                worker.join()
    windows = []
    for identity in identities:
        response = client.get(f"/api/v1/analyses/{identity}/result")
        assert response.status_code == 200
        assert len(response.json()["artifact_urls"]) == 5
        for path in response.json()["artifact_urls"].values():
            assert client.get(path).status_code == 200
        with sessions() as session:
            record = session.get(AnalysisRecord, UUID(identity))
            assert record.attempts == 1 and len(record.artifacts) == 7
            windows.append((record.started_at, record.completed_at))
        assert Job.fetch(identity, connection=queue.queue.connection).get_status().value == "finished"
    assert any(a[0] < b[1] and b[0] < a[1] for i, a in enumerate(windows) for b in windows[i + 1 :])


def test_real_rq_retries_only_transient_processing_failure(environment, monkeypatch):
    client, sessions, queue, engine, _ = environment
    from stepwise.jobs import run_analysis

    counter = "test_failure_" + uuid4().hex

    def fail_once(*args):
        connection = Redis.from_url(os.environ["STEPWISE_TEST_REDIS_URL"])
        if connection.incr(counter) == 1:
            raise TimeoutError("injected transient failure")
        return run_analysis(*args)

    monkeypatch.setattr("stepwise.jobs.run_analysis", fail_once)
    identity = upload(client).json()["analysis_id"]
    run_worker(queue.queue.name, queue.queue.connection, engine)
    assert client.get(f"/api/v1/analyses/{identity}/result").status_code == 200
    with sessions() as session:
        record = session.get(AnalysisRecord, UUID(identity))
        assert record.attempts == 2 and record.status == "SUCCEEDED"
        assert len([a for a in record.artifacts if a.artifact_type == "generated"]) == 5
    queue.queue.connection.delete(counter)


def test_delayed_retry_enters_scheduled_registry_then_completes(environment, monkeypatch):
    from rq.scheduler import RQScheduler

    from stepwise.jobs import run_analysis

    client, sessions, queue, engine, _ = environment
    queue.settings.retry_delay_s = 1
    counter = "test_delayed_" + uuid4().hex

    def fail_once(*args):
        if queue.queue.connection.incr(counter) == 1:
            raise TimeoutError()
        return run_analysis(*args)

    monkeypatch.setattr("stepwise.jobs.run_analysis", fail_once)
    identity = upload(client).json()["analysis_id"]
    run_worker(queue.queue.name, queue.queue.connection, engine)
    assert Job.fetch(identity, connection=queue.queue.connection).get_status().value == "scheduled"
    assert client.get(f"/api/v1/analyses/{identity}").json()["status"] == "QUEUED"
    scheduler = RQScheduler([queue.queue.name], connection=queue.queue.connection)
    scheduler.acquire_locks()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and queue.queue.count == 0:
            scheduler.enqueue_scheduled_jobs()
            time.sleep(0.05)
        assert queue.queue.count == 1
        run_worker(queue.queue.name, queue.queue.connection, engine)
        assert client.get(f"/api/v1/analyses/{identity}/result").status_code == 200
    finally:
        queue.queue.connection.delete(counter, scheduler.get_locking_key(queue.queue.name))


def test_postgres_concurrent_claim_has_one_winner(environment):
    from stepwise.repository import AnalysisRepository

    client, sessions, _, _, _ = environment
    identity = UUID(upload(client).json()["analysis_id"])
    barrier = Barrier(6)

    def claim(_):
        with sessions() as session:
            repo = AnalysisRepository(session)
            record = repo.get(identity)
            barrier.wait(timeout=10)
            return repo.mark_running(record)

    with ThreadPoolExecutor(max_workers=6) as pool:
        assert sum(pool.map(claim, range(6))) == 1
    with sessions() as session:
        assert session.get(AnalysisRecord, identity).attempts == 1


def test_real_rq_permanent_failure_is_not_retried(environment, monkeypatch):
    client, sessions, queue, engine, _ = environment

    def invalid(*args):
        raise ValueError("injected invalid processing data")

    monkeypatch.setattr("stepwise.jobs.run_analysis", invalid)
    identity = upload(client).json()["analysis_id"]
    run_worker(queue.queue.name, queue.queue.connection, engine)
    assert Job.fetch(identity, connection=queue.queue.connection).get_status().value == "failed"
    with sessions() as session:
        record = session.get(AnalysisRecord, UUID(identity))
        assert record.attempts == 1 and record.status == "FAILED"
    assert client.get(f"/api/v1/analyses/{identity}/artifacts/report.html").status_code == 409


def test_workhorse_abrupt_exit_is_persisted_as_failed(environment, monkeypatch):
    client, sessions, queue, engine, _ = environment
    monkeypatch.setattr("stepwise.jobs.run_analysis", lambda *args: os._exit(17))
    identity = upload(client).json()["analysis_id"]
    run_worker(queue.queue.name, queue.queue.connection, engine)
    with sessions() as session:
        record = session.get(AnalysisRecord, UUID(identity))
        assert record.status == "FAILED" and record.error_code == "EXECUTION_INTERRUPTED"
    assert Job.fetch(identity, connection=queue.queue.connection).get_status().value == "failed"


def test_workhorse_exit_during_publication_never_exposes_outputs(environment, monkeypatch):
    from stepwise.s3_storage import S3ArtifactStore
    from stepwise.storage import ArtifactStore

    client, sessions, queue, engine, settings = environment
    identity = upload(client).json()["analysis_id"]
    if settings.storage_backend == "s3":
        original = S3ArtifactStore._put

        def partial(self, *args):
            original(self, *args)
            os._exit(17)

        monkeypatch.setattr(S3ArtifactStore, "_put", partial)
    else:
        original = ArtifactStore.publish

        def published(self, *args):
            original(self, *args)
            os._exit(17)

        monkeypatch.setattr(ArtifactStore, "publish", published)
    run_worker(queue.queue.name, queue.queue.connection, engine)
    with sessions() as session:
        record = session.get(AnalysisRecord, UUID(identity))
        assert record.status == "FAILED" and record.error_code == "EXECUTION_INTERRUPTED"
        assert record.result is None
        assert not any(a.artifact_type == "generated" for a in record.artifacts)
    assert client.get(f"/api/v1/analyses/{identity}/artifacts/report.html").status_code == 409


def test_missing_redis_job_is_reconciled(environment):
    client, sessions, queue, _, _ = environment
    identity = upload(client).json()["analysis_id"]
    Job.fetch(identity, connection=queue.queue.connection).delete()
    with sessions() as session:
        record = session.get(AnalysisRecord, UUID(identity))
        record.created_at = datetime.now(UTC) - timedelta(minutes=10)
        session.commit()
    assert reconcile(sessions, lambda _: None)["failed"] == 1
    assert client.get(f"/api/v1/analyses/{identity}").json()["error_code"] == "QUEUE_JOB_LOST"


def test_whole_worker_termination_is_recovered_after_lease(environment, monkeypatch):
    client, sessions, queue, engine, _ = environment
    pid_key = "test_pid_" + uuid4().hex

    def blocking(*args):
        connection = Redis.from_url(os.environ["STEPWISE_TEST_REDIS_URL"])
        connection.set(pid_key, os.getpid(), ex=60)
        time.sleep(60)

    monkeypatch.setattr("stepwise.jobs.run_analysis", blocking)
    identity = upload(client).json()["analysis_id"]
    worker = multiprocessing.get_context("fork").Process(
        target=run_worker, args=(queue.queue.name, queue.queue.connection, engine)
    )
    worker.start()
    horse_pid = None
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            value = queue.queue.connection.get(pid_key)
            if value:
                horse_pid = int(value)
                break
            time.sleep(0.05)
        assert horse_pid is not None
        worker.kill()
        worker.join(timeout=5)
        os.kill(horse_pid, signal.SIGKILL)
        horse_pid = None
        with sessions() as session:
            record = session.get(AnalysisRecord, UUID(identity))
            assert record.status == "RUNNING"
            record.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
            session.commit()
        assert reconcile(sessions, lambda _: "started")["failed"] == 1
        assert client.get(f"/api/v1/analyses/{identity}").json()["error_code"] == "EXECUTION_INTERRUPTED"
    finally:
        if worker.is_alive():
            worker.kill()
            worker.join()
        if horse_pid:
            os.kill(horse_pid, signal.SIGKILL)
        queue.queue.connection.delete(pid_key)
