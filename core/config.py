from typing import Literal, Self

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PLACEHOLDERS = ("change-me", "changeme", "change_me", "secret", "password")


class Settings(BaseSettings):
    DATABASE_URL: str
    JWT_SECRET_KEY: str = Field(min_length=32)
    JWT_ALGORITHM: Literal["HS256", "HS384", "HS512"] = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=720, ge=1, le=60 * 24 * 30)
    TEACHER_INVITE_CODE: str = Field(min_length=8)

    DEFAULT_SERVICE_SECONDS: int = Field(default=300, ge=30, le=3600)
    MAX_QUEUE_SIZE: int = Field(default=300, ge=1)
    MAX_ENTRIES_PER_SESSION: int = Field(default=1000, ge=1)
    NAME_MAX_LENGTH: int = Field(default=60, ge=1, le=60)
    SESSION_AUTO_CLOSE_HOURS: float = Field(default=6.0, ge=0)
    SESSION_REAPER_INTERVAL_SECONDS: int = Field(default=60, ge=1)
    NOT_READY_MAX_PER_SESSION: int = Field(default=2, ge=0, le=10)
    PRESENCE_IP_FAILURES: int = Field(default=5, ge=1)
    PRESENCE_SESSION_FAILURES: int = Field(default=30, ge=1)
    PRESENCE_FAILURE_WINDOW_SECONDS: int = Field(default=60, ge=1)
    PRESENCE_LOCK_SECONDS: int = Field(default=120, ge=1)
    DATA_RETENTION_DAYS: int = Field(default=180, ge=0)
    RETENTION_INTERVAL_SECONDS: int = Field(default=3600, ge=60)
    BUDGET_RESERVE_PERCENT: int = Field(default=10, ge=0, le=50)
    BUDGET_MAX_DURATION_MINUTES: int = Field(default=720, ge=1)

    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_IP_PER_MINUTE: int = Field(default=6000, ge=0)
    RATE_LIMIT_JOIN_PER_IP_PER_MINUTE: int = Field(default=600, ge=0)
    RATE_LIMIT_AUTH_PER_IP_PER_MINUTE: int = Field(default=20, ge=0)
    RATE_LIMIT_TOKEN_PER_MINUTE: int = Field(default=120, ge=0)
    MAX_CONCURRENT_REQUESTS: int = Field(default=400, ge=0)

    SSE_ENABLED: bool = True
    SSE_MAX_PER_SESSION: int = Field(default=400, ge=0)
    SSE_HEARTBEAT_SECONDS: float = Field(default=15.0, gt=0)
    SSE_COALESCE_SECONDS: float = Field(default=0.3, ge=0)

    DB_ECHO: bool = False
    DB_POOL_SIZE: int = Field(default=30, ge=1)
    DB_MAX_OVERFLOW: int = Field(default=0, ge=0)
    DB_POOL_TIMEOUT_SECONDS: int = Field(default=10, ge=1)

    MAX_ASSIGNMENTS_PER_TEACHER: int = Field(default=50, ge=1)
    MAX_QUESTIONS_PER_ASSIGNMENT: int = Field(default=200, ge=1)
    MAX_CRITERIA_PER_ASSIGNMENT: int = Field(default=20, ge=1)
    MAX_SPACES_OWNED: int = Field(default=5, ge=1)
    MAX_SPACES_JOINED: int = Field(default=10, ge=1)
    MAX_SPACE_MEMBERS: int = Field(default=30, ge=2)
    MAX_ROSTER_PER_GROUP: int = Field(default=100, ge=1)
    MAX_ROSTER_GROUPS: int = Field(default=30, ge=1)
    MAX_SPACE_PUBLICATIONS: int = Field(default=100, ge=1)
    MAX_PUBLICATIONS_PER_AUTHOR: int = Field(default=20, ge=1)

    ANALYTICS_MIN_SUBMISSIONS: int = Field(default=5, ge=2)
    ANALYTICS_OUTLIER_FACTOR: float = Field(default=2.0, gt=1)

    AI_ENABLED: bool = False
    OLLAMA_URL: str = "http://host.docker.internal:11434"
    AI_MODEL: str = "qwen3.5:9b"
    AI_TIMEOUT_SECONDS: float = Field(default=300.0, gt=0)
    AI_MAX_SOURCE_CHARS: int = Field(default=12000, ge=100)
    AI_MAX_RESPONSE_CHARS: int = Field(default=20000, ge=100)
    AI_MAX_OUTPUT_TOKENS: int = Field(default=3000, ge=100)
    AI_MIN_QUESTIONS: int = Field(default=8, ge=1, le=40)
    AI_DRAFTS_PER_TEACHER_PER_HOUR: int = Field(default=5, ge=1)
    AI_PRACTICE_PER_IP_PER_HOUR: int = Field(default=30, ge=1)
    AI_PRACTICE_PER_SESSION_PER_HOUR: int = Field(default=300, ge=1)
    AI_PRACTICE_ANSWER_MAX_CHARS: int = Field(default=1500, ge=10)
    AI_PRACTICE_QUEUE_MAX: int = Field(default=10, ge=1)
    AI_PRACTICE_JOB_TTL_SECONDS: int = Field(default=600, ge=10)

    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    LOG_FORMAT: Literal["text", "json"] = "text"
    ACCESS_LOG: bool = True
    METRICS_TOKEN: str | None = None
    CORS_ORIGINS: list[str] = []

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("DATABASE_URL")
    @classmethod
    def database_url_must_use_asyncpg(cls, value: str) -> str:
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must start with postgresql+asyncpg://")
        return value

    @field_validator("JWT_SECRET_KEY", "TEACHER_INVITE_CODE")
    @classmethod
    def secrets_must_not_be_placeholders(cls, value: str) -> str:
        if value.strip().lower().startswith(PLACEHOLDERS):
            raise ValueError("replace the placeholder with a real secret")
        return value

    @model_validator(mode="after")
    def limits_must_be_consistent(self) -> Self:
        if self.MAX_ENTRIES_PER_SESSION < self.MAX_QUEUE_SIZE:
            raise ValueError("MAX_ENTRIES_PER_SESSION must be >= MAX_QUEUE_SIZE")
        return self


settings = Settings()  # type: ignore[call-arg]
