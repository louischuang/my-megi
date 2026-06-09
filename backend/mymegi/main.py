from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from mymegi.config import get_settings
from mymegi.db import database


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    await database.connect(settings)
    yield
    await database.disconnect()


app = FastAPI(
    title="My Megi API",
    version="0.1.0",
    description="Local-first business card and relationship manager.",
    openapi_url="/openapi.json",
    docs_url="/docs",
    lifespan=lifespan,
)


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": "my-megi", "docs": "/docs"}


@app.get("/health")
async def health() -> dict[str, Any]:
    settings = get_settings()
    db_ok = False
    async with database.acquire() as connection:
        db_ok = bool(await connection.fetchval("select true"))

    return {
        "status": "ok" if db_ok else "degraded",
        "database": "ok" if db_ok else "unavailable",
        "environment": settings.app_env,
        "ocrEngine": settings.ocr_engine,
        "llmModel": settings.llm_model,
    }

