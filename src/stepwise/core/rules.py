from __future__ import annotations

import math
from typing import Any

from stepwise.core.models import ScreeningCard

# These are project-specific engineering screening thresholds. Literature supports
# the qualitative direction of evidence, not these cutoffs as clinical standards.
FRONTAL_TILT_THRESHOLD_DEG = 5.0
STRONG_FRONTAL_TILT_DEG = 8.0
ML_RATIO_MARGIN_THRESHOLD = 0.06
COP_ML_THRESHOLD = 0.04
ARCH_RATIO_SUPPORT_THRESHOLD = 0.10
LANDING_ANGLE_THRESHOLD_DEG = 12.0
EARLY_FOREFOOT_RATIO_THRESHOLD = 0.65
EARLY_REARFOOT_RATIO_THRESHOLD = 0.65


def _number(metrics: dict[str, Any], key: str) -> float | None:
    value = metrics.get(key)
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _metric(metrics: dict[str, Any], key: str, label: str, digits: int = 3) -> str:
    value = _number(metrics, key)
    return f"{label}: {'unavailable' if value is None else f'{value:.{digits}f}'}"


def evaluate_rules(
    metrics: dict[str, Any],
    summary: dict[str, Any],
    *,
    has_standing_calibration: bool,
    pitch_eversion_sign: str,
) -> list[ScreeningCard]:
    cards: list[ScreeningCard] = []
    stance_count = int(summary.get("detected_steps_single_foot", 0))
    quality = str(summary.get("data_quality", "Low"))

    def add(
        title: str,
        level: str,
        evidence: list[str],
        interpretation: str,
        action: str,
        limitation: str,
    ) -> None:
        cards.append(
            ScreeningCard(
                title=title,
                level=level,
                evidence=evidence,
                interpretation=interpretation,
                action=action,
                limitation=limitation,
            )
        )

    if stance_count < 3:
        add(
            "Walking posture cannot be inferred from this trial",
            "High",
            [f"Data quality: {quality}", f"Valid stance phases: {stance_count}"],
            "The recording does not contain enough walking cycles for a posture tendency.",
            "Repeat the trial and aim for 10-20 valid stance phases.",
            "Very short or non-walking recordings are limited to software and sensor checks.",
        )
        return cards

    medial = _number(metrics, "MedialRatio_mean")
    lateral = _number(metrics, "LateralRatio_mean")
    arch = _number(metrics, "ArchRatio_mean")
    cop_ml = _number(metrics, "CoP_ML_mean")
    pitch_delta = _number(metrics, "PitchDelta_stance_from_standing")
    landing_angle = _number(metrics, "LandingSoleGroundAngle_deg")
    medial_margin = medial - lateral if medial is not None and lateral is not None else None

    positive_is_eversion = pitch_eversion_sign == "positive"
    eversion_imu = bool(
        has_standing_calibration
        and pitch_delta is not None
        and (
            pitch_delta >= FRONTAL_TILT_THRESHOLD_DEG
            if positive_is_eversion
            else pitch_delta <= -FRONTAL_TILT_THRESHOLD_DEG
        )
    )
    inversion_imu = bool(
        has_standing_calibration
        and pitch_delta is not None
        and (
            pitch_delta <= -FRONTAL_TILT_THRESHOLD_DEG
            if positive_is_eversion
            else pitch_delta >= FRONTAL_TILT_THRESHOLD_DEG
        )
    )
    eversion_pressure = bool(
        (medial_margin is not None and medial_margin >= ML_RATIO_MARGIN_THRESHOLD)
        or (cop_ml is not None and cop_ml >= COP_ML_THRESHOLD)
        or (arch is not None and arch >= ARCH_RATIO_SUPPORT_THRESHOLD)
    )
    inversion_pressure = bool(
        (medial_margin is not None and medial_margin <= -ML_RATIO_MARGIN_THRESHOLD)
        or (cop_ml is not None and cop_ml <= -COP_ML_THRESHOLD)
    )

    common_frontal_evidence = [
        _metric(metrics, "MedialRatio_mean", "Medial forefoot ratio"),
        _metric(metrics, "LateralRatio_mean", "Lateral forefoot ratio"),
        _metric(metrics, "ArchRatio_mean", "Arch/midfoot ratio"),
        _metric(metrics, "CoP_ML_mean", "Medial-lateral CoP proxy"),
        _metric(metrics, "PitchDelta_stance_from_standing", "Calibrated Pitch delta", 2),
    ]
    conflicting = (eversion_imu and inversion_pressure) or (inversion_imu and eversion_pressure)
    if conflicting:
        add(
            "Mixed frontal-plane evidence",
            "Medium",
            common_frontal_evidence,
            "Pressure distribution and calibrated Pitch point in different directions.",
            "Repeat standing calibration and the walking trial with unchanged sensor placement.",
            "StepWise does not force an inversion or eversion label when evidence conflicts.",
        )
    elif eversion_imu:
        add(
            "Possible eversion / over-pronation related tendency",
            "High" if eversion_pressure or abs(pitch_delta or 0) >= STRONG_FRONTAL_TILT_DEG else "Medium",
            common_frontal_evidence,
            "Calibrated foot tilt points toward eversion; pressure is supporting evidence only.",
            "Repeat under the same setup and confirm with frontal or top-view video.",
            "This is an engineering screening tendency, not a diagnosis of pronation or flat foot.",
        )
    elif inversion_imu:
        add(
            "Possible inversion / over-supination related tendency",
            "High" if inversion_pressure or abs(pitch_delta or 0) >= STRONG_FRONTAL_TILT_DEG else "Medium",
            common_frontal_evidence,
            "Calibrated foot tilt points toward inversion; pressure is supporting evidence only.",
            "Repeat under the same setup and confirm with frontal or top-view video.",
            "This is an engineering screening tendency, not a diagnosis of supination or instability.",
        )
    elif eversion_pressure:
        add(
            "Medial loading bias; eversion not confirmed",
            "Low",
            common_frontal_evidence,
            "Pressure is biased medially, but calibrated Pitch does not confirm eversion.",
            "Use this as a loading note and confirm with repeated trials or video.",
            "Pressure distribution alone cannot confirm eversion or pronation.",
        )
    elif inversion_pressure:
        add(
            "Lateral loading bias; inversion not confirmed",
            "Low",
            common_frontal_evidence,
            "Pressure is biased laterally, but calibrated Pitch does not confirm inversion.",
            "Use this as a loading note and confirm with repeated trials or video.",
            "Pressure distribution alone cannot confirm inversion or supination.",
        )

    early_front = _number(metrics, "EarlyFrontRatio_mean")
    early_rear = _number(metrics, "EarlyRearRatio_mean")
    forefoot = (early_front is not None and early_front >= EARLY_FOREFOOT_RATIO_THRESHOLD) or (
        landing_angle is not None and landing_angle <= -LANDING_ANGLE_THRESHOLD_DEG
    )
    rearfoot = (early_rear is not None and early_rear >= EARLY_REARFOOT_RATIO_THRESHOLD) or (
        landing_angle is not None and landing_angle >= LANDING_ANGLE_THRESHOLD_DEG
    )
    if forefoot and not rearfoot:
        add(
            "Forefoot-first landing tendency",
            "Medium",
            [
                _metric(metrics, "EarlyFrontRatio_mean", "Early forefoot ratio"),
                _metric(metrics, "LandingSoleGroundAngle_deg", "Landing angle", 2),
            ],
            "Forefoot pressure appears early or the forefoot is lower than the heel at contact.",
            "Confirm the pattern with side-view video.",
            "The 0.65 ratio and 12 degree deadband are project engineering thresholds, not clinical cutoffs.",
        )
    elif rearfoot and not forefoot:
        add(
            "Rearfoot / heel-first landing tendency",
            "Medium",
            [
                _metric(metrics, "EarlyRearRatio_mean", "Early rearfoot ratio"),
                _metric(metrics, "LandingSoleGroundAngle_deg", "Landing angle", 2),
            ],
            "Heel pressure appears early or the heel is lower than the forefoot at contact.",
            "Confirm the pattern with side-view video.",
            "The 0.65 ratio and 12 degree deadband are project engineering thresholds, not clinical cutoffs.",
        )

    push_off = _number(metrics, "PushOffRatio_late_stance_mean")
    if push_off is not None and push_off < 0.75:
        add(
            "Reduced late-stance forefoot push-off warning",
            "Medium",
            [_metric(metrics, "PushOffRatio_late_stance_mean", "Late push-off proxy")],
            "Forefoot pressure is relatively low near the end of stance.",
            "Check forefoot sensor placement and repeat at a natural walking speed.",
            "This does not diagnose muscle weakness or neurological impairment.",
        )

    progression = _number(metrics, "CoP_AP_progression")
    if progression is not None and progression < 0.15:
        add(
            "Reduced heel-to-forefoot pressure transfer",
            "Medium",
            [_metric(metrics, "CoP_AP_progression", "Anterior CoP progression")],
            "The four-sensor pressure-center proxy moved only slightly toward the forefoot.",
            "Inspect the pressure curves and repeat at a natural walking speed.",
            "This is not a force-platform center-of-pressure measurement.",
        )

    stride_cv = _number(metrics, "StrideCV")
    if stance_count >= 8 and stride_cv is not None and stride_cv >= 0.12:
        add(
            "High stride timing variability",
            "Medium",
            [_metric(metrics, "StrideCV", "Stride-time coefficient of variation")],
            "Same-foot stride timing varies across the trial.",
            "Repeat on a straight path at a stable speed.",
            "Single-foot timing over a short trial is a screening indicator only.",
        )

    if not cards:
        add(
            "No clear StepWise-supported tendency",
            "Low",
            common_frontal_evidence,
            "No configured engineering rule was strongly triggered in this trial.",
            "Repeat if the observed walking pattern or symptoms remain concerning.",
            "A normal-like screening result does not rule out a gait or foot condition.",
        )
    return cards
