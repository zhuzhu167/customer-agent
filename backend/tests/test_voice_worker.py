from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models import VoiceEvent
from backend.app.worker.audio_publish import AudioPublishResult
from backend.app.worker.asr_sink import BufferedTencentRealtimeASRSink, StreamingTencentRealtimeASRSink
from backend.app.worker.livekit_voice_worker import (
    LiveKitVoiceWorker,
    PlaybackState,
    extract_final_transcript_text,
    pcm_rms_16bit,
)


def test_prepare_voice_worker_returns_room_token_and_records_event(
    client: TestClient,
    db_session: Session,
) -> None:
    session_id = client.post("/api/v1/sessions", json={"mode": "demo"}).json()["session_id"]

    response = client.post(f"/api/v1/sessions/{session_id}/voice-worker")

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == session_id
    assert body["room_name"].startswith("voice-")
    assert body["worker_identity"].startswith("worker-")
    assert body["token"]

    events = db_session.scalars(
        select(VoiceEvent).where(VoiceEvent.session_id == session_id).order_by(VoiceEvent.created_at)
    ).all()
    assert events[-1].event_type == "voice_worker.prepared"
    assert events[-1].payload["room_name"] == body["room_name"]


def test_voice_worker_audio_frame_pushes_pcm_to_sink_without_persisting_audio(
    client: TestClient,
    db_session: Session,
    settings,
) -> None:
    import asyncio

    session_id = client.post("/api/v1/sessions", json={"mode": "demo"}).json()["session_id"]
    pushed: list[dict] = []

    class Sink:
        async def push_pcm(self, pcm: bytes, *, sample_rate: int, num_channels: int) -> None:
            pushed.append({"pcm": pcm, "sample_rate": sample_rate, "num_channels": num_channels})

    class Frame:
        data = b"pcm-bytes"
        sample_rate = 16000
        num_channels = 1
        samples_per_channel = 4

    worker = LiveKitVoiceWorker(db=db_session, settings=settings, session_id=session_id, audio_sink=Sink())

    asyncio.run(worker.handle_audio_frame(Frame()))

    assert pushed == [{"pcm": b"pcm-bytes", "sample_rate": 16000, "num_channels": 1}]
    event = db_session.scalars(
        select(VoiceEvent).where(VoiceEvent.session_id == session_id, VoiceEvent.event_type == "voice_worker.audio_frame")
    ).one()
    assert event.payload["pcm_bytes"] == len(b"pcm-bytes")
    assert "pcm" not in event.payload
    assert "audio_bytes" not in event.payload


def test_buffered_tencent_realtime_asr_sink_flushes_16k_mono_pcm() -> None:
    import asyncio

    class Session:
        async def recognize_pcm(self, pcm: bytes, *, voice_id=None):
            return [{"text": pcm.decode("utf-8"), "final": 1}]

    sink = BufferedTencentRealtimeASRSink(session=Session())
    asyncio.run(sink.push_pcm(b"hello", sample_rate=16000, num_channels=1))
    asyncio.run(sink.push_pcm(b"-ignored", sample_rate=48000, num_channels=1))
    messages = asyncio.run(sink.flush())

    assert messages == [{"text": "hello", "final": 1}]
    assert sink.buffer == bytearray()


def test_streaming_tencent_realtime_asr_sink_pushes_pcm_and_pops_messages() -> None:
    import asyncio

    class Stream:
        def __init__(self):
            self.started = False
            self.pushed: list[bytes] = []
            self.closed = False

        async def start(self):
            self.started = True

        async def push_pcm(self, pcm: bytes):
            self.pushed.append(pcm)

        async def pop_messages(self):
            return [{"text": "hello", "final": 1}]

        async def finish(self):
            return [{"text": "final hello", "final": 1}]

        async def close(self):
            self.closed = True

    stream = Stream()
    sink = StreamingTencentRealtimeASRSink(stream=stream)

    asyncio.run(sink.push_pcm(b"hello", sample_rate=16000, num_channels=1))
    asyncio.run(sink.push_pcm(b"-ignored", sample_rate=48000, num_channels=1))
    messages = asyncio.run(sink.pop_messages())
    final_messages = asyncio.run(sink.finish())
    asyncio.run(sink.close())

    assert stream.started is True
    assert stream.pushed == [b"hello"]
    assert messages == [{"text": "hello", "final": 1}]
    assert final_messages == [{"text": "final hello", "final": 1}]
    assert stream.closed is True


