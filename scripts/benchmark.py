from __future__ import annotations

import argparse
import json
import platform
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from stepwise.core.json_utils import json_safe
from stepwise.core.models import AnalysisConfig
from stepwise.pipeline import run_analysis


def percentile(values: list[float], quantile: float) -> float:
    return float(np.percentile(np.asarray(values), quantile))


def benchmark(runs: int, fixtures: Path) -> dict[str, object]:
    walking = (fixtures / "normal_like.synthetic.txt").read_text(encoding="utf-8")
    standing = (fixtures / "standing_neutral.synthetic.txt").read_text(encoding="utf-8")
    run_analysis(walking, standing, AnalysisConfig())
    durations: list[float] = []
    for _ in range(runs):
        started = time.perf_counter()
        run_analysis(walking, standing, AnalysisConfig())
        durations.append((time.perf_counter() - started) * 1000)
    median_ms = statistics.median(durations)
    return {
        "generated_at": datetime.now(UTC),
        "methodology": "Direct pipeline call including parsing, charts, and report generation",
        "fixture": "normal_like.synthetic.txt plus standing_neutral.synthetic.txt",
        "synthetic_data_notice": "Software-test data only; not clinical validation data",
        "runs": runs,
        "warmup_runs": 1,
        "median_ms": median_ms,
        "p95_ms": percentile(durations, 95),
        "min_ms": min(durations),
        "max_ms": max(durations),
        "sequential_analyses_per_minute_from_median": 60000 / median_ms,
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "processor": platform.processor() or "unavailable",
        },
    }


def markdown(result: dict[str, object]) -> str:
    environment = result["environment"]
    assert isinstance(environment, dict)
    return f"""# StepWise Local Benchmark

This is a small sequential benchmark using deterministic synthetic software-test data. It is not a
clinical accuracy test or internet-scale load test.

| Measurement | Result |
|---|---:|
| Timed runs | {result["runs"]} |
| Median | {result["median_ms"]:.2f} ms |
| p95 | {result["p95_ms"]:.2f} ms |
| Minimum | {result["min_ms"]:.2f} ms |
| Maximum | {result["max_ms"]:.2f} ms |
| Sequential analyses/minute, derived from median | {result["sequential_analyses_per_minute_from_median"]:.2f} |

Method: {result["methodology"]}.

Environment: {environment["platform"]}; Python {environment["python"]}; processor {environment["processor"]}.
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--fixtures", type=Path, default=Path("fixtures/synthetic"))
    parser.add_argument("--output-dir", type=Path, default=Path("benchmark-results"))
    args = parser.parse_args()
    if args.runs < 2:
        parser.error("--runs must be at least 2")
    result = benchmark(args.runs, args.fixtures)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "latest.json").write_text(
        json.dumps(json_safe(result), indent=2, allow_nan=False), encoding="utf-8"
    )
    (args.output_dir / "latest.md").write_text(markdown(result), encoding="utf-8")
    print(markdown(result))


if __name__ == "__main__":
    main()
