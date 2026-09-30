from pathlib import Path

from stepwise.core.contact import adaptive_threshold, hysteresis_contact
from stepwise.core.features import add_signal_features, extract_stance_features
from stepwise.core.models import SensorMapping
from stepwise.core.parser import parse_sensor_text
from stepwise.core.segmentation import segment_stances

FIXTURES = Path(__file__).parents[2] / "fixtures" / "synthetic"


def analyze(name: str):
    frame = add_signal_features(parse_sensor_text((FIXTURES / name).read_text()), SensorMapping(), 3)
    enter, exit_ = adaptive_threshold(frame["TotalPressure"], 5.0, 0.08)
    contact = hysteresis_contact(frame["TotalPressure"], enter, exit_)
    return frame, extract_stance_features(frame, segment_stances(contact, frame["Time_s"], 0.08))


def test_normal_fixture_has_expected_segmentation() -> None:
    _, steps = analyze("normal_like.synthetic.txt")
    assert len(steps) == 8
    assert steps["StartSample"].tolist() == [1, 120, 240, 360, 480, 600, 720, 840]


def test_mapping_controls_medial_lateral_direction() -> None:
    _, medial = analyze("medial_loading.synthetic.txt")
    _, lateral = analyze("lateral_loading.synthetic.txt")
    assert medial["CoP_ML_mean"].mean() > 0.04
    assert lateral["CoP_ML_mean"].mean() < -0.04


def test_forefoot_loading_preserves_late_push_off_behavior() -> None:
    _, steps = analyze("forefoot_dominant.synthetic.txt")
    assert steps["FrontRatio_mean"].mean() > steps["RearRatio_mean"].mean()
    assert steps["PushOffRatio_late_stance_mean"].mean() > 0.80
