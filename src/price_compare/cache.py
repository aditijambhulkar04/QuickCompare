"""Minimal in-memory TTL cache.

Deliberately simple: a plain dict with timestamps, no external cache
dependency, nothing persisted across restarts. Its only job is to stop an
identical search moments apart from triggering a second full crawl — both
sites already told us via robots.txt they'd rather not be crawled at all,
so re-hitting them for a search someone just ran is worth avoiding, and it
also makes the app feel instant on a repeat search. A single-process,
in-memory cache is the right amount of machinery for a personal tool; a
shared/external cache would only matter if this ran as more than one
process, which it doesn't.
"""

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


class TTLCache:
    def __init__(self, ttl_seconds: int = 600):
        self._ttl_seconds = ttl_seconds
        self._store: dict[str, tuple[float, dict]] = {}

    def get(self, key: str) -> Optional[dict]:
        entry = self._store.get(key)
        if entry is None:
            logger.debug("cache miss | key=%r", key)
            return None
        stored_at, value = entry
        if time.monotonic() - stored_at > self._ttl_seconds:
            logger.debug("cache expired | key=%r", key)
            del self._store[key]
            return None
        logger.info("cache hit | key=%r", key)
        return value

    def set(self, key: str, value: dict) -> None:
        self._store[key] = (time.monotonic(), value)
        logger.debug("cache stored | key=%r ttl=%ds", key, self._ttl_seconds)
