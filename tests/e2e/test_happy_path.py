from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from stepwise.api.main import create_app
from stepwise.db import Base, get_db
from stepwise.jobs import execute_analysis_job
from stepwise.settings import Settings
from stepwise.storage import ArtifactStore

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "fixtures" / "synthetic"


class ImmediateQueue:
    def __init__(self, sessions: sessionmaker[Session], store: ArtifactStore) -> None:
        self.sessions = sessions
        self.store = store

    def enqueue_analysis(self, analysis_id: UUID) -> None:
        execute_analysis_job(str(analysis_id), self.sessions, self.store)


def test_synthetic_file_to_retrievable_report(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'e2e.sqlite3'}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    artifact_root = tmp_path / "artifacts"
    store = ArtifactStore(artifact_root)
    settings = Settings(
        database_url=database_url,
        redis_url="redis://unused:6379/0",
        artifact_root=artifact_root,
    )
    app = create_app(settings=settings, queue=ImmediateQueue(sessions, store))

    def override_db():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    walking = (FIXTURES / "normal_like.synthetic.txt").read_bytes()
    standing = (FIXTURES / "standing_neutral.synthetic.txt").read_bytes()
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/analyses",
            files={
                "walking_file": ("normal_like.synthetic.txt", walking, "text/plain"),
                "standing_file": ("standing_neutral.synthetic.txt", standing, "text/plain"),
            },
            data={"config_json": "{}"},
        )
        assert created.status_code == 202, created.text
        analysis_id = created.json()["analysis_id"]
        status_response = client.get(f"/api/v1/analyses/{analysis_id}")
        assert status_response.json()["status"] == "SUCCEEDED"
        result_response = client.get(f"/api/v1/analyses/{analysis_id}/result")
        assert result_response.status_code == 200
        assert result_response.json()["result"]["summary"]["detected_steps_single_foot"] == 8
        assert not any("storage_key" in key for key in result_response.json())
        report = client.get(f"/api/v1/analyses/{analysis_id}/artifacts/report.html")
        assert report.status_code == 200
        assert "StepWise Analysis Report" in report.text

        mobile_created = client.post(
            "/api/v1/analyses/text",
            json={
                "walkingText": walking.decode(),
                "standingText": standing.decode(),
                "heel": "P2",
                "arch": "P3",
                "medialForefoot": "P4",
                "lateralForefoot": "P1",
                "pitchEversionSign": "positive",
            },
        )
        assert mobile_created.status_code == 202
        mobile_result = client.get(f"/api/v1/analyses/{mobile_created.json()['analysis_id']}/result")
        assert mobile_result.status_code == 200
        history = client.get("/api/v1/analyses")
        assert history.json()["total"] == 2
