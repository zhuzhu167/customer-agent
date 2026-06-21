from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.sql import Select
from sqlalchemy.orm import Session

from backend.app.db.models import VoiceSession, VoiceWorkerJob
from backend.app.services.event_store import EventStore


class VoiceWorkerJobService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.events = EventStore(db)

    def enqueue(self, *, session_id: UUID) -> VoiceWorkerJob:
        existing = self.db.scalar(select(VoiceWorkerJob).where(VoiceWorkerJob.session_id == session_id))
        if existing is not None:
            return existing

        job = VoiceWorkerJob(session_id=session_id, status="queued")
        self.db.add(job)
        self.db.flush()
        self.events.create_event(
            session_id=session_id,
            event_type="voice_worker.start_requested",
            payload={"mode": "external", "job_id": str(job.id)},
        )
        return job

    def claim_next(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        max_attempts: int,
    ) -> VoiceWorkerJob | None:
        now = _utcnow()
        job = self.db.scalar(self._claimable_job_query(now))
        if job is None:
            return None

        session = self.db.get(VoiceSession, job.session_id)
        if session is None or session.status == "ended":
            self.mark_cancelled(job=job, reason="session_not_active")
            return None

        if job.status == "running":
            self.events.create_event(
                session_id=job.session_id,
                event_type="voice_worker.job_lease_expired",
                payload={
                    "job_id": str(job.id),
                    "previous_worker_id": job.worker_id,
                    "attempts": job.attempts,
                },
            )

        if job.attempts >= max_attempts:
            self.mark_failed(job=job, error=RuntimeError("voice worker job exceeded max attempts"))
            return None

        job.status = "running"
        job.worker_id = worker_id
        job.attempts += 1
        job.claimed_at = now
        job.heartbeat_at = now
        job.lease_expires_at = now + timedelta(seconds=lease_seconds)
        job.updated_at = now
        self.events.create_event(
            session_id=job.session_id,
            event_type="voice_worker.job_claimed",
            payload={
                "job_id": str(job.id),
                "worker_id": worker_id,
                "attempts": job.attempts,
                "lease_expires_at": job.lease_expires_at.isoformat(),
            },
        )
        return job

    def _claimable_job_query(self, now: datetime) -> Select:
        return _build_claimable_job_query(
            now=now,
            use_skip_locked=_supports_skip_locked(self.db),
        )

    def heartbeat(self, *, job: VoiceWorkerJob, worker_id: str, lease_seconds: int) -> bool:
        if job.status != "running" or job.worker_id != worker_id:
            return False

        now = _utcnow()
        job.heartbeat_at = now
        job.lease_expires_at = now + timedelta(seconds=lease_seconds)
        job.updated_at = now
        self.events.create_event(
            session_id=job.session_id,
            event_type="voice_worker.job_heartbeat",
            payload={
                "job_id": str(job.id),
                "worker_id": worker_id,
                "lease_expires_at": job.lease_expires_at.isoformat(),
            },
        )
        return True

    def mark_completed(self, *, job: VoiceWorkerJob, audio_frames_seen: int) -> None:
        now = _utcnow()
        job.status = "completed"
        job.finished_at = now
        job.lease_expires_at = None
        job.updated_at = now
        self.events.create_event(
            session_id=job.session_id,
            event_type="voice_worker.job_completed",
            payload={"job_id": str(job.id), "audio_frames_seen": audio_frames_seen},
        )

    def mark_failed(self, *, job: VoiceWorkerJob, error: BaseException) -> None:
        now = _utcnow()
        job.status = "failed"
        job.last_error = str(error)
        job.finished_at = now
        job.lease_expires_at = None
        job.updated_at = now
        self.events.create_event(
            session_id=job.session_id,
            event_type="voice_worker.job_failed",
            payload={"job_id": str(job.id), "error_code": "voice_worker.job_failed", "message": str(error)},
        )

    def mark_cancelled(self, *, job: VoiceWorkerJob, reason: str) -> None:
        now = _utcnow()
        job.status = "cancelled"
        job.last_error = reason
        job.finished_at = now
        job.lease_expires_at = None
        job.updated_at = now
        self.events.create_event(
            session_id=job.session_id,
            event_type="voice_worker.job_cancelled",
            payload={"job_id": str(job.id), "reason": reason},
        )

    def cancel_pending_for_session(self, *, session_id: UUID, reason: str) -> int:
        jobs = self.db.scalars(
            select(VoiceWorkerJob).where(
                VoiceWorkerJob.session_id == session_id,
                VoiceWorkerJob.status.in_(("queued", "running")),
            )
        ).all()
        for job in jobs:
            self.mark_cancelled(job=job, reason=reason)
        return len(jobs)

    def summarize_jobs(self, *, limit: int = 20, heartbeat_stale_seconds: int | None = None) -> dict:
        now = _utcnow()
        normalized_limit = max(1, min(limit, 100))
        stale_seconds = heartbeat_stale_seconds or 2 * 60

        jobs = self.db.scalars(select(VoiceWorkerJob)).all()
        recent_jobs = self.db.scalars(
            select(VoiceWorkerJob)
            .order_by(VoiceWorkerJob.updated_at.desc(), VoiceWorkerJob.created_at.desc())
            .limit(normalized_limit)
        ).all()
        recent_failed_jobs = self.db.scalars(
            select(VoiceWorkerJob)
            .where(VoiceWorkerJob.status == "failed")
            .order_by(VoiceWorkerJob.updated_at.desc(), VoiceWorkerJob.created_at.desc())
            .limit(normalized_limit)
        ).all()

        counts = Counter(job.status for job in jobs)
        expired_running_jobs = sum(1 for job in jobs if _lease_expired(job, now))
        heartbeat_stale_jobs = sum(1 for job in jobs if _heartbeat_stale(job, now, stale_seconds))

        return {
            "generated_at": now,
            "total": len(jobs),
            "counts_by_status": dict(counts),
            "queued_jobs": counts.get("queued", 0),
            "running_jobs": counts.get("running", 0),
            "expired_running_jobs": expired_running_jobs,
            "heartbeat_stale_jobs": heartbeat_stale_jobs,
            "failed_jobs": counts.get("failed", 0),
            "recent_jobs": [_snapshot_job(job, now=now, heartbeat_stale_seconds=stale_seconds) for job in recent_jobs],
            "recent_failed_jobs": [
                _snapshot_job(job, now=now, heartbeat_stale_seconds=stale_seconds) for job in recent_failed_jobs
            ],
        }


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _supports_skip_locked(db: Session) -> bool:
    bind = db.get_bind()
    return bind.dialect.name == "postgresql"


