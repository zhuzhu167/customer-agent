from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from pathlib import Path

from backend.app.core.errors import ProviderNotConfiguredError


@dataclass(frozen=True)
class TencentCloudCredentials:
    secret_id: str
    secret_key: str
    app_id: str | None = None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _resolve_path(path: str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return _repo_root() / candidate


def _read_env_names(secret_ref: str | None) -> list[str]:
    if not secret_ref or not secret_ref.startswith("env:"):
        return []
    return [item.strip() for item in secret_ref.removeprefix("env:").split(",") if item.strip()]


def resolve_env_secret(secret_ref: str | None, *, provider: str, fallback_file: str | None = None) -> str:
    for env_name in _read_env_names(secret_ref):
        value = os.getenv(env_name)
        if value:
            return value.strip()

    if fallback_file:
        path = _resolve_path(fallback_file)
        if path.exists():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value

    raise ProviderNotConfiguredError(
        f"{provider} 缺少可用密钥",
        detail={"provider": provider, "secret_ref": secret_ref, "fallback_file": fallback_file},
    )


def resolve_tencent_credentials(
    secret_ref: str | None,
    *,
    fallback_file: str | None = None,
    app_id: str | None = None,
) -> TencentCloudCredentials:
    env_names = _read_env_names(secret_ref)
    secret_id = os.getenv(env_names[0]).strip() if len(env_names) >= 1 and os.getenv(env_names[0]) else None
    secret_key = os.getenv(env_names[1]).strip() if len(env_names) >= 2 and os.getenv(env_names[1]) else None
    env_app_id = os.getenv("TENCENT_CLOUD_APP_ID") or os.getenv("TENCENT_CLOUD_APPID")

    if secret_id and secret_key:
        return TencentCloudCredentials(secret_id=secret_id, secret_key=secret_key, app_id=app_id or env_app_id)

    if fallback_file:
        path = _resolve_path(fallback_file)
        if path.exists():
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                row = next(reader, None)
                if row:
                    file_secret_id = (row.get("SecretId") or row.get("secret_id") or "").strip()
                    file_secret_key = (row.get("SecretKey") or row.get("secret_key") or "").strip()
                    file_app_id = (row.get("APPID") or row.get("AppId") or row.get("app_id") or "").strip()
                    if file_secret_id and file_secret_key:
                        return TencentCloudCredentials(
                            secret_id=file_secret_id,
                            secret_key=file_secret_key,
                            app_id=app_id or env_app_id or file_app_id or None,
                        )

    raise ProviderNotConfiguredError(
        "腾讯云缺少可用 SecretId/SecretKey",
        detail={"provider": "tencent_cloud", "secret_ref": secret_ref, "fallback_file": fallback_file},
    )
