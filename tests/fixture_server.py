"""Serves the dashboard with FAKE product data, for looking at the UI only.

This is a development aid, not part of the app. It runs the real matcher,
cache and history code on hand-written product lists in place of a live
crawl, so the dashboard can be viewed and tested without a browser crawl,
an LLM key, or network access. Nothing it shows is real Blinkit or Zepto
data. Queries to try: "dove soap", "atta", "milk"; "partial" simulates one
platform failing; "nothing" returns no products.

    python tests/fixture_server.py 8765
"""

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import support  # noqa: F401

from price_compare.cache import TTLCache
from price_compare.matcher import match_products
from price_compare.service import ComparisonService
from price_compare.storage import HistoryStore

WEB_DIR = support.SRC_DIR / "price_compare" / "web"
DELAY_SECONDS = float(os.environ.get("FIXTURE_DELAY", "3"))


def p(brand, simple, name, price, mrp, qty):
    return {"brand": brand, "simple_name": simple, "name": name, "price": price, "mrp": mrp, "quantity": qty}


FIXTURES = {
    "dove soap": (
        [
            p("Dove", "soap bar, cream beauty", "Dove Cream Beauty Bathing Bar", 62, 70, "100 g"),
            p("Dove", "soap bar, cream beauty", "Dove Cream Beauty Bathing Bar (Pack of 3)", 175, 210, "3 x 100 g"),
            p("Dove", "soap bar, deeply nourishing", "Dove Deeply Nourishing Beauty Bar", 58, 65, "100 g"),
            p("Dove", "soap bar, go fresh cucumber", "Dove Go Fresh Cucumber & Green Tea Bar", 60, 66, "100 g"),
            p("Dove", "hair shampoo, intense repair", "Dove Intense Repair Shampoo", 190, 225, "340 ml"),
            p("Dove", "body wash, deeply nourishing", "Dove Deeply Nourishing Body Wash", 255, 310, "250 ml"),
            p("Lux", "soap bar, soft touch", "Lux Soft Touch Soap Bar", 38, 42, "100 g"),
            p("Dove", "soap bar, cream beauty", "Dove Cream Beauty Bathing Bar Family Pack", 130, 150, "2 x 125 g"),
        ],
        [
            p("Dove", "soap bar, cream beauty", "Dove Cream Beauty Bar Soap", 59, 70, "100 g"),
            p("Dove", "soap bar, cream beauty", "Dove Cream Beauty Bar Soap Pack of 3", 180, 210, "3 x 100 g"),
            p("Dove", "soap bar, deeply nourishing", "Dove Deeply Nourishing Moisturising Bar", 58, 65, "100 g"),
            p("Dove", "soap bar, go fresh cucumber", "Dove Cucumber & Green Tea Soap Bar", 57, 66, "100 g"),
            p("Dove", "hair shampoo, intense repair", "Dove Intense Repair Hair Shampoo", 205, 225, "340 ml"),
            p("Dove", "body wash, deeply nourishing", "Dove Deeply Nourishing Body Wash", 249, 310, "250 ml"),
            p("Lux", "soap bar, soft touch", "Lux Soft Touch Soap", 38, 42, "100 g"),
            p("Dove", "soap bar, white", "Dove White Beauty Bar", 55, 62, "100 g"),
        ],
    ),
    "atta": (
        [
            p("Aashirvaad", "whole wheat flour", "Aashirvaad Select Sharbati Atta", 285, 315, "5 kg"),
            p("Aashirvaad", "whole wheat flour", "Aashirvaad Superior MP Atta", 62, 68, "1 kg"),
            p("Pillsbury", "whole wheat flour", "Pillsbury Chakki Fresh Atta", 275, 300, "5 kg"),
            p("Fortune", "whole wheat flour", "Fortune Chakki Fresh Atta", 268, 290, "5 kg"),
        ],
        [
            p("Aashirvaad", "whole wheat flour", "Aashirvaad Select Atta", 279, 315, "5 kg"),
            p("Aashirvaad", "whole wheat flour", "Aashirvaad Superior MP Whole Wheat Atta", 64, 68, "1 kg"),
            p("Pillsbury", "whole wheat flour", "Pillsbury Chakki Fresh Atta", 279, 300, "5 kg"),
            p("Annapurna", "whole wheat flour", "Annapurna Multigrain Atta", 295, 320, "5 kg"),
        ],
    ),
    "milk": (
        [
            p("Amul", "milk, toned", "Amul Taaza Toned Milk", 27, 27, "500 ml"),
            p("Amul", "milk, full cream", "Amul Gold Full Cream Milk", 35, 35, "500 ml"),
            p("Mother Dairy", "milk, toned", "Mother Dairy Toned Milk", 28, 28, "500 ml"),
        ],
        [
            p("Amul", "milk, toned", "Amul Taaza Homogenised Toned Milk", 27, 27, "500 ml"),
            p("Amul", "milk, full cream", "Amul Gold Milk", 34, 35, "500 ml"),
            p("Nandini", "milk, toned", "Nandini Toned Milk", 25, 25, "500 ml"),
        ],
    ),
}


