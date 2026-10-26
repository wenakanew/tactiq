from __future__ import annotations

from tactiq.agents.localization import LANGUAGES
from tactiq.agents.narrative import DeterministicNarrativeGenerator
from tactiq.domain.models import MatchState
from tactiq.evaluation.gates import release_gates
from tactiq.evaluation.narrative import evaluate_narratives
from tactiq.intelligence.engine import detect_insights, reduce_match_state
from tactiq.simulator.scenarios import load_scenario


def run_smoke() -> dict:
    outcomes = []
    examples = []
    for scenario in ("sustained_pressure", "rhythm_shift", "player_influence"):
        for seed in range(20):
            match_id = f"golden_{scenario}_{seed}"
            _, events = load_scenario(scenario, match_id, seed)
            state = MatchState(match_id=match_id, scenario_id=scenario, seed=seed)
            history = []
            insights = []
            for event in events:
                history.append(event)
                reduce_match_state(state, event, history)
                insights.extend(detect_insights(state, history, insights))
            ids = {event.event_id for event in history}
            expected = scenario
            present = expected in {insight.type for insight in insights}
            grounded = all(key in ids for insight in insights for item in insight.evidence for key in item.supporting_event_ids)
            outcomes.append({"scenario": scenario, "seed": seed, "expected_detected": present, "evidence_resolves": grounded})
            if seed == 0:
                examples.append(next(insight for insight in insights if insight.type == expected))
    generator = DeterministicNarrativeGenerator()
    protected = []
    for insight in examples:
        for language in LANGUAGES:
            narrative = generator.generate(insight, "analyst", language)
            preserved = all(f"{item.metric}={item.value}" in narrative.body for item in insight.evidence)
            protected.append({"language": language, "insight_type": insight.type, "numbers_preserved": preserved})
    source = examples[0]
    evidence = source.evidence[0]
    cases = []
    for index in range(10):
        response = {
            "title": source.title,
            "body": f"Team A produced {evidence.value} progressive actions.",
            "claims": [{"metric": evidence.metric, "value": evidence.value,
                        "supporting_event_ids": evidence.supporting_event_ids[:1]}],
            "entities": [source.team_id],
        }
        cases.append({"case_id": f"grounded-{index}", "insight": source.model_dump(),
                      "response": response, "expected_valid": True})
        cases.append({"case_id": f"fabricated-{index}", "insight": source.model_dump(),
                      "response": {**response, "body": "Team A produced 999 progressive actions."}, "expected_valid": False})
    narrative_score = evaluate_narratives(cases)
    report = {
        "schema_version": "1.0.0",
        "dataset": {"domain": "synthetic", "source": "seeded-rule-fixtures-v2",
                    "note": "Regression fixtures, not a football CV or human translation benchmark."},
        "scenarios": outcomes,
        "narrative": narrative_score,
        "localization": {
            "entity_number_preservation": sum(case["numbers_preserved"] for case in protected) / len(protected),
            "reviewed_locales": [], "fixtures": protected,
        },
        "tracking": {}, "calibration": {}, "replay": {}, "platform": {}, "value_model": {},
    }
    report["smoke_passed"] = (
        all(item["expected_detected"] and item["evidence_resolves"] for item in outcomes)
        and all(item["numbers_preserved"] for item in protected)
        and narrative_score["unsupported_claims"] == 0 and narrative_score["valid_acceptance_rate"] == 1
    )
    report["production_gates"] = release_gates(report)
    return report
