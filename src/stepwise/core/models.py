from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PRESSURE_CHANNELS = {"P1", "P2", "P3", "P4"}


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class SensorMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    heel: tuple[str, ...] = ("P2",)
    arch: tuple[str, ...] = ("P3",)
    medial_forefoot: tuple[str, ...] = ("P4",)
    lateral_forefoot: tuple[str, ...] = ("P1",)
    toe: tuple[str, ...] = ()
    pitch_eversion_sign: str = "positive"

    @field_validator("heel", "arch", "medial_forefoot", "lateral_forefoot", "toe")
    @classmethod
    def validate_channels(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(channel not in PRESSURE_CHANNELS for channel in value):
            raise ValueError("Sensor roles must use P1, P2, P3, or P4")
        return value

    @field_validator("pitch_eversion_sign")
    @classmethod
    def validate_pitch_sign(cls, value: str) -> str:
        if value not in {"positive", "negative"}:
            raise ValueError("pitch_eversion_sign must be positive or negative")
        return value

    @model_validator(mode="after")
    def validate_unique_mapping(self) -> SensorMapping:
        configured = self.heel + self.arch + self.medial_forefoot + self.lateral_forefoot + self.toe
        if len(configured) != len(set(configured)):
            raise ValueError("A pressure channel cannot be assigned to more than one sensor role")
        if not self.heel or not self.medial_forefoot or not self.lateral_forefoot:
            raise ValueError("Heel, medial forefoot, and lateral forefoot roles are required")
        return self

    @property
    def rear(self) -> tuple[str, ...]:
        return self.heel

    @property
    def front(self) -> tuple[str, ...]:
        return self.medial_forefoot + self.lateral_forefoot + self.toe


class AnalysisConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sensor_mapping: SensorMapping = Field(default_factory=SensorMapping)
    smooth_window: int = Field(default=3, ge=1, le=31)
    min_threshold_n: float = Field(default=5.0, gt=0, le=500)
    threshold_ratio: float = Field(default=0.08, gt=0, lt=1)
    min_stance_s: float = Field(default=0.08, gt=0, le=2)


class StanceInterval(BaseModel):
    start_index: int
    end_index: int


class ScreeningCard(BaseModel):
    title: str
    level: str
    evidence: list[str]
    interpretation: str
    action: str
    limitation: str


class ArtifactInfo(BaseModel):
    name: str
    media_type: str


class AnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    generated_at: datetime
    summary: dict[str, Any]
    metrics: dict[str, Any]
    stance_intervals: list[StanceInterval]
    screening_cards: list[ScreeningCard]
    artifacts: list[ArtifactInfo] = Field(default_factory=list)
    disclaimer: str = (
        "StepWise is a non-diagnostic engineering screening prototype and is not clinically validated."
    )
