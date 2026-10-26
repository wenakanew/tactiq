from __future__ import annotations

from pydantic import ValidationError

from tactiq.agents.grounding import GeneratedNarrative, GroundingError, validate_text
from tactiq.domain.models import Insight


def evaluate_narratives(cases: list[dict]) -> dict:
    if not cases:
        raise ValueError("Narrative evaluation needs labelled cases")
    accepted_bad = rejected_bad = accepted_good = rejected_good = 0
    results = []
    for case in cases:
        insight = Insight.model_validate(case["insight"])
        try:
            generated = GeneratedNarrative.model_validate(case["response"])
            validate_text(f"{generated.title} {generated.body}", insight, generated.claims, generated.entities)
            accepted = True
            reason = "structure-numbers-and-entity-ids-checked"
        except (ValidationError, GroundingError) as exc:
            accepted = False
            reason = type(exc).__name__
        expected = case["expected_valid"]
        if not isinstance(expected, bool):
            raise ValueError("Narrative labels must be booleans")
        if expected:
            accepted_good += accepted
            rejected_good += not accepted
        else:
            accepted_bad += accepted
            rejected_bad += not accepted
        results.append({"case_id": case["case_id"], "accepted": accepted, "expected_valid": expected, "reason": reason})
    return {
        "evaluated_cases": len(cases), "unsupported_claims": accepted_bad,
        "valid_acceptance_rate": accepted_good / (accepted_good + rejected_good) if accepted_good + rejected_good else None,
        "invalid_rejection_rate": rejected_bad / (accepted_bad + rejected_bad) if accepted_bad + rejected_bad else None,
        "results": results,
        "scope": "Deterministic structure/numeric/entity-ID checks; semantic and multilingual review remain separate.",
    }
