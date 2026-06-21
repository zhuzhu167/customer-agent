from __future__ import annotations

import argparse
import asyncio
from uuid import UUID

from backend.app.core.config import Settings, get_settings
from backend.app.db.models import VoiceWorkerJob
from backend.app.db.session import get_session_local
from backend.app.services.voice_worker_jobs import VoiceWorkerJobService
from backend.app.worker.livekit_voice_worker import LiveKitVoiceWorker


async def run_voice_worker_runner(
    *,
    settings: Settings,
    worker_id: str,
    once: bool = False,
    max_seconds_per_job: float | None = None,
) -> int:
    session_local = get_session_local()
    jobs_processed = 0

    while True:
        with session_local() as db:
            service = VoiceWorkerJobService(db)
            job = service.claim_next(
                worker_id=worker_id,
                lease_seconds=settings.voice_worker_job_lease_seconds,
                max_attempts=settings.voice_worker_max_attempts,
            )
            db.commit()

            if job is None:
                if once:
                    return jobs_processed
                await asyncio.sleep(settings.voice_worker_poll_interval_seconds)
                continue

            job_id = job.id
            session_id = job.session_id

        heartbeat_task: asyncio.Task | None = None
        try:
            heartbeat_task = asyncio.create_task(
                _heartbeat_job(job_id=job_id, worker_id=worker_id, settings=settings)
            )
            with session_local() as worker_db:
                result = await LiveKitVoiceWorker(
                    db=worker_db,
                    settings=settings,
                    session_id=session_id,
                ).run(max_seconds=max_seconds_per_job)
            await _stop_heartbeat_task(heartbeat_task)
            with session_local() as done_db:
                service = VoiceWorkerJobService(done_db)
                fresh_job = done_db.get(VoiceWorkerJob, job_id)
                if fresh_job is not None:
                    service.mark_completed(job=fresh_job, audio_frames_seen=result.audio_frames_seen)
                    done_db.commit()
        except BaseException as exc:  # noqa: BLE001
            await _stop_heartbeat_task(heartbeat_task)
            with session_local() as failed_db:
                service = VoiceWorkerJobService(failed_db)
                fresh_job = failed_db.get(VoiceWorkerJob, job_id)
                if fresh_job is not None:
                    service.mark_failed(job=fresh_job, error=exc)
                    failed_db.commit()
            raise

        jobs_processed += 1
        if once:
            return jobs_processed


async def _stop_heartbeat_task(task: asyncio.Task | None) -> None:
    if task is None:
        return
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def _heartbeat_job(
    *,
    job_id: UUID,
    worker_id: str,
    settings: Settings,
) -> None:
    session_local = get_session_local()
    while True:
        await asyncio.sleep(settings.voice_worker_heartbeat_interval_seconds)
        with session_local() as db:
            job = db.get(VoiceWorkerJob, job_id)
            if job is None:
                return
            service = VoiceWorkerJobService(db)
            if not service.heartbeat(
                job=job,
                worker_id=worker_id,
                lease_seconds=settings.voice_worker_job_lease_seconds,
            ):
                db.commit()
                return
            db.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run external LiveKit voice worker queue runner.")
    parser.add_argument("--worker-id", default=None)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--max-seconds-per-job", type=float, default=None)
    args = parser.parse_args()

    settings = get_settings()
    worker_id = args.worker_id or settings.voice_worker_id
    asyncio.run(
        run_voice_worker_runner(
            settings=settings,
            worker_id=worker_id,
            once=args.once,
            max_seconds_per_job=args.max_seconds_per_job,
        )
    )


if __name__ == "__main__":
    main()
