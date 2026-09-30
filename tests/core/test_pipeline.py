import json
from pathlib import Path

from stepwise.core.models import AnalysisConfig
from stepwise.pipeline import run_analysis

FIXTURES = Path(__file__).parents[2] / "fixtures" / "synthetic"


def test_pipeline_generates_standard_json_and_reports() -> None:
    bundle = run_analysis(
        (FIXTURES / "normal_like.synthetic.txt").read_text(),
        (FIXTURES / "standing_neutral.synthetic.txt").read_text(),
        AnalysisConfig(),
    )
    assert bundle.result.summary["detected_steps_single_foot"] == 8
    assert {"report.html", "pressure.png", "orientation.png", "steps.csv", "result.json"} == set(
        bundle.artifacts
    )
    result_json = bundle.artifacts["result.json"][1].decode()
    json.loads(result_json, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    assert "C:\\Users" not in result_json
