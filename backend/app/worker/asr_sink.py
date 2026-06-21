from __future__ import annotations

from backend.app.providers.tencent_realtime_asr import TencentRealtimeASRSession, TencentRealtimeASRStream


class BufferedTencentRealtimeASRSink:
    def __init__(self, *, session: TencentRealtimeASRSession, min_bytes: int = 1280) -> None:
        self.session = session
        self.min_bytes = min_bytes
        self.buffer = bytearray()
        self.messages: list[dict] = []

    async def push_pcm(self, pcm: bytes, *, sample_rate: int, num_channels: int) -> None:
        if sample_rate != 16000 or num_channels != 1:
            return
        self.buffer.extend(pcm)

    async def flush(self) -> list[dict]:
        if not self.buffer:
            return []
        messages = await self.session.recognize_pcm(bytes(self.buffer))
        self.messages.extend(messages)
        self.buffer.clear()
        return messages


class StreamingTencentRealtimeASRSink:
    def __init__(self, *, stream: TencentRealtimeASRStream) -> None:
        self.stream = stream
        self.messages: list[dict] = []
        self.started = False

    async def push_pcm(self, pcm: bytes, *, sample_rate: int, num_channels: int) -> None:
        if sample_rate != 16000 or num_channels != 1:
            return
        if not self.started:
            await self.stream.start()
            self.started = True
        await self.stream.push_pcm(pcm)

    async def pop_messages(self) -> list[dict]:
        messages = await self.stream.pop_messages()
        self.messages.extend(messages)
        return messages

    async def finish(self) -> list[dict]:
        if not self.started:
            return []
        messages = await self.stream.finish()
        self.messages.extend(messages)
        self.started = False
        return messages

    async def close(self) -> None:
        await self.stream.close()
        self.started = False
