from tactiq.application.service import MatchService
from tactiq.domain.models import Event, EvidenceItem, Insight, MatchState


def _insight(match_id: str, title: str, confidence: float, team_id: str = "team_a") -> Insight:
    return Insight(
        match_id=match_id,
        type=title.lower().replace(" ", "_"),
        priority="high",
        confidence=confidence,
        team_id=team_id,
        window_start_ms=60_000,
        window_end_ms=90_000,
        title=title,
        summary=f"{title} detected.",
        evidence=[
            EvidenceItem(metric="metric_a", value=1, unit="count", supporting_event_ids=["e1"]),
            EvidenceItem(metric="metric_b", value=2, unit="count", supporting_event_ids=["e2"]),
        ],
    )


def test_generate_recap_requires_insights() -> None:
    service = MatchService()
    match_id = "match_empty"
    service.matches[match_id] = MatchState(match_id=match_id, scenario_id="sustained_pressure", seed=1)

    try:
        service.generate_recap(match_id=match_id, audience_mode="fan", language="en-GB")
        raise AssertionError("Expected ValueError")
    except ValueError as exc:
        assert "No insights available" in str(exc)


def test_generate_recap_returns_top_insights() -> None:
    service = MatchService()
    match_id = "match_recap"
    service.matches[match_id] = MatchState(match_id=match_id, scenario_id="sustained_pressure", seed=1)
    service.insights[match_id] = [
        _insight(match_id, "Pressure Building", 0.70),
        _insight(match_id, "Momentum Shift", 0.93),
        _insight(match_id, "Player Influence Rising", 0.84),
    ]
    service.events[match_id] = [
        Event(event_id=f"e{index}", match_id=match_id, sequence=index, match_clock_ms=60_000 + index,
              type="pass_completed", team_id="team_a")
        for index in (1, 2)
    ]

    recap, top, chapters = service.generate_recap(match_id=match_id, audience_mode="analyst", language="en-GB")

    assert "Post-Match" in recap.title
    assert recap.provider in {"foundry", "deterministic"}
    assert len(top) == 3
    assert len(chapters) == 3
    assert top[0].title == "Momentum Shift"