def test_extract_final_transcript_text_handles_tencent_result_shape() -> None:
    assert (
        extract_final_transcript_text({"result": {"slice_type": 2, "voice_text_str": "  你好  "}})
        == "你好"
    )
    assert extract_final_transcript_text({"result": {"slice_type": 1, "voice_text_str": "中间结果"}}) is None


def test_pcm_rms_16bit_normalizes_little_endian_samples() -> None:
    assert pcm_rms_16bit(b"") == 0.0
    assert pcm_rms_16bit((0).to_bytes(2, "little", signed=True) * 4) == 0.0
    assert pcm_rms_16bit((32767).to_bytes(2, "little", signed=True) * 2) > 0.99


def test_voice_worker_final_transcript_triggers_agent_turn_without_persisting_audio(
    client: TestClient,
    db_session: Session,
    settings,
) -> None:
    import asyncio

    session_id = client.post("/api/v1/sessions", json={"mode": "debug"}).json()["session_id"]
    worker = LiveKitVoiceWorker(db=db_session, settings=settings, session_id=session_id)

    asyncio.run(worker.handle_final_transcript("我想咨询办理资料"))

    events = db_session.scalars(
        select(VoiceEvent).where(VoiceEvent.session_id == session_id).order_by(VoiceEvent.created_at)
    ).all()
    event_types = {event.event_type for event in events}
    assert {"transcript.final", "agent.reply.preparing", "agent.reply.answered", "voice_worker.agent_reply_ready"}.issubset(
        event_types
    )
    assert all("audio_bytes" not in event.payload or event.event_type == "agent.reply.answered" for event in events)


def test_voice_worker_final_transcript_publishes_frontend_events_and_wav_audio(
    client: TestClient,
    db_session: Session,
    settings,
    monkeypatch,
) -> None:
    import asyncio
    import backend.app.worker.livekit_voice_worker as worker_module
    import json
    from dataclasses import dataclass
    from uuid import uuid4

    published: list[dict] = []
    data_packets: list[dict] = []

    async def fake_publish(room, wav_bytes: bytes, *, track_name: str = "agent-voice", **kwargs) -> AudioPublishResult:
        published.append({"room": room, "wav_bytes": wav_bytes, "track_name": track_name})
        return AudioPublishResult(frames_published=1, interrupted=False)

    @dataclass(frozen=True)
    class FakeTTSResult:
        audio_bytes: bytes
        content_type: str

    @dataclass(frozen=True)
    class FakeAgentResult:
        user_turn_id: object
        preparing_turn_id: object
        answered_turn_id: object
        response_text: str
        preparing_visible: bool
        tts_result: FakeTTSResult

    class FakeAgentTurnService:
        def __init__(self, db, settings):
            pass

        async def handle_transcript_turn_internal(self, **kwargs):
            return FakeAgentResult(
                user_turn_id=uuid4(),
                preparing_turn_id=uuid4(),
                answered_turn_id=uuid4(),
                response_text="请您补充一下办理诉求。",
                preparing_visible=True,
                tts_result=FakeTTSResult(audio_bytes=b"wav-bytes", content_type="audio/wav"),
            )

    class FakeParticipant:
        async def publish_data(self, payload, *, reliable=True, destination_identities=None, topic=""):
            data_packets.append(
                {
                    "payload": json.loads(payload),
                    "reliable": reliable,
                    "destination_identities": destination_identities,
                    "topic": topic,
                }
            )

    class FakeRoom:
        local_participant = FakeParticipant()

    monkeypatch.setattr(worker_module, "publish_wav_audio", fake_publish)
    monkeypatch.setattr(worker_module, "AgentTurnService", FakeAgentTurnService)
    session_id = client.post("/api/v1/sessions", json={"mode": "debug"}).json()["session_id"]
    worker = LiveKitVoiceWorker(db=db_session, settings=settings, session_id=session_id)
    worker.room = FakeRoom()

    asyncio.run(worker.handle_final_transcript("我想咨询办理资料"))

    assert published
    assert published[0]["track_name"] == "agent-voice"
    assert [packet["payload"]["type"] for packet in data_packets] == [
        "transcript.final",
        "agent.reply.preparing",
        "agent.reply.answered",
    ]
    assert {packet["topic"] for packet in data_packets} == {"voice-event"}
    assert all(packet["reliable"] is True for packet in data_packets)
    assert data_packets[0]["payload"]["text"] == "我想咨询办理资料"
    assert data_packets[1]["payload"]["visibility"] == "demo_only"
    assert data_packets[2]["payload"]["completed"] is True
    event = db_session.scalars(
        select(VoiceEvent).where(
            VoiceEvent.session_id == session_id,
            VoiceEvent.event_type == "voice_worker.agent_audio_published",
        )
    ).one()
    assert event.payload["track_name"] == "agent-voice"


