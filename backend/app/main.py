from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.app.api.routes import router
from backend.app.core.config import get_settings
from backend.app.core.errors import BackendError
from backend.app.core.runtime_guard import validate_runtime_configuration
from backend.app.schemas import HealthResponse
from backend.app.services.voice_worker_manager import VoiceWorkerManager


settings = get_settings()
validate_runtime_configuration(settings)

app = FastAPI(title=settings.app_name)
app.state.voice_worker_manager = VoiceWorkerManager(settings)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.backend_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(BackendError)
async def backend_error_handler(request: Request, exc: BackendError) -> JSONResponse:
    status_code = status.HTTP_400_BAD_REQUEST
    if exc.error_code == "session.not_found":
        status_code = status.HTTP_404_NOT_FOUND
    return JSONResponse(
        status_code=status_code,
        content={"error_code": exc.error_code, "message": exc.message, "detail": exc.detail},
    )


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", app=settings.app_name, environment=settings.app_env)


@app.on_event("shutdown")
async def shutdown_voice_workers() -> None:
    await app.state.voice_worker_manager.shutdown()


app.include_router(router, prefix=settings.api_prefix)
