import pytest

from tactiq.domain.models import Position
from tactiq.evaluation.value import LabelledAction, fit_value_model, score_value_model


def test_fitted_models_keep_training_and_validation_separate():
    actions = [
        LabelledAction(match_id="training", kind="shot", position=Position(x=60 + index, y=50),
                       goal=index % 3 == 0)
        for index in range(30)
    ]
    actions.extend([
        LabelledAction(match_id="training", kind="move", position=Position(x=30, y=50),
                       end_position=Position(x=70, y=50))
        for _ in range(10)
    ])
    model = fit_value_model(actions, "unit-fit-v1", "generated-unit-fixture")
    assert not model.validated
    assert model.training_sample_count == 40
    assert model.xg_distance <= 0
    with pytest.raises(ValueError, match="disjoint"):
        score_value_model(model, actions)
    heldout = [action.model_copy(update={"match_id": "heldout"}) for action in actions]
    metrics = score_value_model(model, heldout)
    assert 0 <= metrics["brier_score"] <= 1
    assert metrics["model_validated"] is False