def test_voice_worker_records_frontend_event_publish_failure(
    client: TestClient,
    db_session: Session,
    settings,
) -> None:
    import asyncio
    from uuid import uuid4

    class BrokenParticipant:
        async def publish_data(self, *args, **kwargs):
            raise RuntimeError("data channel unavailable")

    class BrokenRoom:
        local_participant = BrokenParticipant()

    session_id = client.post("/api/v1/sessions", json={"mode": "debug"}).json()["session_id"]
    worker = LiveKitVoiceWorker(db=db_session, settings=settings, session_id=session_id)
    worker.room = BrokenRoom()

    asyncio.run(
        worker._publish_frontend_turn_started(
            transcript_text="用户发言",
            user_turn_id=uuid4(),
            preparing_turn_id=uuid4(),
            response_text="客服回答",
            preparing_visible=True,
        )
    )

    events = db_session.scalars(
        select(VoiceEvent).where(
            VoiceEvent.session_id == session_id,
            VoiceEvent.event_type == "voice_worker.data_publish_failed",
        )
    ).all()
    assert len(events) == 2
    assert {event.payload["frontend_event_type"] for event in events} == {
        "transcript.final",
        "agent.reply.preparing",
    }


def test_voice_worker_publishes_frontend_error_when_agent_audio_publish_fails(
    client: TestClient,
    db_session: Session,
    settings,
    monkeypatch,
) -> None:
    import asyncio
    import backend.app.worker.livekit_voice_worker as worker_module
    import json
    from dataclasses import dataclass
    from uuid import uuid4

    data_packets: list[dict] = []

    async def failing_publish(*args, **kwargs):
        raise RuntimeError("publish failed")

    @dataclass(frozen=True)
    class FakeTTSResult:
        audio_bytes: bytes
        content_type: str

    @dataclass(frozen=True)
    class FakeAgentResult:
        user_turn_id: object
        preparing_turn_id: object
        answered_turn_id: object
        response_text: str
        preparing_visible: bool
        tts_result: FakeTTSResult

    class FakeAgentTurnService:
        def __init__(self, db, settings):
            pass

        async def handle_transcript_turn_internal(self, **kwargs):
            return FakeAgentResult(
                user_turn_id=uuid4(),
                preparing_turn_id=uuid4(),
                answered_turn_id=uuid4(),
                response_text="请您补充一下办理诉求。",
                preparing_visible=True,
                tts_result=FakeTTSResult(audio_bytes=b"wav-bytes", content_type="audio/wav"),
            )

    class FakeParticipant:
        async def publish_data(self, payload, *, reliable=True, destination_identities=None, topic=""):
            data_packets.append(json.loads(payload))

    class FakeRoom:
        local_participant = FakeParticipant()

    monkeypatch.setattr(worker_module, "publish_wav_audio", failing_publish)
    monkeypatch.setattr(worker_module, "AgentTurnService", FakeAgentTurnService)
    session_id = client.post("/api/v1/sessions", json={"mode": "debug"}).json()["session_id"]
    worker = LiveKitVoiceWorker(db=db_session, settings=settings, session_id=session_id)
    worker.room = FakeRoom()

    asyncio.run(worker.handle_final_transcript("我想咨询办理资料"))

    assert [packet["type"] for packet in data_packets] == [
        "transcript.final",
        "agent.reply.preparing",
        "session.error",
    ]
    assert data_packets[-1]["error_code"] == "tts.playback_failed"
    assert not any(packet["type"] == "agent.reply.answered" for packet in data_packets)
    stored_error = db_session.scalars(
        select(VoiceEvent).where(
            VoiceEvent.session_id == session_id,
            VoiceEvent.event_type == "voice_worker.error",
        )
    ).one()
    assert stored_error.payload["error_code"] == "voice_worker.agent_audio_publish_failed"


