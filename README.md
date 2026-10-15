# Price Matcher: Blinkit vs Zepto

Search a grocery product. The backend opens Blinkit and Zepto in a browser
(crawl4ai), a language model reads each results page, the same products are
paired across the two apps, and a web dashboard shows which one is cheaper.

## Run it

```bash
pip install -e .
crawl4ai-setup                 # one time: downloads the browser

export GEMINI_API_KEY=...      # or GROQ_API_KEY / OPENAI_API_KEY, see .env.example

uvicorn price_compare.api:app --port 8000
```

Open <http://localhost:8000>. API documentation is at `/docs`.

Run the tests (no browser, API key or internet needed):

```bash
python -m unittest discover -s tests -v
```

## What the dashboard has

| Section | What it shows |
| --- | --- |
| Dashboard | Totals across every saved search, who wins overall, the biggest price gap, recent searches |
| Product search | The search box, with a live timer while both apps are read |
| Scraped data | Every listing the extraction returned, filterable, with CSV download |
| Comparison | A verdict, a price bar per matched product, per-100 g/ml prices, and a basket total for the products you tick |
| Product matching | The four checks a pair must pass, each pair's scores, and for every unpaired listing the closest candidate and why it was rejected |
| About project | The pipeline, the live settings, and the limits to know about |

## How matching works

A Blinkit and a Zepto listing are the same product only if they pass, in
order: same brand, pack size within 10%, similar standard name (`simple_name`,
which the extraction model writes in plain grocery terms), and a price gap
under 3x. Price is deliberately not part of the match score, because using it
would hide the pairs with the biggest gaps, which are the ones worth finding.
Pairs are assigned best score first, so the result doesn't depend on the
order of the lists. See `src/price_compare/matcher.py`.

## API

| Endpoint | Returns |
| --- | --- |
| `GET /api/compare?query=dove+soap&max_items=20&refresh=false` | Matched pairs, platform-only listings (with their closest candidate), counts, errors |
| `GET /api/history`, `GET /api/history/{id}` | Saved searches; reopen one without crawling again |
| `GET /api/stats` | Totals shown on the dashboard |
| `GET /api/config` | Model, input format, delivery area, cache time |
| `GET /api/health` | Liveness check |

A search where a platform failed, or nothing was found, is shown but never
cached or saved, so searching again retries instead of replaying the failure.

## Layout

```
src/price_compare/
  config.py       per-platform crawl config, model and key (from the environment)
  models.py       Product schema, extraction prompt, clean_products()
  scraper.py      one crawl4ai crawl per platform
  matcher.py      the matching rules (pure functions)
  comparison.py   crawl both concurrently, match, shape the result
  cache.py        in-memory TTL cache
  storage.py      SQLite search history
  service.py      cache + history rules, one crawl at a time
  api.py          FastAPI routes + serves the dashboard
  web/            index.html, styles.css, app.js (no build step)
tests/            matcher, service and storage tests; fixture_server.py (UI preview)
```

## Changed in this version

- Fixed the startup crash: `config.py` referenced an undefined `GEMINI_API_KEY`. The key is now read from the environment.
- Matching rewritten: a name gate (size and price can no longer outvote a different name), pack-size gate, no price signal, best-first pairing, explanations for unpaired listings.
- Failed or empty searches are no longer cached.
- Zepto: after typing the query, the script now presses Enter and submits the form, and waits longer for results.
- Extraction: one model call per page (`apply_chunking=False`) and the page is sent as markdown by default (`PRICE_COMPARE_INPUT_FORMAT=html` to switch back).
- New: SQLite history, stats, the web dashboard, tests.

## What was and wasn't verified

Verified: the matcher, cache, history and service rules are covered by 26
unit tests that pass. The dashboard was driven in a real browser (search,
loading, results, empty, one-platform-failed and server-down states, basket
totals, filtering, CSV download, reload, phone width) against
`tests/fixture_server.py`, which runs the real matcher on **made-up** product
lists. Nothing in that preview is real Blinkit or Zepto data.

Not verified, because the environment this was built in could not reach the
live sites or install `fastapi`, `rapidfuzz` or `crawl4ai`:

- A live crawl of Blinkit or Zepto, including whether Zepto's search runs after the Enter/submit step, and whether either site blocks automated browsers.
- `api.py` itself running under FastAPI (the logic it calls is tested; the route wiring is not).
- Matching thresholds against real `rapidfuzz` scores. The tests used a stand-in built on `difflib` (`tests/support.py`), so scores can differ by a few points. Run the tests with `rapidfuzz` installed (they use it automatically) and adjust `NAME_MIN_SCORE` / `MATCH_THRESHOLD` in `matcher.py` if real data pairs too much or too little.
- The model name `gemini/gemini-3.5-flash-lite`. Check it against litellm's current list.

## Using it responsibly

Both sites' robots.txt disallow automated access to these pages. crawl4ai
does not enforce robots.txt, so this will run, but treat it as a personal,
low-volume tool. Prices depend on delivery location: Blinkit uses the cookies
in `config.py` (Pune); to change the area, copy the `gr_1_*` cookies from your
own Blinkit session.

## Debugging a crawl

```bash
price-compare-cli --query "dove soap" --headful
```

`--headful` shows the browser. Set `PRICE_COMPARE_LOG_LEVEL=DEBUG` for every
match decision and cache hit.
