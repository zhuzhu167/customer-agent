from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from backend.app.core.config import Settings, get_settings
from backend.app.db.session import get_db
from backend.app.schemas import (
    AgentTurnRequest,
    AgentTurnResponse,
    AgentAudioTurnResponse,
    EventCreateRequest,
    EventResponse,
    ProviderConfigListResponse,
    ProviderConfigResponse,
    ProviderSmokeResponse,
    RuntimeReadinessResponse,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionEndRequest,
    SessionEndResponse,
    VoiceWorkerPrepareResponse,
    VoiceWorkerJobStatusResponse,
)
from backend.app.services.agent_service import AgentTurnService
from backend.app.services.event_store import EventStore
from backend.app.services.provider_config import ProviderConfigService
from backend.app.services.provider_smoke import ProviderSmokeService
from backend.app.services.runtime_metrics import METRIC_CONTENT_TYPE, build_runtime_metrics
from backend.app.services.runtime_readiness import build_runtime_readiness
from backend.app.services.session_service import SessionService
from backend.app.services.voice_worker import VoiceWorkerService
from backend.app.services.voice_worker_jobs import VoiceWorkerJobService

router = APIRouter()


@router.get("/runtime/readiness", response_model=RuntimeReadinessResponse)
def get_runtime_readiness(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RuntimeReadinessResponse:
    return RuntimeReadinessResponse(**build_runtime_readiness(db=db, settings=settings))


@router.get("/runtime/metrics", response_class=PlainTextResponse)
def get_runtime_metrics(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PlainTextResponse:
    return PlainTextResponse(
        build_runtime_metrics(db=db, settings=settings),
        media_type=METRIC_CONTENT_TYPE,
    )


@router.post("/sessions", response_model=SessionCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    request: SessionCreateRequest,
    fastapi_request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SessionCreateResponse:
    service = SessionService(db, settings)
    session, livekit = service.create_session(
        mode=request.mode,
        client_capabilities=request.client_capabilities,
    )
    if settings.auto_start_voice_worker and settings.voice_worker_launch_mode == "in_process":
        fastapi_request.app.state.voice_worker_manager.start(session_id=session.id)
    elif settings.auto_start_voice_worker and settings.voice_worker_launch_mode == "external":
        VoiceWorkerJobService(db).enqueue(session_id=session.id)
        db.commit()
    return SessionCreateResponse(
        session_id=session.id,
        status=session.status,
        mode=session.mode,
        livekit=livekit,
    )


@router.post("/sessions/{session_id}/end", response_model=SessionEndResponse)
async def end_session(
    session_id: UUID,
    request: SessionEndRequest,
    fastapi_request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SessionEndResponse:
    service = SessionService(db, settings)
    session = service.end_session(session_id=session_id, reason=request.reason)
    if settings.auto_start_voice_worker and settings.voice_worker_launch_mode == "in_process":
        await fastapi_request.app.state.voice_worker_manager.stop(session_id=session.id)
    elif settings.auto_start_voice_worker and settings.voice_worker_launch_mode == "external":
        VoiceWorkerJobService(db).cancel_pending_for_session(
            session_id=session.id,
            reason="session_ended",
        )
        db.commit()
    return SessionEndResponse(session_id=session.id, status=session.status, ended_at=session.ended_at)


@router.post("/sessions/{session_id}/voice-worker", response_model=VoiceWorkerPrepareResponse)
def prepare_voice_worker(
    session_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> VoiceWorkerPrepareResponse:
    info = VoiceWorkerService(db, settings).prepare_worker(session_id=session_id)
    return VoiceWorkerPrepareResponse(
        session_id=info.session_id,
        room_name=info.room_name,
        worker_identity=info.worker_identity,
        token=info.token,
        livekit_url=info.livekit_url,
        token_expires_in=info.token_expires_in,
    )


@router.get("/voice-worker/jobs/status", response_model=VoiceWorkerJobStatusResponse)
def get_voice_worker_job_status(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    limit: int = Query(default=20, ge=1, le=100),
    heartbeat_stale_seconds: int | None = Query(default=None, ge=1, le=86400),
) -> VoiceWorkerJobStatusResponse:
    stale_seconds = heartbeat_stale_seconds or max(
        settings.voice_worker_job_lease_seconds,
        int(settings.voice_worker_heartbeat_interval_seconds * 3),
    )
    summary = VoiceWorkerJobService(db).summarize_jobs(
        limit=limit,
        heartbeat_stale_seconds=stale_seconds,
    )
    return VoiceWorkerJobStatusResponse(**summary)


@router.get("/provider-configs", response_model=ProviderConfigListResponse)
def list_provider_configs(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ProviderConfigListResponse:
    service = ProviderConfigService(db, settings)
    return ProviderConfigListResponse(
        providers=[ProviderConfigResponse.model_validate(config) for config in service.list_provider_configs()]
    )


@router.post("/provider-configs/smoke", response_model=ProviderSmokeResponse)
async def smoke_provider_configs(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ProviderSmokeResponse:
    result = await ProviderSmokeService(db, settings).run()
    return ProviderSmokeResponse(
        llm_provider=result.llm_result.provider_name,
        llm_model=result.llm_result.model,
        llm_reply=result.llm_result.text,
        tts_provider=result.tts_result.provider_name,
        tts_content_type=result.tts_result.content_type,
        tts_audio_bytes=len(result.tts_result.audio_bytes),
        asr_provider=result.asr_provider,
        asr_transcript=result.transcript_result.text,
        asr_confidence=result.transcript_result.confidence,
    )


@router.post("/sessions/{session_id}/events", response_model=EventResponse, status_code=status.HTTP_201_CREATED)
def create_event(
    session_id: UUID,
    request: EventCreateRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> EventResponse:
    SessionService(db, settings).require_session(session_id)
    event = EventStore(db).create_event(
        session_id=session_id,
        turn_id=request.turn_id,
        event_type=request.event_type,
        payload=request.payload,
    )
    db.commit()
    db.refresh(event)
    return EventResponse(
        id=event.id,
        session_id=event.session_id,
        turn_id=event.turn_id,
        event_type=event.event_type,
        payload=event.payload,
        created_at=event.created_at,
    )


@router.post("/agent/turn", response_model=AgentTurnResponse)
async def handle_agent_turn(
    request: AgentTurnRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AgentTurnResponse:
    result = await AgentTurnService(db, settings).handle_text_turn(
        session_id=request.session_id,
        text=request.text,
    )
    return AgentTurnResponse(**result)


@router.post("/agent/audio-turn", response_model=AgentAudioTurnResponse)
async def handle_agent_audio_turn(
    session_id: UUID = Form(...),
    audio: UploadFile = File(...),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AgentAudioTurnResponse:
    payload = await audio.read()
    result = await AgentTurnService(db, settings).handle_audio_turn(
        session_id=session_id,
        audio=payload,
        content_type=audio.content_type or "application/octet-stream",
    )
    return AgentAudioTurnResponse(**result)
