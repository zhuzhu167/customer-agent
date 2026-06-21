from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models import VoiceEvent


def test_event_api_rejects_raw_audio_payload(client: TestClient, db_session: Session) -> None:
    session_id = client.post("/api/v1/sessions", json={"mode": "demo"}).json()["session_id"]

    response = client.post(
        f"/api/v1/sessions/{session_id}/events",
        json={"event_type": "client.audio", "payload": {"raw_audio": "base64-audio"}},
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "event.unsafe_payload"

    unsafe = db_session.scalars(
        select(VoiceEvent).where(
            VoiceEvent.session_id == session_id,
            VoiceEvent.event_type == "client.audio",
        )
    ).all()
    assert unsafe == []


def test_event_api_accepts_text_only_payload(client: TestClient) -> None:
    session_id = client.post("/api/v1/sessions", json={"mode": "demo"}).json()["session_id"]

    response = client.post(
        f"/api/v1/sessions/{session_id}/events",
        json={"event_type": "transcript.partial", "payload": {"text": "您好"}},
    )

    assert response.status_code == 201
    assert response.json()["payload"] == {"text": "您好"}
