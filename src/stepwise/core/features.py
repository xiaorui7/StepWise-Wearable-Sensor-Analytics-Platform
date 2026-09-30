from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from stepwise.core.models import SensorMapping, StanceInterval


def _smooth(series: pd.Series, window: int) -> pd.Series:
    window = max(1, int(window))
    window += 1 if window % 2 == 0 else 0
    return (
        series.rolling(window, center=True, min_periods=1)
        .median()
        .rolling(window, center=True, min_periods=1)
        .mean()
    )


def _sum_channels(frame: pd.DataFrame, channels: tuple[str, ...]) -> pd.Series:
    if not channels:
        return pd.Series(0.0, index=frame.index)
    return frame[[f"{channel}_smooth" for channel in channels]].sum(axis=1)


def add_signal_features(raw: pd.DataFrame, mapping: SensorMapping, smooth_window: int) -> pd.DataFrame:
    frame = raw.copy()
    pressure_channels = ["P1", "P2", "P3", "P4"]
    for channel in pressure_channels:
        frame[f"{channel}_smooth"] = _smooth(frame[channel].clip(lower=0), smooth_window)

    frame["TotalPressure"] = frame[[f"{channel}_smooth" for channel in pressure_channels]].sum(axis=1)
    frame["RearPressure"] = _sum_channels(frame, mapping.rear)
    frame["ArchPressure"] = _sum_channels(frame, mapping.arch)
    frame["FrontPressure"] = _sum_channels(frame, mapping.front)
    frame["MedialPressure"] = _sum_channels(frame, mapping.medial_forefoot)
    frame["LateralPressure"] = _sum_channels(frame, mapping.lateral_forefoot)
    frame["ToePressure"] = _sum_channels(frame, mapping.toe)
    frame["PushOffPressure"] = frame["ToePressure"] if mapping.toe else frame["FrontPressure"]

    total = frame["TotalPressure"].replace(0, np.nan)
    frame["RearRatio"] = frame["RearPressure"] / total
    frame["ArchRatio"] = frame["ArchPressure"] / total
    frame["FrontRatio"] = frame["FrontPressure"] / total
    frame["MedialRatio"] = frame["MedialPressure"] / total
    frame["LateralRatio"] = frame["LateralPressure"] / total
    frame["ToeRatio"] = frame["ToePressure"] / total if mapping.toe else np.nan
    frame["PushOffRatio"] = frame["PushOffPressure"] / total
    frame["MedialLateralBalance"] = (frame["MedialPressure"] - frame["LateralPressure"]) / (
        frame["MedialPressure"] + frame["LateralPressure"]
    ).replace(0, np.nan)

    ap_weights = dict.fromkeys(pressure_channels, 0.0)
    ml_weights = dict.fromkeys(pressure_channels, 0.0)
    for channel in mapping.arch:
        ap_weights[channel] = 0.45
    for channel in mapping.front:
        ap_weights[channel] = 1.0
    for channel in mapping.medial_forefoot:
        ml_weights[channel] = 0.45
    for channel in mapping.lateral_forefoot:
        ml_weights[channel] = -0.45
    frame["CoP_AP"] = (
        sum(frame[f"{channel}_smooth"] * ap_weights[channel] for channel in pressure_channels) / total
    )
    frame["CoP_ML"] = (
        sum(frame[f"{channel}_smooth"] * ml_weights[channel] for channel in pressure_channels) / total
    )
    frame["AccMag"] = np.sqrt(frame["AccX"] ** 2 + frame["AccY"] ** 2 + frame["AccZ"] ** 2)
    frame["GyrMag"] = np.sqrt(frame["GyrX"] ** 2 + frame["GyrY"] ** 2 + frame["GyrZ"] ** 2)
    return frame


def _step_pattern(row: dict[str, Any]) -> str:
    labels: list[str] = []
    if row["FrontRatio_mean"] >= 0.70:
        labels.append("Forefoot-heavy")
    if row["RearRatio_mean"] >= 0.70:
        labels.append("Rearfoot-heavy")
    if row["MedialRatio_mean"] >= 0.65:
        labels.append("Medial overload")
    if row["LateralRatio_mean"] >= 0.65:
        labels.append("Lateral overload")
    if row["ArchRatio_mean"] >= 0.25:
        labels.append("High arch/midfoot loading")
    if row["PushOffRatio_late_stance_mean"] < 0.30:
        labels.append("Weak push-off candidate")
    return "; ".join(labels) if labels else "Balanced-like contact"


