from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.config import Settings
from backend.app.db.session import get_session_local
from backend.app.services.event_store import EventStore
from backend.app.worker.livekit_voice_worker import LiveKitVoiceWorker


@dataclass
class ManagedVoiceWorker:
    session_id: UUID
    task: asyncio.Task


class VoiceWorkerManager:
    def __init__(self, settings: Settings, session_local_factory: sessionmaker[Session] | None = None) -> None:
        self.settings = settings
        self.session_local_factory = session_local_factory
        self.workers: dict[UUID, ManagedVoiceWorker] = {}

    def start(self, *, session_id: UUID) -> None:
        if not self.settings.auto_start_voice_worker:
            return
        existing = self.workers.get(session_id)
        if existing and not existing.task.done():
            return

        task = asyncio.create_task(self._run_worker(session_id=session_id))
        self.workers[session_id] = ManagedVoiceWorker(session_id=session_id, task=task)
        task.add_done_callback(lambda completed: self._on_done(session_id, completed))

    async def stop(self, *, session_id: UUID) -> None:
        managed = self.workers.pop(session_id, None)
        if managed is None:
            return
        if managed.task.done():
            return
        managed.task.cancel()
        await asyncio.gather(managed.task, return_exceptions=True)

    async def shutdown(self) -> None:
        session_ids = list(self.workers)
        await asyncio.gather(*(self.stop(session_id=session_id) for session_id in session_ids))

    async def _run_worker(self, *, session_id: UUID) -> None:
        session_local = self.session_local_factory or get_session_local()
        with session_local() as db:
            EventStore(db).create_event(
                session_id=session_id,
                event_type="voice_worker.autostart",
                payload={"mode": "in_process"},
            )
            db.commit()
            worker = LiveKitVoiceWorker(db=db, settings=self.settings, session_id=session_id)
            await worker.run()

    def _on_done(self, session_id: UUID, completed: asyncio.Task) -> None:
        self.workers.pop(session_id, None)
        if completed.cancelled():
            return
        error = completed.exception()
        if error is None:
            return

        session_local = self.session_local_factory or get_session_local()
        with session_local() as db:
            EventStore(db).create_event(
                session_id=session_id,
                event_type="voice_worker.autostart_failed",
                payload={"error_code": "voice_worker.autostart_failed", "message": str(error)},
            )
            db.commit()
