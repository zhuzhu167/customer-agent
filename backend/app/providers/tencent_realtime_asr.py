from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, AsyncContextManager, AsyncIterator, Awaitable, Callable, Iterator
from urllib.parse import urlencode

from backend.app.core.secrets import TencentCloudCredentials


DEFAULT_AUDIO_CHUNK_MS = 200
DEFAULT_SAMPLE_RATE = 16000
DEFAULT_BYTES_PER_SAMPLE = 2


@dataclass(frozen=True)
class TencentRealtimeASRConfig:
    host: str
    engine_model_type: str = "16k_zh"
    voice_format: int = 1
    filter_dirty: int = 0
    filter_modal: int = 0
    filter_punc: int = 0
    convert_num_mode: int = 1
    need_vad: int = 1
    word_info: int = 0


class TencentRealtimeASRSigner:
    def __init__(self, *, credentials: TencentCloudCredentials, config: TencentRealtimeASRConfig) -> None:
        if not credentials.app_id:
            raise ValueError("腾讯云实时 ASR 需要 APPID")
        self.credentials = credentials
        self.config = config

    def build_url(
        self,
        *,
        voice_id: str | None = None,
        timestamp: int | None = None,
        expired: int | None = None,
        nonce: int | None = None,
    ) -> str:
        now = int(time.time()) if timestamp is None else timestamp
        query = {
            "secretid": self.credentials.secret_id,
            "timestamp": now,
            "expired": expired if expired is not None else now + 24 * 60 * 60,
            "nonce": nonce if nonce is not None else int(uuid.uuid4().int % 10000000000),
            "engine_model_type": self.config.engine_model_type,
            "voice_id": voice_id or uuid.uuid4().hex,
            "voice_format": self.config.voice_format,
            "filter_dirty": self.config.filter_dirty,
            "filter_modal": self.config.filter_modal,
            "filter_punc": self.config.filter_punc,
            "convert_num_mode": self.config.convert_num_mode,
            "needvad": self.config.need_vad,
            "word_info": self.config.word_info,
        }
        canonical_query = urlencode(sorted(query.items()))
        signing_path = f"{self.config.host}/asr/v2/{self.credentials.app_id}?{canonical_query}"
        signature = base64.b64encode(
            hmac.new(self.credentials.secret_key.encode("utf-8"), signing_path.encode("utf-8"), hashlib.sha1).digest()
        ).decode("utf-8")
        return f"wss://{signing_path}&signature={urlencode({'': signature})[1:]}"


class TencentRealtimeASRSession:
    def __init__(
        self,
        *,
        signer: TencentRealtimeASRSigner,
        connect: Callable[[str], Awaitable[AsyncContextManager[Any]]] | None = None,
        chunk_ms: int = DEFAULT_AUDIO_CHUNK_MS,
        final_timeout_seconds: float = 10.0,
    ) -> None:
        self.signer = signer
        self.connect = connect or _default_connect
        self.chunk_ms = chunk_ms
        self.final_timeout_seconds = final_timeout_seconds

    async def recognize_pcm(self, pcm_audio: bytes, *, voice_id: str | None = None) -> list[dict]:
        stream = TencentRealtimeASRStream(
            signer=self.signer,
            connect=self.connect,
            chunk_ms=self.chunk_ms,
            final_timeout_seconds=self.final_timeout_seconds,
        )
        try:
            await stream.start(voice_id=voice_id)
            await stream.push_pcm(pcm_audio)
            return await stream.finish()
        finally:
            await stream.close()


