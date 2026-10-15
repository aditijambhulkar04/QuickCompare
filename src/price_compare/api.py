"""FastAPI server: the JSON API plus the web dashboard.

Run with:
    uvicorn price_compare.api:app --reload --port 8000

Then open http://localhost:8000 for the dashboard, or /docs for the
interactive API documentation.
"""

import logging
import os
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .cache import TTLCache
from .comparison import run_comparison
from .config import (
    BLINKIT_DELIVERY_LOCATION,
    CACHE_TTL_SECONDS,
    DATABASE_PATH,
    DEFAULT_MAX_ITEMS,
    EXTRACTION_INPUT_FORMAT,
    EXTRACTION_LLM_PROVIDER,
)
from .logging_config import configure_logging
from .service import ComparisonService
from .storage import HistoryStore

configure_logging()
logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent / "web"

app = FastAPI(title="Price Compare API", version="0.2.0")

# The dashboard is served by this same app, so it needs no CORS at all.
# These origins are only for other front ends running on your own machine
# (for example the Expo web build); set PRICE_COMPARE_CORS_ORIGINS to a
# comma-separated list to change them.
_default_origins = "http://localhost:8000,http://127.0.0.1:8000,http://localhost:8081,http://localhost:19006"
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("PRICE_COMPARE_CORS_ORIGINS", _default_origins).split(","),
    allow_methods=["GET"],
    allow_headers=["*"],
)

service = ComparisonService(
    runner=run_comparison,
    cache=TTLCache(ttl_seconds=CACHE_TTL_SECONDS),
    history=HistoryStore(DATABASE_PATH),
)


@app.middleware("http")
async def log_every_request(request: Request, call_next):
    """Transport-level logging: every request this server receives, and how
    it was answered. Business-level detail (what the crawl actually did)
    is logged separately in comparison.py/scraper.py.
    """
    started_at = time.perf_counter()
    logger.info("%s %s | query_params=%s", request.method, request.url.path, dict(request.query_params))

    response = await call_next(request)

    elapsed_ms = (time.perf_counter() - started_at) * 1000
    logger.info("%s %s -> %d (%.0fms)", request.method, request.url.path, response.status_code, elapsed_ms)
    return response


@app.get("/api/health")
def health_check() -> dict:
    return {"status": "ok"}


@app.get("/api/config")
def get_config() -> dict:
    """Settings the About page shows, so the page never hard-codes them."""
    return {
        "llm_provider": EXTRACTION_LLM_PROVIDER,
        "input_format": EXTRACTION_INPUT_FORMAT,
        "cache_ttl_seconds": CACHE_TTL_SECONDS,
        "default_max_items": DEFAULT_MAX_ITEMS,
        "blinkit_location": BLINKIT_DELIVERY_LOCATION["locality"],
    }


@app.get("/api/compare")
async def compare_prices(
    query: str = Query(..., min_length=1, max_length=80, description="Product search term, e.g. 'dove soap'"),
    max_items: int = Query(DEFAULT_MAX_ITEMS, ge=1, le=50),
    refresh: bool = Query(False, description="Skip the cache and crawl again"),
) -> dict:
    search_query = " ".join(query.split())
    if not search_query:
        raise HTTPException(status_code=422, detail="Search term is empty")
    try:
        return await service.compare(search_query=search_query, max_items=max_items, refresh=refresh)
    except Exception as exc:
        logger.exception("Unhandled error while comparing | query=%r max_items=%d", search_query, max_items)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/history")
def list_history(limit: int = Query(20, ge=1, le=100)) -> list[dict]:
    return service.history(limit)


@app.get("/api/history/{search_id}")
def get_history_item(search_id: int) -> dict:
    result = service.get_search(search_id)
    if result is None:
        raise HTTPException(status_code=404, detail="No saved search with that id")
    return result


@app.get("/api/stats")
def get_stats() -> dict:
    return service.stats()


# Mounted last so the /api routes above always win over the static files.
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
