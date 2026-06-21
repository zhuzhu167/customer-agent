from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models import VoiceEvent, VoiceWorkerJob
from backend.app.services.session_service import SessionService
from backend.app.services.voice_worker_jobs import VoiceWorkerJobService, _build_claimable_job_query


def test_external_launch_mode_enqueues_worker_job_without_in_process_start(
    db_session: Session,
    settings,
    monkeypatch,
) -> None:
    from backend.app.core.config import get_settings
    from backend.app.db.session import get_db
    from backend.app.main import app

    started: list[str] = []

    def override_get_db():
        yield db_session

    def override_get_settings():
        return settings.model_copy(
            update={
                "auto_start_voice_worker": True,
                "voice_worker_launch_mode": "external",
            }
        )

    class FakeManager:
        def start(self, *, session_id):
            started.append(str(session_id))

        async def stop(self, *, session_id):
            started.append(f"stop:{session_id}")

        async def shutdown(self):
            return None

    original_manager = app.state.voice_worker_manager
    app.state.voice_worker_manager = FakeManager()
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_settings] = override_get_settings

    try:
        with TestClient(app) as client:
            response = client.post("/api/v1/sessions", json={"mode": "debug"})
    finally:
        app.dependency_overrides.clear()
        app.state.voice_worker_manager = original_manager

    assert response.status_code == 201
    session_id = response.json()["session_id"]
    assert started == []

    job = db_session.scalar(select(VoiceWorkerJob).where(VoiceWorkerJob.session_id == UUID(session_id)))
    assert job is not None
    assert job.status == "queued"

    events = db_session.scalars(
        select(VoiceEvent).where(VoiceEvent.session_id == UUID(session_id)).order_by(VoiceEvent.created_at)
    ).all()
    assert "voice_worker.start_requested" in [event.event_type for event in events]


def test_voice_worker_job_service_claims_and_cancels_jobs(db_session: Session, settings) -> None:
    session, _ = SessionService(db_session, settings).create_session(mode="debug", client_capabilities={})
    service = VoiceWorkerJobService(db_session)
    job = service.enqueue(session_id=session.id)
    db_session.commit()

    claimed = service.claim_next(worker_id="worker-a", lease_seconds=30, max_attempts=3)
    db_session.commit()

    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == "running"
    assert claimed.worker_id == "worker-a"
    assert claimed.attempts == 1
    assert claimed.lease_expires_at is not None
    assert claimed.heartbeat_at is not None

    service.cancel_pending_for_session(session_id=session.id, reason="session_ended")
    db_session.commit()
    db_session.refresh(claimed)

    assert claimed.status == "cancelled"
    assert claimed.last_error == "session_ended"
    assert claimed.lease_expires_at is None


def test_voice_worker_job_claim_query_uses_skip_locked_for_postgresql() -> None:
    query = _build_claimable_job_query(now=datetime.utcnow(), use_skip_locked=True)
    compiled = str(query.compile(dialect=postgresql.dialect())).upper()

    assert "FOR UPDATE SKIP LOCKED" in compiled


def test_voice_worker_job_claim_query_omits_lock_for_sqlite() -> None:
    query = _build_claimable_job_query(now=datetime.utcnow(), use_skip_locked=False)
    compiled = str(query.compile(dialect=sqlite.dialect())).upper()

    assert "FOR UPDATE" not in compiled
    assert "SKIP LOCKED" not in compiled


def test_voice_worker_job_service_reclaims_expired_running_job(db_session: Session, settings) -> None:
    session, _ = SessionService(db_session, settings).create_session(mode="debug", client_capabilities={})
    service = VoiceWorkerJobService(db_session)
    job = service.enqueue(session_id=session.id)
    db_session.commit()

    first_claim = service.claim_next(worker_id="worker-a", lease_seconds=30, max_attempts=3)
    assert first_claim is not None
    first_claim.lease_expires_at = datetime.utcnow() - timedelta(seconds=1)
    db_session.commit()

    reclaimed = service.claim_next(worker_id="worker-b", lease_seconds=30, max_attempts=3)
    db_session.commit()

    assert reclaimed is not None
    assert reclaimed.id == job.id
    assert reclaimed.status == "running"
    assert reclaimed.worker_id == "worker-b"
    assert reclaimed.attempts == 2
    event_types = [
        event.event_type
        for event in db_session.scalars(
            select(VoiceEvent).where(VoiceEvent.session_id == session.id).order_by(VoiceEvent.created_at)
        ).all()
    ]
    assert "voice_worker.job_lease_expired" in event_types


