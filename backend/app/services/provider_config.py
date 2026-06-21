from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.core.errors import ProviderNotConfiguredError
from backend.app.db.models import ProviderConfig
from backend.app.providers.alibaba import AlibabaLLMProvider
from backend.app.providers.base import ASRProvider, LLMProvider, ProviderConfigDTO, TTSProvider
from backend.app.providers.mock import MockASRProvider, MockLLMProvider, MockTTSProvider
from backend.app.providers.tencent import TencentCloudASRProvider, TencentCloudTTSProvider


def default_provider_configs(settings: Settings) -> list[ProviderConfigDTO]:
    configs: list[ProviderConfigDTO] = [
        ProviderConfigDTO(
            provider_type="asr",
            provider_name="mock",
            model="mock-asr-local",
            region="local",
            secret_ref=None,
            enabled=settings.default_asr_provider == "mock",
        ),
        ProviderConfigDTO(
            provider_type="tts",
            provider_name="mock",
            model="mock-tts-local",
            region="local",
            secret_ref=None,
            enabled=settings.default_tts_provider == "mock",
        ),
        ProviderConfigDTO(
            provider_type="llm",
            provider_name="mock",
            model="mock-customer-agent",
            region="local",
            secret_ref=None,
            enabled=settings.default_llm_provider == "mock",
        ),
        ProviderConfigDTO(
            provider_type="asr",
            provider_name="tencent_cloud",
            model=settings.tencent_cloud_asr_model,
            region=settings.tencent_cloud_region,
            secret_ref=settings.tencent_cloud_secret_ref,
            enabled=settings.default_asr_provider == "tencent_cloud",
            options={
                "endpoint": settings.tencent_cloud_asr_endpoint,
                "realtime_host": settings.tencent_cloud_realtime_asr_host,
                "realtime_engine_model_type": settings.tencent_cloud_realtime_asr_engine_model_type,
                "realtime_voice_format": settings.tencent_cloud_realtime_asr_voice_format,
                "realtime_filter_dirty": settings.tencent_cloud_realtime_asr_filter_dirty,
                "realtime_filter_modal": settings.tencent_cloud_realtime_asr_filter_modal,
                "realtime_filter_punc": settings.tencent_cloud_realtime_asr_filter_punc,
                "realtime_convert_num_mode": settings.tencent_cloud_realtime_asr_convert_num_mode,
                "secret_file": settings.tencent_cloud_secret_file,
                "app_id": settings.tencent_cloud_app_id,
            },
        ),
        ProviderConfigDTO(
            provider_type="tts",
            provider_name="tencent_cloud",
            model=settings.tencent_cloud_tts_voice,
            region=settings.tencent_cloud_region,
            secret_ref=settings.tencent_cloud_secret_ref,
            enabled=settings.default_tts_provider == "tencent_cloud",
            options={
                "endpoint": settings.tencent_cloud_tts_endpoint,
                "codec": settings.tencent_cloud_tts_codec,
                "sample_rate": settings.tencent_cloud_tts_sample_rate,
                "secret_file": settings.tencent_cloud_secret_file,
                "app_id": settings.tencent_cloud_app_id,
            },
        ),
        ProviderConfigDTO(
            provider_type="llm",
            provider_name="alibaba_cloud",
            model=settings.alibaba_cloud_llm_model,
            region=settings.alibaba_cloud_region,
            secret_ref=settings.alibaba_cloud_secret_ref,
            enabled=settings.default_llm_provider == "alibaba_cloud",
            options={
                "endpoint": settings.alibaba_cloud_endpoint,
                "secret_file": settings.alibaba_cloud_secret_file,
            },
        ),
    ]
    if settings.app_env == "test":
        return configs
    return [config for config in configs if config.provider_name != "mock"]


class ProviderConfigService:
    def __init__(self, db: Session, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    def list_provider_configs(self) -> list[ProviderConfigDTO]:
        rows = self.db.scalars(select(ProviderConfig)).all()
        if rows:
            defaults = {
                (item.provider_type, item.provider_name): item for item in default_provider_configs(self.settings)
            }
            configs: list[ProviderConfigDTO] = []
            for row in rows:
                default = defaults.get((row.provider_type, row.provider_name))
                configs.append(
                    ProviderConfigDTO(
                    provider_type=row.provider_type,
                    provider_name=row.provider_name,
                    model=row.model,
                    region=row.region,
                    secret_ref=row.secret_ref,
                    enabled=row.enabled,
                    options=default.options if default else None,
                )
                )
            return configs
        return default_provider_configs(self.settings)

    def get_active_config(self, provider_type: str) -> ProviderConfigDTO:
        active = [item for item in self.list_provider_configs() if item.provider_type == provider_type and item.enabled]
        if active:
            return active[0]

        fallback_name = {
            "asr": self.settings.default_asr_provider,
            "tts": self.settings.default_tts_provider,
            "llm": self.settings.default_llm_provider,
        }[provider_type]
        fallback = next(
            (
                item
                for item in default_provider_configs(self.settings)
                if item.provider_type == provider_type and item.provider_name == fallback_name
            ),
            None,
        )
        if fallback is None:
            raise ProviderNotConfiguredError(
                "Provider 未配置为当前环境允许的供应商",
                detail={
                    "provider_type": provider_type,
                    "provider_name": fallback_name,
                    "app_env": self.settings.app_env,
                },
            )
        return fallback

    def build_asr_provider(self) -> ASRProvider:
        config = self.get_active_config("asr")
        if config.provider_name == "tencent_cloud":
            return TencentCloudASRProvider(config)
        if config.provider_name == "mock" and self.settings.app_env == "test":
            return MockASRProvider(config)
        raise ProviderNotConfiguredError(
            "ASR Provider 未配置为生产可用供应商",
            detail={"provider_type": "asr", "provider_name": config.provider_name},
        )

    def build_tts_provider(self) -> TTSProvider:
        config = self.get_active_config("tts")
        if config.provider_name == "tencent_cloud":
            return TencentCloudTTSProvider(config)
        if config.provider_name == "mock" and self.settings.app_env == "test":
            return MockTTSProvider(config)
        raise ProviderNotConfiguredError(
            "TTS Provider 未配置为生产可用供应商",
            detail={"provider_type": "tts", "provider_name": config.provider_name},
        )

    def build_llm_provider(self) -> LLMProvider:
        config = self.get_active_config("llm")
        if config.provider_name == "alibaba_cloud":
            return AlibabaLLMProvider(config)
        if config.provider_name == "mock" and self.settings.app_env == "test":
            return MockLLMProvider(config)
        raise ProviderNotConfiguredError(
            "LLM Provider 未配置为生产可用供应商",
            detail={"provider_type": "llm", "provider_name": config.provider_name},
        )
