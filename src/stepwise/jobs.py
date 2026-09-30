from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol
from uuid import UUID

import redis
import structlog
from rq import Queue
from sqlalchemy.orm import Session

from stepwise.core.json_utils import json_safe
from stepwise.core.models import AnalysisConfig
from stepwise.db import SessionLocal
from stepwise.pipeline import run_analysis
from stepwise.repository import AnalysisRepository
from stepwise.settings import get_settings
from stepwise.storage import ArtifactStore

logger = structlog.get_logger()


class JobQueue(Protocol):
    def enqueue_analysis(self, analysis_id: UUID) -> None: ...


class RQJobQueue:
    def __init__(self, redis_url: str | None = None) -> None:
        connection = redis.Redis.from_url(redis_url or get_settings().redis_url)
        self.queue = Queue("stepwise", connection=connection, default_timeout=300)

    def enqueue_analysis(self, analysis_id: UUID) -> None:
        self.queue.enqueue(process_analysis_job, str(analysis_id), job_id=str(analysis_id))


def execute_analysis_job(
    analysis_id: str,
    session_factory: Callable[[], Session],
    store: ArtifactStore,
) -> None:
    started = time.perf_counter()
    with session_factory() as session:
        repository = AnalysisRepository(session)
        record = repository.get(UUID(analysis_id))
        if record is None:
            logger.error("analysis_missing", analysis_id=analysis_id)
            return
        repository.mark_running(record)
        logger.info("analysis_state", analysis_id=analysis_id, state="RUNNING")
        try:
            walking_artifact = repository.find_artifact(record.id, "walking-input.txt")
            standing_artifact = repository.find_artifact(record.id, "standing-input.txt")
            if walking_artifact is None:
                raise ValueError("Walking input artifact is missing")
            walking_text = store.read(walking_artifact.storage_key).decode("utf-8", errors="replace")
            standing_text = (
                store.read(standing_artifact.storage_key).decode("utf-8", errors="replace")
                if standing_artifact
                else None
            )
            bundle = run_analysis(
                walking_text,
                standing_text,
                AnalysisConfig.model_validate(record.sensor_config),
            )
            for name, (media_type, content) in bundle.artifacts.items():
                key = store.put(record.id, name, content)
                repository.add_artifact(record, name, media_type, key)
            repository.mark_succeeded(record, json_safe(bundle.result))
            logger.info(
                "analysis_state",
                analysis_id=analysis_id,
                state="SUCCEEDED",
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
        except (ValueError, UnicodeError) as exc:
            repository.mark_failed(record, "INVALID_SENSOR_DATA", str(exc))
            logger.warning("analysis_failed", analysis_id=analysis_id, failure_category="invalid_input")
        except Exception:
            repository.mark_failed(record, "ANALYSIS_FAILED", "The analysis could not be completed safely")
            logger.exception("analysis_failed", analysis_id=analysis_id, failure_category="internal")


def process_analysis_job(analysis_id: str) -> None:
    settings = get_settings()
    execute_analysis_job(analysis_id, SessionLocal, ArtifactStore(settings.artifact_root))
