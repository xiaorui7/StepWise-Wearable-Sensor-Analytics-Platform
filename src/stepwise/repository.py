from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import CursorResult, desc, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from stepwise.core.json_utils import json_safe
from stepwise.core.models import AnalysisConfig, JobStatus
from stepwise.db_models import AnalysisRecord, ArtifactRecord


class IdempotencyConflict(ValueError):
    pass


class LostExecutionClaim(RuntimeError):
    pass


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

    def create_submission(
        self, config: AnalysisConfig, metadata: dict[str, object], key: str | None, fingerprint: str
    ) -> tuple[AnalysisRecord, bool]:
        record = AnalysisRecord(
            status=JobStatus.QUEUED.value,
            sensor_config=json_safe(config),
            input_metadata=json_safe(metadata),
            idempotency_key=key,
            request_fingerprint=fingerprint,
        )
        self.session.add(record)
        try:
            self.session.commit()
            self.session.refresh(record)
            return record, True
        except IntegrityError:
            self.session.rollback()
            existing = self.session.scalar(
                select(AnalysisRecord).where(AnalysisRecord.idempotency_key == key)
            )
            if key is None or existing is None:
                raise
            if existing.request_fingerprint != fingerprint:
                raise IdempotencyConflict(
                    "Idempotency-Key was already used with different request content"
                ) from None
            return existing, False

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

    def mark_running(self, record: AnalysisRecord, lease_s: int = 360, max_attempts: int = 3) -> bool:
        now = datetime.now(UTC)
        token = str(uuid4())
        result = self.session.execute(
            update(AnalysisRecord)
            .where(
                AnalysisRecord.id == record.id,
                AnalysisRecord.status == JobStatus.QUEUED.value,
                AnalysisRecord.attempts < max_attempts,
            )
            .values(
                status=JobStatus.RUNNING.value,
                started_at=now,
                completed_at=None,
                attempts=AnalysisRecord.attempts + 1,
                execution_token=token,
                lease_expires_at=now + timedelta(seconds=lease_s),
            )
        )
        self.session.commit()
        self.session.refresh(record)
        return bool(cast(CursorResult[object], result).rowcount)

    def owned(self, analysis_id: UUID, token: str) -> AnalysisRecord:
        record = self.session.scalar(
            select(AnalysisRecord)
            .where(
                AnalysisRecord.id == analysis_id,
                AnalysisRecord.status == JobStatus.RUNNING.value,
                AnalysisRecord.execution_token == token,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if record is None:
            raise LostExecutionClaim("Execution claim is no longer active")
        return record

    def finish(
        self, analysis_id: UUID, token: str, result: dict[str, object], artifacts: dict[str, tuple[str, str]]
    ) -> None:
        record = self.owned(analysis_id, token)
        for name, (media_type, key) in artifacts.items():
            self.session.add(
                ArtifactRecord(
                    analysis_id=analysis_id,
                    name=name,
                    media_type=media_type,
                    storage_key=key,
                    artifact_type="generated",
                )
            )
        self.mark_succeeded(record, result)

    def fail_attempt(self, analysis_id: UUID, token: str, code: str, message: str, retry: bool) -> None:
        record = self.owned(analysis_id, token)
        record.status = JobStatus.QUEUED.value if retry else JobStatus.FAILED.value
        record.error_code = code
        record.error_message = message
        record.execution_token = None
        record.lease_expires_at = None
        record.completed_at = None if retry else datetime.now(UTC)
        self.session.commit()

    def mark_succeeded(self, record: AnalysisRecord, result: dict[str, object]) -> None:
        if record.status != JobStatus.RUNNING.value:
            raise LostExecutionClaim("Only a running analysis can complete")
        record.status = JobStatus.SUCCEEDED.value
        record.summary = json_safe(result.get("summary"))
        record.result = json_safe(result)
        record.completed_at = datetime.now(UTC)
        record.error_code = None
        record.error_message = None
        record.execution_token = None
        record.lease_expires_at = None
        self.session.commit()

    def mark_failed(self, record: AnalysisRecord, code: str, message: str) -> None:
        # An ambiguous queue acknowledgement must not overwrite a worker's claim or result.
        self.session.execute(
            update(AnalysisRecord)
            .where(AnalysisRecord.id == record.id, AnalysisRecord.status == JobStatus.QUEUED.value)
            .values(
                status=JobStatus.FAILED.value,
                error_code=code,
                error_message=message[:1000],
                completed_at=datetime.now(UTC),
            )
            .execution_options(synchronize_session=False)
        )
        self.session.commit()
        self.session.refresh(record)

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
