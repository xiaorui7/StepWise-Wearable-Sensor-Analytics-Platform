import os
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from stepwise.api.main import create_app
from stepwise.db import Base, get_db
from stepwise.jobs import execute_analysis_job
from stepwise.settings import Settings
from stepwise.storage import ArtifactStore

POSTGRES_URL = os.getenv("STEPWISE_TEST_POSTGRES_URL")


class ImmediateQueue:
    def __init__(self, sessions: sessionmaker[Session], store: ArtifactStore) -> None:
        self.sessions = sessions
        self.store = store

    def enqueue_analysis(self, analysis_id: UUID) -> None:
        execute_analysis_job(str(analysis_id), self.sessions, self.store)


@pytest.mark.postgres
@pytest.mark.skipif(not POSTGRES_URL, reason="STEPWISE_TEST_POSTGRES_URL is not configured")
def test_api_worker_and_postgres(tmp_path: Path) -> None:
    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    store = ArtifactStore(tmp_path / "postgres-artifacts")
    settings = Settings(database_url=POSTGRES_URL, artifact_root=tmp_path / "postgres-artifacts")
    app = create_app(settings=settings, queue=ImmediateQueue(sessions, store))

    def override_db():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    fixture = Path(__file__).parents[2] / "fixtures" / "synthetic" / "normal_like.synthetic.txt"
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/analyses",
            files={"walking_file": ("normal_like.synthetic.txt", fixture.read_bytes(), "text/plain")},
            data={"config_json": "{}"},
        )
        assert response.status_code == 202
        result = client.get(f"/api/v1/analyses/{response.json()['analysis_id']}/result")
        assert result.status_code == 200
        assert result.json()["status"] == "SUCCEEDED"
