from __future__ import annotations

import base64
import datetime
import json
import time
from dataclasses import dataclass
from typing import Any

from backend.app.core.config import Settings
from backend.app.core.errors import RuntimeConfigurationError
from backend.app.core.runtime_guard import is_production_like


@dataclass(frozen=True)
class LiveKitTokenResult:
    token: str
    expires_in: int


class LiveKitTokenService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def issue_join_token(self, *, room_name: str, identity: str, name: str | None = None) -> LiveKitTokenResult:
        try:
            from livekit import api as livekit_api

            grants = livekit_api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
            token = (
                livekit_api.AccessToken(self.settings.livekit_api_key, self.settings.livekit_api_secret)
                .with_identity(identity)
                .with_name(name or identity)
                .with_ttl(datetime.timedelta(seconds=self.settings.livekit_token_ttl_seconds))
                .with_grants(grants)
                .to_jwt()
            )
        except Exception as exc:
            if is_production_like(self.settings):
                raise RuntimeConfigurationError(
                    "生产环境 LiveKit token 签发失败",
                    detail={"room_name": room_name, "identity": identity, "reason": str(exc)},
                ) from exc
            token = self._issue_placeholder_token(room_name=room_name, identity=identity)
        return LiveKitTokenResult(token=token, expires_in=self.settings.livekit_token_ttl_seconds)

    def _issue_placeholder_token(self, *, room_name: str, identity: str) -> str:
        payload: dict[str, Any] = {
            "iss": self.settings.livekit_api_key,
            "sub": identity,
            "room": room_name,
            "exp": int(time.time()) + self.settings.livekit_token_ttl_seconds,
            "placeholder": True,
        }
        encoded = base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True).encode("utf-8")).decode("ascii")
        return f"local-placeholder.{encoded}.signature"
