from __future__ import annotations

from io import BytesIO

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from stepwise.core.models import StanceInterval


def pressure_chart(frame: pd.DataFrame, intervals: list[StanceInterval]) -> bytes:
    figure, axis = plt.subplots(figsize=(10, 4.5))
    colors = {"P1": "#e45756", "P2": "#4c78a8", "P3": "#72b7b2", "P4": "#f2cf5b"}
    for channel, color in colors.items():
        axis.plot(frame["Time_s"], frame[f"{channel}_smooth"], label=channel, color=color, linewidth=1.2)
    axis.plot(frame["Time_s"], frame["TotalPressure"], label="Total", color="#17212b", linewidth=1.7)
    for interval in intervals:
        axis.axvspan(
            frame["Time_s"].iloc[interval.start_index],
            frame["Time_s"].iloc[interval.end_index],
            color="#4c956c",
            alpha=0.10,
        )
    axis.set(title="Plantar pressure and detected stance phases", xlabel="Time (s)", ylabel="Pressure (N)")
    axis.grid(alpha=0.22)
    axis.legend(ncol=5, frameon=False)
    figure.tight_layout()
    buffer = BytesIO()
    figure.savefig(buffer, format="png", dpi=150)
    plt.close(figure)
    return buffer.getvalue()


def orientation_chart(frame: pd.DataFrame) -> bytes:
    figure, axis = plt.subplots(figsize=(10, 3.8))
    for channel, color in [("Pitch", "#e45756"), ("Roll", "#4c78a8"), ("Yaw", "#72b7b2")]:
        axis.plot(frame["Time_s"], frame[channel], label=channel, color=color, linewidth=1.2)
    axis.set(title="Foot orientation signals", xlabel="Time (s)", ylabel="Angle (deg)")
    axis.grid(alpha=0.22)
    axis.legend(frameon=False)
    figure.tight_layout()
    buffer = BytesIO()
    figure.savefig(buffer, format="png", dpi=150)
    plt.close(figure)
    return buffer.getvalue()
