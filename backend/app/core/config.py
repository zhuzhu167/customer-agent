from functools import lru_cache
from typing import List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "customer-agent-backend"
    app_env: str = "local"
    api_prefix: str = "/api/v1"
    backend_cors_origins: List[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    database_url: str = "postgresql+psycopg://customer_agent:customer_agent@localhost:5432/customer_agent"
    redis_url: str = "redis://localhost:6379/0"

    livekit_url: str = "ws://localhost:7880"
    public_livekit_url: Optional[str] = None
    livekit_api_key: str = "devkey"
    livekit_api_secret: str = "secret"
    livekit_token_ttl_seconds: int = 3600

    default_asr_provider: str = "tencent_cloud"
    default_tts_provider: str = "tencent_cloud"
    default_llm_provider: str = "alibaba_cloud"

    tencent_cloud_region: str = "ap-guangzhou"
    tencent_cloud_asr_model: str = "16k_zh"
    tencent_cloud_asr_endpoint: str = "asr.tencentcloudapi.com"
    tencent_cloud_realtime_asr_host: str = "asr.cloud.tencent.com"
    tencent_cloud_realtime_asr_engine_model_type: str = "16k_zh"
    tencent_cloud_realtime_asr_voice_format: int = 1
    tencent_cloud_realtime_asr_filter_dirty: int = 0
    tencent_cloud_realtime_asr_filter_modal: int = 0
    tencent_cloud_realtime_asr_filter_punc: int = 0
    tencent_cloud_realtime_asr_convert_num_mode: int = 1
    tencent_cloud_tts_endpoint: str = "tts.tencentcloudapi.com"
    tencent_cloud_tts_voice: str = "101001"
    tencent_cloud_tts_codec: str = "wav"
    tencent_cloud_tts_sample_rate: int = 16000
    tencent_cloud_secret_ref: str = "env:TENCENT_CLOUD_SECRET_ID,TENCENT_CLOUD_SECRET_KEY"
    tencent_cloud_secret_file: str = "docs/密钥/腾讯云密钥.csv"
    tencent_cloud_app_id: Optional[str] = None

    alibaba_cloud_region: str = "cn-hangzhou"
    alibaba_cloud_llm_model: str = "qwen-plus"
    alibaba_cloud_endpoint: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    alibaba_cloud_secret_ref: str = "env:ALIBABA_CLOUD_API_KEY"
    alibaba_cloud_secret_file: str = "docs/密钥/阿里云密钥.md"

    session_persist_audio: bool = False
    reply_preparing_visibility: str = "demo_debug_only"
    allow_tts_text_only_fallback: bool = False
    auto_start_voice_worker: bool = True
    turn_low_confidence_threshold: float = 0.72
    voice_worker_launch_mode: str = "in_process"
    voice_worker_id: str = "local-worker-1"
    voice_worker_poll_interval_seconds: float = 1.0
    voice_worker_session_poll_seconds: float = 1.0
    voice_worker_job_lease_seconds: int = 120
    voice_worker_heartbeat_interval_seconds: float = 15.0
    voice_worker_max_attempts: int = 3
    turn_silence_timeout_seconds: float = 12.0
    turn_silence_check_interval_seconds: float = 1.0
    turn_barge_in_rms_threshold: float = 0.018
    turn_barge_in_min_voice_frames: int = 2
    turn_interruption_recovery_timeout_seconds: float = 2.5

    @field_validator("backend_cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value):
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("voice_worker_launch_mode")
    @classmethod
    def validate_voice_worker_launch_mode(cls, value):
        normalized = str(value).strip().lower()
        if normalized not in {"in_process", "external"}:
            raise ValueError("VOICE_WORKER_LAUNCH_MODE must be in_process or external")
        return normalized


@lru_cache
def get_settings() -> Settings:
    return Settings()
