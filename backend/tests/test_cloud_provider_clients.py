from __future__ import annotations

import base64

import httpx
import pytest

from backend.app.core.secrets import TencentCloudCredentials, resolve_tencent_credentials
from backend.app.providers.alibaba import AlibabaLLMProvider
from backend.app.providers.base import ProviderConfigDTO
from backend.app.providers.tencent import TencentCloudTTSProvider
from backend.app.providers.tencent_realtime_asr import (
    TencentRealtimeASRSession,
    TencentRealtimeASRConfig,
    TencentRealtimeASRSigner,
    TencentRealtimeASRStream,
    iter_pcm_chunks,
)


@pytest.mark.asyncio
async def test_alibaba_llm_provider_calls_openai_compatible_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    async def fake_post(self, url, *, headers=None, json=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "choices": [{"message": {"content": "云服务接入正常。"}}],
                "usage": {"total_tokens": 12},
            },
        )

    monkeypatch.setenv("ALIBABA_CLOUD_API_KEY", "test-api-key")
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    provider = AlibabaLLMProvider(
        ProviderConfigDTO(
            provider_type="llm",
            provider_name="alibaba_cloud",
            model="qwen-plus",
            secret_ref="env:ALIBABA_CLOUD_API_KEY",
            options={"endpoint": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
        )
    )

    result = await provider.generate_reply(session_id="s1", user_text="你好", history=[])

    assert result.text == "云服务接入正常。"
    assert captured["url"].endswith("/chat/completions")
    assert captured["headers"]["Authorization"] == "Bearer test-api-key"
    assert captured["json"]["model"] == "qwen-plus"
    assert captured["json"]["stream"] is False


@pytest.mark.asyncio
async def test_tencent_tts_provider_calls_text_to_voice(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    async def fake_post(self, url, *, content=None, headers=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["content"] = content
        return httpx.Response(
            200,
            json={
                "Response": {
                    "Audio": base64.b64encode(b"mp3-bytes").decode("ascii"),
                    "RequestId": "request-1",
                }
            },
        )

    monkeypatch.setenv("TENCENT_CLOUD_SECRET_ID", "test-secret-id")
    monkeypatch.setenv("TENCENT_CLOUD_SECRET_KEY", "test-secret-key")
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    provider = TencentCloudTTSProvider(
        ProviderConfigDTO(
            provider_type="tts",
            provider_name="tencent_cloud",
            model="101001",
            region="ap-guangzhou",
            secret_ref="env:TENCENT_CLOUD_SECRET_ID,TENCENT_CLOUD_SECRET_KEY",
            options={"endpoint": "tts.tencentcloudapi.com", "codec": "wav", "sample_rate": 16000},
        )
    )

    result = await provider.synthesize("这是一轮测试。")

    assert result.audio_bytes == b"mp3-bytes"
    assert result.content_type == "audio/wav"
    assert captured["url"] == "https://tts.tencentcloudapi.com"
    assert captured["headers"]["X-TC-Action"] == "TextToVoice"


def test_tencent_credentials_can_be_loaded_from_existing_secret_file() -> None:
    credentials = resolve_tencent_credentials(
        "env:TENCENT_CLOUD_SECRET_ID,TENCENT_CLOUD_SECRET_KEY",
        fallback_file="docs/密钥/腾讯云密钥.csv",
    )

    assert credentials.secret_id.startswith("AKID")
    assert credentials.secret_key
    assert credentials.app_id


def test_tencent_realtime_asr_signer_builds_official_websocket_url() -> None:
    signer = TencentRealtimeASRSigner(
        credentials=TencentCloudCredentials(
            secret_id="test-secret-id",
            secret_key="test-secret-key",
            app_id="1250000000",
        ),
        config=TencentRealtimeASRConfig(host="asr.cloud.tencent.com"),
    )

    url = signer.build_url(voice_id="voice-1", timestamp=1710000000, expired=1710086400, nonce=123456)

    assert url.startswith("wss://asr.cloud.tencent.com/asr/v2/1250000000?")
    assert "engine_model_type=16k_zh" in url
    assert "voice_format=1" in url
    assert "voice_id=voice-1" in url
    assert "signature=" in url


def test_iter_pcm_chunks_uses_200ms_boundaries_for_16k_mono_pcm() -> None:
    chunks = list(iter_pcm_chunks(b"x" * 12800))

    assert [len(chunk) for chunk in chunks] == [6400, 6400]


@pytest.mark.asyncio
async def test_tencent_realtime_asr_session_sends_pcm_chunks_and_end_message() -> None:
    sent: list = []

    class FakeWebsocket:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def send(self, payload):
            sent.append(payload)

        def __aiter__(self):
            self._messages = iter(['{"result":{"slice_type":2,"voice_text_str":"你好"},"final":1}'])
            return self

        async def __anext__(self):
            try:
                return next(self._messages)
            except StopIteration:
                raise StopAsyncIteration

    async def fake_connect(url: str):
        assert url.startswith("wss://asr.cloud.tencent.com/asr/v2/1250000000?")
        return FakeWebsocket()

    signer = TencentRealtimeASRSigner(
        credentials=TencentCloudCredentials(
            secret_id="test-secret-id",
            secret_key="test-secret-key",
            app_id="1250000000",
        ),
        config=TencentRealtimeASRConfig(host="asr.cloud.tencent.com"),
    )
    session = TencentRealtimeASRSession(signer=signer, connect=fake_connect)

    messages = await session.recognize_pcm(b"x" * 12800, voice_id="voice-1")

    assert [len(item) for item in sent if isinstance(item, bytes)] == [6400, 6400]
    assert sent[-1] == '{"type": "end"}'
    assert messages[0]["final"] == 1


@pytest.mark.asyncio
async def test_tencent_realtime_asr_stream_sends_incremental_pcm_and_collects_messages() -> None:
    import asyncio

    sent: list = []

    class FakeWebsocket:
        def __init__(self):
            self.ended = asyncio.Event()
            self._messages = iter(
                [
                    '{"result":{"slice_type":1,"voice_text_str":"你"}}',
                    '{"result":{"slice_type":2,"voice_text_str":"你好"}}',
                ]
            )

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def send(self, payload):
            sent.append(payload)
            if payload == '{"type": "end"}':
                self.ended.set()

        def __aiter__(self):
            return self

        async def __anext__(self):
            await self.ended.wait()
            try:
                return next(self._messages)
            except StopIteration:
                raise StopAsyncIteration

    async def fake_connect(url: str):
        assert url.startswith("wss://asr.cloud.tencent.com/asr/v2/1250000000?")
        return FakeWebsocket()

    signer = TencentRealtimeASRSigner(
        credentials=TencentCloudCredentials(
            secret_id="test-secret-id",
            secret_key="test-secret-key",
            app_id="1250000000",
        ),
        config=TencentRealtimeASRConfig(host="asr.cloud.tencent.com"),
    )
    stream = TencentRealtimeASRStream(signer=signer, connect=fake_connect)

    await stream.start(voice_id="voice-1")
    await stream.push_pcm(b"x" * 3200)
    assert sent == []
    await stream.push_pcm(b"y" * 3200)
    assert [len(item) for item in sent if isinstance(item, bytes)] == [6400]

    messages = await stream.finish()
    await stream.close()

    assert sent[-1] == '{"type": "end"}'
    assert messages[-1]["result"]["voice_text_str"] == "你好"


@pytest.mark.asyncio
async def test_tencent_realtime_asr_stream_stops_sending_after_terminal_message() -> None:
    import asyncio

    sent: list = []

    class FakeWebsocket:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def send(self, payload):
            sent.append(payload)

        def __aiter__(self):
            self._messages = iter(['{"result":{"slice_type":2,"voice_text_str":"你好"}}'])
            return self

        async def __anext__(self):
            await asyncio.sleep(0)
            try:
                return next(self._messages)
            except StopIteration:
                raise StopAsyncIteration

    async def fake_connect(url: str):
        return FakeWebsocket()

    signer = TencentRealtimeASRSigner(
        credentials=TencentCloudCredentials(
            secret_id="test-secret-id",
            secret_key="test-secret-key",
            app_id="1250000000",
        ),
        config=TencentRealtimeASRConfig(host="asr.cloud.tencent.com"),
    )
    stream = TencentRealtimeASRStream(signer=signer, connect=fake_connect)

    await stream.start(voice_id="voice-1")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await stream.push_pcm(b"x" * 6400)
    messages = await stream.finish()
    await stream.close()

    assert sent == []
    assert messages[0]["result"]["voice_text_str"] == "你好"
