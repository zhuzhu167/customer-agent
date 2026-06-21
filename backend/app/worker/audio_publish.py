from __future__ import annotations

import asyncio
import io
import wave
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class PcmAudio:
    pcm: bytes
    sample_rate: int
    num_channels: int
    sample_width: int


@dataclass(frozen=True)
class AudioPublishResult:
    frames_published: int
    interrupted: bool


def decode_wav_audio(wav_bytes: bytes) -> PcmAudio:
    with wave.open(io.BytesIO(wav_bytes), "rb") as reader:
        return PcmAudio(
            pcm=reader.readframes(reader.getnframes()),
            sample_rate=reader.getframerate(),
            num_channels=reader.getnchannels(),
            sample_width=reader.getsampwidth(),
        )


def iter_pcm_frame_bytes(audio: PcmAudio, *, frame_ms: int = 20) -> Iterator[bytes]:
    bytes_per_frame = int(audio.sample_rate * audio.num_channels * audio.sample_width * frame_ms / 1000)
    bytes_per_frame = max(audio.num_channels * audio.sample_width, bytes_per_frame)
    for offset in range(0, len(audio.pcm), bytes_per_frame):
        yield audio.pcm[offset : offset + bytes_per_frame]


async def publish_wav_audio(
    room,
    wav_bytes: bytes,
    *,
    track_name: str = "agent-voice",
    interrupt_event: asyncio.Event | None = None,
) -> AudioPublishResult:
    from livekit import rtc

    audio = decode_wav_audio(wav_bytes)
    if audio.sample_width != 2:
        raise ValueError("LiveKit 发布当前只支持 16-bit PCM WAV")

    source = rtc.AudioSource(sample_rate=audio.sample_rate, num_channels=audio.num_channels)
    track = rtc.LocalAudioTrack.create_audio_track(track_name, source)
    await room.local_participant.publish_track(track)

    frames_published = 0
    for chunk in iter_pcm_frame_bytes(audio):
        if interrupt_event is not None and interrupt_event.is_set():
            return AudioPublishResult(frames_published=frames_published, interrupted=True)
        if len(chunk) % (audio.num_channels * audio.sample_width) != 0:
            continue
        frame = rtc.AudioFrame(
            chunk,
            sample_rate=audio.sample_rate,
            num_channels=audio.num_channels,
            samples_per_channel=len(chunk) // (audio.num_channels * audio.sample_width),
        )
        await source.capture_frame(frame)
        frames_published += 1
        await asyncio.sleep(frame.duration)
    return AudioPublishResult(frames_published=frames_published, interrupted=False)