def tag(items, platform):
    return [{**item, "platform": platform} for item in items]


async def fake_runner(search_query, max_items):
    await asyncio.sleep(DELAY_SECONDS)
    key = search_query.lower()
    errors = None
    if key == "nothing":
        blinkit, zepto = [], []
    elif key == "partial":
        blinkit, zepto = [], tag(FIXTURES["milk"][1], "zepto")
        errors = {"blinkit": "Blinkit crawl failed: page did not load in time"}
    else:
        blinkit, zepto = FIXTURES.get(key, FIXTURES["milk"])
        blinkit, zepto = tag(blinkit[:max_items], "blinkit"), tag(zepto[:max_items], "zepto")

    matched, blinkit_only, zepto_only = match_products(blinkit, zepto)
    return {
        "query": search_query,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "counts": {"blinkit_total": len(blinkit), "zepto_total": len(zepto), "matched": len(matched)},
        "matched": matched,
        "blinkit_only": blinkit_only,
        "zepto_only": zepto_only,
        "errors": errors,
    }


class Handler(BaseHTTPRequestHandler):
    service: ComparisonService = None
    loop: asyncio.AbstractEventLoop = None

    def log_message(self, *args):
        pass

    def _json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        params = {k: v[0] for k, v in parse_qs(url.query).items()}
        path = url.path
        try:
            if path == "/api/config":
                self._json({"llm_provider": "gemini/gemini-3.5-flash-lite", "input_format": "markdown",
                            "cache_ttl_seconds": 600, "default_max_items": 20, "blinkit_location": "Pune"})
            elif path == "/api/stats":
                self._json(self.service.stats())
            elif path == "/api/history":
                self._json(self.service.history(int(params.get("limit", 20))))
            elif path.startswith("/api/history/"):
                result = self.service.get_search(int(path.rsplit("/", 1)[1]))
                self._json(result, 200) if result else self._json({"detail": "No saved search with that id"}, 404)
            elif path == "/api/compare":
                future = asyncio.run_coroutine_threadsafe(
                    self.service.compare(params["query"], int(params.get("max_items", 20)), params.get("refresh") == "true"),
                    self.loop,
                )
                self._json(future.result())
            else:
                self._static(path)
        except Exception as exc:  # fixture server: surface everything
            self._json({"detail": str(exc)}, 500)

    def _static(self, path):
        relative = "index.html" if path in ("", "/") else path.lstrip("/")
        file_path = (WEB_DIR / relative).resolve()
        if WEB_DIR not in file_path.parents or not file_path.is_file():
            self.send_response(404)
            self.end_headers()
            return
        content_types = {".html": "text/html", ".css": "text/css", ".js": "text/javascript"}
        body = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_types.get(file_path.suffix, "application/octet-stream") + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main(port):
    import threading

    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    tmp = tempfile.mkdtemp()
    Handler.service = ComparisonService(fake_runner, TTLCache(600), HistoryStore(str(Path(tmp) / "fixture.db")))
    Handler.loop = loop
    print(f"Fixture server (FAKE data) on http://127.0.0.1:{port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8765)
