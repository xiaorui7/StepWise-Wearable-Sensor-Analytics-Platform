from pathlib import Path

from stepwise.core.models import AnalysisConfig
from stepwise.pipeline import run_analysis

FIXTURES = Path(__file__).parents[2] / "fixtures" / "synthetic"


def summary_for(name: str) -> dict[str, object]:
    bundle = run_analysis(
        (FIXTURES / name).read_text(encoding="utf-8"),
        None,
        AnalysisConfig(),
    )
    return bundle.result.summary


def test_duplicate_timestamps_are_reported() -> None:
    summary = summary_for("duplicate_timestamps.synthetic.txt")
    assert summary["duplicate_timestamp_count"] > 0
    assert any("repeated timestamps" in warning for warning in summary["warnings"])


def test_zero_pressure_channel_is_reported() -> None:
    summary = summary_for("zero_arch_channel.synthetic.txt")
    assert any("P3" in warning for warning in summary["warnings"])


def test_short_trial_has_low_data_quality() -> None:
    summary = summary_for("short_trial.synthetic.txt")
    assert summary["detected_steps_single_foot"] == 2
    assert summary["data_quality"] == "Low"
