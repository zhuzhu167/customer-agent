from __future__ import annotations

from backend.app.providers.base import (
    ASRProvider,
    LLMProvider,
    LLMResult,
    ProviderConfigDTO,
    TTSProvider,
    TTSResult,
    TranscriptResult,
)


class MockASRProvider(ASRProvider):
    def __init__(self, config: ProviderConfigDTO) -> None:
        self.config = config

    async def transcribe(self, audio: bytes, *, content_type: str) -> TranscriptResult:
        return TranscriptResult(text="这是本地 Mock ASR 转写结果", confidence=0.99, is_final=True)


class MockTTSProvider(TTSProvider):
    def __init__(self, config: ProviderConfigDTO) -> None:
        self.config = config

    async def synthesize(self, text: str) -> TTSResult:
        return TTSResult(
            audio_bytes=f"mock-audio:{text}".encode("utf-8"),
            content_type="audio/mock",
            provider_name=self.config.provider_name,
        )


class MockLLMProvider(LLMProvider):
    def __init__(self, config: ProviderConfigDTO) -> None:
        self.config = config

    async def generate_reply(self, *, session_id: str, user_text: str, history: list[dict]) -> LLMResult:
        prefix = "好的，我先帮您确认一下。"
        if any(keyword in user_text for keyword in ("结束", "不用了", "再见")):
            text = "好的，本次咨询先到这里。后续如果还需要了解业务办理或资料准备，可以随时再来咨询。"
        elif len(user_text.strip()) < 8:
            text = f"{prefix}您方便补充一下具体想咨询的新办、变更、进度查询，还是资料准备问题吗？"
        else:
            text = (
                f"{prefix}您刚才提到“{user_text.strip()}”。"
                "我建议先确认业务类型、办理主体和期望完成时间，再根据资料清单逐项准备；"
                "涉及费用、审批或具体时限的部分，需要以人工或正式系统确认为准。"
            )
        return LLMResult(text=text, model=self.config.model, provider_name=self.config.provider_name)
