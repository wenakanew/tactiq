from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re

from pydantic import BaseModel, ConfigDict, Field

from tactiq.domain.models import Insight


class GroundingError(ValueError):
    pass


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric: str
    value: float = Field(allow_inf_nan=False)
    supporting_event_ids: list[str] = Field(min_length=1, max_length=200)


class GeneratedNarrative(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=2000)
    claims: list[Claim] = Field(default_factory=list, max_length=30)
    entities: list[str] = Field(default_factory=list, max_length=50)


class GeneratedLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=500)
    claims: list[Claim] = Field(default_factory=list, max_length=10)
    entities: list[str] = Field(default_factory=list, max_length=30)


def _number(value: str | int | float) -> Decimal:
    return Decimal(str(value)).normalize()


def validate_text(text: str, insight: Insight, claims: list[Claim] | None = None, entities: list[str] | None = None) -> None:
    allowed_entities = {insight.team_id, *insight.context.get("known_player_ids", [])}
    if insight.player_id:
        allowed_entities.add(insight.player_id)
    allowed_entities.update(insight.context.get("team_display_names", {}).values())
    if any(entity not in allowed_entities for entity in entities or []):
        raise GroundingError("Generated response names an unsupported entity")
    for identifier in re.findall(r"\b(?:team_[a-z0-9_]+|[abpq]_\d+|(?:player|ball)_\d+)\b", text, re.IGNORECASE):
        if identifier not in allowed_entities:
            raise GroundingError("Generated response attributes an unsupported player or team")
    if re.search(r"\b(?:entire match|whole match|all game|dominated the match|won the match|final score|sent off|injured|penalty awarded|world record)\b",
                 text, re.IGNORECASE):
        raise GroundingError("Generated response exceeds the supported temporal or event scope")
    if re.search(r"\bscored\b", text, re.IGNORECASE) and "goal" not in insight.context.get("event_types", []):
        raise GroundingError("Generated response claims a goal outside the supported window")
    allowed_numbers = {_number(item.value) for item in insight.evidence}
    allowed_numbers.update({
        _number(insight.window_start_ms), _number(insight.window_end_ms),
        _number(insight.window_start_ms // 1000), _number(insight.window_end_ms // 1000),
        _number(round(insight.confidence * 100)), _number(insight.confidence),
    })
    checked = text
    for entity in sorted(allowed_entities, key=len, reverse=True):
        checked = checked.replace(entity, "")
    if insight.context.get("language", "en-GB") == "en-GB":
        vocabulary = set(re.findall(r"[A-Za-z]+", f"{insight.title} {insight.summary}"))
        vocabulary |= {
            "A", "An", "The", "This", "That", "These", "Those", "They", "Their", "It", "Its", "We",
            "Now", "Look", "Yes", "No", "And", "But", "So", "Here", "There", "What", "Why", "How",
            "Pressure", "Building", "Momentum", "Shift", "Player", "Influence", "Rising", "Speed", "Burst",
            "Detected", "Match", "Insight", "Window", "Evidence", "Team", "Fast", "Transition", "Local",
            "Numerical", "Overload", "Pressing", "Convergence", "Post", "Recap", "Strong", "Sharp",
            "Quick", "Rapid", "Measured", "High", "Sustained", "Limited", "More", "Absolutely", "Agreed",
            "Across", "During", "Within", "However", "Because", "Taken", "Together", "Crucially", "Rather",
            "Only", "Keep", "Although", "Each", "Both", "Instead", "Every", "Overall", "For", "Without",
            "Tracking", "Calibration", "Simulated", "Recorded", "Replay", "From", "Our", "Notice",
        }
        if any(word not in vocabulary for word in re.findall(r"\b[A-Z][a-z]{2,}\b", checked)):
            raise GroundingError("Generated response contains an unprovided proper-name candidate")
        words = {
            "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
            "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
            "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
            "nineteen": 19, "twenty": 20,
        }
        for word, count in words.items():
            checked = re.sub(rf"\b{word}\b(?=\s+(?:shots?|passes|entries|players?|progressive actions))",
                             str(count), checked, flags=re.IGNORECASE)
    numeric_tokens = re.findall(r"(?<![\w])[-+]?\d+(?:[.,]\d+)?(?![\w])", checked)
    for token in numeric_tokens:
        try:
            number = _number(token.replace(",", "."))
        except InvalidOperation as exc:
            raise GroundingError("Generated response contains an invalid number") from exc
        if number not in allowed_numbers:
            raise GroundingError("Generated response contains a number absent from the evidence")
    for evidence in insight.evidence:
        label = re.escape(evidence.metric.replace("_", " "))
        for value in re.findall(rf"([-+]?\d+(?:[.,]\d+)?)\s+{label}\b", checked, re.IGNORECASE):
            if _number(value.replace(",", ".")) != _number(evidence.value):
                raise GroundingError("Generated text assigns the wrong value to a metric")
    if numeric_tokens and not claims:
        raise GroundingError("Numerical statements must declare their evidence")
    for claim in claims or []:
        matches = [item for item in insight.evidence if item.metric == claim.metric and _number(item.value) == _number(claim.value)]
        if not matches or not any(set(claim.supporting_event_ids) <= set(item.supporting_event_ids) for item in matches):
            raise GroundingError("Generated claim does not resolve to the supplied evidence")
