from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

HEADER = """StepWise synthetic software-test fixture
SYNTHETIC DATA - NOT CLINICAL OR HUMAN-SUBJECT DATA
Generated deterministically for parser, segmentation, API, and demo testing.
Fields: Sample SystemTime P1 P2 P3 P4 AccX AccY AccZ GyrX GyrY GyrZ Pitch Roll Yaw
--------------------------------------------------------------------------------
"""


@dataclass(frozen=True)
class Scenario:
    name: str
    heel_scale: float = 1.0
    arch_scale: float = 1.0
    medial_scale: float = 1.0
    lateral_scale: float = 1.0
    pitch_delta: float = 0.0
    roll_delta: float = 0.0
    noise: float = 1.0
    steps: int = 8
    duplicate_timestamps: bool = False
    zero_channel: str | None = None


SCENARIOS = [
    Scenario("normal_like"),
    Scenario("forefoot_dominant", heel_scale=0.35, medial_scale=1.35, lateral_scale=1.35, roll_delta=-16),
    Scenario("rearfoot_dominant", heel_scale=1.65, medial_scale=0.45, lateral_scale=0.45, roll_delta=16),
    Scenario("medial_loading", medial_scale=1.8, lateral_scale=0.45, arch_scale=1.3, pitch_delta=7),
    Scenario("lateral_loading", medial_scale=0.45, lateral_scale=1.8, pitch_delta=-7),
    Scenario("noisy_signal", noise=10.0),
    Scenario("zero_arch_channel", zero_channel="P3"),
    Scenario("duplicate_timestamps", duplicate_timestamps=True),
    Scenario("short_trial", steps=2),
]


def pressure_profile(phase: float, scenario: Scenario) -> tuple[float, float, float, float]:
    heel = 210.0 * np.exp(-(((phase - 0.18) / 0.18) ** 2)) * scenario.heel_scale
    arch = 75.0 * np.exp(-(((phase - 0.50) / 0.24) ** 2)) * scenario.arch_scale
    forefoot = 190.0 * np.exp(-(((phase - 0.76) / 0.22) ** 2))
    medial = forefoot * 0.53 * scenario.medial_scale
    lateral = forefoot * 0.47 * scenario.lateral_scale
    values = {"P1": lateral, "P2": heel, "P3": arch, "P4": medial}
    if scenario.zero_channel:
        values[scenario.zero_channel] = 0.0
    return values["P1"], values["P2"], values["P3"], values["P4"]


def write_walking(path: Path, scenario: Scenario, seed: int = 445) -> None:
    rng = np.random.default_rng(seed)
    stride_samples = 120
    stance_samples = 62
    total_samples = scenario.steps * stride_samples
    start = datetime(2026, 1, 1, 12, 0, 0)
    rows: list[str] = []

    for i in range(total_samples):
        within = i % stride_samples
        contact = within < stance_samples
        phase = within / max(1, stance_samples - 1)
        if contact:
            p1, p2, p3, p4 = pressure_profile(phase, scenario)
            noise = rng.normal(0, scenario.noise, 4)
            p1, p2, p3, p4 = [
                max(0.0, value + delta) for value, delta in zip((p1, p2, p3, p4), noise, strict=True)
            ]
            if scenario.zero_channel:
                channel_index = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}[scenario.zero_channel]
                pressure = [p1, p2, p3, p4]
                pressure[channel_index] = 0.0
                p1, p2, p3, p4 = pressure
        else:
            p1 = p2 = p3 = p4 = 0.0

        gait_wave = np.sin(2 * np.pi * within / stride_samples)
        acc_x = 0.8 * gait_wave + rng.normal(0, 0.02)
        acc_y = 0.4 * np.cos(2 * np.pi * within / stride_samples) + rng.normal(0, 0.02)
        acc_z = 9.81 + 0.7 * gait_wave + rng.normal(0, 0.03)
        gyr_x = 0.5 * gait_wave
        gyr_y = 0.3 * gait_wave
        gyr_z = 0.15 * gait_wave
        pitch = scenario.pitch_delta + 1.2 * gait_wave
        roll = scenario.roll_delta * (1.0 - min(phase, 1.0)) if contact else 0.0
        yaw = 0.2 * gait_wave

        tick = i if not scenario.duplicate_timestamps else i // 2
        timestamp = start + timedelta(milliseconds=10 * tick)
        time_text = timestamp.strftime("%H:%M:%S.%f")[:-3]
        values = [
            i + 1,
            time_text,
            p1,
            p2,
            p3,
            p4,
            acc_x,
            acc_y,
            acc_z,
            gyr_x,
            gyr_y,
            gyr_z,
            pitch,
            roll,
            yaw,
        ]
        rows.append(" ".join([str(values[0]), values[1], *[f"{float(v):.6f}" for v in values[2:]]]))

    path.write_text(HEADER + "\n".join(rows) + "\n", encoding="utf-8")


def write_standing(path: Path, seed: int = 445) -> None:
    rng = np.random.default_rng(seed)
    start = datetime(2026, 1, 1, 11, 59, 0)
    rows: list[str] = []
    for i in range(500):
        timestamp = start + timedelta(milliseconds=10 * i)
        values = [
            i + 1,
            timestamp.strftime("%H:%M:%S.%f")[:-3],
            70 + rng.normal(0, 0.5),
            120 + rng.normal(0, 0.5),
            65 + rng.normal(0, 0.5),
            75 + rng.normal(0, 0.5),
            rng.normal(0, 0.01),
            rng.normal(0, 0.01),
            9.81 + rng.normal(0, 0.01),
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
        ]
        rows.append(" ".join([str(values[0]), values[1], *[f"{float(v):.6f}" for v in values[2:]]]))
    path.write_text(HEADER + "\n".join(rows) + "\n", encoding="utf-8")


def generate(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    write_standing(output_dir / "standing_neutral.synthetic.txt")
    for index, scenario in enumerate(SCENARIOS):
        write_walking(output_dir / f"{scenario.name}.synthetic.txt", scenario, seed=445 + index)
    (output_dir / "malformed.synthetic.txt").write_text(
        "SYNTHETIC MALFORMED INPUT\n1 12:00:00.000 1 2 3\nnot-a-valid-row\n",
        encoding="utf-8",
    )
    (output_dir / "README.md").write_text(
        "# Synthetic StepWise fixtures\n\n"
        "These files are deterministic software-test and recruiter-demo fixtures. "
        "They are not human-subject recordings, do not model clinical distributions, "
        "and must not be used to estimate diagnostic accuracy.\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("fixtures/synthetic"))
    args = parser.parse_args()
    generate(args.output_dir)
    print(f"Generated deterministic synthetic fixtures in {args.output_dir}")


if __name__ == "__main__":
    main()