def test_voice_worker_audio_frame_flushes_asr_message_into_final_transcript(
    client: TestClient,
    db_session: Session,
    settings,
    monkeypatch,
) -> None:
    import asyncio
    import backend.app.worker.livekit_voice_worker as worker_module
    from dataclasses import dataclass
    from uuid import uuid4

    @dataclass(frozen=True)
    class FakeTTSResult:
        audio_bytes: bytes
        content_type: str

    @dataclass(frozen=True)
    class FakeAgentResult:
        user_turn_id: object
        preparing_turn_id: object
        answered_turn_id: object
        response_text: str
        preparing_visible: bool
        tts_result: FakeTTSResult

    class FakeAgentTurnService:
        def __init__(self, db, settings):
            pass

        async def handle_transcript_turn_internal(self, *, text, **kwargs):
            assert text == "用户最终发言"
            return FakeAgentResult(
                user_turn_id=uuid4(),
                preparing_turn_id=uuid4(),
                answered_turn_id=uuid4(),
                response_text="客服回答",
                preparing_visible=True,
                tts_result=FakeTTSResult(audio_bytes=b"wav-bytes", content_type="audio/wav"),
            )

    class Sink:
        async def push_pcm(self, pcm: bytes, *, sample_rate: int, num_channels: int) -> None:
            return None

        async def flush(self):
            return [{"result": {"slice_type": 2, "voice_text_str": "用户最终发言"}}]

    class Frame:
        data = b"pcm-bytes"
        sample_rate = 16000
        num_channels = 1
        samples_per_channel = 4

    async def fake_publish(*args, **kwargs):
        return AudioPublishResult(frames_published=1, interrupted=False)

    monkeypatch.setattr(worker_module, "AgentTurnService", FakeAgentTurnService)
    monkeypatch.setattr(worker_module, "publish_wav_audio", fake_publish)
    session_id = client.post("/api/v1/sessions", json={"mode": "debug"}).json()["session_id"]
    worker = LiveKitVoiceWorker(db=db_session, settings=settings, session_id=session_id, audio_sink=Sink())
    worker.room = object()

    asyncio.run(worker.handle_audio_frame(Frame()))

    events = db_session.scalars(
        select(VoiceEvent).where(VoiceEvent.session_id == session_id).order_by(VoiceEvent.created_at)
    ).all()
    assert any(event.event_type == "voice_worker.agent_reply_ready" for event in events)


