from __future__ import annotations

import os
import subprocess
import sys

import pytest

from backend.app.core.config import Settings
from backend.app.core.errors import RuntimeConfigurationError
from backend.app.core.runtime_guard import validate_runtime_configuration


def test_runtime_guard_allows_local_development_defaults() -> None:
    validate_runtime_configuration(Settings(app_env="local"))


def test_runtime_guard_rejects_production_development_livekit_secret() -> None:
    settings = Settings(
        app_env="production",
        livekit_api_key="devkey",
        livekit_api_secret="secret",
        tencent_cloud_secret_file="",
        alibaba_cloud_secret_file="",
    )

    with pytest.raises(RuntimeConfigurationError) as exc_info:
        validate_runtime_configuration(settings)

    fields = {item["field"] for item in exc_info.value.detail["violations"]}
    assert {"LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"}.issubset(fields)


def test_runtime_guard_rejects_production_mock_provider_and_secret_files() -> None:
    settings = Settings(
        app_env="production",
        livekit_api_key="prod-key",
        livekit_api_secret="x" * 32,
        default_asr_provider="mock",
        default_tts_provider="tencent_cloud",
        default_llm_provider="alibaba_cloud",
        tencent_cloud_secret_file="docs/密钥/腾讯云密钥.csv",
        alibaba_cloud_secret_file="docs/密钥/阿里云密钥.md",
    )

    with pytest.raises(RuntimeConfigurationError) as exc_info:
        validate_runtime_configuration(settings)

    fields = {item["field"] for item in exc_info.value.detail["violations"]}
    assert {"DEFAULT_ASR_PROVIDER", "TENCENT_CLOUD_SECRET_FILE", "ALIBABA_CLOUD_SECRET_FILE"}.issubset(fields)


def test_runtime_guard_rejects_production_missing_env_secret_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TENCENT_CLOUD_SECRET_ID", raising=False)
    monkeypatch.delenv("TENCENT_CLOUD_SECRET_KEY", raising=False)
    monkeypatch.delenv("ALIBABA_CLOUD_API_KEY", raising=False)

    settings = Settings(
        app_env="production",
        livekit_api_key="prod-key",
        livekit_api_secret="x" * 32,
        tencent_cloud_secret_file="",
        alibaba_cloud_secret_file="",
        tencent_cloud_secret_ref="env:TENCENT_CLOUD_SECRET_ID,TENCENT_CLOUD_SECRET_KEY",
        alibaba_cloud_secret_ref="env:ALIBABA_CLOUD_API_KEY",
    )

    with pytest.raises(RuntimeConfigurationError) as exc_info:
        validate_runtime_configuration(settings)

    fields = {item["field"] for item in exc_info.value.detail["violations"]}
    assert {"TENCENT_CLOUD_SECRET_REF", "ALIBABA_CLOUD_SECRET_REF"}.issubset(fields)


def test_runtime_guard_rejects_production_placeholder_secret_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TENCENT_CLOUD_SECRET_ID", "replace-me")
    monkeypatch.setenv("TENCENT_CLOUD_SECRET_KEY", "replace-me")
    monkeypatch.setenv("ALIBABA_CLOUD_API_KEY", "replace-me")

    settings = Settings(
        app_env="production",
        livekit_api_key="prod-key",
        livekit_api_secret="x" * 32,
        tencent_cloud_secret_file="",
        alibaba_cloud_secret_file="",
        tencent_cloud_secret_ref="env:TENCENT_CLOUD_SECRET_ID,TENCENT_CLOUD_SECRET_KEY",
        alibaba_cloud_secret_ref="env:ALIBABA_CLOUD_API_KEY",
    )

    with pytest.raises(RuntimeConfigurationError) as exc_info:
        validate_runtime_configuration(settings)

    reasons = " ".join(item["reason"] for item in exc_info.value.detail["violations"])
    assert "占位值" in reasons


def test_runtime_guard_allows_production_env_secret_refs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TENCENT_CLOUD_SECRET_ID", "AKIDprod")
    monkeypatch.setenv("TENCENT_CLOUD_SECRET_KEY", "prod-secret-key")
    monkeypatch.setenv("ALIBABA_CLOUD_API_KEY", "sk-prod")

    settings = Settings(
        app_env="production",
        livekit_api_key="prod-key",
        livekit_api_secret="x" * 32,
        tencent_cloud_secret_file="",
        alibaba_cloud_secret_file="",
        tencent_cloud_secret_ref="env:TENCENT_CLOUD_SECRET_ID,TENCENT_CLOUD_SECRET_KEY",
        alibaba_cloud_secret_ref="env:ALIBABA_CLOUD_API_KEY",
    )

    validate_runtime_configuration(settings)


def test_runtime_config_check_cli_fails_missing_production_secrets() -> None:
    env = {
        **os.environ,
        "APP_ENV": "production",
        "LIVEKIT_API_KEY": "prod-key",
        "LIVEKIT_API_SECRET": "x" * 32,
        "TENCENT_CLOUD_SECRET_FILE": "",
        "ALIBABA_CLOUD_SECRET_FILE": "",
    }
    env.pop("TENCENT_CLOUD_SECRET_ID", None)
    env.pop("TENCENT_CLOUD_SECRET_KEY", None)
    env.pop("ALIBABA_CLOUD_API_KEY", None)

    result = subprocess.run(
        [sys.executable, "-m", "backend.app.worker.runtime_config_check"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert '"ok": false' in result.stdout
    assert "TENCENT_CLOUD_SECRET_REF" in result.stdout
