# @file: backend/core/config.py
# @exports: Settings, get_settings
# @depends_on: .env.example
# @purpose: Validated environment configuration with project-relative dotenv loading.
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / '.env', env_file_encoding='utf-8', extra='forbid',
    )
    postgres_db: str = 'trafficsense'
    postgres_user: str = 'trafficsense'
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_password: SecretStr = SecretStr('trafficsense_dev')
    database_url: SecretStr = SecretStr(
        'postgresql+asyncpg://trafficsense:trafficsense_dev@localhost:5432/trafficsense'
    )
    redis_url: SecretStr = SecretStr('redis://localhost:6379/0')
    osrm_base_url: str = 'https://router.project-osrm.org'
    inference_mode: Literal['hybrid_setfit_plus_cloud_api', 'local_setfit', 'calibrated_teacher',
                            'cloud_finetuned_api', 'zero_shot_api'] = 'hybrid_setfit_plus_cloud_api'
    llm_api_key: SecretStr = SecretStr('')
    llm_base_url: str = 'https://api.openai.com/v1'
    llm_model_id: str = ''
    setfit_model_path: Path = ROOT / 'models/setfit_gatekeeper'
    nlp_batch_size: int = Field(default=50, ge=1, le=200)
    nlp_max_batches: int = Field(default=10, ge=1, le=100)
    nlp_max_attempts: int = Field(default=3, ge=1, le=5)
    finetuned_llm_model_id: str = ''
    tomtom_api_key: SecretStr = SecretStr('')
    here_api_key: SecretStr = SecretStr('')
    geoapify_api_key: SecretStr = SecretStr('')
    ors_api_key: SecretStr = SecretStr('')
    serper_api_key: SecretStr = SecretStr('')
    tavily_api_key: SecretStr = SecretStr('')
    youtube_api_key: SecretStr = SecretStr('')
    newsdata_api_key: SecretStr = SecretStr('')
    currents_api_key: SecretStr = SecretStr('')
    gnews_api_key: SecretStr = SecretStr('')
    data_gov_in_api_key: SecretStr = SecretStr('')
    x_auth_token: SecretStr = SecretStr('')
    x_ct0: SecretStr = SecretStr('')
    x_bearer_token: SecretStr = SecretStr('')
    x_search_query_id: str = ''
    data_gov_resource_ids: list[str] = Field(default_factory=list)
    telegram_channels: list[str] = Field(default_factory=lambda: ['abpganga', 'News18India'])
    direct_scrapers_enabled: bool = True
    dbscan_eps_meters: float = Field(default=500, gt=0)
    dbscan_window_minutes: int = Field(default=60, gt=0)
    macro_sentry_poll_hours: float = Field(default=2, gt=0)
    nominatim_user_agent: str = 'TrafficSense-GEU-MP2026CSE126'
    sse_heartbeat_seconds: float = Field(default=15, gt=0, le=60)
    ingestion_enabled: bool = True
    ingestion_source_mode: Literal['live', 'fixtures', 'fallback'] = 'live'
    ingestion_max_lookback_hours: float = Field(default=48, gt=0, le=720)
    ingestion_catchup_threshold_hours: float = Field(default=2, ge=0)
    ingestion_weather_enabled: bool = False
    advisory_feed_urls: list[str] = Field(default_factory=list)
    nominatim_enabled: bool = False
    nominatim_base_url: str = 'https://nominatim.openstreetmap.org'
    geocode_min_confidence: float = Field(default=0.65, ge=0, le=1)
    spatial_batch_size: int = Field(default=50, ge=1, le=200)
    spatial_max_batches: int = Field(default=10, ge=1, le=100)
    spatial_max_attempts: int = Field(default=3, ge=1, le=5)
    analytics_batch_size: int = Field(default=50, ge=1, le=200)
    analytics_max_batches: int = Field(default=10, ge=1, le=100)
    analytics_max_attempts: int = Field(default=3, ge=1, le=5)
    analytics_include_seed: bool = False
    osrm_enabled: bool = False
    osrm_timeout_seconds: float = Field(default=3, gt=0, le=15)
    osrm_max_retries: int = Field(default=1, ge=0, le=2)
    scout_topology_cache_ttl_seconds: int = Field(default=300, ge=1, le=3600)
    scout_live_traffic_enabled: bool = False
    scout_fallback_speed_kmh: float = Field(default=40, gt=0, le=130)
    assistant_cloud_slots_enabled: bool = False
    assistant_timeout_seconds: float = Field(default=5, gt=0, le=15)

    @field_validator('telegram_channels', 'data_gov_resource_ids')
    @classmethod
    def safe_identifiers(cls, values):
        import re
        if any(not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value) for value in values):
            raise ValueError('Channel/resource identifiers must be plain URL path identifiers')
        return values

    @field_validator('x_search_query_id')
    @classmethod
    def safe_operation(cls, value):
        import re
        if value and not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value):
            raise ValueError('X operation ID must be a plain identifier')
        return value

    @field_validator('database_url')
    @classmethod
    def asyncpg_url(cls, value: SecretStr) -> SecretStr:
        if make_url(value.get_secret_value()).drivername != 'postgresql+asyncpg':
            raise ValueError('DATABASE_URL must use postgresql+asyncpg')
        return value

    @field_validator('redis_url')
    @classmethod
    def redis_scheme(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().startswith(('redis://', 'rediss://')):
            raise ValueError('REDIS_URL must use redis:// or rediss://')
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
