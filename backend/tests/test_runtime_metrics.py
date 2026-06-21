from __future__ import annotations

from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.app.core.config import Settings, get_settings
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services.event_store import EventStore
from backend.app.services.runtime_metrics import build_runtime_metrics
from backend.app.services.session_service import SessionService
from backend.app.services.voice_worker_jobs import VoiceWorkerJobService


def test_runtime_metrics_api_reports_mock_provider_without_sensitive_values(client: TestClient) -> None:
    response = client.get("/api/v1/runtime/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    assert 'customer_agent_provider_mock_active{provider_type="asr"} 1' in body
    assert 'customer_agent_provider_active{provider_name="mock",provider_type="asr"} 1' in body
    assert "secret_ref" not in body
    assert "TENCENT_CLOUD_SECRET" not in body
    assert "ALIBABA_CLOUD_API_KEY" not in body


def test_runtime_metrics_reports_sessions_turns_events_and_worker_jobs(
    db_session: Session,
    settings: Settings,
) -> None:
    session_service = SessionService(db_session, settings)
    session, _ = session_service.create_session(mode="debug", client_capabilities={})
    user_turn = session_service.add_turn(
        session_id=session.id,
        turn_index=1,
        role="user",
        text="这段用户文本不应该进入 metrics",
        visibility="public",
        status="final",
    )
    EventStore(db_session).create_event(
        session_id=session.id,
        turn_id=user_turn.id,
        event_type="session.error",
        payload={"message": "错误细节不应该进入 metrics"},
    )
    service = VoiceWorkerJobService(db_session)
    job = service.enqueue(session_id=session.id)
    job.status = "running"
    job.worker_id = "metrics-worker"
    job.attempts = 2
    job.heartbeat_at = datetime.utcnow() - timedelta(seconds=300)
    job.lease_expires_at = datetime.utcnow() - timedelta(seconds=1)
    db_session.commit()

    body = build_runtime_metrics(db=db_session, settings=settings)

    assert 'customer_agent_voice_sessions_total{status="listening"} 1' in body
    assert "customer_agent_voice_sessions_active 1" in body
    assert 'customer_agent_conversation_turns_total{role="user",status="final"} 1' in body
    assert 'customer_agent_voice_events_errors_total{event_type="session.error"} 1' in body
    assert "customer_agent_voice_events_errors_recent_15m 1" in body
    assert 'customer_agent_voice_worker_jobs_total{status="running"} 1' in body
    assert "customer_agent_voice_worker_expired_running_jobs 1" in body
    assert "customer_agent_voice_worker_heartbeat_stale_jobs 1" in body
    assert "customer_agent_voice_worker_job_attempts_max 2" in body
    assert str(session.id) not in body
    assert "这段用户文本" not in body
    assert "错误细节" not in body


def test_runtime_metrics_api_can_report_real_provider_selection(
    db_session: Session,
    settings: Settings,
) -> None:
    def override_get_db():
        yield db_session

    def override_get_settings() -> Settings:
        return settings.model_copy(
            update={
                "default_asr_provider": "tencent_cloud",
                "default_tts_provider": "tencent_cloud",
                "default_llm_provider": "alibaba_cloud",
            }
        )

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_settings] = override_get_settings
    try:
        with TestClient(app) as api_client:
            response = api_client.get("/api/v1/runtime/metrics")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.text
    assert 'customer_agent_provider_active{provider_name="tencent_cloud",provider_type="asr"} 1' in body
    assert 'customer_agent_provider_active{provider_name="tencent_cloud",provider_type="tts"} 1' in body
    assert 'customer_agent_provider_active{provider_name="alibaba_cloud",provider_type="llm"} 1' in body
    assert 'customer_agent_provider_mock_active{provider_type="asr"} 0' in body
    assert "secret_ref" not in body
