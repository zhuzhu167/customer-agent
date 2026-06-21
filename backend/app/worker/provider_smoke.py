from __future__ import annotations

import argparse
import asyncio
import json

from backend.app.core.config import get_settings
from backend.app.db.base import Base
from backend.app.db.session import get_session_local
from backend.app.providers.tencent_realtime_asr import TencentRealtimeASRSession
from backend.app.services.provider_config import ProviderConfigService
from backend.app.services.provider_smoke import ProviderSmokeService, SMOKE_TTS_TEXT
from backend.app.worker.audio_publish import decode_wav_audio


async def run_provider_smoke(*, include_realtime_asr: bool, use_config_database: bool = False) -> dict:
    settings = get_settings()
    if use_config_database:
        session_local = get_session_local()
        with session_local() as db:
            return await _run_provider_smoke_with_db(
                db=db,
                settings=settings,
                include_realtime_asr=include_realtime_asr,
            )

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    try:
        with session_local() as db:
            return await _run_provider_smoke_with_db(
                db=db,
                settings=settings,
                include_realtime_asr=include_realtime_asr,
            )
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


async def _run_provider_smoke_with_db(*, db, settings, include_realtime_asr: bool) -> dict:
    service = ProviderSmokeService(db, settings)
    smoke = await service.run()
    result = {
        "llm": {
            "provider": smoke.llm_result.provider_name,
            "model": smoke.llm_result.model,
            "reply_preview": smoke.llm_result.text[:80],
        },
        "tts": {
            "provider": smoke.tts_result.provider_name,
            "content_type": smoke.tts_result.content_type,
            "audio_bytes": len(smoke.tts_result.audio_bytes),
        },
        "asr": {
            "provider": smoke.asr_provider,
            "transcript": smoke.transcript_result.text,
            "confidence": smoke.transcript_result.confidence,
        },
    }

    if include_realtime_asr:
        provider = ProviderConfigService(db, settings).build_asr_provider()
        build_realtime_signer = getattr(provider, "build_realtime_signer", None)
        if build_realtime_signer is None:
            result["realtime_asr"] = {
                "provider": provider.config.provider_name,
                "skipped": True,
                "reason": "当前 ASR Provider 不支持实时 ASR",
            }
        elif smoke.tts_result.content_type != "audio/wav":
            result["realtime_asr"] = {
                "provider": provider.config.provider_name,
                "skipped": True,
                "reason": "实时 ASR smoke 需要 TTS 返回 audio/wav",
            }
        else:
            pcm_audio = decode_wav_audio(smoke.tts_result.audio_bytes)
            if pcm_audio.sample_rate != 16000 or pcm_audio.num_channels != 1 or pcm_audio.sample_width != 2:
                result["realtime_asr"] = {
                    "provider": provider.config.provider_name,
                    "skipped": True,
                    "reason": (
                        "实时 ASR smoke 需要 16k mono 16-bit WAV，"
                        f"当前为 {pcm_audio.sample_rate}Hz/{pcm_audio.num_channels}ch/{pcm_audio.sample_width}B"
                    ),
                }
            else:
                session = TencentRealtimeASRSession(signer=build_realtime_signer())
                messages = await session.recognize_pcm(pcm_audio.pcm, voice_id="provider-smoke")
                result["realtime_asr"] = {
                    "provider": provider.config.provider_name,
                    "messages": len(messages),
                    "final_text": _extract_final_text(messages),
                }

    result["smoke_text"] = SMOKE_TTS_TEXT
    return result


def _extract_final_text(messages: list[dict]) -> str | None:
    for message in reversed(messages):
        result = message.get("result")
        if isinstance(result, dict):
            for key in ("voice_text_str", "text", "result"):
                value = result.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        for key in ("text", "voice_text_str", "result"):
            value = message.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Run real cloud provider smoke checks.")
    parser.add_argument(
        "--include-realtime-asr",
        action="store_true",
        help="Also send TTS-generated WAV PCM through Tencent realtime ASR WebSocket.",
    )
    parser.add_argument(
        "--use-config-database",
        action="store_true",
        help="Use configured DATABASE_URL instead of an in-memory smoke database.",
    )
    args = parser.parse_args()
    result = asyncio.run(
        run_provider_smoke(
            include_realtime_asr=args.include_realtime_asr,
            use_config_database=args.use_config_database,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