def test_voice_worker_job_service_marks_exhausted_job_failed(db_session: Session, settings) -> None:
    session, _ = SessionService(db_session, settings).create_session(mode="debug", client_capabilities={})
    service = VoiceWorkerJobService(db_session)
    job = service.enqueue(session_id=session.id)
    job.status = "running"
    job.attempts = 3
    job.worker_id = "worker-a"
    job.lease_expires_at = datetime.utcnow() - timedelta(seconds=1)
    db_session.commit()

    claimed = service.claim_next(worker_id="worker-b", lease_seconds=30, max_attempts=3)
    db_session.commit()
    db_session.refresh(job)

    assert claimed is None
    assert job.status == "failed"
    assert "max attempts" in (job.last_error or "")


def test_voice_worker_job_service_heartbeat_extends_lease(db_session: Session, settings) -> None:
    session, _ = SessionService(db_session, settings).create_session(mode="debug", client_capabilities={})
    service = VoiceWorkerJobService(db_session)
    job = service.enqueue(session_id=session.id)
    claimed = service.claim_next(worker_id="worker-a", lease_seconds=30, max_attempts=3)
    assert claimed is not None
    claimed.lease_expires_at = datetime.utcnow() - timedelta(seconds=1)
    db_session.commit()

    assert service.heartbeat(job=claimed, worker_id="worker-a", lease_seconds=45) is True
    db_session.commit()

    assert claimed.lease_expires_at is not None
    assert claimed.lease_expires_at > datetime.utcnow()


def test_voice_worker_job_service_summarizes_queue_health(db_session: Session, settings) -> None:
    service = VoiceWorkerJobService(db_session)
    queued_session, _ = SessionService(db_session, settings).create_session(mode="debug", client_capabilities={})
    running_session, _ = SessionService(db_session, settings).create_session(mode="debug", client_capabilities={})
    failed_session, _ = SessionService(db_session, settings).create_session(mode="debug", client_capabilities={})

    queued_job = service.enqueue(session_id=queued_session.id)
    running_job = service.enqueue(session_id=running_session.id)
    failed_job = service.enqueue(session_id=failed_session.id)

    running_job.status = "running"
    running_job.worker_id = "worker-stale"
    running_job.attempts = 2
    running_job.heartbeat_at = datetime.utcnow() - timedelta(seconds=90)
    running_job.lease_expires_at = datetime.utcnow() - timedelta(seconds=1)
    failed_job.status = "failed"
    failed_job.last_error = "provider timeout"
    failed_job.finished_at = datetime.utcnow()
    db_session.commit()

    summary = service.summarize_jobs(limit=3, heartbeat_stale_seconds=30)

    assert summary["total"] == 3
    assert summary["counts_by_status"] == {"queued": 1, "running": 1, "failed": 1}
    assert summary["queued_jobs"] == 1
    assert summary["running_jobs"] == 1
    assert summary["failed_jobs"] == 1
    assert summary["expired_running_jobs"] == 1
    assert summary["heartbeat_stale_jobs"] == 1
    assert len(summary["recent_jobs"]) == 3
    assert len(summary["recent_failed_jobs"]) == 1
    failed_snapshot = summary["recent_failed_jobs"][0]
    assert failed_snapshot["id"] == failed_job.id
    assert failed_snapshot["last_error"] == "provider timeout"
    assert failed_snapshot["lease_expired"] is False
    assert any(snapshot["id"] == queued_job.id for snapshot in summary["recent_jobs"])


