from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models import VoiceEvent
from backend.app.providers.base import TranscriptResult


def test_audio_turn_transcribes_replies_and_returns_tts_audio(
    client: TestClient,
    db_session: Session,
) -> None:
    session_id = client.post("/api/v1/sessions", json={"mode": "debug"}).json()["session_id"]

    response = client.post(
        "/api/v1/agent/audio-turn",
        data={"session_id": session_id},
        files={"audio": ("turn.wav", b"fake-audio", "audio/wav")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["transcript_text"]
    assert body["response_text"]
    assert body["tts_audio_base64"]
    assert body["tts_audio_bytes"] > 0

    events = db_session.scalars(select(VoiceEvent).where(VoiceEvent.session_id == session_id)).all()
    event_types = {event.event_type for event in events}
    assert {"transcript.final", "agent.reply.preparing", "agent.reply.answered"}.issubset(event_types)
    for event in events:
        assert "raw_audio" not in event.payload
        assert "audio_bytes" not in event.payload
        assert "tts_audio_base64" not in event.payload


def test_audio_turn_low_confidence_asks_for_confirmation_without_llm(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    from backend.app.providers.mock import MockASRProvider, MockLLMProvider

    async def low_confidence_transcribe(self, audio: bytes, *, content_type: str) -> TranscriptResult:
        return TranscriptResult(text="办业务资料", confidence=0.31, is_final=True)

    async def fail_if_llm_called(self, *, session_id: str, user_text: str, history: list[dict]):
        raise AssertionError("低置信度转写不应直接调用 LLM 推进业务回答")

    monkeypatch.setattr(MockASRProvider, "transcribe", low_confidence_transcribe)
    monkeypatch.setattr(MockLLMProvider, "generate_reply", fail_if_llm_called)

    session_id = client.post("/api/v1/sessions", json={"mode": "debug"}).json()["session_id"]

    response = client.post(
        "/api/v1/agent/audio-turn",
        data={"session_id": session_id},
        files={"audio": ("turn.wav", b"fake-audio", "audio/wav")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["transcript_confidence"] == 0.31
    assert "没有听清楚" in body["response_text"]
    assert "办业务资料" in body["response_text"]
    assert body["tts_audio_bytes"] > 0

    events = db_session.scalars(
        select(VoiceEvent).where(VoiceEvent.session_id == session_id).order_by(VoiceEvent.created_at)
    ).all()
    low_confidence_events = [event for event in events if event.event_type == "turn.low_confidence"]
    assert len(low_confidence_events) == 1
    assert low_confidence_events[0].payload["confidence"] == 0.31
    assert low_confidence_events[0].payload["reason"] == "asr.low_confidence"
    event_types = {event.event_type for event in events}
    assert {"transcript.final", "agent.reply.preparing", "agent.reply.answered"}.issubset(event_types)
