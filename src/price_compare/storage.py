"""SQLite history of past comparisons.

Every successful search is stored whole (as JSON) next to a few summary
columns, so the dashboard can list past searches, reopen one without
crawling again, and show totals across all searches. Plain `sqlite3` from
the standard library: one file, no server, nothing to install.
"""

import json
import sqlite3
from contextlib import contextmanager
from typing import Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS searches (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    query          TEXT NOT NULL,
    fetched_at     TEXT NOT NULL,
    blinkit_total  INTEGER NOT NULL,
    zepto_total    INTEGER NOT NULL,
    matched        INTEGER NOT NULL,
    blinkit_wins   INTEGER NOT NULL,
    zepto_wins     INTEGER NOT NULL,
    ties           INTEGER NOT NULL,
    total_saving   REAL NOT NULL,
    result_json    TEXT NOT NULL
)
"""


def summarize(result: dict) -> dict:
    """The numbers the dashboard shows for one comparison."""
    matched_pairs = result.get("matched", [])
    wins = {"blinkit": 0, "zepto": 0, "ties": 0}
    for pair in matched_pairs:
        cheaper = pair.get("cheaper_platform")
        wins[cheaper if cheaper in ("blinkit", "zepto") else "ties"] += 1
    return {
        "wins": wins,
        "total_saving": round(sum(pair.get("price_diff") or 0 for pair in matched_pairs), 2),
    }


class HistoryStore:
    def __init__(self, database_path: str):
        self._database_path = database_path
        with self._connect() as connection:
            connection.execute(_SCHEMA)

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        try:
            with connection:  # commits on success, rolls back on error
                yield connection
        finally:
            connection.close()

    def save(self, result: dict) -> int:
        summary = summarize(result)
        counts = result["counts"]
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO searches (query, fetched_at, blinkit_total, zepto_total, matched, "
                "blinkit_wins, zepto_wins, ties, total_saving, result_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    result["query"], result["fetched_at"], counts["blinkit_total"], counts["zepto_total"],
                    counts["matched"], summary["wins"]["blinkit"], summary["wins"]["zepto"],
                    summary["wins"]["ties"], summary["total_saving"], json.dumps(result, ensure_ascii=False),
                ),
            )
            return cursor.lastrowid

    def list_recent(self, limit: int = 20) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, query, fetched_at, blinkit_total, zepto_total, matched, blinkit_wins, "
                "zepto_wins, ties, total_saving FROM searches ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "query": row["query"],
                "fetched_at": row["fetched_at"],
                "counts": {
                    "blinkit_total": row["blinkit_total"],
                    "zepto_total": row["zepto_total"],
                    "matched": row["matched"],
                },
                "wins": {"blinkit": row["blinkit_wins"], "zepto": row["zepto_wins"], "ties": row["ties"]},
                "total_saving": row["total_saving"],
            }
            for row in rows
        ]

    def get(self, search_id: int) -> Optional[dict]:
        with self._connect() as connection:
            row = connection.execute("SELECT result_json FROM searches WHERE id = ?", (search_id,)).fetchone()
        if row is None:
            return None
        result = json.loads(row["result_json"])
        result["search_id"] = search_id
        return result

    def stats(self) -> dict:
        with self._connect() as connection:
            totals = connection.execute(
                "SELECT COUNT(*) AS searches, COALESCE(SUM(blinkit_total + zepto_total), 0) AS scraped, "
                "COALESCE(SUM(matched), 0) AS matched, COALESCE(SUM(blinkit_wins), 0) AS blinkit_wins, "
                "COALESCE(SUM(zepto_wins), 0) AS zepto_wins, COALESCE(SUM(ties), 0) AS ties FROM searches"
            ).fetchone()
            result_rows = connection.execute("SELECT id, result_json FROM searches").fetchall()

        # Average and biggest saving are computed from the stored pairs, so
        # they reflect individual products, not whole searches.
        saving_percents, biggest = [], None
        for row in result_rows:
            result = json.loads(row["result_json"])
            for pair in result.get("matched", []):
                saving_percents.append(pair.get("price_diff_percent") or 0)
                amount = pair.get("price_diff") or 0
                if biggest is None or amount > biggest["amount"]:
                    biggest = {
                        "amount": amount,
                        "percent": pair.get("price_diff_percent") or 0,
                        "query": result["query"],
                        "name": (pair.get("blinkit") or {}).get("name"),
                        "cheaper_platform": pair.get("cheaper_platform"),
                        "search_id": row["id"],
                    }

        return {
            "searches": totals["searches"],
            "products_scraped": totals["scraped"],
            "matched_total": totals["matched"],
            "wins": {"blinkit": totals["blinkit_wins"], "zepto": totals["zepto_wins"], "ties": totals["ties"]},
            "average_saving_percent": round(sum(saving_percents) / len(saving_percents), 1) if saving_percents else 0,
            "biggest_saving": biggest,
        }