def test_voice_worker_audio_frame_pops_streaming_asr_message_into_final_transcript(
    client: TestClient,
    db_session: Session,
    settings,
    monkeypatch,
) -> None:
    import asyncio
    import backend.app.worker.livekit_voice_worker as worker_module
    from dataclasses import dataclass
    from uuid import uuid4

    @dataclass(frozen=True)
    class FakeTTSResult:
        audio_bytes: bytes
        content_type: str

    @dataclass(frozen=True)
    class FakeAgentResult:
        user_turn_id: object
        preparing_turn_id: object
        answered_turn_id: object
        response_text: str
        preparing_visible: bool
        tts_result: FakeTTSResult

    class FakeAgentTurnService:
        def __init__(self, db, settings):
            pass

        async def handle_transcript_turn_internal(self, *, text, **kwargs):
            assert text == "实时最终发言"
            return FakeAgentResult(
                user_turn_id=uuid4(),
                preparing_turn_id=uuid4(),
                answered_turn_id=uuid4(),
                response_text="客服回答",
                preparing_visible=True,
                tts_result=FakeTTSResult(audio_bytes=b"wav-bytes", content_type="audio/wav"),
            )

    class Sink:
        async def push_pcm(self, pcm: bytes, *, sample_rate: int, num_channels: int) -> None:
            return None

        async def pop_messages(self):
            return [{"result": {"slice_type": 2, "voice_text_str": "实时最终发言"}}]

    class Frame:
        data = b"pcm-bytes"
        sample_rate = 16000
        num_channels = 1
        samples_per_channel = 4

    async def fake_publish(*args, **kwargs):
        return AudioPublishResult(frames_published=1, interrupted=False)

    monkeypatch.setattr(worker_module, "AgentTurnService", FakeAgentTurnService)
    monkeypatch.setattr(worker_module, "publish_wav_audio", fake_publish)
    session_id = client.post("/api/v1/sessions", json={"mode": "debug"}).json()["session_id"]
    worker = LiveKitVoiceWorker(db=db_session, settings=settings, session_id=session_id, audio_sink=Sink())
    worker.room = object()

    asyncio.run(worker.handle_audio_frame(Frame()))

    events = db_session.scalars(
        select(VoiceEvent).where(VoiceEvent.session_id == session_id).order_by(VoiceEvent.created_at)
    ).all()
    assert any(event.event_type == "voice_worker.agent_reply_ready" for event in events)


def test_voice_worker_audio_frame_interrupts_active_agent_playback_after_vad_threshold(
    client: TestClient,
    db_session: Session,
    settings,
) -> None:
    import asyncio
    import json
    from uuid import uuid4

    data_packets: list[dict] = []

    class FakeParticipant:
        async def publish_data(self, payload, *, reliable=True, destination_identities=None, topic=""):
            data_packets.append(json.loads(payload))

    class FakeRoom:
        local_participant = FakeParticipant()

    class Sink:
        async def push_pcm(self, pcm: bytes, *, sample_rate: int, num_channels: int) -> None:
            return None

    class Frame:
        data = (12000).to_bytes(2, "little", signed=True) * 160
        sample_rate = 16000
        num_channels = 1
        samples_per_channel = 160

    session_id = client.post("/api/v1/sessions", json={"mode": "debug"}).json()["session_id"]
    answered_turn_id = uuid4()
    barge_in_settings = settings.model_copy(
        update={
            "turn_barge_in_rms_threshold": 0.01,
            "turn_barge_in_min_voice_frames": 2,
        }
    )
    worker = LiveKitVoiceWorker(db=db_session, settings=barge_in_settings, session_id=session_id, audio_sink=Sink())
    worker.room = FakeRoom()

    async def run_interrupt() -> asyncio.Event:
        interrupt_event = asyncio.Event()
        worker.current_playback = PlaybackState(
            answered_turn_id=answered_turn_id,
            response_text="这是正在播报的客服回答。",
            interrupt_event=interrupt_event,
        )
        await worker.handle_audio_frame(Frame())
        assert not interrupt_event.is_set()
        await worker.handle_audio_frame(Frame())
        return interrupt_event

    interrupt_event = asyncio.run(run_interrupt())

    assert interrupt_event.is_set()
    stored_event = db_session.scalars(
        select(VoiceEvent).where(
            VoiceEvent.session_id == session_id,
            VoiceEvent.event_type == "agent.reply.interrupted",
        )
    ).one()
    assert stored_event.turn_id == answered_turn_id
    assert stored_event.payload["reason"] == "barge_in"
    assert stored_event.payload["voice_frames_seen"] == 2
    assert stored_event.payload["rms"] > 0.01
    assert [packet["type"] for packet in data_packets] == ["agent.reply.interrupted"]
    assert data_packets[0]["reason"] == "barge_in"
    assert data_packets[0]["interruption_id"] == stored_event.payload["interruption_id"]


