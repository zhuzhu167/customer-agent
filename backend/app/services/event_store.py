from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.core.errors import UnsafeEventPayloadError
from backend.app.db.models import VoiceEvent


AUDIO_FIELD_NAMES = {
    "audio_bytes",
    "audio_base64",
    "audio_blob",
    "raw_audio",
    "pcm",
    "wav",
    "media",
}


def _contains_audio_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized == "audio" and not isinstance(item, bool):
                return True
            if normalized in AUDIO_FIELD_NAMES or normalized.endswith("_audio"):
                return True
            if _contains_audio_key(item):
                return True
    elif isinstance(value, list):
        return any(_contains_audio_key(item) for item in value)
    return False


class EventStore:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create_event(
        self,
        *,
        session_id: UUID,
        event_type: str,
        payload: dict[str, Any] | None = None,
        turn_id: UUID | None = None,
    ) -> VoiceEvent:
        safe_payload = payload or {}
        if _contains_audio_key(safe_payload):
            raise UnsafeEventPayloadError(
                "事件 payload 不允许包含原始音频字段",
                detail={"event_type": event_type, "blocked_fields": sorted(AUDIO_FIELD_NAMES)},
            )

        event = VoiceEvent(
            session_id=session_id,
            turn_id=turn_id,
            event_type=event_type,
            payload=safe_payload,
        )
        self.db.add(event)
        self.db.flush()
        return event
