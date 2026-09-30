from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from stepwise.core.models import AnalysisConfig, JobStatus
from stepwise.db import Base
from stepwise.repository import AnalysisRepository
from stepwise.storage import ArtifactStore


def test_job_and_artifact_persistence(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'repository.sqlite3'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    store = ArtifactStore(tmp_path / "artifacts")
    with sessions() as session:
        repository = AnalysisRepository(session)
        record = repository.create(AnalysisConfig(), {"walking_size_bytes": 123})
        key = store.put(record.id, "report.html", b"<html>synthetic report</html>")
        repository.add_artifact(record, "report.html", "text/html", key)
        repository.mark_running(record)
        repository.mark_succeeded(record, {"summary": {"data_quality": "High"}})

    with sessions() as session:
        persisted = AnalysisRepository(session).get(record.id)
        assert persisted is not None
        assert persisted.status == JobStatus.SUCCEEDED.value
        assert persisted.result == {"summary": {"data_quality": "High"}}
        artifact = AnalysisRepository(session).find_artifact(record.id, "report.html")
        assert artifact is not None
        assert store.read(artifact.storage_key).startswith(b"<html>")