def test_voice_worker_interruption_recovers_when_no_follow_up_transcript(
    client: TestClient,
    db_session: Session,
    settings,
) -> None:
    import asyncio
    import json
    from uuid import uuid4

    data_packets: list[dict] = []

    class FakeParticipant:
        async def publish_data(self, payload, *, reliable=True, destination_identities=None, topic=""):
            data_packets.append(json.loads(payload))

    class FakeRoom:
        local_participant = FakeParticipant()

    class Sink:
        async def push_pcm(self, pcm: bytes, *, sample_rate: int, num_channels: int) -> None:
            return None

    class Frame:
        data = (12000).to_bytes(2, "little", signed=True) * 160
        sample_rate = 16000
        num_channels = 1
        samples_per_channel = 160

    session_id = client.post("/api/v1/sessions", json={"mode": "debug"}).json()["session_id"]
    answered_turn_id = uuid4()
    recovery_settings = settings.model_copy(
        update={
            "turn_barge_in_rms_threshold": 0.01,
            "turn_barge_in_min_voice_frames": 1,
            "turn_interruption_recovery_timeout_seconds": 0.01,
        }
    )
    worker = LiveKitVoiceWorker(db=db_session, settings=recovery_settings, session_id=session_id, audio_sink=Sink())
    worker.room = FakeRoom()

    async def run_recovery() -> None:
        worker.current_playback = PlaybackState(
            answered_turn_id=answered_turn_id,
            response_text="这是正在播报的客服回答。",
            interrupt_event=asyncio.Event(),
        )
        await worker.handle_audio_frame(Frame())
        await asyncio.sleep(0.03)

    asyncio.run(run_recovery())

    event_types = [packet["type"] for packet in data_packets]
    assert event_types == ["agent.reply.interrupted", "agent.reply.interrupt_recovered"]
    assert data_packets[1]["interruption_id"] == data_packets[0]["interruption_id"]
    assert data_packets[1]["reason"] == "no_follow_up_transcript"
    stored_event = db_session.scalars(
        select(VoiceEvent).where(
            VoiceEvent.session_id == session_id,
            VoiceEvent.event_type == "agent.reply.interrupt_recovered",
        )
    ).one()
    assert stored_event.turn_id == answered_turn_id
    assert stored_event.payload["interruption_id"] == data_packets[0]["interruption_id"]
    assert stored_event.payload["timeout_seconds"] == 0.01


