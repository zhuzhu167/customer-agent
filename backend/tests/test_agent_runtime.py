from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models import ConversationTurn, VoiceEvent


def test_mock_agent_turn_persists_user_preparing_and_answered_turns(
    client: TestClient,
    db_session: Session,
) -> None:
    session_id = client.post("/api/v1/sessions", json={"mode": "debug"}).json()["session_id"]

    response = client.post(
        "/api/v1/agent/turn",
        json={"session_id": session_id, "text": "我想了解一下办理业务需要准备什么资料"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["preparing_visible"] is True
    assert "资料" in body["response_text"]
    assert "智能客服" in body["response_text"] or "以人工或正式系统确认为准" in body["response_text"]

    turns = db_session.scalars(
        select(ConversationTurn).where(ConversationTurn.session_id == session_id).order_by(ConversationTurn.role)
    ).all()
    roles = {turn.role for turn in turns}
    assert roles == {"user", "agent_preparing", "agent_answered"}

    events = db_session.scalars(select(VoiceEvent).where(VoiceEvent.session_id == session_id)).all()
    event_types = {event.event_type for event in events}
    assert {"transcript.final", "agent.reply.preparing", "agent.reply.answered"}.issubset(event_types)
