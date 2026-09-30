from __future__ import annotations

import argparse
import json
from pathlib import Path

from stepwise.core.json_utils import json_safe
from stepwise.core.models import AnalysisConfig, SensorMapping
from stepwise.pipeline import run_analysis


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze a StepWise TXT recording without the API")
    parser.add_argument("input", type=Path)
    parser.add_argument("--standing-file", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("stepwise_output"))
    parser.add_argument("--heel", nargs="+", default=["P2"])
    parser.add_argument("--arch", nargs="+", default=["P3"])
    parser.add_argument("--medial-forefoot", nargs="+", default=["P4"])
    parser.add_argument("--lateral-forefoot", nargs="+", default=["P1"])
    parser.add_argument("--toe", nargs="*", default=[])
    parser.add_argument("--pitch-eversion-sign", choices=["positive", "negative"], default="positive")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    mapping = SensorMapping(
        heel=tuple(args.heel),
        arch=tuple(args.arch),
        medial_forefoot=tuple(args.medial_forefoot),
        lateral_forefoot=tuple(args.lateral_forefoot),
        toe=tuple(args.toe),
        pitch_eversion_sign=args.pitch_eversion_sign,
    )
    standing = (
        args.standing_file.read_text(encoding="utf-8", errors="replace") if args.standing_file else None
    )
    bundle = run_analysis(
        args.input.read_text(encoding="utf-8", errors="replace"),
        standing,
        AnalysisConfig(sensor_mapping=mapping),
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, (_, content) in bundle.artifacts.items():
        (args.output_dir / name).write_bytes(content)
    print(json.dumps(json_safe(bundle.result.summary), indent=2, allow_nan=False))
    print(f"Artifacts written to {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
