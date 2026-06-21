from __future__ import annotations

import sys

import pytest

from backend.app.core.config import Settings
from backend.app.core.errors import RuntimeConfigurationError
from backend.app.services.livekit_tokens import LiveKitTokenService


def test_livekit_token_service_issues_real_jwt_when_sdk_available(settings) -> None:
    token = LiveKitTokenService(settings).issue_join_token(
        room_name="voice-test",
        identity="worker-test",
        name="voice-worker",
    )

    assert token.token.count(".") == 2
    assert not token.token.startswith("local-placeholder.")


def test_livekit_token_service_allows_placeholder_only_outside_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "livekit", None)
    settings = Settings(app_env="local")

    token = LiveKitTokenService(settings).issue_join_token(room_name="voice-test", identity="worker-test")

    assert token.token.startswith("local-placeholder.")


def test_livekit_token_service_rejects_placeholder_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "livekit", None)
    settings = Settings(
        app_env="production",
        livekit_api_key="prod-key",
        livekit_api_secret="x" * 32,
        tencent_cloud_secret_file="",
        alibaba_cloud_secret_file="",
    )

    with pytest.raises(RuntimeConfigurationError):
        LiveKitTokenService(settings).issue_join_token(room_name="voice-test", identity="worker-test")
