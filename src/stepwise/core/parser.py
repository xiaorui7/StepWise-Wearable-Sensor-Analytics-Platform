from __future__ import annotations

from io import StringIO

import pandas as pd

DEFAULT_COLUMNS = [
    "Sample",
    "SystemTime",
    "P1",
    "P2",
    "P3",
    "P4",
    "AccX",
    "AccY",
    "AccZ",
    "GyrX",
    "GyrY",
    "GyrZ",
    "Pitch",
    "Roll",
    "Yaw",
]


class SensorDataError(ValueError):
    """Raised when a sensor recording is structurally unusable."""


def parse_sensor_text(text: str) -> pd.DataFrame:
    if not text.strip():
        raise SensorDataError("The uploaded sensor file is empty")

    rows: list[list[str]] = []
    for line in StringIO(text):
        parts = line.strip().split()
        if len(parts) == len(DEFAULT_COLUMNS) and parts[0].isdigit():
            rows.append(parts)
    if not rows:
        raise SensorDataError("No valid 15-field sensor rows were found")

    frame = pd.DataFrame(rows, columns=DEFAULT_COLUMNS)
    for column in DEFAULT_COLUMNS:
        if column != "SystemTime":
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame.drop(columns=["SystemTime"]).isna().any().any():
        raise SensorDataError("One or more sensor values are not numeric")

    times = pd.to_datetime(frame["SystemTime"], format="%H:%M:%S.%f", errors="coerce")
    if times.isna().any():
        raise SensorDataError("SystemTime values must use HH:MM:SS.mmm")
    frame = frame.reset_index(drop=True)
    elapsed = (times - times.iloc[0]).dt.total_seconds()
    frame["Time_s"] = elapsed.mask(elapsed < 0, elapsed + 24 * 3600)
    return frame
