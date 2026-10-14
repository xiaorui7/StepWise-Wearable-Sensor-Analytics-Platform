from __future__ import annotations

import errno
import time
from collections.abc import Callable
from typing import Any, Protocol
from uuid import UUID

import redis
import structlog
from rq import Queue, Retry
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from stepwise.core.json_utils import json_safe
from stepwise.core.models import AnalysisConfig
from stepwise.db import SessionLocal
from stepwise.pipeline import run_analysis
from stepwise.repository import AnalysisRepository, LostExecutionClaim
from stepwise.settings import Settings, get_settings
from stepwise.storage import ArtifactStore

logger = structlog.get_logger()


class RetryableAnalysisError(RuntimeError):
    """Safe RQ-facing exception; PostgreSQL holds the retry decision."""


class PermanentAnalysisError(RuntimeError):
    pass


def is_retryable(exc: Exception) -> bool:
    if isinstance(exc, OperationalError):
        sqlstate = getattr(exc.orig, "sqlstate", "") or ""
        return bool(
            exc.connection_invalidated
            or sqlstate.startswith(("08", "40"))
            or sqlstate in {"57P01", "57P02", "57P03"}
            or getattr(exc.orig, "sqlite_errorcode", None) in {5, 6}
        )
    return isinstance(
        exc,
        (
            redis.exceptions.ConnectionError,
            redis.exceptions.TimeoutError,
            TimeoutError,
            ConnectionError,
        ),
    ) or (
        isinstance(exc, OSError)
        and exc.errno in {errno.EAGAIN, errno.EBUSY, errno.ETIMEDOUT, errno.ECONNRESET}
    )


class JobQueue(Protocol):
    def enqueue_analysis(self, analysis_id: UUID) -> None: ...


class RQJobQueue:
    def __init__(self, redis_url: str | None = None, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        connection = redis.Redis.from_url(
            redis_url or self.settings.redis_url, socket_connect_timeout=3, socket_timeout=5
        )
        self.queue = Queue("stepwise", connection=connection, default_timeout=self.settings.job_timeout_s)

    def enqueue_analysis(self, analysis_id: UUID) -> None:
        retry = (
            Retry(max=self.settings.max_attempts - 1, interval=self.settings.retry_delay_s)
            if self.settings.max_attempts > 1
            else None
        )
        self.queue.enqueue(
            process_analysis_job,
            str(analysis_id),
            job_id=str(analysis_id),
            retry=retry,
            result_ttl=86400,
            failure_ttl=604800,
        )


def execute_analysis_job(
    analysis_id: str,
    session_factory: Callable[[], Session],
    store: ArtifactStore,
    settings: Settings | None = None,
) -> None:
    settings = settings or get_settings()
    started = time.perf_counter()
    token: str | None = None
    attempt = 0
    try:
        with session_factory() as session:
            repository = AnalysisRepository(session)
            record = repository.get(UUID(analysis_id))
            if record is None:
                raise PermanentAnalysisError("Analysis record does not exist")
            if not repository.mark_running(
                record, settings.job_timeout_s + settings.recovery_grace_s, settings.max_attempts
            ):
                if record.status == "FAILED":
                    raise PermanentAnalysisError("Analysis already failed")
                if record.status == "QUEUED":
                    repository.mark_failed(record, "ATTEMPTS_EXHAUSTED", "The retry budget is exhausted")
                    raise PermanentAnalysisError("The retry budget is exhausted")
                logger.info(
                    "analysis_duplicate_execution_skipped", analysis_id=analysis_id, state=record.status
                )
                return
            token = record.execution_token
            assert token is not None
            attempt = record.attempts
            metadata = record.input_metadata
            config = AnalysisConfig.model_validate(record.sensor_config)
            walking = repository.find_artifact(record.id, "walking-input.txt")
            standing = repository.find_artifact(record.id, "standing-input.txt")
            if walking is None:
                raise ValueError("Walking input is missing")
            walking_key = walking.storage_key
            standing_key = standing.storage_key if standing else None
        logger.info(
            "analysis_state",
            analysis_id=analysis_id,
            state="RUNNING",
            attempt=attempt,
            request_id=metadata.get("request_id"),
        )
        bundle = run_analysis(
            store.read(walking_key).decode("utf-8"),
            store.read(standing_key).decode("utf-8") if standing_key else None,
            config,
        )
        if set(bundle.artifacts) != {artifact.name for artifact in bundle.result.artifacts} or any(
            not content for _, content in bundle.artifacts.values()
        ):
            raise RuntimeError("Analysis did not generate its declared artifact set")
        keys = store.publish(UUID(analysis_id), token, bundle.artifacts)
        with session_factory() as session:
            AnalysisRepository(session).finish(UUID(analysis_id), token, json_safe(bundle.result), keys)
        logger.info(
            "analysis_state",
            analysis_id=analysis_id,
            state="SUCCEEDED",
            attempt=attempt,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )
    except LostExecutionClaim:
        logger.warning("analysis_claim_lost", analysis_id=analysis_id, attempt=attempt)
        raise PermanentAnalysisError("Execution ownership was revoked") from None
    except Exception as exc:
        retry = is_retryable(exc) and attempt < settings.max_attempts
        code = "TRANSIENT_FAILURE" if retry else "ANALYSIS_FAILED"
        if isinstance(exc, (ValueError, UnicodeError)):
            code = "INVALID_SENSOR_DATA"
        message = (
            "Temporary processing failure; retry scheduled"
            if retry
            else "The analysis could not be completed safely"
        )
        if code == "INVALID_SENSOR_DATA":
            message = "The sensor recording is structurally invalid"
        if token:
            try:
                with session_factory() as session:
                    AnalysisRepository(session).fail_attempt(UUID(analysis_id), token, code, message, retry)
            except LostExecutionClaim:
                with session_factory() as session:
                    current = AnalysisRepository(session).get(UUID(analysis_id))
                    if current is not None and current.status == "SUCCEEDED":
                        return
                raise PermanentAnalysisError("Execution ownership was revoked") from None
        logger.warning(
            "analysis_failed",
            analysis_id=analysis_id,
            attempt=attempt,
            retry=retry,
            failure_category=code,
            error_type=type(exc).__name__,
        )
        if retry:
            raise RetryableAnalysisError(message) from None
        raise PermanentAnalysisError(message) from None


def process_analysis_job(analysis_id: str) -> None:
    settings = get_settings()
    try:
        execute_analysis_job(analysis_id, SessionLocal, ArtifactStore(settings.artifact_root), settings)
    except PermanentAnalysisError:
        # RQ retries every raised exception unless its remaining retry budget is cleared.
        from rq import get_current_job

        job = get_current_job()
        if job is not None:
            job.retries_left = 0
            job.save()
        raise


def handle_workhorse_killed(job: Any, retpid: int, ret_val: int, rusage: Any) -> None:
    """Unexpected process exits are not classified as transient processing failures."""
    job.retries_left = 0
    job.save()
    with SessionLocal() as session:
        repo = AnalysisRepository(session)
        record = repo.get(UUID(job.id))
        if record is not None and record.status == "RUNNING" and record.execution_token:
            repo.fail_attempt(
                record.id,
                record.execution_token,
                "EXECUTION_INTERRUPTED",
                "The worker process was interrupted",
                False,
            )
    logger.warning("workhorse_killed", analysis_id=job.id, exit_status=ret_val)