class TencentRealtimeASRStream:
    def __init__(
        self,
        *,
        signer: TencentRealtimeASRSigner,
        connect: Callable[[str], Awaitable[AsyncContextManager[Any]]] | None = None,
        chunk_ms: int = DEFAULT_AUDIO_CHUNK_MS,
        final_timeout_seconds: float = 10.0,
    ) -> None:
        self.signer = signer
        self.connect = connect or _default_connect
        self.chunk_ms = chunk_ms
        self.final_timeout_seconds = final_timeout_seconds
        self.voice_id: str | None = None
        self.websocket_context: AsyncContextManager[Any] | None = None
        self.websocket: Any | None = None
        self.receive_task: asyncio.Task | None = None
        self.pending_pcm = bytearray()
        self.messages: list[dict] = []
        self.receive_error: BaseException | None = None
        self.started = False
        self.ending = False
        self.terminal_received = False

    async def start(self, *, voice_id: str | None = None) -> None:
        if self.started:
            return
        self.voice_id = voice_id or uuid.uuid4().hex
        url = self.signer.build_url(voice_id=self.voice_id)
        self.websocket_context = await self.connect(url)
        self.websocket = await self.websocket_context.__aenter__()
        self.started = True
        self.receive_task = asyncio.create_task(self._receive_loop())

    async def push_pcm(self, pcm_audio: bytes) -> None:
        if not pcm_audio:
            return
        await self.start()
        self._raise_receive_error_if_any()
        self._sync_receive_task_state()
        if self.terminal_received:
            return
        self.pending_pcm.extend(pcm_audio)
        chunk_size = pcm_chunk_size(chunk_ms=self.chunk_ms)
        while len(self.pending_pcm) >= chunk_size:
            chunk = bytes(self.pending_pcm[:chunk_size])
            del self.pending_pcm[:chunk_size]
            await self._send(chunk)

    async def pop_messages(self) -> list[dict]:
        self._raise_receive_error_if_any()
        messages = self.messages[:]
        self.messages.clear()
        return messages

    async def finish(self) -> list[dict]:
        if not self.started or self.ending:
            return await self.pop_messages()

        self.ending = True
        if self.terminal_received:
            return await self.pop_messages()
        if self.pending_pcm:
            await self._send(bytes(self.pending_pcm))
            self.pending_pcm.clear()
        await self._send(json.dumps({"type": "end"}))
        if self.receive_task is not None:
            try:
                await asyncio.wait_for(self.receive_task, timeout=self.final_timeout_seconds)
            except asyncio.TimeoutError:
                self.receive_task.cancel()
                raise TimeoutError("腾讯云实时 ASR 结束消息等待超时")
        return await self.pop_messages()

    async def close(self) -> None:
        if self.receive_task is not None and not self.receive_task.done():
            self.receive_task.cancel()
            await asyncio.gather(self.receive_task, return_exceptions=True)
        self.receive_task = None

        if self.websocket_context is not None:
            await self.websocket_context.__aexit__(None, None, None)
        self.websocket_context = None
        self.websocket = None
        self.started = False

    async def _send(self, payload: bytes | str) -> None:
        if self.websocket is None:
            raise RuntimeError("腾讯云实时 ASR WebSocket 尚未连接")
        await self.websocket.send(payload)

    async def _receive_loop(self) -> None:
        try:
            async for message in _iter_messages(self.websocket):
                self.messages.append(message)
                if _is_terminal_message(message):
                    self.terminal_received = True
                    break
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self.receive_error = exc

    def _raise_receive_error_if_any(self) -> None:
        if self.receive_error is not None:
            raise RuntimeError("腾讯云实时 ASR 接收消息失败") from self.receive_error

    def _sync_receive_task_state(self) -> None:
        if self.receive_task is None or not self.receive_task.done():
            return
        self._raise_receive_error_if_any()


def iter_pcm_chunks(
    pcm_audio: bytes,
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    bytes_per_sample: int = DEFAULT_BYTES_PER_SAMPLE,
    chunk_ms: int = DEFAULT_AUDIO_CHUNK_MS,
) -> Iterator[bytes]:
    chunk_size = pcm_chunk_size(
        sample_rate=sample_rate,
        bytes_per_sample=bytes_per_sample,
        chunk_ms=chunk_ms,
    )
    for offset in range(0, len(pcm_audio), chunk_size):
        yield pcm_audio[offset : offset + chunk_size]


def pcm_chunk_size(
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    bytes_per_sample: int = DEFAULT_BYTES_PER_SAMPLE,
    chunk_ms: int = DEFAULT_AUDIO_CHUNK_MS,
) -> int:
    return max(1, int(sample_rate * bytes_per_sample * chunk_ms / 1000))


async def _iter_messages(websocket) -> AsyncIterator[dict]:
    async for raw in websocket:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            yield json.loads(raw)
        except json.JSONDecodeError:
            yield {"raw": raw}


def _is_terminal_message(message: dict) -> bool:
    if message.get("final") == 1 or message.get("is_final") is True:
        return True
    result = message.get("result")
    if isinstance(result, dict):
        return result.get("slice_type") == 2 or result.get("final") == 1
    return False


def _is_final_message(message: dict) -> bool:
    if _is_terminal_message(message):
        return True
    result = message.get("result")
    if isinstance(result, dict):
        return result.get("slice_type") == 2 or result.get("final") == 1
    return False


async def _default_connect(url: str) -> AsyncContextManager[Any]:
    import websockets

    return websockets.connect(url)
