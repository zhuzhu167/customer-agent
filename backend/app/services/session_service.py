from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.core.errors import SessionNotFoundError
from backend.app.db.models import ConversationTurn, LiveKitRoomSession, VoiceSession
from backend.app.schemas import SessionMode
from backend.app.services.event_store import EventStore
from backend.app.services.livekit_tokens import LiveKitTokenService


class SessionService:
    def __init__(self, db: Session, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.events = EventStore(db)
        self.livekit_tokens = LiveKitTokenService(settings)

    def create_session(self, *, mode: SessionMode, client_capabilities: dict) -> tuple[VoiceSession, dict]:
        session = VoiceSession(status="connecting", mode=mode)
        self.db.add(session)
        self.db.flush()

        room_name = f"voice-{session.id.hex[:12]}"
        user_identity = f"user-{session.id.hex[:12]}"
        worker_identity = f"worker-{session.id.hex[:12]}"
        token = self.livekit_tokens.issue_join_token(
            room_name=room_name,
            identity=user_identity,
            name="web-caller",
        )
        room = LiveKitRoomSession(
            session_id=session.id,
            room_name=room_name,
            user_identity=user_identity,
            worker_identity=worker_identity,
        )
        self.db.add(room)
        self.events.create_event(
            session_id=session.id,
            event_type="session.create",
            payload={
                "mode": mode,
                "client_capabilities": client_capabilities,
                "livekit_room": room_name,
            },
        )
        session.status = "listening"
        self.events.create_event(
            session_id=session.id,
            event_type="session.connected",
            payload={"room_name": room_name, "worker_identity": worker_identity},
        )
        self.db.commit()
        self.db.refresh(session)

        return session, {
            "url": self.settings.public_livekit_url or self.settings.livekit_url,
            "room_name": room_name,
            "token": token.token,
            "user_identity": user_identity,
            "worker_identity": worker_identity,
            "token_expires_in": token.expires_in,
        }

    def end_session(self, *, session_id: UUID, reason: str) -> VoiceSession:
        session = self.db.get(VoiceSession, session_id)
        if not session:
            raise SessionNotFoundError("会话不存在", detail={"session_id": str(session_id)})
        session.status = "ended"
        session.ended_at = datetime.now(timezone.utc)
        session.error_code = None if reason == "user_requested" else reason
        self.events.create_event(
            session_id=session.id,
            event_type="session.closed",
            payload={"reason": reason},
        )
        self.db.commit()
        self.db.refresh(session)
        return session

    def next_turn_index(self, session_id: UUID) -> int:
        current = self.db.scalar(
            select(func.max(ConversationTurn.turn_index)).where(ConversationTurn.session_id == session_id)
        )
        return int(current or 0) + 1

    def get_history(self, session_id: UUID) -> list[dict]:
        turns = self.db.scalars(
            select(ConversationTurn)
            .where(ConversationTurn.session_id == session_id)
            .order_by(ConversationTurn.turn_index, ConversationTurn.created_at)
        ).all()
        return [
            {
                "role": turn.role,
                "text": turn.text,
                "status": turn.status,
                "visibility": turn.visibility,
            }
            for turn in turns
        ]

    def add_turn(
        self,
        *,
        session_id: UUID,
        turn_index: int,
        role: str,
        text: str,
        visibility: str = "public",
        status: str = "completed",
    ) -> ConversationTurn:
        turn = ConversationTurn(
            session_id=session_id,
            turn_index=turn_index,
            role=role,
            text=text,
            visibility=visibility,
            status=status,
            completed_at=datetime.now(timezone.utc),
        )
        self.db.add(turn)
        self.db.flush()
        return turn

    def require_session(self, session_id: UUID) -> VoiceSession:
        session = self.db.get(VoiceSession, session_id)
        if not session:
            raise SessionNotFoundError("会话不存在", detail={"session_id": str(session_id)})
        return session
