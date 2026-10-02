from __future__ import annotations

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from tactiq.application.service import service
from tactiq.simulator.scenarios import list_scenarios

app = FastAPI(title="Match Intelligence API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CreateMatchRequest(BaseModel):
    scenario_id: str = Field(default="sustained_pressure")
    seed: int = Field(default=42001)


class NarrativeRequest(BaseModel):
    audience_mode: str = Field(default="fan")
    language: str = Field(default="en-GB")
    insight_id: str | None = None
    selected_player_id: str | None = None


class RecapRequest(BaseModel):
    audience_mode: str = Field(default="fan")
    language: str = Field(default="en-GB")
    selected_player_id: str | None = None


class SpeechRequest(BaseModel):
    text: str
    language: str = Field(default="en-GB")
    voice: str | None = None
    rate: str = Field(default="+2%")
    pitch: str = Field(default="+1st")


class DualSpeechRequest(BaseModel):
    primary_text: str
    secondary_text: str
    language: str = Field(default="en-GB")
    primary_voice: str | None = None
    secondary_voice: str | None = None


@app.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
def ready() -> dict[str, str]:
    return {"status": "ready"}


@app.post("/api/v1/matches")
def create_match(payload: CreateMatchRequest) -> dict:
    try:
        state = service.create_match(scenario_id=payload.scenario_id, seed=payload.seed)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return state.model_dump()


@app.get("/api/v1/scenarios")
def get_scenarios() -> list[dict]:
    return [scenario.__dict__ for scenario in list_scenarios()]


@app.post("/api/v1/matches/{match_id}/start")
async def start_match(match_id: str) -> dict[str, str]:
    if match_id not in service.matches:
        raise HTTPException(status_code=404, detail="Match not found")
    await service.start_match(match_id)
    return {"status": "started"}


@app.get("/api/v1/matches/{match_id}/state")
def get_state(match_id: str) -> dict:
    if match_id not in service.matches:
        raise HTTPException(status_code=404, detail="Match not found")
    return service.get_match(match_id).model_dump()


@app.get("/api/v1/matches/{match_id}/events")
def get_events(match_id: str) -> list[dict]:
    if match_id not in service.matches:
        raise HTTPException(status_code=404, detail="Match not found")
    return [e.model_dump() for e in service.get_events(match_id)]


@app.get("/api/v1/matches/{match_id}/insights")
def get_insights(match_id: str) -> list[dict]:
    if match_id not in service.matches:
        raise HTTPException(status_code=404, detail="Match not found")
    return [i.model_dump() for i in service.get_insights(match_id)]


@app.post("/api/v1/matches/{match_id}/narratives")
def generate_narrative(match_id: str, payload: NarrativeRequest) -> dict:
    if match_id not in service.matches:
        raise HTTPException(status_code=404, detail="Match not found")
    try:
        result = service.generate_narrative(
            match_id=match_id,
            audience_mode=payload.audience_mode,
            language=payload.language,
            insight_id=payload.insight_id,
            selected_player_id=payload.selected_player_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "title": result.title,
        "body": result.body,
        "audience_mode": result.audience_mode,
        "language": result.language,
        "fallback_used": result.fallback_used,
        "provider": result.provider,
    }


@app.post("/api/v1/matches/{match_id}/recap")
def generate_recap(match_id: str, payload: RecapRequest) -> dict:
    if match_id not in service.matches:
        raise HTTPException(status_code=404, detail="Match not found")
    try:
        recap, top_insights = service.generate_recap(
            match_id=match_id,
            audience_mode=payload.audience_mode,
            language=payload.language,
            selected_player_id=payload.selected_player_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "title": recap.title,
        "body": recap.body,
        "audience_mode": recap.audience_mode,
        "language": recap.language,
        "fallback_used": recap.fallback_used,
        "provider": recap.provider,
        "top_insights": [
            {
                "insight_id": insight.insight_id,
                "title": insight.title,
                "confidence": insight.confidence,
                "team_id": insight.team_id,
            }
            for insight in top_insights
        ],
    }


@app.post("/api/v1/speech/synthesize")
def synthesize_speech(payload: SpeechRequest) -> dict:
    try:
        result = service.synthesize_speech(
            text=payload.text,
            language=payload.language,
            voice=payload.voice,
            rate=payload.rate,
            pitch=payload.pitch,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {
        "provider": result.provider,
        "content_type": result.content_type,
        "audio_base64": result.audio_base64,
    }


@app.post("/api/v1/speech/synthesize-dual")
def synthesize_dual_speech(payload: DualSpeechRequest) -> dict:
    try:
        result = service.synthesize_dual_commentary(
            primary_text=payload.primary_text,
            secondary_text=payload.secondary_text,
            language=payload.language,
            primary_voice=payload.primary_voice,
            secondary_voice=payload.secondary_voice,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {
        "provider": result.provider,
        "content_type": result.content_type,
        "audio_base64": result.audio_base64,
    }


@app.websocket("/api/v1/ws/matches/{match_id}")
async def stream_match(websocket: WebSocket, match_id: str) -> None:
    if match_id not in service.matches:
        await websocket.close(code=4404)
        return

    await websocket.accept()
    queue = await service.subscribe(match_id)

    try:
        await websocket.send_json({"type": "match.snapshot", "payload": service.get_match(match_id).model_dump()})
        while True:
            message = await queue.get()
            await websocket.send_json(message)
    except WebSocketDisconnect:
        service.unsubscribe(match_id, queue)
