from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from stepwise.api.main import create_app
from stepwise.core.models import JobStatus
from stepwise.db import Base, get_db
from stepwise.repository import AnalysisRepository
from stepwise.settings import Settings

FIXTURES = Path(__file__).parents[2] / "fixtures" / "synthetic"


class NoopQueue:
    def enqueue_analysis(self, analysis_id: UUID) -> None:
        pass


class FailingQueue:
    def enqueue_analysis(self, analysis_id: UUID) -> None:
        raise ConnectionError("synthetic queue failure")


def build_client(tmp_path: Path, queue: NoopQueue | FailingQueue):
    database_url = f"sqlite:///{tmp_path / 'api.sqlite3'}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    settings = Settings(
        database_url=database_url,
        artifact_root=tmp_path / "artifacts",
    )
    app = create_app(settings=settings, queue=queue)

    def override_db():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    return TestClient(app), sessions


def test_result_is_unavailable_while_job_is_queued(tmp_path: Path) -> None:
    client, _ = build_client(tmp_path, NoopQueue())
    walking = (FIXTURES / "normal_like.synthetic.txt").read_bytes()

    with client:
        created = client.post(
            "/api/v1/analyses",
            files={"walking_file": ("walk.txt", walking, "text/plain")},
        )
        analysis_id = created.json()["analysis_id"]
        result = client.get(f"/api/v1/analyses/{analysis_id}/result")

    assert created.status_code == 202
    assert result.status_code == 409


def test_queue_failure_is_persisted_and_returned_safely(tmp_path: Path) -> None:
    client, sessions = build_client(tmp_path, FailingQueue())
    walking = (FIXTURES / "normal_like.synthetic.txt").read_bytes()

    with client:
        response = client.post(
            "/api/v1/analyses",
            files={"walking_file": ("walk.txt", walking, "text/plain")},
        )

    assert response.status_code == 503
    assert response.json()["detail"] == "The analysis queue is unavailable"
    with sessions() as session:
        records, total = AnalysisRepository(session).list(page=1, page_size=10)
        assert total == 1
        assert records[0].status == JobStatus.FAILED.value
        assert records[0].error_code == "QUEUE_UNAVAILABLE"


def test_empty_upload_is_rejected_before_enqueue(tmp_path: Path) -> None:
    client, _ = build_client(tmp_path, NoopQueue())
    with client:
        response = client.post(
            "/api/v1/analyses",
            files={"walking_file": ("empty.txt", b"", "text/plain")},
        )
    assert response.status_code == 422
