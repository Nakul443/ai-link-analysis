# Reads app, AI model, web fetch, cache, and CORS settings
# from environment variables or a .env file,
# using default values and hiding the API key when printed.

# Turns the comma-separated CORS text into a clean list of allowed URLs.

# Saves one settings object in memory so the app does not
# reload it every time (clear the cache in tests when changing settings).


from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # App
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 4000
    log_level: str = "INFO"

    # LLM
    llm_provider: str = "gemini"
    llm_api_key: str = Field(default="", repr=False)
    llm_model: str = ""
    llm_fallback_model: str = ""
    llm_timeout_seconds: int = 60
    llm_max_retries: int = 2
    llm_max_output_tokens: int = 6000
    llm_temperature: float = 0.2
    # Optional fallback. Empty provider/key = reuse the primary ones.
    llm_fallback_provider: str = ""
    llm_fallback_api_key: str = Field(default="", repr=False)

    # Fetching
    fetch_timeout_seconds: int = 15
    fetch_max_bytes: int = 2_000_000
    fetch_max_redirects: int = 3
    max_content_chars: int = 12_000
    extra_pages_max: int = 2
    user_agent: str = (
        "Mozilla/5.0 (compatible; AILinkAnalysisBot/1.0; "
        "+https://github.com/YOUR_USERNAME/ai-link-analysis)"
    )

    # Cache
    cache_ttl_seconds: int = 3600

    # CORS
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
