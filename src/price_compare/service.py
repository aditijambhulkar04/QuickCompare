"""The application layer between the HTTP routes and the crawl.

Framework-free on purpose: it takes the crawl function as an argument, so
the cache and history rules below can be tested without a browser, an LLM
key or a web framework.
"""

import asyncio
import logging
from typing import Awaitable, Callable, Optional

from .cache import TTLCache
from .storage import HistoryStore

logger = logging.getLogger(__name__)

ComparisonRunner = Callable[..., Awaitable[dict]]


def _cache_key(search_query: str, max_items: int) -> str:
    return f"{' '.join(search_query.lower().split())}|{max_items}"


class ComparisonService:
    def __init__(self, runner: ComparisonRunner, cache: TTLCache, history: HistoryStore):
        self._runner = runner
        self._cache = cache
        self._history = history
        self._crawl_lock = asyncio.Lock()

    async def compare(self, search_query: str, max_items: int, refresh: bool = False) -> dict:
        key = _cache_key(search_query, max_items)

        if not refresh:
            cached = self._cache.get(key)
            if cached is not None:
                return {**cached, "cached": True}

        # One crawl at a time: each crawl opens two browsers, and a second
        # identical request that waits here finds the first one's result in
        # the cache instead of crawling again.
        async with self._crawl_lock:
            if not refresh:
                cached = self._cache.get(key)
                if cached is not None:
                    return {**cached, "cached": True}
            return await self._crawl_and_store(search_query, max_items, key)

    async def _crawl_and_store(self, search_query: str, max_items: int, key: str) -> dict:
        result = await self._runner(search_query=search_query, max_items=max_items)

        # A result with a failed platform, or with nothing from either, is
        # shown to the person but never cached or saved: repeating the
        # search should try again, not replay the failure for 10 minutes.
        has_errors = bool(result.get("errors"))
        has_products = result["counts"]["blinkit_total"] + result["counts"]["zepto_total"] > 0
        if has_errors or not has_products:
            logger.info("not caching or saving | query=%r errors=%s products=%s", search_query, has_errors, has_products)
            return {**result, "cached": False, "search_id": None}

        search_id = self._history.save(result)
        stored = {**result, "search_id": search_id}
        self._cache.set(key, stored)
        return {**stored, "cached": False}

    def history(self, limit: int = 20) -> list[dict]:
        return self._history.list_recent(limit)

    def get_search(self, search_id: int) -> Optional[dict]:
        return self._history.get(search_id)

    def stats(self) -> dict:
        return self._history.stats()
