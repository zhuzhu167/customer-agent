from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.core.runtime_guard import (
    collect_runtime_configuration_violations,
    is_production_like,
)
from backend.app.db.models import ConversationTurn, VoiceEvent, VoiceSession, VoiceWorkerJob
from backend.app.services.provider_config import ProviderConfigService
from backend.app.services.voice_worker_jobs import VoiceWorkerJobService


METRIC_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
ACTIVE_SESSION_STATUSES = {"connecting", "listening"}


def build_runtime_metrics(*, db: Session, settings: Settings) -> str:
    metrics: list[str] = []
    _append_metric(
        metrics,
        "customer_agent_build_info",
        1,
        labels={
            "app": settings.app_name,
            "environment": settings.app_env,
            "voice_worker_launch_mode": settings.voice_worker_launch_mode,
        },
        help_text="Static build/runtime information for the customer agent backend.",
        metric_type="gauge",
    )
    _append_metric(
        metrics,
        "customer_agent_runtime_production_like",
        1 if is_production_like(settings) else 0,
        help_text="Whether the backend is running in a production-like environment.",
        metric_type="gauge",
    )
    _append_metric(
        metrics,
        "customer_agent_runtime_configuration_violations_total",
        len(collect_runtime_configuration_violations(settings)),
        help_text="Number of production runtime configuration guard violations.",
        metric_type="gauge",
    )

    for provider_type, provider_name in _active_provider_names(db, settings).items():
        _append_metric(
            metrics,
            "customer_agent_provider_active",
            1,
            labels={"provider_type": provider_type, "provider_name": provider_name},
            help_text="Currently active provider selection by provider type.",
            metric_type="gauge",
        )
        _append_metric(
            metrics,
            "customer_agent_provider_mock_active",
            1 if provider_name == "mock" else 0,
            labels={"provider_type": provider_type},
            help_text="Whether the active provider for a type is mock.",
            metric_type="gauge",
        )

    _append_session_metrics(metrics, db)
    _append_turn_metrics(metrics, db)
    _append_event_metrics(metrics, db)
    _append_worker_job_metrics(metrics, db, settings)
    return "\n".join(metrics) + "\n"


def _append_session_metrics(metrics: list[str], db: Session) -> None:
    rows = db.execute(select(VoiceSession.status, func.count()).group_by(VoiceSession.status)).all()
    counts = Counter({str(status): int(count) for status, count in rows})
    for status, count in sorted(counts.items()):
        _append_metric(
            metrics,
            "customer_agent_voice_sessions_total",
            count,
            labels={"status": status},
            help_text="Voice sessions grouped by current status.",
            metric_type="gauge",
        )
    active_count = sum(count for status, count in counts.items() if status in ACTIVE_SESSION_STATUSES)
    _append_metric(
        metrics,
        "customer_agent_voice_sessions_active",
        active_count,
        help_text="Current active voice sessions.",
        metric_type="gauge",
    )


def _append_turn_metrics(metrics: list[str], db: Session) -> None:
    rows = db.execute(
        select(ConversationTurn.role, ConversationTurn.status, func.count()).group_by(
            ConversationTurn.role,
            ConversationTurn.status,
        )
    ).all()
    for role, status, count in rows:
        _append_metric(
            metrics,
            "customer_agent_conversation_turns_total",
            int(count),
            labels={"role": str(role), "status": str(status)},
            help_text="Conversation turns grouped by role and status.",
            metric_type="gauge",
        )


def _append_event_metrics(metrics: list[str], db: Session) -> None:
    since = datetime.utcnow() - timedelta(minutes=15)
    error_rows = db.execute(
        select(VoiceEvent.event_type, func.count())
        .where(VoiceEvent.event_type.in_(("session.error", "voice_worker.error", "voice_worker.job_failed")))
        .group_by(VoiceEvent.event_type)
    ).all()
    for event_type, count in error_rows:
        _append_metric(
            metrics,
            "customer_agent_voice_events_errors_total",
            int(count),
            labels={"event_type": str(event_type)},
            help_text="Error-like voice events grouped by event type.",
            metric_type="gauge",
        )

    recent_error_count = db.scalar(
        select(func.count())
        .select_from(VoiceEvent)
        .where(
            VoiceEvent.created_at >= since,
            VoiceEvent.event_type.in_(("session.error", "voice_worker.error", "voice_worker.job_failed")),
        )
    )
    _append_metric(
        metrics,
        "customer_agent_voice_events_errors_recent_15m",
        int(recent_error_count or 0),
        help_text="Error-like voice events created in the last 15 minutes.",
        metric_type="gauge",
    )


def _append_worker_job_metrics(metrics: list[str], db: Session, settings: Settings) -> None:
    summary = VoiceWorkerJobService(db).summarize_jobs(
        limit=1,
        heartbeat_stale_seconds=max(
            settings.voice_worker_job_lease_seconds,
            int(settings.voice_worker_heartbeat_interval_seconds * 3),
        ),
    )
    for status, count in sorted(summary["counts_by_status"].items()):
        _append_metric(
            metrics,
            "customer_agent_voice_worker_jobs_total",
            int(count),
            labels={"status": str(status)},
            help_text="Voice worker jobs grouped by current status.",
            metric_type="gauge",
        )
    for name in (
        "queued_jobs",
        "running_jobs",
        "expired_running_jobs",
        "heartbeat_stale_jobs",
        "failed_jobs",
    ):
        _append_metric(
            metrics,
            f"customer_agent_voice_worker_{name}",
            int(summary[name]),
            help_text=f"Voice worker queue summary field {name}.",
            metric_type="gauge",
        )

    max_attempts = db.scalar(select(func.max(VoiceWorkerJob.attempts)).select_from(VoiceWorkerJob))
    _append_metric(
        metrics,
        "customer_agent_voice_worker_job_attempts_max",
        int(max_attempts or 0),
        help_text="Maximum attempts observed across voice worker jobs.",
        metric_type="gauge",
    )


def _active_provider_names(db: Session, settings: Settings) -> dict[str, str]:
    service = ProviderConfigService(db, settings)
    return {
        config.provider_type: config.provider_name
        for config in (
            service.get_active_config("asr"),
            service.get_active_config("tts"),
            service.get_active_config("llm"),
        )
    }


def _append_metric(
    metrics: list[str],
    name: str,
    value: int | float,
    *,
    labels: dict[str, str] | None = None,
    help_text: str | None = None,
    metric_type: str | None = None,
) -> None:
    if help_text is not None:
        metrics.append(f"# HELP {name} {help_text}")
    if metric_type is not None:
        metrics.append(f"# TYPE {name} {metric_type}")
    metrics.append(f"{name}{_format_labels(labels or {})} {value}")


def _format_labels(labels: dict[str, str]) -> str:
    if not labels:
        return ""
    rendered = ",".join(f'{key}="{_escape_label_value(value)}"' for key, value in sorted(labels.items()))
    return f"{{{rendered}}}"


def _escape_label_value(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')
