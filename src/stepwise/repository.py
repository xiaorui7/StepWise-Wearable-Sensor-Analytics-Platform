from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from stepwise.core.json_utils import json_safe
from stepwise.core.models import AnalysisConfig, JobStatus
from stepwise.db_models import AnalysisRecord, ArtifactRecord


class AnalysisRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, config: AnalysisConfig, input_metadata: dict[str, object]) -> AnalysisRecord:
        record = AnalysisRecord(
            status=JobStatus.QUEUED.value,
            sensor_config=json_safe(config),
            input_metadata=json_safe(input_metadata),
        )
        self.session.add(record)
        self.session.commit()
        self.session.refresh(record)
        return record

    def get(self, analysis_id: UUID) -> AnalysisRecord | None:
        return self.session.get(AnalysisRecord, analysis_id)

    def list(self, page: int, page_size: int) -> tuple[list[AnalysisRecord], int]:
        total = self.session.scalar(select(func.count()).select_from(AnalysisRecord)) or 0
        records = list(
            self.session.scalars(
                select(AnalysisRecord)
                .order_by(desc(AnalysisRecord.created_at))
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return records, int(total)

    def mark_running(self, record: AnalysisRecord) -> None:
        record.status = JobStatus.RUNNING.value
        record.started_at = datetime.now(UTC)
        self.session.commit()

    def mark_succeeded(self, record: AnalysisRecord, result: dict[str, object]) -> None:
        record.status = JobStatus.SUCCEEDED.value
        record.summary = json_safe(result.get("summary"))
        record.result = json_safe(result)
        record.completed_at = datetime.now(UTC)
        self.session.commit()

    def mark_failed(self, record: AnalysisRecord, code: str, message: str) -> None:
        record.status = JobStatus.FAILED.value
        record.error_code = code
        record.error_message = message[:1000]
        record.completed_at = datetime.now(UTC)
        self.session.commit()

    def add_artifact(
        self,
        record: AnalysisRecord,
        name: str,
        media_type: str,
        storage_key: str,
        artifact_type: str = "generated",
    ) -> ArtifactRecord:
        artifact = ArtifactRecord(
            analysis_id=record.id,
            name=name,
            media_type=media_type,
            storage_key=storage_key,
            artifact_type=artifact_type,
        )
        self.session.add(artifact)
        self.session.commit()
        self.session.refresh(artifact)
        return artifact

    def find_artifact(self, analysis_id: UUID, name: str) -> ArtifactRecord | None:
        return self.session.scalar(
            select(ArtifactRecord).where(
                ArtifactRecord.analysis_id == analysis_id, ArtifactRecord.name == name
            )
        )
