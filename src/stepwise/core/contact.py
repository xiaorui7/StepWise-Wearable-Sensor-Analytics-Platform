from __future__ import annotations

import numpy as np
import pandas as pd


def adaptive_threshold(total_pressure: pd.Series, minimum_n: float, peak_ratio: float) -> tuple[float, float]:
    enter = max(minimum_n, float(total_pressure.max()) * peak_ratio)
    return enter, enter * 0.55


def hysteresis_contact(total_pressure: pd.Series, enter_n: float, exit_n: float) -> np.ndarray:
    contact = np.zeros(len(total_pressure), dtype=bool)
    active = False
    for index, value in enumerate(total_pressure.to_numpy()):
        if not active and value >= enter_n:
            active = True
        elif active and value <= exit_n:
            active = False
        contact[index] = active
    return contact
