from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.core.errors import ProviderNotConfiguredError
from backend.app.core.runtime_guard import (
    collect_runtime_configuration_violations,
    is_production_like,
)
from backend.app.services.provider_config import ProviderConfigService
from backend.app.services.voice_worker_jobs import VoiceWorkerJobService


def build_runtime_readiness(
    *,
    db: Session,
    settings: Settings,
    job_limit: int = 5,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    provider_service = ProviderConfigService(db, settings)
    active_providers: dict[str, Any] = {}
    provider_errors: list[dict[str, Any]] = []
    for provider_type in ("asr", "tts", "llm"):
        try:
            config = provider_service.get_active_config(provider_type)
        except ProviderNotConfiguredError as exc:
            provider_errors.append(
                {
                    "provider_type": provider_type,
                    "error_code": exc.error_code,
                    "message": exc.message,
                    "detail": exc.detail,
                }
            )
            continue
        active_providers[config.provider_type] = {
            "provider_name": config.provider_name,
            "model": config.model,
            "region": config.region,
            "enabled": config.enabled,
        }
    active_provider_names = {
        provider_type: str(payload["provider_name"]) for provider_type, payload in active_providers.items()
    }

    db_ok, db_detail = _check_database(db)
    checks.append({"name": "database", "status": "ok" if db_ok else "failed", "detail": db_detail})

    violations = collect_runtime_configuration_violations(settings)
    checks.append(
        {
            "name": "runtime_configuration",
            "status": "ok" if not violations else "failed",
            "detail": {
                "production_like": is_production_like(settings),
                "violations": violations,
            },
        }
    )

    mock_providers = [
        {"provider_type": provider_type, "provider_name": provider_name}
        for provider_type, provider_name in active_provider_names.items()
        if provider_name == "mock"
    ]
    missing_provider_types = [
        provider_type for provider_type in ("asr", "tts", "llm") if provider_type not in active_provider_names
    ]
    checks.append(
        {
            "name": "provider_selection",
            "status": "ok" if not mock_providers and not missing_provider_types and not provider_errors else "failed",
            "detail": {
                "active_providers": active_provider_names,
                "mock_providers": mock_providers,
                "missing_provider_types": missing_provider_types,
                "provider_errors": provider_errors,
            },
        }
    )

    worker_summary = VoiceWorkerJobService(db).summarize_jobs(limit=job_limit)
    worker_problem_count = (
        worker_summary["expired_running_jobs"]
        + worker_summary["heartbeat_stale_jobs"]
        + worker_summary["failed_jobs"]
    )
    checks.append(
        {
            "name": "voice_worker_queue",
            "status": "ok" if worker_problem_count == 0 else "warning",
            "detail": {
                "queued_jobs": worker_summary["queued_jobs"],
                "running_jobs": worker_summary["running_jobs"],
                "expired_running_jobs": worker_summary["expired_running_jobs"],
                "heartbeat_stale_jobs": worker_summary["heartbeat_stale_jobs"],
                "failed_jobs": worker_summary["failed_jobs"],
                "recent_failed_jobs": worker_summary["recent_failed_jobs"],
            },
        }
    )

    status = "ready"
    if any(check["status"] == "failed" for check in checks):
        status = "not_ready"
    elif any(check["status"] == "warning" for check in checks):
        status = "degraded"

    return {
        "status": status,
        "generated_at": datetime.now(timezone.utc),
        "app": settings.app_name,
        "environment": settings.app_env,
        "production_like": is_production_like(settings),
        "voice_worker_launch_mode": settings.voice_worker_launch_mode,
        "active_providers": active_providers,
        "checks": checks,
    }


def _check_database(db: Session) -> tuple[bool, dict[str, Any]]:
    try:
        db.execute(text("SELECT 1")).scalar_one()
    except Exception as exc:  # noqa: BLE001
        return False, {"message": str(exc)}
    return True, {"message": "database query succeeded"}
