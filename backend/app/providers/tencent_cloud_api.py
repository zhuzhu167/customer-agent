from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any

import httpx

from backend.app.core.errors import ProviderInvocationError
from backend.app.core.secrets import TencentCloudCredentials


def _sign(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


class TencentCloudApiClient:
    def __init__(
        self,
        *,
        credentials: TencentCloudCredentials,
        service: str,
        endpoint: str,
        region: str,
        timeout_seconds: float = 30,
    ) -> None:
        self.credentials = credentials
        self.service = service
        self.endpoint = endpoint
        self.region = region
        self.timeout_seconds = timeout_seconds

    async def call(self, *, action: str, version: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        timestamp = int(datetime.now(timezone.utc).timestamp())
        date = datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%d")
        authorization = self._build_authorization(body=body, timestamp=timestamp, date=date)

        headers = {
            "Authorization": authorization,
            "Content-Type": "application/json; charset=utf-8",
            "Host": self.endpoint,
            "X-TC-Action": action,
            "X-TC-Region": self.region,
            "X-TC-Timestamp": str(timestamp),
            "X-TC-Version": version,
        }

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(f"https://{self.endpoint}", content=body.encode("utf-8"), headers=headers)

        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderInvocationError(
                "腾讯云 API 返回非 JSON 响应",
                detail={"service": self.service, "action": action, "status_code": response.status_code},
            ) from exc

        if response.status_code >= 400 or data.get("Response", {}).get("Error"):
            error = data.get("Response", {}).get("Error", {})
            raise ProviderInvocationError(
                f"腾讯云 {action} 调用失败",
                detail={
                    "service": self.service,
                    "action": action,
                    "status_code": response.status_code,
                    "code": error.get("Code"),
                    "message": error.get("Message"),
                    "request_id": data.get("Response", {}).get("RequestId"),
                },
            )

        return data.get("Response", {})

    def _build_authorization(self, *, body: str, timestamp: int, date: str) -> str:
        algorithm = "TC3-HMAC-SHA256"
        http_request_method = "POST"
        canonical_uri = "/"
        canonical_query_string = ""
        canonical_headers = f"content-type:application/json; charset=utf-8\nhost:{self.endpoint}\n"
        signed_headers = "content-type;host"
        hashed_request_payload = hashlib.sha256(body.encode("utf-8")).hexdigest()
        canonical_request = "\n".join(
            [
                http_request_method,
                canonical_uri,
                canonical_query_string,
                canonical_headers,
                signed_headers,
                hashed_request_payload,
            ]
        )

        credential_scope = f"{date}/{self.service}/tc3_request"
        hashed_canonical_request = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
        string_to_sign = "\n".join([algorithm, str(timestamp), credential_scope, hashed_canonical_request])

        secret_date = _sign(("TC3" + self.credentials.secret_key).encode("utf-8"), date)
        secret_service = _sign(secret_date, self.service)
        secret_signing = _sign(secret_service, "tc3_request")
        signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

        return (
            f"{algorithm} "
            f"Credential={self.credentials.secret_id}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, "
            f"Signature={signature}"
        )


def encode_audio_base64(audio: bytes) -> str:
    return base64.b64encode(audio).decode("ascii")


def decode_audio_base64(audio: str) -> bytes:
    return base64.b64decode(audio.encode("ascii"))
