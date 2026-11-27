import logging

from fastapi import FastAPI

from app.config import get_settings

settings = get_settings()

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

app = FastAPI(
    title="AI Link Analysis API",
    description="Analyzes a website URL with an LLM and returns competitor, SEO, "
    "search query, services and audience insights.",
    version="0.1.0",
)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "env": settings.app_env}
