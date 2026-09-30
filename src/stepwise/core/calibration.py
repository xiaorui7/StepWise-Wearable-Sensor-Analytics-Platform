from __future__ import annotations

from typing import Any

import pandas as pd


def standing_calibration(processed: pd.DataFrame) -> dict[str, Any]:
    peak = float(processed["TotalPressure"].max()) if len(processed) else 0.0
    loaded = processed[processed["TotalPressure"] >= max(5.0, peak * 0.08)]
    if loaded.empty:
        loaded = processed
    return {
        "samples": int(len(processed)),
        "duration_s": float(processed["Time_s"].iloc[-1] - processed["Time_s"].iloc[0])
        if len(processed) > 1
        else 0.0,
        "roll_neutral_mean": float(loaded["Roll"].mean()),
        "pitch_neutral_mean": float(loaded["Pitch"].mean()),
        "yaw_neutral_mean": float(loaded["Yaw"].mean()),
        "static_rear_ratio_mean": float(loaded["RearRatio"].mean()),
        "static_arch_ratio_mean": float(loaded["ArchRatio"].mean()),
        "static_medial_ratio_mean": float(loaded["MedialRatio"].mean()),
        "static_lateral_ratio_mean": float(loaded["LateralRatio"].mean()),
        "static_cop_ml_mean": float(loaded["CoP_ML"].mean()),
    }
