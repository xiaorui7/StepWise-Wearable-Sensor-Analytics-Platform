from pathlib import Path

import pytest

from stepwise.core.parser import SensorDataError, parse_sensor_text

FIXTURES = Path(__file__).parents[2] / "fixtures" / "synthetic"


def test_parses_recording_and_normalizes_time() -> None:
    frame = parse_sensor_text((FIXTURES / "normal_like.synthetic.txt").read_text())
    assert len(frame) == 960
    assert frame["Time_s"].iloc[0] == 0
    assert frame["Time_s"].iloc[-1] == pytest.approx(9.59)


@pytest.mark.parametrize("text", ["", "broken rows only", "1 12:00:00.000 1 2 3"])
def test_rejects_empty_or_malformed_recording(text: str) -> None:
    with pytest.raises(SensorDataError):
        parse_sensor_text(text)
