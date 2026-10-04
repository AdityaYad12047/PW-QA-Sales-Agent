"""
backend/app/config/settings.py
Centralised settings loaded from environment variables.
All external config goes here — never scattered through the codebase.
"""
from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env", "../../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── API Keys ──────────────────────────────────────────────────────────────
    sarvam_api_key: str = ""
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    openai_api_key: str = ""

    # ── Model & Provider Config ──────────────────────────────────────────────
    llm_provider: str = ""  # auto-detected or explicit: "gemini" | "claude" | "openai"
    claude_model: str = ""
    gemini_model: str = "gemini-2.5-flash"
    openai_model: str = "gpt-4o-mini"
    llm_price_version: str = "v1-2024-10"
    llm_cost_per_million_input_usd: float = 3.00
    llm_cost_per_million_output_usd: float = 15.00

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str = ""

    # ── CORS ──────────────────────────────────────────────────────────────────
    cors_origins: str = "http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173,http://127.0.0.1:3000,http://localhost:8000,http://127.0.0.1:8000"

    # ── Security & Webhooks ───────────────────────────────────────────────────
    demo_access_token: str = ""
    sarvam_webhook_token: str = ""
    public_base_url: str = ""

    # ── App ───────────────────────────────────────────────────────────────────
    log_level: str = "INFO"
    max_upload_mb: int = 200
    max_audio_minutes: int = 120
    allowed_audio_extensions: str = "mp3,wav,m4a,ogg,flac,aac"

    # ── Reviewer-time assumptions (user-editable via settings table) ──────────
    assumption_manual_review_minutes: int = 25
    assumption_ai_assisted_review_minutes: int = 8

    # ── Cache ─────────────────────────────────────────────────────────────────
    cache_dir: str = ".cache"

    @property
    def is_vercel_env(self) -> bool:
        import os
        return bool(os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV"))

    @property
    def default_db_path(self) -> Path:
        if self.is_vercel_env:
            data_dir = Path("/tmp/data")
        else:
            backend_dir = Path(__file__).resolve().parent.parent.parent
            data_dir = backend_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir / "pw_qa.db"

    @property
    def effective_database_url(self) -> str:
        if self.database_url and self.database_url != "sqlite:///./pw_qa.db":
            url = self.database_url.strip()
            if url.startswith("spostgresql://"):
                url = "postgresql://" + url[len("spostgresql://"):]
            if url.startswith("postgres://"):
                url = url.replace("postgres://", "postgresql+psycopg://", 1)
            elif url.startswith("postgresql://") and "+psycopg" not in url:
                url = url.replace("postgresql://", "postgresql+psycopg://", 1)
            return url
        return f"sqlite:///{self.default_db_path.resolve().as_posix()}"

    @property
    def cors_origins_list(self) -> list[str]:
        if not self.cors_origins:
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def cache_path(self) -> Path:
        if self.is_vercel_env:
            p = Path("/tmp") / self.cache_dir.lstrip("./")
        else:
            p = Path(self.cache_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p


    @property
    def uploads_dir(self) -> Path:
        if self.is_vercel_env:
            p = Path("/tmp/uploads")
        else:
            p = Path("uploads")
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def effective_max_upload_mb(self) -> int:
        # Vercel serverless request body limit is 4.5 MB.
        if self.is_vercel_env:
            return min(self.max_upload_mb, 4)
        return self.max_upload_mb

    @property
    def max_upload_bytes(self) -> int:
        return self.effective_max_upload_mb * 1024 * 1024

    @property
    def allowed_audio_extensions_list(self) -> list[str]:
        return [f".{ext.strip().lstrip('.').lower()}" for ext in self.allowed_audio_extensions.split(",") if ext.strip()]

    @property
    def effective_public_base_url(self) -> str:
        if self.public_base_url:
            base = self.public_base_url.strip()
            if not base.startswith("http://") and not base.startswith("https://"):
                base = f"https://{base}"
            return base.rstrip("/")
        import os
        vercel_prod = os.environ.get("VERCEL_PROJECT_PRODUCTION_URL")
        if vercel_prod:
            return f"https://{vercel_prod.strip().rstrip('/')}"
        vercel_url = os.environ.get("VERCEL_URL")
        if vercel_url:
            return f"https://{vercel_url.strip().rstrip('/')}"
        return ""


@lru_cache
def get_settings() -> Settings:
    """Return a cached singleton Settings instance."""
    return Settings()
