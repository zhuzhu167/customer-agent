from __future__ import annotations

from base64 import b64encode
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.providers.base import TTSResult
from backend.app.runtime.agent_graph import LangGraphAgentRuntime
from backend.app.runtime.turn_manager import TurnManager
from backend.app.services.event_store import EventStore
from backend.app.services.provider_config import ProviderConfigService
from backend.app.services.session_service import SessionService


class AgentTurnService:
    def __init__(self, db: Session, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.sessions = SessionService(db, settings)
        self.events = EventStore(db)
        self.providers = ProviderConfigService(db, settings)
        self.turn_manager = TurnManager(
            low_confidence_threshold=settings.turn_low_confidence_threshold,
        )

    async def handle_text_turn(self, *, session_id: UUID, text: str) -> dict:
        result = await self._handle_transcript_turn(
            session_id=session_id,
            text=text,
            transcript_payload={"text": text, "source": "api"},
        )
        result.pop("tts_audio_base64", None)
        return result

    async def handle_audio_turn(self, *, session_id: UUID, audio: bytes, content_type: str) -> dict:
        asr_provider = self.providers.build_asr_provider()
        transcript = await asr_provider.transcribe(audio, content_type=content_type)
        result = await self._handle_transcript_turn(
            session_id=session_id,
            text=transcript.text,
            transcript_payload={
                "text": transcript.text,
                "source": "audio_upload",
                "content_type": content_type,
                "confidence": transcript.confidence,
                "is_final": transcript.is_final,
            },
            confidence=transcript.confidence,
        )
        result["transcript_text"] = transcript.text
        result["transcript_confidence"] = transcript.confidence
        return result

    async def _handle_transcript_turn(
        self,
        *,
        session_id: UUID,
        text: str,
        transcript_payload: dict,
        confidence: float | None = None,
    ) -> dict:
        result = await self.handle_transcript_turn_internal(
            session_id=session_id,
            text=text,
            transcript_payload=transcript_payload,
            confidence=confidence,
        )

        return {
            "session_id": session_id,
            "user_turn_id": result.user_turn_id,
            "preparing_turn_id": result.preparing_turn_id,
            "answered_turn_id": result.answered_turn_id,
            "response_text": result.response_text,
            "preparing_visible": result.preparing_visible,
            "tts_provider": result.tts_result.provider_name,
            "tts_content_type": result.tts_result.content_type,
            "tts_audio_bytes": len(result.tts_result.audio_bytes),
            "tts_audio_base64": b64encode(result.tts_result.audio_bytes).decode("ascii"),
        }

    async def handle_transcript_turn_internal(
        self,
        *,
        session_id: UUID,
        text: str,
        transcript_payload: dict,
        confidence: float | None = None,
    ) -> "AgentTurnInternalResult":
        session = self.sessions.require_session(session_id)
        turn_index = self.sessions.next_turn_index(session_id)
        turn_decision = self.turn_manager.decide_transcript(text=text, confidence=confidence)

        user_turn = self.sessions.add_turn(
            session_id=session_id,
            turn_index=turn_index,
            role="user",
            text=text,
            visibility="public",
            status="final",
        )
        self.events.create_event(
            session_id=session_id,
            turn_id=user_turn.id,
            event_type="transcript.final",
            payload=transcript_payload,
        )

        if turn_decision.action == "confirm_transcript":
            self.events.create_event(
                session_id=session_id,
                turn_id=user_turn.id,
                event_type="turn.low_confidence",
                payload={
                    "confidence": confidence,
                    "threshold": self.settings.turn_low_confidence_threshold,
                    "reason": turn_decision.reason,
                },
            )
            response_text = turn_decision.response_text or "抱歉，我刚才没有听清楚。麻烦您再说一遍。"
        else:
            llm_provider = self.providers.build_llm_provider()
            runtime = LangGraphAgentRuntime(llm_provider)
            response_text = await runtime.handle_user_turn(
                session_id=str(session_id),
                user_text=text,
                history=self.sessions.get_history(session_id),
            )
        tts_provider = self.providers.build_tts_provider()
        tts_result = await tts_provider.synthesize(response_text)

        preparing_visibility = "demo_only" if session.mode in {"demo", "debug"} else "internal"
        preparing_turn = self.sessions.add_turn(
            session_id=session_id,
            turn_index=turn_index,
            role="agent_preparing",
            text=response_text,
            visibility=preparing_visibility,
            status="completed",
        )
        self.events.create_event(
            session_id=session_id,
            turn_id=preparing_turn.id,
            event_type="agent.reply.preparing",
            payload={
                "text": response_text,
                "visibility": preparing_visibility,
                "visible_to_client": session.mode in {"demo", "debug"},
            },
        )

        answered_turn = self.sessions.add_turn(
            session_id=session_id,
            turn_index=turn_index,
            role="agent_answered",
            text=response_text,
            visibility="public",
            status="completed",
        )
        self.events.create_event(
            session_id=session_id,
            turn_id=answered_turn.id,
            event_type="agent.reply.answered",
            payload={
                "text": response_text,
                "playback": "tts_synthesized",
                "tts_provider": tts_result.provider_name,
                "tts_content_type": tts_result.content_type,
                "tts_audio_bytes": len(tts_result.audio_bytes),
            },
        )
        self.db.commit()

        return AgentTurnInternalResult(
            session_id=session_id,
            user_turn_id=user_turn.id,
            preparing_turn_id=preparing_turn.id,
            answered_turn_id=answered_turn.id,
            response_text=response_text,
            preparing_visible=session.mode in {"demo", "debug"},
            tts_result=tts_result,
        )


@dataclass(frozen=True)
class AgentTurnInternalResult:
    session_id: UUID
    user_turn_id: UUID
    preparing_turn_id: UUID
    answered_turn_id: UUID
    response_text: str
    preparing_visible: bool
    tts_result: TTSResult
