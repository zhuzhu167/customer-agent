from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.core.errors import BackendError
from backend.app.db.models import LiveKitRoomSession
from backend.app.services.event_store import EventStore
from backend.app.services.livekit_tokens import LiveKitTokenService
from backend.app.services.session_service import SessionService


class VoiceWorkerSessionError(BackendError):
    error_code = "voice_worker.session_not_ready"


@dataclass(frozen=True)
class VoiceWorkerJoinInfo:
    session_id: UUID
    room_name: str
    worker_identity: str
    token: str
    livekit_url: str
    token_expires_in: int


class VoiceWorkerService:
    def __init__(self, db: Session, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.sessions = SessionService(db, settings)
        self.events = EventStore(db)
        self.livekit_tokens = LiveKitTokenService(settings)

    def prepare_worker(self, *, session_id: UUID) -> VoiceWorkerJoinInfo:
        session = self.sessions.require_session(session_id)
        room = self.db.scalar(select(LiveKitRoomSession).where(LiveKitRoomSession.session_id == session.id))
        if not room:
            raise VoiceWorkerSessionError(
                "会话缺少 LiveKit 房间映射",
                detail={"session_id": str(session_id)},
            )

        token = self.livekit_tokens.issue_join_token(
            room_name=room.room_name,
            identity=room.worker_identity,
            name="voice-worker",
        )
        self.events.create_event(
            session_id=session.id,
            event_type="voice_worker.prepared",
            payload={
                "room_name": room.room_name,
                "worker_identity": room.worker_identity,
                "livekit_url": self.settings.livekit_url,
            },
        )
        self.db.commit()
        return VoiceWorkerJoinInfo(
            session_id=session.id,
            room_name=room.room_name,
            worker_identity=room.worker_identity,
            token=token.token,
            livekit_url=self.settings.livekit_url,
            token_expires_in=token.expires_in,
        )
