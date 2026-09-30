from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from stepwise.core.models import JobStatus, SensorMapping


class AnalysisCreateConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sensor_mapping: SensorMapping = Field(default_factory=SensorMapping)
    smooth_window: int = Field(default=3, ge=1, le=31)


class TextAnalysisCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    walking_text: str = Field(alias="walkingText", min_length=1)
    standing_text: str | None = Field(default=None, alias="standingText")
    heel: str = "P2"
    arch: str = "P3"
    medial_forefoot: str = Field(default="P4", alias="medialForefoot")
    lateral_forefoot: str = Field(default="P1", alias="lateralForefoot")
    pitch_eversion_sign: str = Field(default="positive", alias="pitchEversionSign")


class AnalysisCreated(BaseModel):
    analysis_id: UUID
    status: JobStatus


class AnalysisStatusResponse(BaseModel):
    analysis_id: UUID
    status: JobStatus
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    input_metadata: dict[str, Any]
    error_code: str | None
    error_message: str | None


class AnalysisResultResponse(BaseModel):
    analysis_id: UUID
    status: JobStatus
    result: dict[str, Any]
    artifact_urls: dict[str, str]


class AnalysisListResponse(BaseModel):
    items: list[AnalysisStatusResponse]
    page: int
    page_size: int
    total: int
