from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import redis
import structlog
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from stepwise.api.schemas import (
    AnalysisCreateConfig,
    AnalysisCreated,
    AnalysisListResponse,
    AnalysisResultResponse,
    AnalysisStatusResponse,
    TextAnalysisCreate,
)
from stepwise.core.models import AnalysisConfig, JobStatus, SensorMapping
from stepwise.core.parser import SensorDataError
from stepwise.db import get_db
from stepwise.jobs import JobQueue, RQJobQueue
from stepwise.logging import configure_logging
from stepwise.repository import AnalysisRepository
from stepwise.settings import Settings, get_settings
from stepwise.storage import ArtifactStore

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
    )


async def _read_upload(upload: UploadFile, maximum_bytes: int) -> bytes:
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
    store: ArtifactStore,
    queue: JobQueue,
    config: AnalysisConfig,
    walking_data: bytes,
    standing_data: bytes | None,
    metadata: dict[str, object],
) -> AnalysisCreated:
    repository = AnalysisRepository(session)
    record = repository.create(config, metadata)

    walking_key = store.put(record.id, "walking-input.txt", walking_data)
    repository.add_artifact(record, "walking-input.txt", "text/plain", walking_key, "input")
    if standing_data is not None:
        standing_key = store.put(record.id, "standing-input.txt", standing_data)
        repository.add_artifact(record, "standing-input.txt", "text/plain", standing_key, "input")

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
    app.state.store = ArtifactStore(app_settings.artifact_root)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-Request-ID"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next: Any) -> Any:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
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
            redis.Redis.from_url(app_settings.redis_url).ping()
        except Exception as exc:
            raise HTTPException(status_code=503, detail="A required dependency is unavailable") from exc
        return {"status": "ready"}

    @app.post("/api/v1/analyses", response_model=AnalysisCreated, status_code=status.HTTP_202_ACCEPTED)
    async def create_analysis(
        walking_file: UploadFile = File(...),
        standing_file: UploadFile | None = File(default=None),
        config_json: str = Form(default="{}"),
        session: Session = Depends(get_db),
    ) -> AnalysisCreated:
        try:
            request_config = AnalysisCreateConfig.model_validate(json.loads(config_json))
        except (json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"Invalid analysis configuration: {exc}") from exc
        walking_data = await _read_upload(walking_file, app_settings.max_upload_bytes)
        standing_data = (
            await _read_upload(standing_file, app_settings.max_upload_bytes) if standing_file else None
        )
        config = AnalysisConfig(
            sensor_mapping=request_config.sensor_mapping, smooth_window=request_config.smooth_window
        )
        job_queue = app.state.queue or RQJobQueue(app_settings.redis_url)
        return _create_analysis_job(
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
            },
        )

    @app.post("/api/v1/analyses/text", response_model=AnalysisCreated, status_code=status.HTTP_202_ACCEPTED)
    def create_text_analysis(
        payload: TextAnalysisCreate,
        session: Session = Depends(get_db),
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
        job_queue = app.state.queue or RQJobQueue(app_settings.redis_url)
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
            },
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
    ) -> FileResponse:
        artifact = AnalysisRepository(session).find_artifact(analysis_id, artifact_name)
        if artifact is None or artifact.artifact_type != "generated":
            raise HTTPException(status_code=404, detail="Artifact not found")
        path = app.state.store.resolve(artifact.storage_key)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Artifact content not found")
        return FileResponse(path, media_type=artifact.media_type)

    return app


app = create_app()
