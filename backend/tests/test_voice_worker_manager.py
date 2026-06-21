from __future__ import annotations

import asyncio
from uuid import UUID

import pytest
from sqlalchemy.orm import sessionmaker

from backend.app.services.session_service import SessionService
from backend.app.services.voice_worker_manager import VoiceWorkerManager


@pytest.mark.asyncio
async def test_voice_worker_manager_starts_and_stops_in_process_worker(
    db_session,
    settings,
    monkeypatch,
) -> None:
    import backend.app.services.voice_worker_manager as manager_module

    started = asyncio.Event()
    stopped = asyncio.Event()

    class FakeLiveKitVoiceWorker:
        def __init__(self, *, db, settings, session_id):
            self.session_id = session_id

        async def run(self):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

    monkeypatch.setattr(manager_module, "LiveKitVoiceWorker", FakeLiveKitVoiceWorker)
    testing_session_local = sessionmaker(
        bind=db_session.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    manager = VoiceWorkerManager(
        settings.model_copy(update={"auto_start_voice_worker": True}),
        session_local_factory=testing_session_local,
    )
    session, _ = SessionService(db_session, settings).create_session(mode="debug", client_capabilities={})
    session_id = UUID(str(session.id))

    manager.start(session_id=session_id)
    await asyncio.wait_for(started.wait(), timeout=1)
    await manager.stop(session_id=session_id)
    await asyncio.wait_for(stopped.wait(), timeout=1)

    assert manager.workers == {}