def test_voice_worker_interruption_does_not_recover_after_follow_up_transcript(
    client: TestClient,
    db_session: Session,
    settings,
    monkeypatch,
) -> None:
    import asyncio
    import backend.app.worker.livekit_voice_worker as worker_module
    import json
    from dataclasses import dataclass
    from uuid import uuid4

    data_packets: list[dict] = []

    @dataclass(frozen=True)
    class FakeTTSResult:
        audio_bytes: bytes
        content_type: str

    @dataclass(frozen=True)
    class FakeAgentResult:
        user_turn_id: object
        preparing_turn_id: object
        answered_turn_id: object
        response_text: str
        preparing_visible: bool
        tts_result: FakeTTSResult

    class FakeAgentTurnService:
        def __init__(self, db, settings):
            pass

        async def handle_transcript_turn_internal(self, **kwargs):
            return FakeAgentResult(
                user_turn_id=uuid4(),
                preparing_turn_id=uuid4(),
                answered_turn_id=uuid4(),
                response_text="客服回答",
                preparing_visible=True,
                tts_result=FakeTTSResult(audio_bytes=b"wav-bytes", content_type="audio/mock"),
            )

    class FakeParticipant:
        async def publish_data(self, payload, *, reliable=True, destination_identities=None, topic=""):
            data_packets.append(json.loads(payload))

    class FakeRoom:
        local_participant = FakeParticipant()

    class Sink:
        async def push_pcm(self, pcm: bytes, *, sample_rate: int, num_channels: int) -> None:
            return None

    class Frame:
        data = (12000).to_bytes(2, "little", signed=True) * 160
        sample_rate = 16000
        num_channels = 1
        samples_per_channel = 160

    monkeypatch.setattr(worker_module, "AgentTurnService", FakeAgentTurnService)
    session_id = client.post("/api/v1/sessions", json={"mode": "debug"}).json()["session_id"]
    recovery_settings = settings.model_copy(
        update={
            "turn_barge_in_rms_threshold": 0.01,
            "turn_barge_in_min_voice_frames": 1,
            "turn_interruption_recovery_timeout_seconds": 0.01,
        }
    )
    worker = LiveKitVoiceWorker(db=db_session, settings=recovery_settings, session_id=session_id, audio_sink=Sink())
    worker.room = FakeRoom()

    async def run_follow_up() -> None:
        worker.current_playback = PlaybackState(
            answered_turn_id=uuid4(),
            response_text="这是正在播报的客服回答。",
            interrupt_event=asyncio.Event(),
        )
        await worker.handle_audio_frame(Frame())
        await worker.handle_final_transcript("用户真实打断后的补充")
        await asyncio.sleep(0.03)

    asyncio.run(run_follow_up())

    assert [packet["type"] for packet in data_packets] == [
        "agent.reply.interrupted",
        "transcript.final",
        "agent.reply.preparing",
        "session.error",
    ]
    recovery_events = db_session.scalars(
        select(VoiceEvent).where(
            VoiceEvent.session_id == session_id,
            VoiceEvent.event_type == "agent.reply.interrupt_recovered",
        )
    ).all()
    assert recovery_events == []


def test_voice_worker_audio_frame_ignores_low_energy_noise_during_playback(
    client: TestClient,
    db_session: Session,
    settings,
) -> None:
    import asyncio
    from uuid import uuid4

    class Sink:
        async def push_pcm(self, pcm: bytes, *, sample_rate: int, num_channels: int) -> None:
            return None

    class Frame:
        data = (64).to_bytes(2, "little", signed=True) * 160
        sample_rate = 16000
        num_channels = 1
        samples_per_channel = 160

    session_id = client.post("/api/v1/sessions", json={"mode": "debug"}).json()["session_id"]
    answered_turn_id = uuid4()
    worker = LiveKitVoiceWorker(
        db=db_session,
        settings=settings.model_copy(
            update={
                "turn_barge_in_rms_threshold": 0.01,
                "turn_barge_in_min_voice_frames": 1,
            }
        ),
        session_id=session_id,
        audio_sink=Sink(),
    )

    async def run_noise() -> asyncio.Event:
        interrupt_event = asyncio.Event()
        worker.current_playback = PlaybackState(
            answered_turn_id=answered_turn_id,
            response_text="这是正在播报的客服回答。",
            interrupt_event=interrupt_event,
        )
        await worker.handle_audio_frame(Frame())
        return interrupt_event

    interrupt_event = asyncio.run(run_noise())

    assert not interrupt_event.is_set()
    events = db_session.scalars(
        select(VoiceEvent).where(
            VoiceEvent.session_id == session_id,
            VoiceEvent.event_type == "agent.reply.interrupted",
        )
    ).all()
    assert events == []


def test_voice_worker_silence_timeout_records_event_and_notifies_frontend(
    client: TestClient,
    db_session: Session,
    settings,
) -> None:
    import asyncio
    import json

    data_packets: list[dict] = []

    class FakeParticipant:
        async def publish_data(self, payload, *, reliable=True, destination_identities=None, topic=""):
            data_packets.append(json.loads(payload))

    class FakeRoom:
        local_participant = FakeParticipant()

    async def run_silence_watch(worker: LiveKitVoiceWorker) -> None:
        stop_event = asyncio.Event()
        task = asyncio.create_task(worker._watch_silence(stop_event=stop_event))
        await asyncio.sleep(0.04)
        stop_event.set()
        await asyncio.gather(task, return_exceptions=True)

    session_id = client.post("/api/v1/sessions", json={"mode": "debug"}).json()["session_id"]
    silence_settings = settings.model_copy(
        update={
            "turn_silence_timeout_seconds": 0.01,
            "turn_silence_check_interval_seconds": 0.01,
        }
    )
    worker = LiveKitVoiceWorker(db=db_session, settings=silence_settings, session_id=session_id)
    worker.room = FakeRoom()
    worker.last_audio_activity_monotonic = 0

    asyncio.run(run_silence_watch(worker))

    stored_event = db_session.scalars(
        select(VoiceEvent).where(
            VoiceEvent.session_id == session_id,
            VoiceEvent.event_type == "turn.silence_timeout",
        )
    ).one()
    assert stored_event.payload["timeout_seconds"] == 0.01
    assert [packet["type"] for packet in data_packets] == ["session.error"]
    assert data_packets[0]["error_code"] == "turn.silence_timeout"
    assert data_packets[0]["recoverable"] is True


