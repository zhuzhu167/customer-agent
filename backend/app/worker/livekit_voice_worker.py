from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from backend.app.core.config import Settings, get_settings
from backend.app.db.models import VoiceSession
from backend.app.db.session import get_session_local
from backend.app.providers.tencent_realtime_asr import TencentRealtimeASRSession, TencentRealtimeASRStream
from backend.app.services.agent_service import AgentTurnService
from backend.app.services.event_store import EventStore
from backend.app.services.provider_config import ProviderConfigService
from backend.app.services.voice_worker import VoiceWorkerService
from backend.app.worker.asr_sink import StreamingTencentRealtimeASRSink
from backend.app.worker.audio_publish import publish_wav_audio


@dataclass(frozen=True)
class WorkerRunResult:
    audio_frames_seen: int


class AudioFrameSink(Protocol):
    async def push_pcm(self, pcm: bytes, *, sample_rate: int, num_channels: int) -> None:
        ...


class NullAudioFrameSink:
    async def push_pcm(self, pcm: bytes, *, sample_rate: int, num_channels: int) -> None:
        return None

    async def close(self) -> None:
        return None


@dataclass
class PlaybackState:
    answered_turn_id: object
    response_text: str
    interrupt_event: asyncio.Event
    interrupted: bool = False
    voice_frames_seen: int = 0
    interruption_id: str | None = None
    follow_up_transcript_received: bool = False


