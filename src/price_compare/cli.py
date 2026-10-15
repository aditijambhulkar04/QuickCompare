"""Thin CLI for local debugging -- mainly for --headful runs, since the API
server (api.py) always crawls headless. Not the primary interface; the
frontend talks to the API, not this.
"""

import argparse
import asyncio
import json
import logging
from pathlib import Path

from .comparison import run_comparison
from .logging_config import configure_logging

logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Debug a Blinkit/Zepto comparison crawl outside the API server")
    parser.add_argument("--query", default="dove soap")
    parser.add_argument("--max-items", type=int, default=20)
    parser.add_argument("--headful", action="store_true", help="Show the browser -- recommended, this is a debugging tool")
    parser.add_argument("--out", default="comparison.json")
    return parser.parse_args()


def main() -> None:
    configure_logging()
    args = _parse_args()

    comparison_result = asyncio.run(
        run_comparison(
            search_query=args.query,
            max_items=args.max_items,
            headless=not args.headful,
            verbose=args.headful,
        )
    )

    output_path = Path(args.out)
    output_path.write_text(json.dumps(comparison_result, indent=2, ensure_ascii=False))
    logger.info("Wrote %s", output_path)


if __name__ == "__main__":
    main()