def extract_stance_features(frame: pd.DataFrame, intervals: list[StanceInterval]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    previous_start: float | None = None
    for step_number, interval in enumerate(intervals, start=1):
        start, end = interval.start_index, interval.end_index
        segment = frame.iloc[start : end + 1]
        count = end - start + 1
        early = frame.iloc[start : start + max(1, int(count * 0.20)) + 1]
        late = frame.iloc[start + int(count * 0.65) : end + 1]
        start_time = float(segment["Time_s"].iloc[0])
        end_time = float(segment["Time_s"].iloc[-1])
        stance_time = end_time - start_time
        stride_time = np.nan if previous_start is None else start_time - previous_start
        swing_time = np.nan if previous_start is None else stride_time - stance_time
        previous_start = start_time
        row: dict[str, Any] = {
            "Step": step_number,
            "StartSample": int(segment["Sample"].iloc[0]),
            "EndSample": int(segment["Sample"].iloc[-1]),
            "StartTime_s": start_time,
            "EndTime_s": end_time,
            "StanceTime_s": stance_time,
            "StrideTime_s": stride_time,
            "SwingTime_s": swing_time,
            "PeakPressure_N": float(segment["TotalPressure"].max()),
            "MeanPressure_N": float(segment["TotalPressure"].mean()),
            "PressureImpulse_Ns": float(np.trapezoid(segment["TotalPressure"], segment["Time_s"])),
            "EarlyRearRatio_mean": float(early["RearRatio"].mean()),
            "EarlyFrontRatio_mean": float(early["FrontRatio"].mean()),
            "RearRatio_mean": float(segment["RearRatio"].mean()),
            "ArchRatio_mean": float(segment["ArchRatio"].mean()),
            "FrontRatio_mean": float(segment["FrontRatio"].mean()),
            "MedialRatio_mean": float(segment["MedialRatio"].mean()),
            "LateralRatio_mean": float(segment["LateralRatio"].mean()),
            "MedialLateralBalance_mean": float(segment["MedialLateralBalance"].mean()),
            "ToeRatio_late_stance_mean": float(late["ToeRatio"].mean()),
            "PushOffRatio_late_stance_mean": float(late["PushOffRatio"].mean()),
            "CoP_AP_early_mean": float(early["CoP_AP"].mean()),
            "CoP_AP_late_mean": float(late["CoP_AP"].mean()),
            "CoP_AP_progression": float(late["CoP_AP"].mean() - early["CoP_AP"].mean()),
            "CoP_ML_mean": float(segment["CoP_ML"].mean()),
            "InitialContactRoll_deg": float(segment["Roll"].iloc[0]),
            "LandingRoll_mean_deg": float(early["Roll"].mean()),
            "InitialContactPitch_deg": float(segment["Pitch"].iloc[0]),
            "LandingPitch_mean_deg": float(early["Pitch"].mean()),
            "PitchRange_deg": float(segment["Pitch"].max() - segment["Pitch"].min()),
            "RollRange_deg": float(segment["Roll"].max() - segment["Roll"].min()),
            "GyrMag_peak": float(segment["GyrMag"].max()),
            "AccMag_peak": float(segment["AccMag"].max()),
        }
        row["Pattern"] = _step_pattern(row)
        rows.append(row)
    return pd.DataFrame(rows)


def aggregate_metrics(steps: pd.DataFrame, processed: pd.DataFrame) -> dict[str, float]:
    keys = [
        "RearRatio_mean",
        "ArchRatio_mean",
        "FrontRatio_mean",
        "MedialRatio_mean",
        "LateralRatio_mean",
        "EarlyRearRatio_mean",
        "EarlyFrontRatio_mean",
        "PushOffRatio_late_stance_mean",
        "CoP_AP_progression",
        "CoP_ML_mean",
        "StrideTime_s",
        "LandingRoll_mean_deg",
        "InitialContactRoll_deg",
        "LandingPitch_mean_deg",
        "InitialContactPitch_deg",
        "RollRange_deg",
        "PitchRange_deg",
    ]
    metrics = {key: float(steps[key].mean()) if key in steps and not steps.empty else np.nan for key in keys}
    stride = steps["StrideTime_s"].dropna() if "StrideTime_s" in steps else pd.Series(dtype=float)
    metrics["StrideCV"] = (
        float(stride.std(ddof=0) / stride.mean()) if len(stride) >= 3 and stride.mean() > 0 else np.nan
    )
    contact = processed[processed["FootContact"]] if "FootContact" in processed else processed
    for axis in ["Roll", "Pitch", "Yaw"]:
        metrics[f"{axis}_stance_mean"] = float(contact[axis].mean()) if not contact.empty else np.nan
        metrics[f"{axis}_stance_std"] = float(contact[axis].std()) if not contact.empty else np.nan
    return metrics


def add_calibrated_metrics(metrics: dict[str, float], calibration: dict[str, Any] | None) -> dict[str, float]:
    updated = dict(metrics)
    updated.update(
        {
            "LandingSoleGroundAngle_deg": np.nan,
            "PitchDelta_stance_from_standing": np.nan,
            "LandingPitchDelta_from_standing": np.nan,
            "RollDelta_stance_from_standing": np.nan,
        }
    )
    if calibration:
        roll_zero = float(calibration["roll_neutral_mean"])
        pitch_zero = float(calibration["pitch_neutral_mean"])
        updated["LandingSoleGroundAngle_deg"] = updated["LandingRoll_mean_deg"] - roll_zero
        updated["RollDelta_stance_from_standing"] = updated["Roll_stance_mean"] - roll_zero
        updated["PitchDelta_stance_from_standing"] = updated["Pitch_stance_mean"] - pitch_zero
        updated["LandingPitchDelta_from_standing"] = updated["LandingPitch_mean_deg"] - pitch_zero
    return updated
