"""Crawls one platform's search page with crawl4ai and returns up to
`max_items` products as plain dicts.

Two scrolling paths, chosen per platform:

- No search_input_selector (Blinkit): crawl4ai's own built-in page-scan
  (scan_full_page + scroll_delay + max_scroll_steps), no custom JS at all.
- search_input_selector set (Zepto): landing on the URL alone shows a
  generic default listing, not real search results, so the query has to
  be typed into the on-page search box first. This can't use
  scan_full_page for the scrolling part -- crawl4ai always runs
  scan_full_page *before* any js_code, so if scrolling were left to it,
  it would scroll the pre-search page, not the results. Typing and
  scrolling are therefore both done in one script, in the right order.

Either way, limiting to `max_items` products happens in the extraction
prompt itself (see models.build_extraction_instruction), not by page
inspection -- a handful of scrolls is already more than enough content for
a 20-item extraction.

wait_until="load" rather than "networkidle": some sites keep a background
connection alive indefinitely (analytics, polling), so networkidle can
hang until the page's own timeout kills the crawl.
"""

import json
import logging
import time

from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CacheMode,
    CrawlerRunConfig,
    LLMConfig,
    LLMExtractionStrategy,
)

from .config import EXTRACTION_INPUT_FORMAT, EXTRACTION_LLM_API_KEY, EXTRACTION_LLM_PROVIDER, PlatformConfig
from .models import Product, build_extraction_instruction, clean_products

logger = logging.getLogger(__name__)

MAX_SCROLL_STEPS = 5

# Types __QUERY__ into the search box, waits for its (debounced) results to
# load, then scrolls a bounded number of times, stopping early if a step
# doesn't reveal a taller page. The native-setter dance is required for
# React-controlled inputs: setting .value directly doesn't trigger React's
# onChange, so the page would never see the typed query.
_TYPE_QUERY_THEN_SCROLL_JS_TEMPLATE = """
const searchInput = document.querySelector(__SELECTOR__);
if (searchInput) {
    const setNativeValue = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
    setNativeValue.call(searchInput, __QUERY__);
    searchInput.dispatchEvent(new Event('input', { bubbles: true }));
    searchInput.dispatchEvent(new Event('change', { bubbles: true }));
    searchInput.focus();
    // Typing alone may only open a suggestions list; pressing Enter is what
    // actually runs the search.
    for (const eventType of ['keydown', 'keypress', 'keyup']) {
        searchInput.dispatchEvent(new KeyboardEvent(eventType, {
            key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true,
        }));
    }
    if (searchInput.form) {
        searchInput.form.requestSubmit();
    }
}

await new Promise((resolve) => setTimeout(resolve, __SEARCH_SETTLE_MS__));

let previousPageHeight = 0;
for (let step = 0; step < __MAX_SCROLL_STEPS__; step++) {
    window.scrollTo(0, document.body.scrollHeight);
    await new Promise((resolve) => setTimeout(resolve, __SCROLL_DELAY_MS__));
    const currentPageHeight = document.body.scrollHeight;
    if (currentPageHeight === previousPageHeight) break;
    previousPageHeight = currentPageHeight;
}
"""


class PlatformCrawlError(Exception):
    """Raised when a platform's page can't be crawled, or its content can't be parsed."""


def _build_search_interaction_js(platform: PlatformConfig, search_query: str) -> str:
    return (
        _TYPE_QUERY_THEN_SCROLL_JS_TEMPLATE.replace("__SELECTOR__", json.dumps(platform.search_input_selector))
        .replace("__QUERY__", json.dumps(search_query))
        .replace("__SEARCH_SETTLE_MS__", "4000")  # time for the submitted search to load
        .replace("__MAX_SCROLL_STEPS__", str(MAX_SCROLL_STEPS))
        .replace("__SCROLL_DELAY_MS__", str(int(platform.scroll_delay_seconds * 1000)))
    )


def _build_browser_config(platform: PlatformConfig, headless: bool) -> BrowserConfig:
    return BrowserConfig(
        headless=headless,
        browser_type="undetected",  # crawl4ai's stealth Chromium (patchright-based)
        viewport_width=1280,
        viewport_height=2000,
        cookies=platform.build_browser_cookies(),
    )


def _build_extraction_strategy(max_items: int, verbose: bool) -> LLMExtractionStrategy:
    return LLMExtractionStrategy(
        llm_config=LLMConfig(provider=EXTRACTION_LLM_PROVIDER, api_token=EXTRACTION_LLM_API_KEY),
        schema=Product.model_json_schema(),
        extraction_type="schema",
        instruction=build_extraction_instruction(max_items),
        input_format=EXTRACTION_INPUT_FORMAT,
        # crawl4ai splits long input into chunks and makes one LLM call per
        # chunk, which multiplies cost and rate-limit hits and lets
        # max_items apply per chunk instead of per page. Send the whole page
        # in one call instead.
        apply_chunking=False,
        verbose=verbose,
    )


def _build_run_config(platform: PlatformConfig, search_query: str, max_items: int, verbose: bool) -> CrawlerRunConfig:
    needs_search_interaction = platform.search_input_selector is not None
    return CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,  # prices change constantly -- never serve a stale cache
        wait_until="load",
        # See the module docstring: these two are mutually exclusive, not
        # combined, because of the fixed order crawl4ai runs them in.
        scan_full_page=platform.requires_scroll_to_load_more_results and not needs_search_interaction,
        scroll_delay=platform.scroll_delay_seconds,
        max_scroll_steps=MAX_SCROLL_STEPS,
        js_code=[_build_search_interaction_js(platform, search_query)] if needs_search_interaction else None,
        extraction_strategy=_build_extraction_strategy(max_items, verbose),
        page_timeout=platform.page_load_timeout_ms,
        verbose=verbose,
    )


def _parse_extracted_products(raw_extracted_content: str, platform: PlatformConfig) -> list[dict]:
    try:
        parsed_content = json.loads(raw_extracted_content)
    except (TypeError, json.JSONDecodeError) as exc:
        raise PlatformCrawlError(f"{platform.label} extraction returned unparseable content") from exc

    raw_products = parsed_content if isinstance(parsed_content, list) else [parsed_content]
    return clean_products(raw_products, platform.key)


async def crawl_platform(
    platform: PlatformConfig,
    search_query: str,
    headless: bool,
    max_items: int,
    verbose: bool = False,
) -> list[dict]:
    search_url = platform.build_search_url(search_query)
    browser_config = _build_browser_config(platform, headless)
    run_config = _build_run_config(platform, search_query, max_items, verbose)

    logger.info("[%s] sending request | url=%s max_items=%d", platform.label, search_url, max_items)

    started_at = time.perf_counter()
    async with AsyncWebCrawler(config=browser_config) as crawler:
        crawl_result = await crawler.arun(url=search_url, config=run_config)
    elapsed_seconds = time.perf_counter() - started_at

    if not crawl_result.success:
        logger.warning(
            "[%s] crawl failed after %.1fs | status=%s error=%s",
            platform.label, elapsed_seconds, crawl_result.status_code, crawl_result.error_message,
        )
        raise PlatformCrawlError(f"{platform.label} crawl failed: {crawl_result.error_message}")

    products = _parse_extracted_products(crawl_result.extracted_content, platform)[:max_items]

    logger.info(
        "[%s] loaded in %.1fs | status=%s | extracted %d product(s)",
        platform.label, elapsed_seconds, crawl_result.status_code, len(products),
    )

    return products
