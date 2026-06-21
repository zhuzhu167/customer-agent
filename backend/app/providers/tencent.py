from __future__ import annotations

from backend.app.core.errors import ProviderInvocationError
from backend.app.core.secrets import resolve_tencent_credentials
from backend.app.providers.base import ASRProvider, ProviderConfigDTO, TTSProvider, TTSResult, TranscriptResult
from backend.app.providers.tencent_cloud_api import (
    TencentCloudApiClient,
    decode_audio_base64,
    encode_audio_base64,
)
from backend.app.providers.tencent_realtime_asr import TencentRealtimeASRConfig, TencentRealtimeASRSigner


class TencentCloudASRProvider(ASRProvider):
    required_secret_hint = "env:TENCENT_CLOUD_SECRET_ID,TENCENT_CLOUD_SECRET_KEY"

    def __init__(self, config: ProviderConfigDTO) -> None:
        self.config = config
        options = config.options or {}
        self.credentials = resolve_tencent_credentials(
            config.secret_ref or self.required_secret_hint,
            fallback_file=options.get("secret_file"),
            app_id=options.get("app_id"),
        )
        self.realtime_config = TencentRealtimeASRConfig(
            host=str(options.get("realtime_host") or "asr.cloud.tencent.com"),
            engine_model_type=str(options.get("realtime_engine_model_type") or config.model),
            voice_format=int(options.get("realtime_voice_format") or 1),
            filter_dirty=int(options.get("realtime_filter_dirty") or 0),
            filter_modal=int(options.get("realtime_filter_modal") or 0),
            filter_punc=int(options.get("realtime_filter_punc") or 0),
            convert_num_mode=int(options.get("realtime_convert_num_mode") or 1),
        )
        self.client = TencentCloudApiClient(
            credentials=self.credentials,
            service="asr",
            endpoint=str(options.get("endpoint") or "asr.tencentcloudapi.com"),
            region=config.region or "ap-guangzhou",
        )

    def build_realtime_signer(self) -> TencentRealtimeASRSigner:
        return TencentRealtimeASRSigner(credentials=self.credentials, config=self.realtime_config)

    async def transcribe(self, audio: bytes, *, content_type: str) -> TranscriptResult:
        if not audio:
            raise ProviderInvocationError(
                "腾讯云 ASR 输入音频为空",
                detail={"provider": self.config.provider_name, "content_type": content_type},
            )

        response = await self.client.call(
            action="SentenceRecognition",
            version="2019-06-14",
            payload={
                "ProjectId": 0,
                "SubServiceType": 2,
                "EngSerViceType": self.config.model,
                "SourceType": 1,
                "VoiceFormat": _guess_voice_format(content_type),
                "UsrAudioKey": "browser-simulated-call",
                "Data": encode_audio_base64(audio),
                "DataLen": len(audio),
            },
        )
        result_text = str(response.get("Result") or "").strip()
        if not result_text:
            raise ProviderInvocationError(
                "腾讯云 ASR 响应缺少 Result",
                detail={"provider": self.config.provider_name, "request_id": response.get("RequestId")},
            )
        confidence = response.get("Confidence")
        return TranscriptResult(
            text=result_text,
            confidence=float(confidence) if isinstance(confidence, (float, int)) else None,
            is_final=True,
        )


class TencentCloudTTSProvider(TTSProvider):
    required_secret_hint = "env:TENCENT_CLOUD_SECRET_ID,TENCENT_CLOUD_SECRET_KEY"

    def __init__(self, config: ProviderConfigDTO) -> None:
        self.config = config
        options = config.options or {}
        self.codec = str(options.get("codec") or "wav").lower()
        self.sample_rate = int(options.get("sample_rate") or 16000)
        self.credentials = resolve_tencent_credentials(
            config.secret_ref or self.required_secret_hint,
            fallback_file=options.get("secret_file"),
            app_id=options.get("app_id"),
        )
        self.client = TencentCloudApiClient(
            credentials=self.credentials,
            service="tts",
            endpoint=str(options.get("endpoint") or "tts.tencentcloudapi.com"),
            region=config.region or "ap-guangzhou",
        )

    async def synthesize(self, text: str) -> TTSResult:
        normalized = " ".join(text.strip().split())
        if not normalized:
            raise ProviderInvocationError("腾讯云 TTS 输入文本为空", detail={"provider": self.config.provider_name})

        response = await self.client.call(
            action="TextToVoice",
            version="2019-08-23",
            payload={
                "Text": normalized[:150],
                "SessionId": "browser-simulated-call",
                "ModelType": 1,
                "VoiceType": int(self.config.model),
                "Codec": self.codec,
                "SampleRate": self.sample_rate,
                "Speed": 0,
                "Volume": 0,
            },
        )
        audio = response.get("Audio")
        if not isinstance(audio, str) or not audio:
            raise ProviderInvocationError(
                "腾讯云 TTS 响应缺少 Audio",
                detail={"provider": self.config.provider_name, "request_id": response.get("RequestId")},
            )
        return TTSResult(
            audio_bytes=decode_audio_base64(audio),
            content_type=_tts_content_type(self.codec),
            provider_name=self.config.provider_name,
            raw_metadata={"request_id": response.get("RequestId")},
        )


def _guess_voice_format(content_type: str) -> str:
    normalized = content_type.lower()
    if "wav" in normalized:
        return "wav"
    if "mp3" in normalized or "mpeg" in normalized:
        return "mp3"
    if "m4a" in normalized:
        return "m4a"
    if "ogg" in normalized:
        return "ogg-opus"
    return "wav"


def _tts_content_type(codec: str) -> str:
    if codec == "wav":
        return "audio/wav"
    if codec == "pcm":
        return "audio/L16"
    return "audio/mpeg"
