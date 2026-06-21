from __future__ import annotations

import os

from backend.app.core.config import Settings
from backend.app.core.errors import RuntimeConfigurationError


PRODUCTION_LIKE_ENVS = {"prod", "production", "staging"}
DEVELOPMENT_LIVEKIT_SECRETS = {"secret", "test-secret", "dev-secret"}
PLACEHOLDER_VALUES = {
    "change-me",
    "changeme",
    "replace-me",
    "replace_me",
    "todo",
    "your-key",
    "your-secret",
    "your-secret-key",
    "your_api_key",
    "your-api-key",
}


def is_production_like(settings: Settings) -> bool:
    return settings.app_env.strip().lower() in PRODUCTION_LIKE_ENVS


def _is_placeholder(value: str) -> bool:
    normalized = value.strip().lower()
    return (
        not normalized
        or normalized in PLACEHOLDER_VALUES
        or normalized.startswith("replace-")
        or normalized.startswith("your-")
        or normalized.startswith("<")
    )


def _env_names(secret_ref: str | None) -> list[str]:
    if not secret_ref or not secret_ref.startswith("env:"):
        return []
    return [item.strip() for item in secret_ref.removeprefix("env:").split(",") if item.strip()]


def _validate_env_secret_ref(
    *,
    field: str,
    secret_ref: str,
    required_count: int,
    violations: list[dict[str, str]],
) -> None:
    names = _env_names(secret_ref)
    if len(names) < required_count:
        violations.append({"field": field, "reason": "生产环境密钥环境变量数量不足"})
        return

    missing = [name for name in names[:required_count] if not os.getenv(name)]
    if missing:
        violations.append({"field": field, "reason": f"生产环境缺少密钥环境变量：{', '.join(missing)}"})
        return

    placeholders = [name for name in names[:required_count] if _is_placeholder(os.getenv(name, ""))]
    if placeholders:
        violations.append({"field": field, "reason": f"生产环境密钥环境变量仍是占位值：{', '.join(placeholders)}"})


def collect_runtime_configuration_violations(settings: Settings) -> list[dict[str, str]]:
    if not is_production_like(settings):
        return []

    violations: list[dict[str, str]] = []

    if settings.livekit_api_key == "devkey" or _is_placeholder(settings.livekit_api_key):
        violations.append({"field": "LIVEKIT_API_KEY", "reason": "生产环境不能使用开发默认 key"})
    if (
        settings.livekit_api_secret in DEVELOPMENT_LIVEKIT_SECRETS
        or len(settings.livekit_api_secret) < 32
        or _is_placeholder(settings.livekit_api_secret)
    ):
        violations.append({"field": "LIVEKIT_API_SECRET", "reason": "生产环境必须使用至少 32 字符的强 secret"})

    for field, value in {
        "DEFAULT_ASR_PROVIDER": settings.default_asr_provider,
        "DEFAULT_TTS_PROVIDER": settings.default_tts_provider,
        "DEFAULT_LLM_PROVIDER": settings.default_llm_provider,
    }.items():
        if value == "mock":
            violations.append({"field": field, "reason": "生产环境不允许使用 mock Provider"})

    if settings.tencent_cloud_secret_file:
        violations.append({"field": "TENCENT_CLOUD_SECRET_FILE", "reason": "生产环境不得依赖本地密钥文件"})
    if settings.alibaba_cloud_secret_file:
        violations.append({"field": "ALIBABA_CLOUD_SECRET_FILE", "reason": "生产环境不得依赖本地密钥文件"})
    if not settings.tencent_cloud_secret_ref.startswith("env:"):
        violations.append({"field": "TENCENT_CLOUD_SECRET_REF", "reason": "生产环境密钥必须来自环境变量或 Secret 注入"})
    else:
        _validate_env_secret_ref(
            field="TENCENT_CLOUD_SECRET_REF",
            secret_ref=settings.tencent_cloud_secret_ref,
            required_count=2,
            violations=violations,
        )
    if not settings.alibaba_cloud_secret_ref.startswith("env:"):
        violations.append({"field": "ALIBABA_CLOUD_SECRET_REF", "reason": "生产环境密钥必须来自环境变量或 Secret 注入"})
    else:
        _validate_env_secret_ref(
            field="ALIBABA_CLOUD_SECRET_REF",
            secret_ref=settings.alibaba_cloud_secret_ref,
            required_count=1,
            violations=violations,
        )

    if settings.session_persist_audio:
        violations.append({"field": "SESSION_PERSIST_AUDIO", "reason": "第一版生产路径不保存原始音频"})
    if settings.allow_tts_text_only_fallback:
        violations.append({"field": "ALLOW_TTS_TEXT_ONLY_FALLBACK", "reason": "生产环境不得静默降级为文本-only TTS"})

    return violations


def validate_runtime_configuration(settings: Settings) -> None:
    violations = collect_runtime_configuration_violations(settings)
    if violations:
        raise RuntimeConfigurationError(
            "生产运行配置不满足 Web Voice MVP 安全边界",
            detail={"app_env": settings.app_env, "violations": violations},
        )
