from stepwise.core.rules import evaluate_rules


def metrics(**overrides):
    result = {
        "MedialRatio_mean": 0.3,
        "LateralRatio_mean": 0.3,
        "ArchRatio_mean": 0.05,
        "CoP_ML_mean": 0.0,
        "PitchDelta_stance_from_standing": 0.0,
        "LandingSoleGroundAngle_deg": 0.0,
        "EarlyFrontRatio_mean": 0.4,
        "EarlyRearRatio_mean": 0.4,
        "PushOffRatio_late_stance_mean": 0.8,
        "CoP_AP_progression": 0.3,
        "StrideCV": 0.03,
    }
    result.update(overrides)
    return result


SUMMARY = {"data_quality": "High", "detected_steps_single_foot": 10}


def test_pitch_and_pressure_support_eversion_tendency() -> None:
    cards = evaluate_rules(
        metrics(PitchDelta_stance_from_standing=7.0, MedialRatio_mean=0.42, LateralRatio_mean=0.25),
        SUMMARY,
        has_standing_calibration=True,
        pitch_eversion_sign="positive",
    )
    assert cards[0].title == "Possible eversion / over-pronation related tendency"


def test_pressure_alone_does_not_confirm_inversion() -> None:
    cards = evaluate_rules(
        metrics(LateralRatio_mean=0.45, MedialRatio_mean=0.25, CoP_ML_mean=-0.08),
        SUMMARY,
        has_standing_calibration=False,
        pitch_eversion_sign="positive",
    )
    assert cards[0].title == "Lateral loading bias; inversion not confirmed"


def test_conflicting_landing_evidence_is_not_a_user_card() -> None:
    cards = evaluate_rules(
        metrics(EarlyRearRatio_mean=0.8, LandingSoleGroundAngle_deg=-15),
        SUMMARY,
        has_standing_calibration=True,
        pitch_eversion_sign="positive",
    )
    assert not any("landing tendency" in card.title.lower() for card in cards)


def test_too_few_stances_blocks_posture_rules() -> None:
    cards = evaluate_rules(
        metrics(PitchDelta_stance_from_standing=9),
        {"data_quality": "Low", "detected_steps_single_foot": 2},
        has_standing_calibration=True,
        pitch_eversion_sign="positive",
    )
    assert len(cards) == 1
    assert "cannot be inferred" in cards[0].title