def test_voice_worker_silence_timeout_only_prompts_once(
    client: TestClient,
    db_session: Session,
    settings,
) -> None:
    import asyncio

    session_id = client.post("/api/v1/sessions", json={"mode": "debug"}).json()["session_id"]
    silence_settings = settings.model_copy(
        update={
            "turn_silence_timeout_seconds": 0.01,
            "turn_silence_check_interval_seconds": 0.01,
        }
    )
    worker = LiveKitVoiceWorker(db=db_session, settings=silence_settings, session_id=session_id)
    worker.last_audio_activity_monotonic = 0

    async def run_once() -> None:
        stop_event = asyncio.Event()
        task = asyncio.create_task(worker._watch_silence(stop_event=stop_event))
        await asyncio.sleep(0.04)
        stop_event.set()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run_once())

    events = db_session.scalars(
        select(VoiceEvent).where(
            VoiceEvent.session_id == session_id,
            VoiceEvent.event_type == "turn.silence_timeout",
        )
    ).all()
    assert len(events) == 1


def test_voice_worker_audio_track_finish_flushes_final_asr_message(
    client: TestClient,
    db_session: Session,
    settings,
    monkeypatch,
) -> None:
    import asyncio
    import backend.app.worker.livekit_voice_worker as worker_module
    from dataclasses import dataclass
    from uuid import uuid4

    @dataclass(frozen=True)
    class FakeTTSResult:
        audio_bytes: bytes
        content_type: str

    @dataclass(frozen=True)
    class FakeAgentResult:
        user_turn_id: object
        preparing_turn_id: object
        answered_turn_id: object
        response_text: str
        preparing_visible: bool
        tts_result: FakeTTSResult

    class FakeAgentTurnService:
        def __init__(self, db, settings):
            pass

        async def handle_transcript_turn_internal(self, *, text, **kwargs):
            assert text == "音轨结束后的最终发言"
            return FakeAgentResult(
                user_turn_id=uuid4(),
                preparing_turn_id=uuid4(),
                answered_turn_id=uuid4(),
                response_text="客服回答",
                preparing_visible=True,
                tts_result=FakeTTSResult(audio_bytes=b"wav-bytes", content_type="audio/wav"),
            )

    class Sink:
        async def finish(self):
            return [{"result": {"slice_type": 2, "voice_text_str": "音轨结束后的最终发言"}}]

    async def fake_publish(*args, **kwargs):
        return AudioPublishResult(frames_published=1, interrupted=False)

    monkeypatch.setattr(worker_module, "AgentTurnService", FakeAgentTurnService)
    monkeypatch.setattr(worker_module, "publish_wav_audio", fake_publish)
    session_id = client.post("/api/v1/sessions", json={"mode": "debug"}).json()["session_id"]
    worker = LiveKitVoiceWorker(db=db_session, settings=settings, session_id=session_id, audio_sink=Sink())
    worker.room = object()

    asyncio.run(worker.finish_audio_track())

    events = db_session.scalars(
        select(VoiceEvent).where(VoiceEvent.session_id == session_id).order_by(VoiceEvent.created_at)
    ).all()
    assert any(event.event_type == "voice_worker.agent_reply_ready" for event in events)
