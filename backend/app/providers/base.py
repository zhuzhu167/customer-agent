from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProviderConfigDTO:
    provider_type: str
    provider_name: str
    model: str
    region: str | None = None
    secret_ref: str | None = None
    enabled: bool = True
    options: dict[str, Any] | None = None


@dataclass(frozen=True)
class TranscriptResult:
    text: str
    confidence: float | None = None
    is_final: bool = True


@dataclass(frozen=True)
class LLMResult:
    text: str
    model: str
    provider_name: str
    raw_metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class TTSResult:
    audio_bytes: bytes
    content_type: str
    provider_name: str
    raw_metadata: dict[str, Any] | None = None


class ASRProvider(ABC):
    config: ProviderConfigDTO

    @abstractmethod
    async def transcribe(self, audio: bytes, *, content_type: str) -> TranscriptResult:
        raise NotImplementedError


class TTSProvider(ABC):
    config: ProviderConfigDTO

    @abstractmethod
    async def synthesize(self, text: str) -> TTSResult:
        raise NotImplementedError


class LLMProvider(ABC):
    config: ProviderConfigDTO

    @abstractmethod
    async def generate_reply(self, *, session_id: str, user_text: str, history: list[dict]) -> LLMResult:
        raise NotImplementedError
