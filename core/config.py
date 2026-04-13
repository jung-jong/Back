from functools import lru_cache
from pathlib import Path
import ssl

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Custom-TA API"
    app_env: str = "local"
    debug: bool = Field(default=True, alias="APP_DEBUG")
    cors_allow_origins: str = Field(
        default=(
            "http://localhost:5173,"
            "http://127.0.0.1:5173,"
            "http://172.20.10.7:5173,"
            "https://custom-ta.vercel.app"
        ),
        alias="CORS_ALLOW_ORIGINS",
    )

    database_url: str = Field(..., alias="DATABASE_URL")
    database_ssl: bool = Field(default=True, alias="DATABASE_SSL")
    database_echo: bool = Field(default=False, alias="DATABASE_ECHO")

    upload_dir: Path = Field(default=Path("static/uploads"), alias="UPLOAD_DIR")
    storage_provider: str = Field(default="local", alias="STORAGE_PROVIDER")
    aws_access_key_id: str | None = Field(default=None, alias="AWS_ACCESS_KEY_ID")
    aws_secret_access_key: str | None = Field(default=None, alias="AWS_SECRET_ACCESS_KEY")
    aws_region: str = Field(default="ap-northeast-2", alias="AWS_REGION")
    s3_bucket_name: str | None = Field(default=None, alias="S3_BUCKET_NAME")
    s3_prefix: str = Field(default="uploads", alias="S3_PREFIX")
    s3_presigned_url_expires: int = Field(default=3600, alias="S3_PRESIGNED_URL_EXPIRES")

    jwt_secret_key: str = Field(..., alias="JWT_SECRET_KEY")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    access_token_expire_minutes: int = Field(
        default=1440,
        alias="ACCESS_TOKEN_EXPIRE_MINUTES",
    )

    ai_provider: str = Field(default="ollama", alias="AI_PROVIDER")
    ai_max_output_tokens: int = Field(default=1024, alias="AI_MAX_OUTPUT_TOKENS")

    ollama_base_url: str = Field(
        default="http://127.0.0.1:11434",
        alias="OLLAMA_BASE_URL",
    )
    ollama_model: str = Field(default="llama3.2:latest", alias="OLLAMA_MODEL")
    ollama_embedding_model: str = Field(
        default="nomic-embed-text",
        alias="OLLAMA_EMBEDDING_MODEL",
    )

    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")
    openai_embedding_model: str = Field(
        default="text-embedding-3-small",
        alias="OPENAI_EMBEDDING_MODEL",
    )
    gemini_api_key: str | None = Field(default=None, alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-2.5-flash-lite", alias="GEMINI_MODEL")
    gemini_embedding_model: str = Field(
        default="gemini-embedding-001",
        alias="GEMINI_EMBEDDING_MODEL",
    )
    gemini_embedding_output_dimensionality: int = Field(
        default=768,
        alias="GEMINI_EMBEDDING_OUTPUT_DIMENSIONALITY",
    )
    embedding_provider: str = Field(default="ollama", alias="EMBEDDING_PROVIDER")
    local_rag_enabled: bool = Field(default=True, alias="LOCAL_RAG_ENABLED")
    rag_chunk_size: int = Field(default=1200, alias="RAG_CHUNK_SIZE")
    rag_chunk_overlap: int = Field(default=200, alias="RAG_CHUNK_OVERLAP")
    rag_top_k: int = Field(default=5, alias="RAG_TOP_K")

    weekly_intervention_scheduler_enabled: bool = Field(
        default=False,
        alias="WEEKLY_INTERVENTION_SCHEDULER_ENABLED",
    )
    weekly_intervention_interval_seconds: int = Field(
        default=3600,
        alias="WEEKLY_INTERVENTION_INTERVAL_SECONDS",
    )
    weekly_intervention_run_on_startup: bool = Field(
        default=False,
        alias="WEEKLY_INTERVENTION_RUN_ON_STARTUP",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def async_database_url(self) -> str:
        if self.database_url.startswith("mysql+asyncmy://"):
            return self.database_url.replace("mysql+asyncmy://", "mysql+aiomysql://", 1)
        if self.database_url.startswith("mysql://"):
            return self.database_url.replace("mysql://", "mysql+aiomysql://", 1)
        return self.database_url

    @property
    def sqlalchemy_connect_args(self) -> dict:
        if not self.database_ssl:
            return {}
        return {"ssl": ssl.create_default_context()}

    @property
    def cors_origins(self) -> list[str]:
        return [
            origin.strip().rstrip("/")
            for origin in self.cors_allow_origins.split(",")
            if origin.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
