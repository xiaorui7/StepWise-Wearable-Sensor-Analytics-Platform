from __future__ import annotations

from typing import Any

import pandas as pd


def summarize_quality(
    frame: pd.DataFrame,
    steps: pd.DataFrame,
    enter_threshold: float,
    exit_threshold: float,
) -> dict[str, Any]:
    duration = float(frame["Time_s"].iloc[-1] - frame["Time_s"].iloc[0])
    delta = frame["Time_s"].diff().dropna()
    sample_rate = float((len(frame) - 1) / duration) if duration > 0 and len(frame) > 1 else None
    duplicate_count = int((delta == 0).sum())
    warnings: list[str] = []
    if sample_rate is not None and sample_rate < 50:
        warnings.append("Actual sample rate is below 50 Hz; event timing may be unreliable.")
    if len(steps) < 6:
        warnings.append("Too few detected stance phases for robust screening.")
    zero_channels = frame[["P1", "P2", "P3", "P4"]].max()
    if (zero_channels == 0).any():
        warnings.append(
            "Pressure channels always zero: " + ", ".join(zero_channels[zero_channels == 0].index)
        )
    if duplicate_count:
        warnings.append(f"{duplicate_count} repeated timestamps found; rate uses total duration.")
    if sample_rate is None or sample_rate < 20 or len(steps) < 4:
        quality = "Low"
    elif sample_rate < 50 or len(steps) < 8:
        quality = "Medium"
    else:
        quality = "High"
    return {
        "samples": int(len(frame)),
        "duration_s": duration,
        "estimated_sample_rate_hz": sample_rate,
        "duplicate_timestamp_count": duplicate_count,
        "detected_steps_single_foot": int(len(steps)),
        "contact_enter_threshold_n": enter_threshold,
        "contact_exit_threshold_n": exit_threshold,
        "mean_stance_time_s": float(steps["StanceTime_s"].mean()) if not steps.empty else None,
        "mean_stride_time_s": float(steps["StrideTime_s"].mean()) if not steps.empty else None,
        "data_quality": quality,
        "warnings": warnings,
    }
