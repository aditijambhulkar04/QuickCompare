import asyncio
import os
import tempfile
import unittest

import support  # noqa: F401

from price_compare.cache import TTLCache
from price_compare.models import clean_products
from price_compare.service import ComparisonService
from price_compare.storage import HistoryStore


def make_result(query, blinkit_total=2, zepto_total=2, errors=None, pairs=None):
    pairs = pairs if pairs is not None else [
        {"blinkit": {"name": "Dove Bar"}, "zepto": {"name": "Dove Bar"}, "cheaper_platform": "zepto",
         "price_diff": 4.0, "price_diff_percent": 7.7, "match_confidence": 95},
        {"blinkit": {"name": "Lux Bar"}, "zepto": {"name": "Lux Bar"}, "cheaper_platform": "blinkit",
         "price_diff": 2.0, "price_diff_percent": 4.0, "match_confidence": 90},
    ]
    return {
        "query": query,
        "fetched_at": "2026-10-13T09:00:00+00:00",
        "counts": {"blinkit_total": blinkit_total, "zepto_total": zepto_total, "matched": len(pairs)},
        "matched": pairs,
        "blinkit_only": [],
        "zepto_only": [],
        "errors": errors,
    }


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.calls = []
        self.next_result = None

        async def runner(search_query, max_items):
            self.calls.append((search_query, max_items))
            await asyncio.sleep(0.01)
            return self.next_result or make_result(search_query)

        self.history = HistoryStore(os.path.join(self.tmp.name, "h.db"))
        self.service = ComparisonService(runner, TTLCache(600), self.history)

    async def test_repeat_search_is_served_from_cache(self):
        first = await self.service.compare("dove soap", 20)
        second = await self.service.compare("Dove   Soap", 20)

        self.assertEqual(len(self.calls), 1)
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(first["search_id"], second["search_id"])

    async def test_refresh_crawls_again(self):
        await self.service.compare("milk", 20)
        await self.service.compare("milk", 20, refresh=True)
        self.assertEqual(len(self.calls), 2)

    async def test_failed_platform_is_not_cached_or_saved(self):
        self.next_result = make_result("atta", blinkit_total=0, errors={"blinkit": "Blinkit crawl failed"})
        first = await self.service.compare("atta", 20)
        await self.service.compare("atta", 20)

        self.assertEqual(len(self.calls), 2)
        self.assertIsNone(first["search_id"])
        self.assertEqual(self.history.list_recent(), [])

    async def test_empty_result_is_not_cached(self):
        self.next_result = make_result("zzzz", 0, 0, pairs=[])
        await self.service.compare("zzzz", 20)
        await self.service.compare("zzzz", 20)
        self.assertEqual(len(self.calls), 2)

    async def test_concurrent_identical_searches_crawl_once(self):
        results = await asyncio.gather(*(self.service.compare("curd", 20) for _ in range(3)))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(sorted(r["cached"] for r in results), [False, True, True])

    async def test_history_and_stats(self):
        await self.service.compare("dove soap", 20)
        self.next_result = make_result("milk", pairs=[
            {"blinkit": {"name": "Amul Milk"}, "zepto": {"name": "Amul Milk"}, "cheaper_platform": "tie",
             "price_diff": 0.0, "price_diff_percent": 0.0, "match_confidence": 99},
        ])
        await self.service.compare("milk", 20)

        recent = self.service.history()
        self.assertEqual([r["query"] for r in recent], ["milk", "dove soap"])
        self.assertEqual(recent[1]["wins"], {"blinkit": 1, "zepto": 1, "ties": 0})
        self.assertEqual(recent[1]["total_saving"], 6.0)

        stats = self.service.stats()
        self.assertEqual(stats["searches"], 2)
        self.assertEqual(stats["matched_total"], 3)
        self.assertEqual(stats["wins"], {"blinkit": 1, "zepto": 1, "ties": 1})
        self.assertEqual(stats["biggest_saving"]["amount"], 4.0)
        self.assertEqual(stats["biggest_saving"]["query"], "dove soap")
        self.assertAlmostEqual(stats["average_saving_percent"], 3.9, places=1)

    async def test_reopen_saved_search(self):
        saved = await self.service.compare("dove soap", 20)
        reopened = self.service.get_search(saved["search_id"])
        self.assertEqual(reopened["query"], "dove soap")
        self.assertEqual(len(reopened["matched"]), 2)
        self.assertIsNone(self.service.get_search(9999))

    async def test_stats_when_empty(self):
        stats = self.service.stats()
        self.assertEqual(stats["searches"], 0)
        self.assertIsNone(stats["biggest_saving"])
        self.assertEqual(stats["average_saving_percent"], 0)


class CleanProductsTests(unittest.TestCase):
    def test_drops_unusable_and_duplicate_entries(self):
        raw = [
            {"name": "Dove Bar", "price": "52", "quantity": "100 g", "error": False},
            {"name": "Dove Bar", "price": 52, "quantity": "100 g"},        # duplicate
            {"name": "", "price": 10},                                      # no name
            {"name": "No price"},                                           # no price
            {"name": "Free thing", "price": 0},                             # zero price
            {"name": "Broken", "price": 5, "error": True},                  # LLM error marker
            "not a dict",
        ]
        cleaned = clean_products(raw, "blinkit")
        self.assertEqual(len(cleaned), 1)
        self.assertEqual(cleaned[0]["price"], 52.0)
        self.assertEqual(cleaned[0]["platform"], "blinkit")
        self.assertNotIn("error", cleaned[0])


if __name__ == "__main__":
    unittest.main()
