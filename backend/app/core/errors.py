from __future__ import annotations


class BackendError(Exception):
    error_code = "backend.error"

    def __init__(self, message: str, *, detail: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}


class ProviderNotConfiguredError(BackendError):
    error_code = "provider.not_configured"


class ProviderInvocationError(BackendError):
    error_code = "provider.invocation_failed"


class UnsafeEventPayloadError(BackendError):
    error_code = "event.unsafe_payload"


class SessionNotFoundError(BackendError):
    error_code = "session.not_found"


class RuntimeConfigurationError(BackendError):
    error_code = "runtime.configuration_invalid"
