from __future__ import annotations

import numpy as np
import pandas as pd

from stepwise.core.models import StanceInterval


def segment_stances(contact: np.ndarray, time_s: pd.Series, min_stance_s: float) -> list[StanceInterval]:
    intervals: list[StanceInterval] = []
    start: int | None = None
    for index, is_contact in enumerate(contact):
        if is_contact and start is None:
            start = index
        if start is not None and (not is_contact or index == len(contact) - 1):
            end = index - 1 if not is_contact else index
            if float(time_s.iloc[end] - time_s.iloc[start]) >= min_stance_s:
                intervals.append(StanceInterval(start_index=start, end_index=end))
            start = None
    return intervals