class LiveKitVoiceWorker:
    def __init__(
        self,
        *,
        db: Session,
        settings: Settings,
        session_id: UUID,
        audio_sink: AudioFrameSink | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.session_id = session_id
        self.events = EventStore(db)
        self.frames_seen = 0
        self.frontend_sequence = 1
        self.audio_sink = audio_sink or build_default_audio_sink(db=db, settings=settings)
        self.room = None
        self.last_audio_activity_monotonic: float | None = None
        self.last_transcript_activity_monotonic: float | None = None
        self.silence_prompt_sent = False
        self.current_playback: PlaybackState | None = None
        self.pending_interruptions: dict[str, PlaybackState] = {}
        self.interruption_recovery_tasks: set[asyncio.Task] = set()

    async def run(self, *, max_seconds: float | None = None) -> WorkerRunResult:
        try:
            from livekit import rtc
        except ImportError as exc:
            self._record_error("voice_worker.sdk_missing", "缺少 livekit RTC SDK，无法订阅音频轨。")
            raise RuntimeError("缺少 livekit RTC SDK，请安装 livekit>=1.1.10") from exc

        join = VoiceWorkerService(self.db, self.settings).prepare_worker(session_id=self.session_id)
        room = rtc.Room()
        self.room = room
        stop_event = asyncio.Event()
        tasks: set[asyncio.Task] = set()

        @room.on("track_subscribed")
        def on_track_subscribed(track, publication, participant):
            if getattr(track, "kind", None) != rtc.TrackKind.KIND_AUDIO:
                return
            self.events.create_event(
                session_id=self.session_id,
                event_type="voice_worker.track_subscribed",
                payload={
                    "participant_identity": getattr(participant, "identity", None),
                    "track_sid": getattr(publication, "sid", None),
                    "track_name": getattr(publication, "name", None),
                },
            )
            self.db.commit()
            task = asyncio.create_task(self._consume_audio_track(rtc, track))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
            task.add_done_callback(self._record_task_error)

        @room.on("disconnected")
        def on_disconnected(*args):
            stop_event.set()

        self.events.create_event(
            session_id=self.session_id,
            event_type="voice_worker.connecting",
            payload={"room_name": join.room_name, "worker_identity": join.worker_identity},
        )
        self.db.commit()

        await room.connect(join.livekit_url, join.token)
        self.events.create_event(
            session_id=self.session_id,
            event_type="voice_worker.connected",
            payload={"room_name": join.room_name, "worker_identity": join.worker_identity},
        )
        self.db.commit()
        session_watch_task = asyncio.create_task(
            self._watch_session_closed(stop_event=stop_event),
        )
        tasks.add(session_watch_task)
        session_watch_task.add_done_callback(tasks.discard)
        session_watch_task.add_done_callback(self._record_task_error)
        silence_watch_task = asyncio.create_task(
            self._watch_silence(stop_event=stop_event),
        )
        tasks.add(silence_watch_task)
        silence_watch_task.add_done_callback(tasks.discard)
        silence_watch_task.add_done_callback(self._record_task_error)

        try:
            if max_seconds is None:
                await stop_event.wait()
            else:
                await asyncio.wait_for(stop_event.wait(), timeout=max_seconds)
        except asyncio.TimeoutError:
            self.events.create_event(
                session_id=self.session_id,
                event_type="voice_worker.timeout",
                payload={"max_seconds": max_seconds, "audio_frames_seen": self.frames_seen},
            )
            self.db.commit()
        finally:
            await room.disconnect()
            self.room = None
            for task in list(tasks):
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await self._cancel_interruption_recovery_tasks()
            await self._close_audio_sink()
        return WorkerRunResult(audio_frames_seen=self.frames_seen)

    async def _watch_session_closed(self, *, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            await asyncio.sleep(self.settings.voice_worker_session_poll_seconds)
            self.db.expire_all()
            session = self.db.get(VoiceSession, self.session_id)
            if session is None or session.status == "ended":
                self.events.create_event(
                    session_id=self.session_id,
                    event_type="voice_worker.session_ended",
                    payload={"session_status": getattr(session, "status", None)},
                )
                self.db.commit()
                stop_event.set()
                return

    async def _watch_silence(self, *, stop_event: asyncio.Event) -> None:
        timeout = self.settings.turn_silence_timeout_seconds
        if timeout <= 0:
            return

        while not stop_event.is_set():
            await asyncio.sleep(self.settings.turn_silence_check_interval_seconds)
            if self.silence_prompt_sent:
                continue

            activity = self.last_transcript_activity_monotonic or self.last_audio_activity_monotonic
            if activity is None:
                continue

            if time.monotonic() - activity < timeout:
                continue

            self.silence_prompt_sent = True
            message = "我这边暂时没有听到新的内容，您可以继续说，或者点击结束通话。"
            self.events.create_event(
                session_id=self.session_id,
                event_type="turn.silence_timeout",
                payload={"timeout_seconds": timeout, "message": message},
            )
            self.db.commit()
            await self._publish_frontend_session_error(
                error_code="turn.silence_timeout",
                message=message,
                recoverable=True,
            )

    async def _consume_audio_track(self, rtc, track) -> None:
        try:
            stream = rtc.AudioStream(track, sample_rate=16000, num_channels=1, frame_size_ms=200)
            async for event in stream:
                await self.handle_audio_frame(event.frame)
            await self.finish_audio_track()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._record_error("voice_worker.audio_track_failed", str(exc))

    def _record_task_error(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is None:
            return
        self._record_error("voice_worker.task_failed", str(error))

    async def handle_audio_frame(self, frame) -> None:
        self.frames_seen += 1
        self.last_audio_activity_monotonic = time.monotonic()
        self.silence_prompt_sent = False
        sample_rate = getattr(frame, "sample_rate", None)
        num_channels = getattr(frame, "num_channels", None)
        pcm = bytes(frame.data)
        await self._handle_possible_barge_in(
            pcm=pcm,
            sample_rate=int(sample_rate or 0),
            num_channels=int(num_channels or 0),
        )
        await self.audio_sink.push_pcm(
            pcm,
            sample_rate=int(sample_rate or 0),
            num_channels=int(num_channels or 0),
        )
        await self._handle_asr_messages_if_available()
        self.events.create_event(
            session_id=self.session_id,
            event_type="voice_worker.audio_frame",
            payload={
                "sample_rate": sample_rate,
                "num_channels": num_channels,
                "samples_per_channel": getattr(frame, "samples_per_channel", None),
                "frame_index": self.frames_seen,
                "pcm_bytes": len(pcm),
            },
        )
        self.db.commit()

    async def _handle_asr_messages_if_available(self) -> None:
        pop_messages = getattr(self.audio_sink, "pop_messages", None)
        if pop_messages is None:
            pop_messages = getattr(self.audio_sink, "flush", None)
        if pop_messages is None:
            return
        messages = await pop_messages()
        for message in messages:
            text = extract_final_transcript_text(message)
            if text:
                await self.handle_final_transcript(text)

    async def finish_audio_track(self) -> None:
        finish = getattr(self.audio_sink, "finish", None)
        if finish is None:
            return
        try:
            messages = await finish()
            for message in messages:
                text = extract_final_transcript_text(message)
                if text:
                    await self.handle_final_transcript(text)
        except Exception as exc:  # noqa: BLE001
            self._record_error("voice_worker.asr_track_finish_failed", str(exc))

    async def _close_audio_sink(self) -> None:
        await self.finish_audio_track()

        close = getattr(self.audio_sink, "close", None)
        if close is not None:
            await close()

    async def _cancel_interruption_recovery_tasks(self) -> None:
        for task in list(self.interruption_recovery_tasks):
            task.cancel()
        if self.interruption_recovery_tasks:
            await asyncio.gather(*self.interruption_recovery_tasks, return_exceptions=True)
        self.interruption_recovery_tasks.clear()
        self.pending_interruptions.clear()

    def _record_error(self, code: str, message: str) -> None:
        self.events.create_event(
            session_id=self.session_id,
            event_type="voice_worker.error",
            payload={"error_code": code, "message": message},
        )
        self.db.commit()

    async def _handle_possible_barge_in(self, *, pcm: bytes, sample_rate: int, num_channels: int) -> None:
        playback = self.current_playback
        if playback is None or playback.interrupted:
            return
        rms = pcm_rms_16bit(pcm)
        if sample_rate != 16000 or num_channels != 1 or rms < self.settings.turn_barge_in_rms_threshold:
            playback.voice_frames_seen = 0
            return

        playback.voice_frames_seen += 1
        if playback.voice_frames_seen < self.settings.turn_barge_in_min_voice_frames:
            return

        interruption_id = uuid4().hex
        playback.interrupted = True
        playback.interruption_id = interruption_id
        playback.interrupt_event.set()
        self.pending_interruptions[interruption_id] = playback
        self.events.create_event(
            session_id=self.session_id,
            turn_id=playback.answered_turn_id,
            event_type="agent.reply.interrupted",
            payload={
                "interruption_id": interruption_id,
                "reason": "barge_in",
                "played_text": playback.response_text,
                "rms": rms,
                "voice_frames_seen": playback.voice_frames_seen,
                "rms_threshold": self.settings.turn_barge_in_rms_threshold,
            },
        )
        self.db.commit()
        await self._publish_frontend_interrupted(
            answered_turn_id=playback.answered_turn_id,
            played_text=playback.response_text,
            reason="barge_in",
            interruption_id=interruption_id,
        )
        self._schedule_interruption_recovery(playback)

    async def handle_final_transcript(self, text: str) -> None:
        normalized = " ".join(text.strip().split())
        if not normalized:
            return
        self.last_transcript_activity_monotonic = time.monotonic()
        self.silence_prompt_sent = False
        self._mark_pending_interruptions_followed_up()
        result = await AgentTurnService(self.db, self.settings).handle_transcript_turn_internal(
            session_id=self.session_id,
            text=normalized,
            transcript_payload={"text": normalized, "source": "voice_worker.realtime_asr", "is_final": True},
        )
        self.events.create_event(
            session_id=self.session_id,
            event_type="voice_worker.agent_reply_ready",
            payload={
                "answered_turn_id": str(result.answered_turn_id),
                "tts_content_type": result.tts_result.content_type,
                "tts_audio_bytes": len(result.tts_result.audio_bytes),
            },
        )
        self.db.commit()
        await self._publish_frontend_turn_started(
            transcript_text=normalized,
            user_turn_id=result.user_turn_id,
            preparing_turn_id=result.preparing_turn_id,
            response_text=result.response_text,
            preparing_visible=result.preparing_visible,
        )
        if self.room is not None and result.tts_result.content_type == "audio/wav":
            playback = PlaybackState(
                answered_turn_id=result.answered_turn_id,
                response_text=result.response_text,
                interrupt_event=asyncio.Event(),
            )
            self.current_playback = playback
            try:
                publish_result = await publish_wav_audio(
                    self.room,
                    result.tts_result.audio_bytes,
                    interrupt_event=playback.interrupt_event,
                )
            except Exception as exc:  # noqa: BLE001
                self.current_playback = None
                self._record_error("voice_worker.agent_audio_publish_failed", str(exc))
                await self._publish_frontend_session_error(
                    error_code="tts.playback_failed",
                    message="客服语音播报失败，请重试或结束本次模拟通话。",
                    recoverable=True,
                )
                return
            finally:
                if self.current_playback is playback:
                    self.current_playback = None
            if publish_result is not None and publish_result.interrupted:
                return
            self.events.create_event(
                session_id=self.session_id,
                event_type="voice_worker.agent_audio_published",
                payload={"answered_turn_id": str(result.answered_turn_id), "track_name": "agent-voice"},
            )
            self.db.commit()
            await self._publish_frontend_answered(
                answered_turn_id=result.answered_turn_id,
                response_text=result.response_text,
            )
        elif self.room is not None:
            await self._publish_frontend_session_error(
                error_code="tts.unsupported_content_type",
                message="当前 TTS 音频格式无法通过 LiveKit 播放。",
                recoverable=True,
            )

    def _mark_pending_interruptions_followed_up(self) -> None:
        for playback in self.pending_interruptions.values():
            playback.follow_up_transcript_received = True

    def _schedule_interruption_recovery(self, playback: PlaybackState) -> None:
        timeout = self.settings.turn_interruption_recovery_timeout_seconds
        if timeout <= 0:
            return
        task = asyncio.create_task(self._watch_interruption_recovery(playback=playback, timeout=timeout))
        self.interruption_recovery_tasks.add(task)
        task.add_done_callback(self.interruption_recovery_tasks.discard)
        task.add_done_callback(self._record_task_error)

    async def _watch_interruption_recovery(self, *, playback: PlaybackState, timeout: float) -> None:
        await asyncio.sleep(timeout)
        interruption_id = playback.interruption_id
        if interruption_id is None:
            return
        self.pending_interruptions.pop(interruption_id, None)
        if playback.follow_up_transcript_received:
            return

        message = "刚才的播报可能被短噪声打断，我已恢复到继续聆听状态。"
        self.events.create_event(
            session_id=self.session_id,
            turn_id=playback.answered_turn_id,
            event_type="agent.reply.interrupt_recovered",
            payload={
                "interruption_id": interruption_id,
                "reason": "no_follow_up_transcript",
                "timeout_seconds": timeout,
                "message": message,
                "played_text": playback.response_text,
            },
        )
        self.db.commit()
        await self._publish_frontend_interrupt_recovered(
            answered_turn_id=playback.answered_turn_id,
            interruption_id=interruption_id,
            message=message,
            reason="no_follow_up_transcript",
        )

    async def _publish_frontend_turn_started(
        self,
        *,
        transcript_text: str,
        user_turn_id,
        preparing_turn_id,
        response_text: str,
        preparing_visible: bool,
    ) -> None:
        if self.room is None:
            return

        events = [
            {
                "type": "transcript.final",
                "session_id": str(self.session_id),
                "turn_id": str(user_turn_id),
                "sequence": self._next_frontend_sequence(),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "text": transcript_text,
                "is_final": True,
            },
            {
                "type": "agent.reply.preparing",
                "session_id": str(self.session_id),
                "turn_id": str(preparing_turn_id),
                "sequence": self._next_frontend_sequence(),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "text": response_text,
                "visibility": "demo_only" if preparing_visible else "internal",
            },
        ]
        for event in events:
            await self._publish_frontend_event(event)

    async def _publish_frontend_answered(self, *, answered_turn_id, response_text: str) -> None:
        await self._publish_frontend_event(
            {
                "type": "agent.reply.answered",
                "session_id": str(self.session_id),
                "turn_id": str(answered_turn_id),
                "sequence": self._next_frontend_sequence(),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "text": response_text,
                "completed": True,
            }
        )

    async def _publish_frontend_interrupted(
        self,
        *,
        answered_turn_id,
        played_text: str,
        reason: str,
        interruption_id: str,
    ) -> None:
        await self._publish_frontend_event(
            {
                "type": "agent.reply.interrupted",
                "session_id": str(self.session_id),
                "turn_id": str(answered_turn_id),
                "sequence": self._next_frontend_sequence(),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "interruption_id": interruption_id,
                "played_text": played_text,
                "reason": reason,
            }
        )

    async def _publish_frontend_interrupt_recovered(
        self,
        *,
        answered_turn_id,
        interruption_id: str,
        message: str,
        reason: str,
    ) -> None:
        await self._publish_frontend_event(
            {
                "type": "agent.reply.interrupt_recovered",
                "session_id": str(self.session_id),
                "turn_id": str(answered_turn_id),
                "sequence": self._next_frontend_sequence(),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "interruption_id": interruption_id,
                "message": message,
                "reason": reason,
            }
        )

    async def _publish_frontend_session_error(
        self,
        *,
        error_code: str,
        message: str,
        recoverable: bool,
    ) -> None:
        await self._publish_frontend_event(
            {
                "type": "session.error",
                "session_id": str(self.session_id),
                "sequence": self._next_frontend_sequence(),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "error_code": error_code,
                "message": message,
                "recoverable": recoverable,
            }
        )

    def _next_frontend_sequence(self) -> int:
        sequence = self.frontend_sequence
        self.frontend_sequence += 1
        return sequence

    async def _publish_frontend_event(self, event: dict) -> None:
        if self.room is None:
            return
        try:
            await self.room.local_participant.publish_data(
                json.dumps(event, ensure_ascii=False),
                reliable=True,
                topic="voice-event",
            )
        except Exception as exc:  # noqa: BLE001
            self.events.create_event(
                session_id=self.session_id,
                event_type="voice_worker.data_publish_failed",
                payload={"frontend_event_type": event.get("type"), "message": str(exc)},
            )
            self.db.commit()


def extract_final_transcript_text(message: dict) -> str | None:
    result = message.get("result")
    is_final = message.get("final") in {1, True} or message.get("is_final") is True
    if isinstance(result, dict):
        is_final = is_final or result.get("slice_type") == 2 or result.get("final") == 1
    if not is_final:
        return None

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


def pcm_rms_16bit(pcm: bytes) -> float:
    if len(pcm) < 2:
        return 0.0
    usable = pcm[: len(pcm) - (len(pcm) % 2)]
    if not usable:
        return 0.0
    square_sum = 0
    samples = len(usable) // 2
    for offset in range(0, len(usable), 2):
        sample = int.from_bytes(usable[offset : offset + 2], byteorder="little", signed=True)
        square_sum += sample * sample
    return (square_sum / samples) ** 0.5 / 32768.0


def build_default_audio_sink(*, db: Session, settings: Settings) -> AudioFrameSink:
    provider = ProviderConfigService(db, settings).build_asr_provider()
    build_realtime_signer = getattr(provider, "build_realtime_signer", None)
    if build_realtime_signer is None:
        return NullAudioFrameSink()

    session = TencentRealtimeASRSession(signer=build_realtime_signer())
    stream = TencentRealtimeASRStream(
        signer=session.signer,
        connect=session.connect,
        chunk_ms=session.chunk_ms,
        final_timeout_seconds=session.final_timeout_seconds,
    )
    return StreamingTencentRealtimeASRSink(stream=stream)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run LiveKit voice worker for one session.")
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--max-seconds", type=float, default=None)
    args = parser.parse_args()

    settings = get_settings()
    session_local = get_session_local()
    with session_local() as db:
        worker = LiveKitVoiceWorker(db=db, settings=settings, session_id=UUID(args.session_id))
        asyncio.run(worker.run(max_seconds=args.max_seconds))


if __name__ == "__main__":
    main()
