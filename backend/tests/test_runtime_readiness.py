from __future__ import annotations

from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.app.core.config import Settings, get_settings
from backend.app.db.models import VoiceWorkerJob
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services.runtime_readiness import build_runtime_readiness
from backend.app.services.session_service import SessionService
from backend.app.services.voice_worker_jobs import VoiceWorkerJobService


def test_runtime_readiness_api_reports_mock_provider_not_ready(client: TestClient) -> None:
    response = client.get("/api/v1/runtime/readiness")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["active_providers"]["asr"]["provider_name"] == "mock"
    provider_check = _check_by_name(body["checks"], "provider_selection")
    assert provider_check["status"] == "failed"
    assert {"provider_type": "asr", "provider_name": "mock"} in provider_check["detail"]["mock_providers"]


def test_runtime_readiness_reports_real_provider_selection_ready(db_session: Session) -> None:
    readiness = build_runtime_readiness(
        db=db_session,
        settings=Settings(
            app_env="local",
            database_url="sqlite+pysqlite:///:memory:",
            default_asr_provider="tencent_cloud",
            default_tts_provider="tencent_cloud",
            default_llm_provider="alibaba_cloud",
        ),
    )

    assert readiness["status"] == "ready"
    assert readiness["active_providers"]["asr"]["provider_name"] == "tencent_cloud"
    assert readiness["active_providers"]["tts"]["provider_name"] == "tencent_cloud"
    assert readiness["active_providers"]["llm"]["provider_name"] == "alibaba_cloud"
    assert _check_by_name(readiness["checks"], "provider_selection")["status"] == "ok"


def test_runtime_readiness_reports_production_configuration_violations(db_session: Session) -> None:
    readiness = build_runtime_readiness(
        db=db_session,
        settings=Settings(
            app_env="production",
            database_url="sqlite+pysqlite:///:memory:",
            livekit_api_key="devkey",
            livekit_api_secret="secret",
            default_asr_provider="mock",
            tencent_cloud_secret_file="docs/密钥/腾讯云密钥.csv",
            alibaba_cloud_secret_file="docs/密钥/阿里云密钥.md",
        ),
    )

    assert readiness["status"] == "not_ready"
    runtime_check = _check_by_name(readiness["checks"], "runtime_configuration")
    fields = {item["field"] for item in runtime_check["detail"]["violations"]}
    assert {"LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "DEFAULT_ASR_PROVIDER"}.issubset(fields)
    provider_check = _check_by_name(readiness["checks"], "provider_selection")
    assert provider_check["status"] == "failed"
    assert provider_check["detail"]["provider_errors"][0]["detail"]["provider_name"] == "mock"


def test_runtime_readiness_reports_worker_queue_degraded(db_session: Session, settings: Settings) -> None:
    service = VoiceWorkerJobService(db_session)
    session, _ = SessionService(db_session, settings).create_session(mode="debug", client_capabilities={})
    job = service.enqueue(session_id=session.id)
    job.status = "running"
    job.worker_id = "stale-worker"
    job.heartbeat_at = datetime.utcnow() - timedelta(seconds=300)
    job.lease_expires_at = datetime.utcnow() - timedelta(seconds=1)
    db_session.commit()

    readiness = build_runtime_readiness(
        db=db_session,
        settings=settings.model_copy(
            update={
                "default_asr_provider": "tencent_cloud",
                "default_tts_provider": "tencent_cloud",
                "default_llm_provider": "alibaba_cloud",
            }
        ),
    )

    assert readiness["status"] == "degraded"
    assert "secret_ref" not in readiness["active_providers"]["asr"]
    worker_check = _check_by_name(readiness["checks"], "voice_worker_queue")
    assert worker_check["status"] == "warning"
    assert worker_check["detail"]["expired_running_jobs"] == 1
    assert worker_check["detail"]["heartbeat_stale_jobs"] == 1


def test_runtime_readiness_api_can_use_real_provider_settings(db_session: Session, settings: Settings) -> None:
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
            response = api_client.get("/api/v1/runtime/readiness")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["active_providers"]["asr"]["provider_name"] == "tencent_cloud"
    assert "secret_ref" not in body["active_providers"]["asr"]


def _check_by_name(checks, name: str):
    return next(check for check in checks if check["name"] == name)
