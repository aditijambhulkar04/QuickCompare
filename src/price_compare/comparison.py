"""Orchestrates a full Blinkit-vs-Zepto comparison: crawl both concurrently,
match equivalent products, and shape the combined result.

Kept separate from api.py and cli.py on purpose, so both entry points share
one implementation instead of duplicating the crawl-then-match sequence.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone

from .config import BLINKIT_CONFIG, ZEPTO_CONFIG
from .matcher import match_products
from .scraper import PlatformCrawlError, crawl_platform

logger = logging.getLogger(__name__)


def _unpack_platform_result(result: object, platform_key: str, errors_by_platform: dict[str, str]) -> list[dict]:
    if isinstance(result, PlatformCrawlError):
        errors_by_platform[platform_key] = str(result)
        return []
    if isinstance(result, BaseException):
        errors_by_platform[platform_key] = f"unexpected error: {result}"
        logger.exception("[%s] unexpected error during crawl", platform_key, exc_info=result)
        return []
    return result


async def run_comparison(
    search_query: str,
    max_items: int = 20,
    headless: bool = True,
    verbose: bool = False,
) -> dict:
    logger.info("Comparison started | query=%r max_items=%d headless=%s", search_query, max_items, headless)
    started_at = time.perf_counter()

    blinkit_result, zepto_result = await asyncio.gather(
        crawl_platform(BLINKIT_CONFIG, search_query, headless, max_items, verbose),
        crawl_platform(ZEPTO_CONFIG, search_query, headless, max_items, verbose),
        return_exceptions=True,
    )

    errors_by_platform: dict[str, str] = {}
    blinkit_products = _unpack_platform_result(blinkit_result, BLINKIT_CONFIG.key, errors_by_platform)
    zepto_products = _unpack_platform_result(zepto_result, ZEPTO_CONFIG.key, errors_by_platform)

    matched_pairs, blinkit_exclusive, zepto_exclusive = match_products(blinkit_products, zepto_products)
    elapsed_seconds = time.perf_counter() - started_at

    logger.info(
        "Comparison finished in %.1fs | query=%r blinkit_total=%d zepto_total=%d matched=%d "
        "blinkit_only=%d zepto_only=%d errors=%s",
        elapsed_seconds,
        search_query,
        len(blinkit_products),
        len(zepto_products),
        len(matched_pairs),
        len(blinkit_exclusive),
        len(zepto_exclusive),
        errors_by_platform or "none",
    )

    return {
        "query": search_query,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "counts": {
            "blinkit_total": len(blinkit_products),
            "zepto_total": len(zepto_products),
            "matched": len(matched_pairs),
        },
        "matched": matched_pairs,
        "blinkit_only": blinkit_exclusive,
        "zepto_only": zepto_exclusive,
        "errors": errors_by_platform or None,
    }
