import pytest
from pydantic import ValidationError

from tactiq.presentation.api.main import DualCommentaryRequest, DualSpeechRequest, NarrativeRequest, RecapRequest, SpeechRequest


@pytest.mark.parametrize("request_type", [NarrativeRequest, DualCommentaryRequest])
@pytest.mark.parametrize("identifier", ["", " ", "contains whitespace", "x" * 257])
def test_explicit_invalid_insight_ids_are_not_treated_as_select_latest(request_type, identifier):
    with pytest.raises(ValidationError):
        request_type(insight_id=identifier)


@pytest.mark.parametrize("request_type", [NarrativeRequest, RecapRequest])
def test_selected_player_ids_are_bounded_and_nonblank(request_type):
    with pytest.raises(ValidationError):
        request_type(audience_mode="player", selected_player_id=" ")


def test_explicit_blank_speech_voices_do_not_silently_select_billable_defaults():
    with pytest.raises(ValidationError):
        SpeechRequest(text="Short text", voice="")
    with pytest.raises(ValidationError):
        DualSpeechRequest(primary_text="Lead", secondary_text="Analyst", primary_voice="")
