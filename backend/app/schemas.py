from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


SessionMode = Literal["normal", "demo", "debug"]


class HealthResponse(BaseModel):
    status: str
    app: str
    environment: str


class RuntimeReadinessCheck(BaseModel):
    name: str
    status: str
    detail: dict[str, Any] = Field(default_factory=dict)


class RuntimeReadinessResponse(BaseModel):
    status: str
    generated_at: datetime
    app: str
    environment: str
    production_like: bool
    voice_worker_launch_mode: str
    active_providers: dict[str, dict[str, Any]]
    checks: list[RuntimeReadinessCheck]


class SessionCreateRequest(BaseModel):
    mode: SessionMode = "demo"
    client_capabilities: dict[str, Any] = Field(default_factory=dict)


class LiveKitJoinInfo(BaseModel):
    url: str
    room_name: str
    token: str
    user_identity: str
    worker_identity: str
    token_expires_in: int


class SessionCreateResponse(BaseModel):
    session_id: UUID
    status: str
    mode: str
    livekit: LiveKitJoinInfo


class SessionEndRequest(BaseModel):
    reason: str = "user_requested"


class SessionEndResponse(BaseModel):
    session_id: UUID
    status: str
    ended_at: datetime


class VoiceWorkerPrepareResponse(BaseModel):
    session_id: UUID
    room_name: str
    worker_identity: str
    token: str
    livekit_url: str
    token_expires_in: int


class ProviderConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    provider_type: str
    provider_name: str
    model: str
    region: str | None = None
    secret_ref: str | None = None
    enabled: bool


class ProviderConfigListResponse(BaseModel):
    providers: list[ProviderConfigResponse]


class ProviderSmokeResponse(BaseModel):
    llm_provider: str
    llm_model: str
    llm_reply: str
    tts_provider: str
    tts_content_type: str
    tts_audio_bytes: int
    asr_provider: str
    asr_transcript: str
    asr_confidence: float | None = None


class EventCreateRequest(BaseModel):
    event_type: str
    turn_id: UUID | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class EventResponse(BaseModel):
    id: UUID
    session_id: UUID
    turn_id: UUID | None
    event_type: str
    payload: dict[str, Any]
    created_at: datetime


class AgentTurnRequest(BaseModel):
    session_id: UUID
    text: str = Field(min_length=1)


class AgentTurnResponse(BaseModel):
    session_id: UUID
    user_turn_id: UUID
    preparing_turn_id: UUID
    answered_turn_id: UUID
    response_text: str
    preparing_visible: bool
    tts_provider: str
    tts_content_type: str
    tts_audio_bytes: int


class AgentAudioTurnResponse(AgentTurnResponse):
    transcript_text: str
    transcript_confidence: float | None = None
    tts_audio_base64: str


class VoiceWorkerJobSnapshot(BaseModel):
    id: UUID
    session_id: UUID
    status: str
    worker_id: str | None = None
    attempts: int
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime
    claimed_at: datetime | None = None
    lease_expires_at: datetime | None = None
    heartbeat_at: datetime | None = None
    finished_at: datetime | None = None
    lease_expired: bool
    heartbeat_stale: bool


class VoiceWorkerJobStatusResponse(BaseModel):
    generated_at: datetime
    total: int
    counts_by_status: dict[str, int]
    queued_jobs: int
    running_jobs: int
    expired_running_jobs: int
    heartbeat_stale_jobs: int
    failed_jobs: int
    recent_jobs: list[VoiceWorkerJobSnapshot]
    recent_failed_jobs: list[VoiceWorkerJobSnapshot]