def test_voice_worker_job_status_api_returns_operational_summary(
    client: TestClient,
    db_session: Session,
    settings,
) -> None:
    service = VoiceWorkerJobService(db_session)
    session, _ = SessionService(db_session, settings).create_session(mode="debug", client_capabilities={})
    job = service.enqueue(session_id=session.id)
    job.status = "running"
    job.worker_id = "worker-stale"
    job.attempts = 1
    job.heartbeat_at = datetime.utcnow() - timedelta(seconds=60)
    job.lease_expires_at = datetime.utcnow() - timedelta(seconds=1)
    db_session.commit()

    response = client.get("/api/v1/voice-worker/jobs/status?limit=5&heartbeat_stale_seconds=10")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["counts_by_status"] == {"running": 1}
    assert body["running_jobs"] == 1
    assert body["expired_running_jobs"] == 1
    assert body["heartbeat_stale_jobs"] == 1
    assert body["recent_jobs"][0]["id"] == str(job.id)
    assert body["recent_jobs"][0]["session_id"] == str(session.id)
    assert body["recent_jobs"][0]["lease_expired"] is True
    assert body["recent_jobs"][0]["heartbeat_stale"] is True


def test_external_voice_worker_runner_claims_and_completes_job(
    db_session: Session,
    settings,
    monkeypatch,
) -> None:
    import asyncio
    import backend.app.worker.voice_worker_runner as runner_module
    from sqlalchemy.orm import sessionmaker

    session, _ = SessionService(db_session, settings).create_session(mode="debug", client_capabilities={})
    VoiceWorkerJobService(db_session).enqueue(session_id=session.id)
    db_session.commit()

    testing_session_local = sessionmaker(
        bind=db_session.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    @dataclass(frozen=True)
    class FakeWorkerResult:
        audio_frames_seen: int

    class FakeLiveKitVoiceWorker:
        def __init__(self, *, db, settings, session_id):
            self.session_id = session_id

        async def run(self, *, max_seconds=None):
            return FakeWorkerResult(audio_frames_seen=7)

    monkeypatch.setattr(runner_module, "get_session_local", lambda: testing_session_local)
    monkeypatch.setattr(runner_module, "LiveKitVoiceWorker", FakeLiveKitVoiceWorker)

    processed = asyncio.run(
        runner_module.run_voice_worker_runner(
            settings=settings,
            worker_id="external-worker-a",
            once=True,
        )
    )

    assert processed == 1
    job = db_session.scalar(select(VoiceWorkerJob).where(VoiceWorkerJob.session_id == session.id))
    assert job is not None
    db_session.refresh(job)
    assert job.status == "completed"
    assert job.worker_id == "external-worker-a"

    events = db_session.scalars(
        select(VoiceEvent).where(VoiceEvent.session_id == session.id).order_by(VoiceEvent.created_at)
    ).all()
    event_types = [event.event_type for event in events]
    assert "voice_worker.job_claimed" in event_types
    assert "voice_worker.job_completed" in event_types


def test_voice_worker_runner_heartbeat_refreshes_running_job(
    db_session: Session,
    settings,
    monkeypatch,
) -> None:
    import asyncio
    import backend.app.worker.voice_worker_runner as runner_module
    from sqlalchemy.orm import sessionmaker

    session, _ = SessionService(db_session, settings).create_session(mode="debug", client_capabilities={})
    service = VoiceWorkerJobService(db_session)
    job = service.enqueue(session_id=session.id)
    claimed = service.claim_next(worker_id="worker-a", lease_seconds=1, max_attempts=3)
    assert claimed is not None
    job_id = claimed.id
    claimed.lease_expires_at = datetime.utcnow() - timedelta(seconds=1)
    db_session.commit()

    testing_session_local = sessionmaker(
        bind=db_session.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    monkeypatch.setattr(runner_module, "get_session_local", lambda: testing_session_local)
    heartbeat_settings = settings.model_copy(
        update={
            "voice_worker_heartbeat_interval_seconds": 0.01,
            "voice_worker_job_lease_seconds": 60,
        }
    )

    async def run_one_heartbeat() -> None:
        task = asyncio.create_task(
            runner_module._heartbeat_job(
                job_id=job_id,
                worker_id="worker-a",
                settings=heartbeat_settings,
            )
        )
        await asyncio.sleep(0.03)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run_one_heartbeat())

    db_session.refresh(job)
    assert job.heartbeat_at is not None
    assert job.lease_expires_at is not None
    assert job.lease_expires_at > datetime.utcnow()
    event_types = [
        event.event_type
        for event in db_session.scalars(
            select(VoiceEvent).where(VoiceEvent.session_id == session.id).order_by(VoiceEvent.created_at)
        ).all()
    ]
    assert "voice_worker.job_heartbeat" in event_types
