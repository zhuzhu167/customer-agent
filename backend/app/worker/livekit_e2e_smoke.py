from __future__ import annotations

import argparse
import asyncio
import json
from uuid import UUID

import httpx
from sqlalchemy import select

from backend.app.core.config import get_settings
from backend.app.db.models import VoiceEvent
from backend.app.db.session import get_session_local
from backend.app.services.provider_config import ProviderConfigService
from backend.app.services.provider_smoke import SMOKE_TTS_TEXT
from backend.app.worker.audio_publish import publish_wav_audio


async def run_livekit_e2e_smoke(*, api_base_url: str, timeout_seconds: float) -> dict:
    session_payload = await _create_session(api_base_url)
    session_id = UUID(session_payload["session_id"])
    livekit = session_payload["livekit"]
    published = False
    try:
        wav_audio = await _build_input_wav()
        await _publish_caller_audio(livekit=livekit, wav_audio=wav_audio)
        published = True
        events = await _wait_for_events(
            session_id=session_id,
            expected={"transcript.final", "voice_worker.agent_audio_published"},
            timeout_seconds=timeout_seconds,
        )
        return {
            "session_id": str(session_id),
            "room_name": livekit["room_name"],
            "published_caller_audio": published,
            "events": [
                {
                    "event_type": event.event_type,
                    "payload": event.payload,
                }
                for event in events
            ],
        }
    finally:
        await _end_session(api_base_url, session_id)


async def _create_session(api_base_url: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{api_base_url.rstrip('/')}/api/v1/sessions",
            json={
                "mode": "debug",
                "client_capabilities": {
                    "livekit": True,
                    "microphone": True,
                    "smoke": "synthetic-livekit-caller",
                },
            },
        )
    response.raise_for_status()
    return response.json()


async def _end_session(api_base_url: str, session_id: UUID) -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        await client.post(
            f"{api_base_url.rstrip('/')}/api/v1/sessions/{session_id}/end",
            json={"reason": "e2e_smoke_finished"},
        )


async def _build_input_wav() -> bytes:
    settings = get_settings()
    session_local = get_session_local()
    with session_local() as db:
        tts = ProviderConfigService(db, settings).build_tts_provider()
        result = await tts.synthesize(SMOKE_TTS_TEXT)
        if result.content_type != "audio/wav":
            raise RuntimeError(f"LiveKit e2e smoke 需要 audio/wav，当前为 {result.content_type}")
        return result.audio_bytes


async def _publish_caller_audio(*, livekit: dict, wav_audio: bytes) -> None:
    from livekit import rtc

    room = rtc.Room()
    await room.connect(livekit["url"], livekit["token"])
    try:
        await publish_wav_audio(room, wav_audio, track_name="synthetic-browser-microphone")
        await asyncio.sleep(0.5)
    finally:
        await room.disconnect()


async def _wait_for_events(
    *,
    session_id: UUID,
    expected: set[str],
    timeout_seconds: float,
) -> list[VoiceEvent]:
    deadline = asyncio.get_event_loop().time() + timeout_seconds
    session_local = get_session_local()
    last_events: list[VoiceEvent] = []
    while asyncio.get_event_loop().time() < deadline:
        with session_local() as db:
            events = db.scalars(
                select(VoiceEvent).where(VoiceEvent.session_id == session_id).order_by(VoiceEvent.created_at)
            ).all()
            last_events = list(events)
            event_types = {event.event_type for event in events}
            if expected.issubset(event_types):
                return [event for event in events if event.event_type in expected]
        await asyncio.sleep(0.5)
    observed = sorted({event.event_type for event in last_events})
    raise TimeoutError(f"等待事件超时，期望 {sorted(expected)}，已观察 {observed}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run synthetic LiveKit caller e2e smoke.")
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout-seconds", type=float, default=45)
    args = parser.parse_args()
    result = asyncio.run(
        run_livekit_e2e_smoke(
            api_base_url=args.api_base_url,
            timeout_seconds=args.timeout_seconds,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
