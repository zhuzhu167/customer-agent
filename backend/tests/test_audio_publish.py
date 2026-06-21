from __future__ import annotations

import io
import wave

from backend.app.worker.audio_publish import decode_wav_audio, iter_pcm_frame_bytes


def _make_wav(pcm: bytes, *, sample_rate: int = 16000, channels: int = 1) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(pcm)
    return buffer.getvalue()


def test_decode_wav_audio_extracts_pcm_metadata() -> None:
    wav = _make_wav(b"\x00\x01" * 160)

    audio = decode_wav_audio(wav)

    assert audio.sample_rate == 16000
    assert audio.num_channels == 1
    assert audio.sample_width == 2
    assert audio.pcm == b"\x00\x01" * 160


def test_iter_pcm_frame_bytes_uses_20ms_frames() -> None:
    audio = decode_wav_audio(_make_wav(b"x" * 1280))

    chunks = list(iter_pcm_frame_bytes(audio, frame_ms=20))

    assert [len(chunk) for chunk in chunks] == [640, 640]
