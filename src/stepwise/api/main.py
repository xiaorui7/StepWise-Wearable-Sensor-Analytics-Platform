from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import redis
import structlog
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from stepwise.api.limits import BodyLimitMiddleware
from stepwise.api.schemas import (
    AnalysisCreateConfig,
    AnalysisCreated,
    AnalysisListResponse,
    AnalysisResultResponse,
    AnalysisStatusResponse,
    TextAnalysisCreate,
)
from stepwise.core.models import AnalysisConfig, JobStatus, SensorMapping
from stepwise.core.parser import SensorDataError, parse_sensor_text
from stepwise.db import get_db
from stepwise.jobs import JobQueue, RQJobQueue
from stepwise.logging import configure_logging
from stepwise.repository import AnalysisRepository, IdempotencyConflict
from stepwise.settings import Settings, get_settings
from stepwise.storage import ArtifactNotFoundError, StorageBackend, create_store

logger = structlog.get_logger()


def _status_response(record: Any) -> AnalysisStatusResponse:
    return AnalysisStatusResponse(
        analysis_id=record.id,
        status=JobStatus(record.status),
        created_at=record.created_at,
        started_at=record.started_at,
        completed_at=record.completed_at,
        input_metadata=record.input_metadata,
        error_code=record.error_code,
        error_message=record.error_message,
        attempts=record.attempts,
    )


async def _read_upload(upload: UploadFile, maximum_bytes: int) -> bytes:
    if upload.filename and not upload.filename.lower().endswith(".txt"):
        raise HTTPException(status_code=415, detail="Sensor uploads must use the .txt format")
    if upload.content_type not in {None, "text/plain", "application/octet-stream"}:
        raise HTTPException(status_code=415, detail="Only plain-text StepWise sensor files are accepted")
    data = await upload.read(maximum_bytes + 1)
    if len(data) > maximum_bytes:
        raise HTTPException(status_code=413, detail="Uploaded file exceeds the configured size limit")
    try:
        text_value = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail="Sensor files must be UTF-8 text") from exc
    if not text_value.strip():
        raise HTTPException(status_code=422, detail="Uploaded sensor file is empty")
    return data


def _create_analysis_job(
    *,
    session: Session,
    store: StorageBackend,
    queue: JobQueue,
    config: AnalysisConfig,
    walking_data: bytes,
    standing_data: bytes | None,
    metadata: dict[str, object],
    idempotency_key: str | None = None,
) -> AnalysisCreated:
    if idempotency_key is not None and (
        not 1 <= len(idempotency_key) <= 128 or not all(33 <= ord(c) <= 126 for c in idempotency_key)
    ):
        raise HTTPException(
            status_code=422, detail="Idempotency-Key must contain 1-128 printable ASCII characters"
        )
    # Length-prefixed parts prevent ambiguous concatenations. Names are not request identity.
    digest = hashlib.sha256()
    for part in (config.model_dump_json().encode(), walking_data, standing_data or b""):
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    parse_sensor_text(walking_data.decode("utf-8"))
    if standing_data is not None:
        parse_sensor_text(standing_data.decode("utf-8"))
    repository = AnalysisRepository(session)
    key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest() if idempotency_key else None
    try:
        record, created = repository.create_submission(config, metadata, key_hash, digest.hexdigest())
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    if not created:
        return AnalysisCreated(analysis_id=record.id, status=JobStatus(record.status))

    try:
        walking_key = store.put(record.id, "walking-input.txt", walking_data)
        repository.add_artifact(record, "walking-input.txt", "text/plain", walking_key, "input")
        if standing_data is not None:
            standing_key = store.put(record.id, "standing-input.txt", standing_data)
            repository.add_artifact(record, "standing-input.txt", "text/plain", standing_key, "input")
    except OSError:
        session.rollback()
        repository.mark_failed(record, "STORAGE_UNAVAILABLE", "Input storage is unavailable")
        raise HTTPException(status_code=503, detail="Input storage is unavailable") from None
    try:
        queue.enqueue_analysis(record.id)
    except Exception as exc:
        repository.mark_failed(record, "QUEUE_UNAVAILABLE", "The analysis queue is unavailable")
        raise HTTPException(status_code=503, detail="The analysis queue is unavailable") from exc

    logger.info("analysis_state", analysis_id=str(record.id), state="QUEUED")
    return AnalysisCreated(analysis_id=record.id, status=JobStatus.QUEUED)


