from __future__ import annotations

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.core.errors import ProviderNotConfiguredError
from backend.app.services.provider_config import ProviderConfigService, default_provider_configs


def test_provider_config_lists_mock_and_cloud_defaults(client: TestClient) -> None:
    response = client.get("/api/v1/provider-configs")

    assert response.status_code == 200
    providers = response.json()["providers"]
    identities = {(item["provider_type"], item["provider_name"]) for item in providers}

    assert ("asr", "mock") in identities
    assert ("tts", "mock") in identities
    assert ("llm", "mock") in identities
    assert ("asr", "tencent_cloud") in identities
    assert ("tts", "tencent_cloud") in identities
    assert ("llm", "alibaba_cloud") in identities
    assert any(item["enabled"] for item in providers if item["provider_name"] == "mock")


def test_provider_config_hides_mock_outside_test_environment() -> None:
    settings = Settings(app_env="local")

    configs = default_provider_configs(settings)

    assert {item.provider_name for item in configs} == {"tencent_cloud", "alibaba_cloud"}
    assert all(item.provider_name != "mock" for item in configs)


def test_provider_config_rejects_mock_default_outside_test_environment(db_session: Session) -> None:
    settings = Settings(
        app_env="local",
        default_asr_provider="mock",
        default_tts_provider="tencent_cloud",
        default_llm_provider="alibaba_cloud",
    )

    with pytest.raises(ProviderNotConfiguredError) as exc_info:
        ProviderConfigService(db_session, settings).build_asr_provider()

    assert exc_info.value.detail["provider_name"] == "mock"
    assert exc_info.value.detail["app_env"] == "local"


def test_provider_smoke_api_covers_llm_tts_and_asr(client: TestClient) -> None:
    response = client.post("/api/v1/provider-configs/smoke")

    assert response.status_code == 200
    body = response.json()
    assert body["llm_provider"] == "mock"
    assert body["tts_provider"] == "mock"
    assert body["asr_provider"] == "mock"
    assert body["llm_reply"]
    assert body["tts_audio_bytes"] > 0
    assert body["asr_transcript"]


@pytest.mark.asyncio
async def test_provider_smoke_cli_skips_realtime_for_mock_provider(monkeypatch) -> None:
    from backend.app.worker.provider_smoke import run_provider_smoke
    from backend.app.core.config import get_settings
    from backend.app.db.base import Base
    from backend.app.db.session import get_session_local
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)

    monkeypatch.setattr(get_session_local, "cache_clear", lambda: None, raising=False)
    monkeypatch.setattr("backend.app.worker.provider_smoke.get_session_local", lambda: TestingSessionLocal)
    monkeypatch.setattr(
        "backend.app.worker.provider_smoke.get_settings",
        lambda: __import__("backend.app.core.config", fromlist=["Settings"]).Settings(
            app_env="test",
            database_url="sqlite+pysqlite:///:memory:",
            default_asr_provider="mock",
            default_tts_provider="mock",
            default_llm_provider="mock",
        ),
    )

    try:
        result = await run_provider_smoke(include_realtime_asr=True)
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()

    assert result["asr"]["provider"] == "mock"
    assert result["realtime_asr"]["skipped"] is True
