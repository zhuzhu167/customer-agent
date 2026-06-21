from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models import LiveKitRoomSession, VoiceEvent, VoiceSession


def test_create_session_returns_livekit_join_info(client: TestClient, db_session: Session) -> None:
    response = client.post(
        "/api/v1/sessions",
        json={"mode": "demo", "client_capabilities": {"audio": True}},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "listening"
    assert body["mode"] == "demo"
    assert body["livekit"]["room_name"].startswith("voice-")
    assert body["livekit"]["token"]

    session = db_session.get(VoiceSession, body["session_id"])
    assert session is not None
    room = db_session.scalar(select(LiveKitRoomSession).where(LiveKitRoomSession.session_id == session.id))
    assert room is not None

    events = db_session.scalars(select(VoiceEvent).where(VoiceEvent.session_id == session.id)).all()
    assert [event.event_type for event in events] == ["session.create", "session.connected"]


def test_create_session_returns_public_livekit_url(client: TestClient, settings) -> None:
    settings.livekit_url = "ws://livekit:7880"
    settings.public_livekit_url = "ws://127.0.0.1:7880"

    response = client.post(
        "/api/v1/sessions",
        json={"mode": "demo", "client_capabilities": {"audio": True}},
    )

    assert response.status_code == 201
    assert response.json()["livekit"]["url"] == "ws://127.0.0.1:7880"


def test_end_session_marks_session_closed(client: TestClient) -> None:
    created = client.post("/api/v1/sessions", json={"mode": "normal"}).json()
    response = client.post(
        f"/api/v1/sessions/{created['session_id']}/end",
        json={"reason": "user_requested"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ended"
    assert response.json()["ended_at"]
