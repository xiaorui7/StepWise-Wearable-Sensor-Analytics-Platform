"""Operator-run reconciliation; never silently resubmit a lost job."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import cast

from redis import Redis
from rq.exceptions import NoSuchJobError
from rq.job import Job
from sqlalchemy import CursorResult, select, update
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from stepwise.core.models import JobStatus
from stepwise.db import SessionLocal
from stepwise.db_models import AnalysisRecord
from stepwise.settings import get_settings


def reconcile(
    session_factory: Callable[[], Session],
    rq_status: Callable[[str], str | None],
    grace_s: int = 60,
    now: datetime | None = None,
    job_timeout_s: int = 300,
) -> dict[str, int]:
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(seconds=grace_s)
    counts = {"failed": 0, "unchanged": 0, "queue_unavailable": 0}
    with session_factory() as session:
        records = list(
            session.scalars(
                select(AnalysisRecord).where(
                    AnalysisRecord.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value])
                )
            )
        )
        for record in records:
            guard: ColumnElement[bool]
            expected = record.status
            token = record.execution_token
            if expected == JobStatus.RUNNING.value:
                expired = record.lease_expires_at
                if expired is None:
                    expired = (record.started_at or record.created_at).replace(tzinfo=UTC) + timedelta(
                        seconds=job_timeout_s + grace_s
                    )
                    guard = AnalysisRecord.lease_expires_at.is_(None)
                else:
                    guard = AnalysisRecord.lease_expires_at <= now
                if expired.replace(tzinfo=UTC) > now:
                    counts["unchanged"] += 1
                    continue
                code = "EXECUTION_INTERRUPTED"
            else:
                age = record.started_at or record.created_at
                if age.replace(tzinfo=UTC) > cutoff:
                    counts["unchanged"] += 1
                    continue
                try:
                    status = rq_status(str(record.id))
                except Exception:
                    counts["queue_unavailable"] += 1
                    continue
                if status in {"queued", "started", "scheduled", "deferred"}:
                    counts["unchanged"] += 1
                    continue
                code = "QUEUE_JOB_LOST" if status is None else "QUEUE_STATE_MISMATCH"
                guard = AnalysisRecord.status == expected
            result = session.execute(
                update(AnalysisRecord)
                .where(
                    AnalysisRecord.id == record.id,
                    AnalysisRecord.status == expected,
                    AnalysisRecord.execution_token == token,
                    guard,
                )
                .values(
                    status=JobStatus.FAILED.value,
                    error_code=code,
                    error_message="Processing was interrupted; submit again with a new idempotency key",
                    completed_at=now,
                    execution_token=None,
                    lease_expires_at=None,
                )
                .execution_options(synchronize_session=False)
            )
            session.commit()
            counts["failed"] += int(cast(CursorResult[object], result).rowcount or 0)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Reconcile expired executions and lost queued jobs")
    parser.parse_args()
    settings = get_settings()
    connection = Redis.from_url(settings.redis_url, socket_timeout=5, socket_connect_timeout=3)

    def status(job_id: str) -> str | None:
        try:
            return Job.fetch(job_id, connection=connection).get_status().value
        except NoSuchJobError:
            return None

    print(
        json.dumps(
            reconcile(SessionLocal, status, settings.recovery_grace_s, job_timeout_s=settings.job_timeout_s)
        )
    )


if __name__ == "__main__":
    main()