def create_app(*, settings: Settings | None = None, queue: JobQueue | None = None) -> FastAPI:
    app_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        configure_logging(app_settings.log_level)
        yield

    app = FastAPI(
        title="StepWise API",
        version="1.0.0",
        description="Asynchronous API for non-diagnostic wearable-sensor analysis.",
        lifespan=lifespan,
    )
    app.state.settings = app_settings
    app.state.queue = queue
    app.state.store = create_store(app_settings)
    app.add_middleware(BodyLimitMiddleware, maximum_bytes=2 * app_settings.max_upload_bytes + 262144)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-Request-ID", "Idempotency-Key"],
        expose_headers=["X-Request-ID"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next: Any) -> Any:
        supplied_id = request.headers.get("X-Request-ID", "")
        request_id = (
            supplied_id
            if 1 <= len(supplied_id) <= 128 and all(33 <= ord(c) <= 126 for c in supplied_id)
            else str(uuid.uuid4())
        )
        request.state.request_id = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        content_length = request.headers.get("content-length")
        if content_length and (
            not content_length.isdigit() or int(content_length) > 2 * app_settings.max_upload_bytes + 262144
        ):
            return JSONResponse(
                status_code=413, content={"detail": "Request body exceeds the configured limit"}
            )
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "http_request",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        return response

    @app.exception_handler(SensorDataError)
    async def sensor_error(_: Request, exc: SensorDataError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"error": "INVALID_SENSOR_DATA", "message": str(exc)})

    @app.exception_handler(Exception)
    async def unexpected_error(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_api_error", error_type=type(exc).__name__)
        return JSONResponse(
            status_code=500,
            content={"error": "INTERNAL_ERROR", "message": "The request could not be completed safely"},
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    def ready(session: Session = Depends(get_db)) -> dict[str, str]:
        try:
            session.execute(text("SELECT 1"))
            redis.Redis.from_url(app_settings.redis_url, socket_connect_timeout=2, socket_timeout=2).ping()
            app.state.store.check()
        except Exception as exc:
            raise HTTPException(status_code=503, detail="A required dependency is unavailable") from exc
        return {"status": "ready"}

    @app.post("/api/v1/analyses", response_model=AnalysisCreated, status_code=status.HTTP_202_ACCEPTED)
    async def create_analysis(
        request: Request,
        walking_file: UploadFile = File(...),
        standing_file: UploadFile | None = File(default=None),
        config_json: str = Form(default="{}"),
        session: Session = Depends(get_db),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> AnalysisCreated:
        try:
            request_config = AnalysisCreateConfig.model_validate(json.loads(config_json))
        except (json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="Invalid analysis configuration") from exc
        walking_data = await _read_upload(walking_file, app_settings.max_upload_bytes)
        standing_data = (
            await _read_upload(standing_file, app_settings.max_upload_bytes) if standing_file else None
        )
        config = AnalysisConfig(
            sensor_mapping=request_config.sensor_mapping, smooth_window=request_config.smooth_window
        )
        job_queue = app.state.queue or RQJobQueue(app_settings.redis_url, app_settings)
        return await run_in_threadpool(
            _create_analysis_job,
            session=session,
            store=app.state.store,
            queue=job_queue,
            config=config,
            walking_data=walking_data,
            standing_data=standing_data,
            metadata={
                "walking_size_bytes": len(walking_data),
                "standing_calibration_supplied": standing_data is not None,
                "synthetic_demo": bool(walking_file.filename and ".synthetic." in walking_file.filename),
                "request_id": request.state.request_id,
            },
            idempotency_key=idempotency_key,
        )

    @app.post("/api/v1/analyses/text", response_model=AnalysisCreated, status_code=status.HTTP_202_ACCEPTED)
    def create_text_analysis(
        payload: TextAnalysisCreate,
        request: Request,
        session: Session = Depends(get_db),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> AnalysisCreated:
        walking_data = payload.walking_text.encode("utf-8")
        standing_data = payload.standing_text.encode("utf-8") if payload.standing_text else None
        if len(walking_data) > app_settings.max_upload_bytes or (
            standing_data is not None and len(standing_data) > app_settings.max_upload_bytes
        ):
            raise HTTPException(status_code=413, detail="Sensor text exceeds the configured size limit")
        try:
            mapping = SensorMapping(
                heel=(payload.heel,),
                arch=(payload.arch,),
                medial_forefoot=(payload.medial_forefoot,),
                lateral_forefoot=(payload.lateral_forefoot,),
                pitch_eversion_sign=payload.pitch_eversion_sign,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        job_queue = app.state.queue or RQJobQueue(app_settings.redis_url, app_settings)
        return _create_analysis_job(
            session=session,
            store=app.state.store,
            queue=job_queue,
            config=AnalysisConfig(sensor_mapping=mapping),
            walking_data=walking_data,
            standing_data=standing_data,
            metadata={
                "walking_size_bytes": len(walking_data),
                "standing_calibration_supplied": standing_data is not None,
                "client": "wechat-miniprogram",
                "request_id": request.state.request_id,
            },
            idempotency_key=idempotency_key,
        )

    @app.get("/api/v1/analyses/{analysis_id}", response_model=AnalysisStatusResponse)
    def get_analysis(analysis_id: UUID, session: Session = Depends(get_db)) -> AnalysisStatusResponse:
        record = AnalysisRepository(session).get(analysis_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Analysis not found")
        return _status_response(record)

    @app.get("/api/v1/analyses/{analysis_id}/result", response_model=AnalysisResultResponse)
    def get_result(analysis_id: UUID, session: Session = Depends(get_db)) -> AnalysisResultResponse:
        record = AnalysisRepository(session).get(analysis_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Analysis not found")
        if record.status != JobStatus.SUCCEEDED.value or record.result is None:
            raise HTTPException(
                status_code=409, detail=f"Analysis result is not available while status is {record.status}"
            )
        generated = [artifact for artifact in record.artifacts if artifact.artifact_type == "generated"]
        urls = {
            artifact.name: f"/api/v1/analyses/{record.id}/artifacts/{artifact.name}" for artifact in generated
        }
        return AnalysisResultResponse(
            analysis_id=record.id,
            status=JobStatus.SUCCEEDED,
            result=record.result,
            artifact_urls=urls,
        )

    @app.get("/api/v1/analyses", response_model=AnalysisListResponse)
    def list_analyses(
        page: int = 1, page_size: int = 20, session: Session = Depends(get_db)
    ) -> AnalysisListResponse:
        if page < 1 or not 1 <= page_size <= 100:
            raise HTTPException(status_code=422, detail="page must be positive and page_size must be 1-100")
        records, total = AnalysisRepository(session).list(page, page_size)
        return AnalysisListResponse(
            items=[_status_response(item) for item in records],
            page=page,
            page_size=page_size,
            total=total,
        )

    @app.get("/api/v1/analyses/{analysis_id}/artifacts/{artifact_name}")
    def get_artifact(
        analysis_id: UUID, artifact_name: str, session: Session = Depends(get_db)
    ) -> Response:
        artifact = AnalysisRepository(session).find_artifact(analysis_id, artifact_name)
        record = AnalysisRepository(session).get(analysis_id)
        if record is not None and record.status != JobStatus.SUCCEEDED.value:
            raise HTTPException(status_code=409, detail="Artifacts are unavailable until analysis succeeds")
        if artifact is None or artifact.artifact_type != "generated":
            raise HTTPException(status_code=404, detail="Artifact not found")
        try:
            content = app.state.store.read(artifact.storage_key)
        except ArtifactNotFoundError:
            raise HTTPException(status_code=404, detail="Artifact content not found") from None
        except OSError:
            raise HTTPException(status_code=503, detail="Artifact storage is unavailable") from None
        return Response(content, media_type=artifact.media_type)

    return app


app = create_app()
