from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.config import get_settings
from backend.app.db.base import Base
from backend.app.services.provider_config import ProviderConfigService
from backend.app.services.provider_smoke import SMOKE_TTS_TEXT


async def build_tts_fixture(*, output: Path, text: str = SMOKE_TTS_TEXT) -> dict:
    settings = get_settings()
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    try:
        with session_local() as db:
            tts = ProviderConfigService(db, settings).build_tts_provider()
            result = await tts.synthesize(text)
            if result.content_type != "audio/wav":
                raise RuntimeError(f"TTS fixture requires audio/wav, got {result.content_type}")
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(result.audio_bytes)
            return {
                "output": str(output),
                "content_type": result.content_type,
                "audio_bytes": len(result.audio_bytes),
                "provider": result.provider_name,
                "text": text,
            }
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a Tencent Cloud TTS WAV fixture for browser smoke tests.")
    parser.add_argument("--output", required=True)
    parser.add_argument("--text", default=SMOKE_TTS_TEXT)
    args = parser.parse_args()
    result = asyncio.run(build_tts_fixture(output=Path(args.output), text=args.text))
    print(result)


if __name__ == "__main__":
    main()
