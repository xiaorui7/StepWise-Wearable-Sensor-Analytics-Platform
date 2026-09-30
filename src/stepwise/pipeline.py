from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from io import StringIO
from typing import Any

from stepwise.core.calibration import standing_calibration
from stepwise.core.contact import adaptive_threshold, hysteresis_contact
from stepwise.core.features import (
    add_calibrated_metrics,
    add_signal_features,
    aggregate_metrics,
    extract_stance_features,
)
from stepwise.core.json_utils import json_safe
from stepwise.core.models import AnalysisConfig, AnalysisResult, ArtifactInfo
from stepwise.core.parser import parse_sensor_text
from stepwise.core.quality import summarize_quality
from stepwise.core.rules import evaluate_rules
from stepwise.core.segmentation import segment_stances
from stepwise.reports.charts import orientation_chart, pressure_chart
from stepwise.reports.generator import generate_user_report


@dataclass(frozen=True)
class AnalysisBundle:
    result: AnalysisResult
    artifacts: dict[str, tuple[str, bytes]]


def run_analysis(
    walking_text: str,
    standing_text: str | None,
    config: AnalysisConfig,
) -> AnalysisBundle:
    processed = add_signal_features(
        parse_sensor_text(walking_text),
        config.sensor_mapping,
        config.smooth_window,
    )
    enter, exit_ = adaptive_threshold(
        processed["TotalPressure"],
        config.min_threshold_n,
        config.threshold_ratio,
    )
    contact = hysteresis_contact(processed["TotalPressure"], enter, exit_)
    processed["FootContact"] = contact
    intervals = segment_stances(contact, processed["Time_s"], config.min_stance_s)
    steps = extract_stance_features(processed, intervals)
    summary = summarize_quality(processed, steps, enter, exit_)

    calibration: dict[str, Any] | None = None
    if standing_text:
        standing = add_signal_features(
            parse_sensor_text(standing_text),
            config.sensor_mapping,
            config.smooth_window,
        )
        calibration = standing_calibration(standing)

    metrics = add_calibrated_metrics(aggregate_metrics(steps, processed), calibration)
    cards = evaluate_rules(
        metrics,
        summary,
        has_standing_calibration=calibration is not None,
        pitch_eversion_sign=config.sensor_mapping.pitch_eversion_sign,
    )
    result = AnalysisResult(
        generated_at=datetime.now(UTC),
        summary=json_safe(summary),
        metrics=json_safe(metrics),
        stance_intervals=intervals,
        screening_cards=cards,
        artifacts=[
            ArtifactInfo(name="report.html", media_type="text/html"),
            ArtifactInfo(name="pressure.png", media_type="image/png"),
            ArtifactInfo(name="orientation.png", media_type="image/png"),
            ArtifactInfo(name="steps.csv", media_type="text/csv"),
            ArtifactInfo(name="result.json", media_type="application/json"),
        ],
    )

    steps_buffer = StringIO()
    steps.to_csv(steps_buffer, index=False)
    artifacts = {
        "report.html": ("text/html", generate_user_report(result).encode("utf-8")),
        "pressure.png": ("image/png", pressure_chart(processed, intervals)),
        "orientation.png": ("image/png", orientation_chart(processed)),
        "steps.csv": ("text/csv", steps_buffer.getvalue().encode("utf-8")),
        "result.json": (
            "application/json",
            json.dumps(json_safe(result), indent=2, allow_nan=False).encode("utf-8"),
        ),
    }
    return AnalysisBundle(result=result, artifacts=artifacts)