def _build_claimable_job_query(*, now: datetime, use_skip_locked: bool) -> Select:
    query = (
        select(VoiceWorkerJob)
        .where(
            or_(
                VoiceWorkerJob.status == "queued",
                and_(
                    VoiceWorkerJob.status == "running",
                    VoiceWorkerJob.lease_expires_at.is_not(None),
                    VoiceWorkerJob.lease_expires_at < now,
                ),
            )
        )
        .order_by(VoiceWorkerJob.created_at)
        .limit(1)
    )
    if use_skip_locked:
        return query.with_for_update(skip_locked=True)
    return query


def _lease_expired(job: VoiceWorkerJob, now: datetime) -> bool:
    return job.status == "running" and job.lease_expires_at is not None and job.lease_expires_at < now


def _heartbeat_stale(job: VoiceWorkerJob, now: datetime, stale_seconds: int) -> bool:
    if job.status != "running" or job.heartbeat_at is None:
        return False
    return job.heartbeat_at < now - timedelta(seconds=stale_seconds)


def _snapshot_job(job: VoiceWorkerJob, *, now: datetime, heartbeat_stale_seconds: int) -> dict:
    return {
        "id": job.id,
        "session_id": job.session_id,
        "status": job.status,
        "worker_id": job.worker_id,
        "attempts": job.attempts,
        "last_error": job.last_error,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "claimed_at": job.claimed_at,
        "lease_expires_at": job.lease_expires_at,
        "heartbeat_at": job.heartbeat_at,
        "finished_at": job.finished_at,
        "lease_expired": _lease_expired(job, now),
        "heartbeat_stale": _heartbeat_stale(job, now, heartbeat_stale_seconds),
    }
