import numpy as np
import pandas as pd
import pytest

from stepwise.core.contact import adaptive_threshold, hysteresis_contact
from stepwise.core.segmentation import segment_stances


def test_adaptive_threshold_and_hysteresis() -> None:
    values = pd.Series([0.0, 4.0, 10.0, 8.0, 3.0, 0.0])
    enter, exit_ = adaptive_threshold(values, 5.0, 0.2)
    assert enter == 5.0
    assert exit_ == pytest.approx(2.75)
    assert np.array_equal(hysteresis_contact(values, enter, exit_), [False, False, True, True, True, False])


def test_known_intervals_are_segmented() -> None:
    contact = np.array([False, True, True, False, False, True, True, True, False])
    time = pd.Series(np.arange(len(contact)) * 0.1)
    intervals = segment_stances(contact, time, 0.1)
    assert [(item.start_index, item.end_index) for item in intervals] == [(1, 2), (5, 7)]
