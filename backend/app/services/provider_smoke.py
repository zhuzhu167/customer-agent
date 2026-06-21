from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.providers.base import LLMResult, TTSResult, TranscriptResult
from backend.app.services.provider_config import ProviderConfigService


SMOKE_TTS_TEXT = "这是一轮云服务语音接入冒烟测试。"


@dataclass(frozen=True)
class ProviderSmokeResult:
    llm_result: LLMResult
    tts_result: TTSResult
    transcript_result: TranscriptResult
    asr_provider: str


class ProviderSmokeService:
    def __init__(self, db: Session, settings: Settings) -> None:
        self.providers = ProviderConfigService(db, settings)

    async def run(self) -> ProviderSmokeResult:
        llm = self.providers.build_llm_provider()
        tts = self.providers.build_tts_provider()
        asr = self.providers.build_asr_provider()

        llm_result = await llm.generate_reply(
            session_id="provider-smoke",
            user_text="请用一句话回复：这是一轮云服务接入冒烟测试。",
            history=[],
        )
        tts_result = await tts.synthesize(SMOKE_TTS_TEXT)
        transcript_result = await asr.transcribe(
            tts_result.audio_bytes,
            content_type=tts_result.content_type,
        )

        return ProviderSmokeResult(
            llm_result=llm_result,
            tts_result=tts_result,
            transcript_result=transcript_result,
            asr_provider=asr.config.provider_name,
        )
