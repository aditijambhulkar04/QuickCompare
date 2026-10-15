/* Price Matcher dashboard.
   Plain JavaScript, no build step. Everything shown comes from the
   backend's /api endpoints; nothing is hard-coded sample data. */
(() => {
  'use strict';

  const PLATFORMS = { blinkit: 'Blinkit', zepto: 'Zepto' };
  const EXAMPLE_QUERIES = ['dove soap', 'atta', 'milk', 'amul butter'];
  const LAST_RESULT_KEY = 'price-matcher:last-search-id';
  const REQUEST_TIMEOUT_MS = 180000;

  const state = {
    config: null,
    stats: null,
    history: [],
    result: null,
    loadError: '',
    searchError: '',
    running: null, // { query, startedAt, controller, timerId }
    picked: new Set(), // indexes into result.matched that count toward the basket
    sortKey: 'saving',
    dataPlatform: 'all',
    dataText: '',
    searchText: '',
    maxItems: 20,
    refresh: false,
  };

  const view = document.getElementById('view');
  const basketBar = document.getElementById('basket-bar');
  const railCurrent = document.getElementById('rail-current');
  const live = document.getElementById('live');

  // ---------- small helpers ----------

  const escapeHtml = (value) =>
    String(value ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));

  const rupeeFormat = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2 });
  const rupees = (amount) => `₹${rupeeFormat.format(amount ?? 0)}`;
  const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

  function relativeTime(iso) {
    const minutes = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
    if (Number.isNaN(minutes)) return '';
    if (minutes < 1) return 'just now';
    if (minutes < 60) return `${plural(minutes, 'minute')} ago`;
    const hours = Math.round(minutes / 60);
    if (hours < 24) return `${plural(hours, 'hour')} ago`;
    return `${plural(Math.round(hours / 24), 'day')} ago`;
  }

  function safeStorage(action) {
    try { return action(window.localStorage); } catch { return null; }
  }

  async function api(path, { signal } = {}) {
    let response;
    try {
      response = await fetch(path, { signal });
    } catch (error) {
      if (error.name === 'AbortError') throw error;
      throw new Error("Can't reach the server. Start it with: uvicorn price_compare.api:app --port 8000");
    }
    if (!response.ok) {
      let detail = '';
      try { detail = (await response.json()).detail; } catch { /* body wasn't JSON */ }
      throw new Error(detail || `The server answered with an error (${response.status}).`);
    }
    return response.json();
  }

  const platformDot = (key) => `<span class="dot ${key}" aria-hidden="true"></span>`;

  // ---------- data selectors ----------

  function winCounts(pairs) {
    const counts = { blinkit: 0, zepto: 0, ties: 0 };
    for (const pair of pairs) {
      if (pair.cheaper_platform === 'blinkit') counts.blinkit += 1;
      else if (pair.cheaper_platform === 'zepto') counts.zepto += 1;
      else counts.ties += 1;
    }
    return counts;
  }

  function allProducts(result) {
    const rows = [];
    for (const pair of result.matched) {
      rows.push({ ...pair.blinkit, platform: 'blinkit', matched: true });
      rows.push({ ...pair.zepto, platform: 'zepto', matched: true });
    }
    for (const product of result.blinkit_only) rows.push({ ...product, platform: 'blinkit', matched: false });
    for (const product of result.zepto_only) rows.push({ ...product, platform: 'zepto', matched: false });
    return rows;
  }

  function basketTotals() {
    let blinkit = 0;
    let zepto = 0;
    for (const index of state.picked) {
      const pair = state.result.matched[index];
      blinkit += pair.blinkit.price;
      zepto += pair.zepto.price;
    }
    return { blinkit: Math.round(blinkit * 100) / 100, zepto: Math.round(zepto * 100) / 100 };
  }

  // ---------- shared fragments ----------

  function splitBar(wins) {
    const total = wins.blinkit + wins.zepto + wins.ties || 1;
    const pct = (n) => (n / total) * 100;
    return `
      <div class="split" role="img" aria-label="Blinkit cheaper on ${wins.blinkit}, Zepto cheaper on ${wins.zepto}, same price on ${wins.ties}">
        <i class="b" style="width:${pct(wins.blinkit)}%"></i><i class="t" style="width:${pct(wins.ties)}%"></i><i class="z" style="width:${pct(wins.zepto)}%"></i>
      </div>
      <div class="split-legend">
        <span>${platformDot('blinkit')}Blinkit cheaper on ${wins.blinkit}</span>
        <span>${platformDot('zepto')}Zepto cheaper on ${wins.zepto}</span>
        ${wins.ties ? `<span>Same price on ${wins.ties}</span>` : ''}
      </div>`;
  }

  function emptyResult(message) {
    return `
      <div class="card empty">
        <h2>No comparison yet</h2>
        <p>${escapeHtml(message)}</p>
        <a class="btn" href="#/search">Search a product</a>
      </div>`;
  }

  function platformErrors(errors) {
    if (!errors) return '';
    return Object.entries(errors).map(([platform, message]) => `
      <div class="notice error" role="alert">
        <strong>${escapeHtml(PLATFORMS[platform] || platform)} could not be read.</strong>
        <span> ${escapeHtml(message)}. Results below only include the other app. Search again to retry.</span>
      </div>`).join('');
  }

  // ---------- views ----------

  function dashboardView() {
    const stats = state.stats;
    let body;
    if (state.loadError) {
      body = `<div class="notice error" role="alert"><strong>Server not reachable.</strong> ${escapeHtml(state.loadError)}</div>`;
    } else if (!stats) {
      body = '<p class="muted">Loading</p>';
    } else if (stats.searches === 0) {
      body = `<div class="card empty"><h2>No searches yet</h2><p>Your first search will show up here, and so will every one after it.</p></div>`;
    } else {
      const biggest = stats.biggest_saving;
      body = `
        <section aria-label="Overall result">
          <h2>Across all your searches</h2>
          ${splitBar(stats.wins)}
        </section>
        <section class="section stat-grid" aria-label="Totals">
          <div class="card stat"><b>${stats.searches}</b><span>${stats.searches === 1 ? 'search' : 'searches'} run</span></div>
          <div class="card stat"><b>${stats.products_scraped}</b><span>product listings scraped</span></div>
          <div class="card stat"><b>${stats.matched_total}</b><span>same products found on both apps</span></div>
          <div class="card stat"><b>${stats.average_saving_percent}%</b><span>average price gap on a matched product</span></div>
        </section>
        ${biggest ? `
        <section class="section">
          <h2>Biggest gap found</h2>
          <button class="history-item" data-action="open" data-id="${biggest.search_id}">
            <span><strong>${escapeHtml(biggest.name)}</strong>
              <span class="meta"><br>${escapeHtml(PLATFORMS[biggest.cheaper_platform] || '')} was cheaper by ${rupees(biggest.amount)} (${biggest.percent}%) in the search for “${escapeHtml(biggest.query)}”</span></span>
          </button>
        </section>` : ''}
        <section class="section">
          <h2>Recent searches</h2>
          <div class="history">
            ${state.history.map((item) => `
              <button class="history-item" data-action="open" data-id="${item.id}">
                <span><strong>${escapeHtml(item.query)}</strong>
                  <span class="meta"><br>${plural(item.counts.matched, 'matched product')} · ${relativeTime(item.fetched_at)}</span></span>
                <span class="mini-split" aria-hidden="true">${miniSplit(item.wins)}</span>
              </button>`).join('')}
          </div>
        </section>`;
    }

    return `
      <header>
        <h1>Which app is cheaper for your groceries?</h1>
        <p class="lede">Price Matcher reads Blinkit and Zepto live, pairs the same product on both, and shows where it costs less.</p>
        <p style="margin-top:18px"><a class="btn" href="#/search">Search a product</a></p>
      </header>
      ${body}`;
  }

  function miniSplit(wins) {
    const total = wins.blinkit + wins.zepto + wins.ties || 1;
    return `<i class="b" style="width:${(wins.blinkit / total) * 100}%"></i><i class="z" style="width:${(wins.zepto / total) * 100}%;margin-left:auto"></i>`;
  }

  function searchView() {
    const running = state.running;
    const busy = Boolean(running);
    return `
      <header>
        <h1>Search a product</h1>
        <p class="lede">Type what you would search for in the app, such as a brand and product.</p>
      </header>

      <form class="search-form" data-action="search-form" role="search">
        <input type="search" name="query" id="query" placeholder="dove soap" autocomplete="off" required maxlength="80"
               aria-label="Product to search" value="${escapeHtml(state.searchText)}" ${busy ? 'disabled' : ''}>
        <button class="btn" type="submit" ${busy ? 'disabled' : ''}>Compare prices</button>
      </form>
      <div class="search-options">
        <label>Listings per app
          <select data-action="max-items" ${busy ? 'disabled' : ''}>
            ${[10, 20, 30, 50].map((n) => `<option value="${n}" ${n === state.maxItems ? 'selected' : ''}>${n}</option>`).join('')}
          </select>
        </label>
        <label><input type="checkbox" data-action="refresh" ${state.refresh ? 'checked' : ''} ${busy ? 'disabled' : ''}>
          Ignore saved results from the last ${Math.round((state.config?.cache_ttl_seconds ?? 600) / 60)} minutes</label>
      </div>

      ${state.searchError ? `
        <div class="notice error" role="alert" style="margin-top:20px;max-width:720px">
          <strong>The search did not finish.</strong> ${escapeHtml(state.searchError)}
        </div>` : ''}

      ${busy ? `
        <section class="card progress" aria-label="Search in progress">
          <h2>Reading both apps for “${escapeHtml(running.query)}”</h2>
          <div class="progress-bars">
            <div class="progress-row blinkit"><span>Blinkit</span><div class="progress-track"></div></div>
            <div class="progress-row zepto"><span>Zepto</span><div class="progress-track"></div></div>
          </div>
          <p class="elapsed" id="elapsed">0s</p>
          <p class="muted">Both apps are loaded in a browser and read at the same time. This usually takes 30 to 60 seconds.</p>
          <p style="margin-top:16px"><button class="btn secondary small" data-action="cancel">Stop waiting</button></p>
        </section>` : `
        <section class="examples">
          <p>Try one of these</p>
          <div class="chips">
            ${EXAMPLE_QUERIES.map((q) => `<button class="chip" data-action="example" data-q="${escapeHtml(q)}">${escapeHtml(q)}</button>`).join('')}
          </div>
        </section>`}`;
  }

  function sortedPairIndexes(result) {
    const indexes = result.matched.map((_, i) => i);
    const nameOf = (i) => result.matched[i].blinkit.name || '';
    const by = {
      saving: (a, b) => (result.matched[b].price_diff || 0) - (result.matched[a].price_diff || 0),
      percent: (a, b) => (result.matched[b].price_diff_percent || 0) - (result.matched[a].price_diff_percent || 0),
      name: (a, b) => nameOf(a).localeCompare(nameOf(b)),
    };
    return indexes.sort(by[state.sortKey] || by.saving);
  }

  function duelRow(pair, index) {
    const blinkitPrice = pair.blinkit.price;
    const zeptoPrice = pair.zepto.price;
    const top = Math.max(blinkitPrice, zeptoPrice);
    const width = (price) => Math.max(52, (price / top) * 100);
    const cheaper = pair.cheaper_platform;
    const tag = (platform) => cheaper === platform
      ? `<span class="save-tag">${rupees(pair.price_diff)} less · ${pair.price_diff_percent}%</span>`
      : '<span class="save-tag none"></span>';
    const line = (platform, price) => `
      <div class="bar-line">
        <div class="bar ${platform}" style="width:${width(price)}%"><span>${PLATFORMS[platform]}</span><span>${rupees(price)}</span></div>
        ${cheaper === 'tie' && platform === 'zepto' ? '<span class="save-tag none">Same price</span>' : tag(platform)}
      </div>`;

    const unit = pair.unit_price;
    const differentName = pair.zepto.name && pair.zepto.name !== pair.blinkit.name;
    const meta = [pair.blinkit.brand, pair.blinkit.quantity].filter(Boolean).join(' · ');

    return `
      <article class="card duel">
        <input class="duel-pick" type="checkbox" data-action="pick" data-index="${index}" ${state.picked.has(index) ? 'checked' : ''}
               aria-label="Include ${escapeHtml(pair.blinkit.name)} in basket">
        <div>
          <p class="duel-name">${escapeHtml(pair.blinkit.name)}</p>
          <p class="duel-meta">${escapeHtml(meta)}</p>
          ${differentName ? `<p class="duel-meta">On Zepto: ${escapeHtml(pair.zepto.name)}${pair.zepto.quantity && pair.zepto.quantity !== pair.blinkit.quantity ? ` (${escapeHtml(pair.zepto.quantity)})` : ''}</p>` : ''}
        </div>
        <div>
          <div class="bars">${line('blinkit', blinkitPrice)}${line('zepto', zeptoPrice)}</div>
          ${unit ? `<p class="unit-line">Per ${escapeHtml(unit.unit)}: ${rupees(unit.blinkit)} on Blinkit, ${rupees(unit.zepto)} on Zepto</p>` : ''}
        </div>
      </article>`;
  }

  function comparisonView() {
    const result = state.result;
    if (!result) return `<header><h1>Comparison</h1></header>${emptyResult('Search for a product to compare its price on Blinkit and Zepto.')}`;

    const pairs = result.matched;
    const wins = winCounts(pairs);
    const unmatched = result.blinkit_only.length + result.zepto_only.length;
    const providers = `${plural(result.counts.blinkit_total, 'Blinkit listing')} and ${plural(result.counts.zepto_total, 'Zepto listing')}`;

    const failedPlatforms = Object.keys(result.errors || {});
    const workingPlatforms = Object.keys(PLATFORMS).filter((key) => !failedPlatforms.includes(key));

    let headline;
    if (failedPlatforms.length && pairs.length === 0) {
      headline = workingPlatforms.length
        ? `Only ${PLATFORMS[workingPlatforms[0]]} could be read for “${escapeHtml(result.query)}”`
        : `Neither app could be read for “${escapeHtml(result.query)}”`;
    } else if (pairs.length === 0) headline = `No product from “${escapeHtml(result.query)}” was found on both apps`;
    else if (wins.blinkit === wins.zepto) headline = `Blinkit and Zepto are level on “${escapeHtml(result.query)}”`;
    else {
      const winner = wins.blinkit > wins.zepto ? 'blinkit' : 'zepto';
      headline = `${PLATFORMS[winner]} is cheaper on ${wins[winner]} of ${plural(pairs.length, 'matched product')}`;
    }

    const best = pairs.length ? pairs.reduce((a, b) => ((b.price_diff || 0) > (a.price_diff || 0) ? b : a)) : null;

    return `
      <header class="verdict">
        <h1>${headline}</h1>
        <p class="lede">${providers} for “${escapeHtml(result.query)}”, read ${relativeTime(result.fetched_at)}${result.cached ? ' (saved result)' : ''}.</p>
        ${pairs.length ? splitBar(wins) : ''}
        <p style="margin-top:14px"><button class="btn secondary small" data-action="search-again">Search again</button></p>
      </header>

      ${platformErrors(result.errors)}

      ${pairs.length === 0 ? `
        <div class="card empty"><h2>Nothing to pair up</h2>
          <p>${failedPlatforms.length
            ? 'Search again to retry the app that failed. Prices can only be compared when both apps are read.'
            : 'Try a more general search term, or open Product matching to see why listings were not paired.'}</p>
          <a class="btn secondary" href="#/matching">See the listings that were read</a></div>` : `
        ${best && best.price_diff > 0 ? `
          <div class="card best-deal"><strong>Biggest gap: ${escapeHtml(best.blinkit.name)}</strong>
            <span>${PLATFORMS[best.cheaper_platform]} is ${rupees(best.price_diff)} cheaper (${best.price_diff_percent}%)</span></div>` : ''}

        <div class="toolbar" role="group" aria-label="Sort and select">
          <span class="muted">Sort by</span>
          <div class="chips">
            ${[['saving', 'Rupees saved'], ['percent', 'Percent saved'], ['name', 'Name']].map(([key, label]) =>
              `<button class="chip" data-action="sort" data-key="${key}" aria-pressed="${state.sortKey === key}">${label}</button>`).join('')}
          </div>
          <span class="spacer"></span>
          <button class="btn secondary small" data-action="pick-all">Select all</button>
          <button class="btn secondary small" data-action="pick-none">Clear selection</button>
        </div>

        <div class="duels">${sortedPairIndexes(result).map((i) => duelRow(pairs[i], i)).join('')}</div>`}

      ${unmatched && pairs.length ? `<p class="muted" style="margin-top:20px">${plural(unmatched, 'listing')} could not be paired with the other app. <a href="#/matching">See why</a></p>` : ''}`;
  }

  function dataView() {
    const result = state.result;
    if (!result) return `<header><h1>Scraped data</h1></header>${emptyResult('Run a search to see every listing read from Blinkit and Zepto.')}`;

    const text = state.dataText.trim().toLowerCase();
    const rows = allProducts(result)
      .filter((p) => state.dataPlatform === 'all' || p.platform === state.dataPlatform)
      .filter((p) => !text || [p.name, p.brand, p.simple_name].some((v) => String(v || '').toLowerCase().includes(text)))
      .sort((a, b) => a.platform.localeCompare(b.platform) || String(a.name).localeCompare(String(b.name)));

    return `
      <header>
        <h1>Scraped data</h1>
        <p class="lede">Every listing read for “${escapeHtml(result.query)}”, as the extraction step returned it.</p>
      </header>

      <div class="toolbar">
        <div class="chips" role="group" aria-label="Platform">
          ${[['all', 'Both apps'], ['blinkit', 'Blinkit'], ['zepto', 'Zepto']].map(([key, label]) =>
            `<button class="chip" data-action="data-platform" data-key="${key}" aria-pressed="${state.dataPlatform === key}">${label}</button>`).join('')}
        </div>
        <input type="search" id="data-filter" placeholder="Filter by name or brand" aria-label="Filter listings" value="${escapeHtml(state.dataText)}">
        <span class="spacer"></span>
        <span class="muted" id="data-count">${plural(rows.length, 'listing')}</span>
        <button class="btn secondary small" data-action="export-csv">Download CSV</button>
      </div>

      <div class="table-wrap" id="data-table">${dataTable(rows)}</div>`;
  }

  function dataTable(rows) {
    if (!rows.length) return '<p class="muted" style="padding:20px">No listings match this filter.</p>';
    return `
      <table>
        <thead><tr><th>App</th><th>Product</th><th>Brand</th><th>Pack</th><th class="num">Price</th><th class="num">MRP</th><th>Paired</th></tr></thead>
        <tbody>
          ${rows.map((p) => `
            <tr>
              <td>${platformDot(p.platform)}${PLATFORMS[p.platform]}</td>
              <td>${escapeHtml(p.name)}${p.simple_name ? `<span class="sub">Standard name: ${escapeHtml(p.simple_name)}</span>` : ''}</td>
              <td>${escapeHtml(p.brand)}</td>
              <td>${escapeHtml(p.quantity || 'Not stated')}</td>
              <td class="num"><strong>${rupees(p.price)}</strong></td>
              <td class="num">${p.mrp && p.mrp > p.price ? `<span class="mrp">${rupees(p.mrp)}</span>` : ''}</td>
              <td><span class="pill ${p.matched ? 'yes' : ''}">${p.matched ? 'Yes' : 'No'}</span></td>
            </tr>`).join('')}
        </tbody>
      </table>`;
  }

  function matchingView() {
    const result = state.result;
    const gates = [
      ['Same brand', 'Different brands are never the same product.'],
      ['Same pack size', 'A 100 g bar and a 125 g bar are different products. Sizes must be within 10%.'],
      ['Similar name', 'Each listing is given a standard name, and those names must be similar. Size and price can never make up for a different name.'],
      ['Believable price', 'A price gap above 3 times means it is almost certainly a different product.'],
    ];

    let body;
    if (!result) {
      body = emptyResult('Run a search to see how its listings were paired.');
    } else {
      const reasonRow = (item, otherPlatform) => `
        <tr>
          <td>${escapeHtml(item.name)}<span class="sub">${escapeHtml([item.brand, item.quantity].filter(Boolean).join(' · '))}</span></td>
          <td>${item.closest ? `${escapeHtml(item.closest.name)}<span class="sub">on ${PLATFORMS[otherPlatform]}, name similarity ${item.closest.name_score}</span>` : `<span class="muted">Nothing found on ${PLATFORMS[otherPlatform]}</span>`}</td>
          <td>${item.closest ? escapeHtml(item.closest.reason) : 'No listings to compare with'}</td>
        </tr>`;
      const unmatchedTable = (title, items, platform, other) => items.length ? `
        <section class="section">
          <h2>${platformDot(platform)}Only on ${title} (${items.length})</h2>
          <div class="table-wrap"><table>
            <thead><tr><th>Listing</th><th>Closest on the other app</th><th>Why it was not paired</th></tr></thead>
            <tbody>${items.map((item) => reasonRow(item, other)).join('')}</tbody></table></div>
        </section>` : '';

      body = `
        <section class="section">
          <h2>Paired products (${result.matched.length})</h2>
          ${result.matched.length ? `
          <div class="table-wrap"><table>
            <thead><tr><th>Blinkit listing</th><th>Zepto listing</th><th>Name similarity</th><th>Pack size</th><th class="num">Match score</th></tr></thead>
            <tbody>
              ${result.matched.map((pair) => `
                <tr>
                  <td>${escapeHtml(pair.blinkit.name)}<span class="sub">${escapeHtml(pair.blinkit.quantity || '')}</span></td>
                  <td>${escapeHtml(pair.zepto.name)}<span class="sub">${escapeHtml(pair.zepto.quantity || '')}</span></td>
                  <td><span class="score"><span class="score-bar"><i style="width:${pair.breakdown.name}%"></i></span>${pair.breakdown.name}</span></td>
                  <td>${pair.breakdown.size === null ? '<span class="muted">Not stated</span>' : 'Matches'}</td>
                  <td class="num"><strong>${pair.match_confidence}</strong></td>
                </tr>`).join('')}
            </tbody></table></div>` : '<p class="muted">No products passed all four checks.</p>'}
        </section>
        ${unmatchedTable('Blinkit', result.blinkit_only, 'blinkit', 'zepto')}
        ${unmatchedTable('Zepto', result.zepto_only, 'zepto', 'blinkit')}`;
    }

    return `
      <header>
        <h1>How products are matched</h1>
        <p class="lede">The same product is often named differently on each app. A listing is paired with another only if it passes four checks, in this order.</p>
      </header>
      <ol class="gates">
        ${gates.map(([title, text]) => `<li class="card"><h3>${title}</h3><p>${text}</p></li>`).join('')}
      </ol>
      ${body}`;
  }

  function aboutView() {
    const config = state.config;
    const steps = [
      ['Search', 'You type a product.'],
      ['Crawl', 'crawl4ai opens Blinkit and Zepto in a browser at the same time.'],
      ['Extract', 'A language model reads each page and returns brand, name, price, MRP and pack size.'],
      ['Match', 'Rules and fuzzy text matching pair the same product across apps.'],
      ['Compare', 'The cheaper app, the saving and a basket total are worked out.'],
    ];
    return `
      <header>
        <h1>About this project</h1>
        <p class="lede">Price Matcher is an AI and data science project that compares live grocery prices on Blinkit and Zepto without any hand-written page selectors.</p>
      </header>

      <section>
        <h2>How a search works</h2>
        <ol class="pipeline">
          ${steps.map(([title, text]) => `<li><b>${title}</b><span>${text}</span></li>`).join('')}
        </ol>
      </section>

      <section class="section">
        <h2>Current settings</h2>
        <dl class="card kv">
          <dt>Extraction model</dt><dd>${escapeHtml(config?.llm_provider ?? 'Unknown (server not reachable)')}</dd>
          <dt>Page sent to the model as</dt><dd>${escapeHtml(config?.input_format ?? 'Unknown')}</dd>
          <dt>Blinkit delivery area</dt><dd>${escapeHtml(config?.blinkit_location ?? 'Unknown')}</dd>
          <dt>Saved results reused for</dt><dd>${config ? `${Math.round(config.cache_ttl_seconds / 60)} minutes` : 'Unknown'}</dd>
          <dt>Built with</dt><dd>Python, FastAPI, crawl4ai, rapidfuzz, SQLite, and this page in plain HTML, CSS and JavaScript</dd>
        </dl>
      </section>

      <section class="section">
        <h2>Things to know</h2>
        <ul class="plain-list">
          <li>Prices depend on delivery location. Blinkit prices are for the area shown above; Zepto shows its own default area.</li>
          <li>A language model reads the pages, so it can occasionally misread a price or a pack size. Scraped data shows exactly what it returned.</li>
          <li>Both sites ask automated tools not to crawl these pages in their robots.txt. Use this for personal, low-volume comparison only.</li>
          <li>Every successful search is saved in a local SQLite file, which is what the dashboard summarises.</li>
        </ul>
      </section>`;
  }

  // ---------- rendering and routing ----------

  const VIEWS = { dashboard: dashboardView, search: searchView, data: dataView, comparison: comparisonView, matching: matchingView, about: aboutView };

  function currentRoute() {
    const name = location.hash.replace(/^#\/?/, '') || 'dashboard';
    return VIEWS[name] ? name : 'dashboard';
  }

  function render({ focus = true } = {}) {
    const route = currentRoute();
    document.querySelectorAll('.nav a').forEach((a) => {
      if (a.dataset.route === route) a.setAttribute('aria-current', 'page');
      else a.removeAttribute('aria-current');
    });
    view.innerHTML = VIEWS[route]();
    renderRail();
    renderBasket();
    if (focus) {
      window.scrollTo(0, 0);
      const heading = view.querySelector('h1');
      if (heading) { heading.setAttribute('tabindex', '-1'); heading.focus({ preventScroll: true }); }
    }
  }

  function renderRail() {
    const result = state.result;
    if (!result) { railCurrent.hidden = true; return; }
    railCurrent.hidden = false;
    railCurrent.innerHTML = `<span>Showing</span><strong>${escapeHtml(result.query)}</strong><span>${plural(result.matched.length, 'matched product')}</span>`;
  }

  function renderBasket() {
    const result = state.result;
    if (currentRoute() !== 'comparison' || !result || result.matched.length === 0) { basketBar.hidden = true; return; }
    basketBar.hidden = false;
    if (state.picked.size === 0) {
      basketBar.innerHTML = '<span>Tick products to add up what the same basket costs on each app.</span>';
      return;
    }
    const totals = basketTotals();
    const diff = Math.round(Math.abs(totals.blinkit - totals.zepto) * 100) / 100;
    const winner = totals.blinkit < totals.zepto ? 'blinkit' : totals.zepto < totals.blinkit ? 'zepto' : null;
    basketBar.innerHTML = `
      <div class="totals">
        <div class="total"><span>${platformDot('blinkit')}Blinkit</span><br><b>${rupees(totals.blinkit)}</b></div>
        <div class="total"><span>${platformDot('zepto')}Zepto</span><br><b>${rupees(totals.zepto)}</b></div>
      </div>
      <span class="winner">${winner ? `${PLATFORMS[winner]} saves you ${rupees(diff)} on these ${plural(state.picked.size, 'product')}` : `Same total on these ${plural(state.picked.size, 'product')}`}</span>`;
  }

  // ---------- actions ----------

  function setResult(result) {
    state.result = result;
    state.picked = new Set(result.matched.map((_, i) => i));
    if (result.search_id) safeStorage((s) => s.setItem(LAST_RESULT_KEY, String(result.search_id)));
  }

  async function refreshSummaries() {
    try {
      [state.stats, state.history] = await Promise.all([api('/api/stats'), api('/api/history?limit=10')]);
      state.loadError = '';
    } catch (error) {
      state.loadError = error.message;
    }
  }

  async function openSaved(id) {
    try {
      setResult(await api(`/api/history/${id}`));
      location.hash = '#/comparison';
    } catch (error) {
      state.loadError = error.message;
      render();
    }
  }

  function stopTimer() {
    if (state.running?.timerId) clearInterval(state.running.timerId);
  }

  async function startSearch(rawQuery, { forceRefresh = false } = {}) {
    const query = rawQuery.trim().replace(/\s+/g, ' ');
    if (!query || state.running) return;

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort('timeout'), REQUEST_TIMEOUT_MS);
    state.searchText = query;
    state.searchError = '';
    state.running = { query, startedAt: Date.now(), controller, timerId: null };
    state.running.timerId = setInterval(() => {
      const el = document.getElementById('elapsed');
      if (el) el.textContent = `${Math.round((Date.now() - state.running.startedAt) / 1000)}s`;
    }, 1000);
    if (currentRoute() !== 'search') location.hash = '#/search'; else render({ focus: false });
    live.textContent = `Searching for ${query}`;

    try {
      const params = new URLSearchParams({ query, max_items: String(state.maxItems) });
      if (state.refresh || forceRefresh) params.set('refresh', 'true');
      const result = await api(`/api/compare?${params}`, { signal: controller.signal });
      stopTimer();
      state.running = null;
      setResult(result);
      refreshSummaries();
      live.textContent = `Comparison for ${query} is ready`;
      location.hash = '#/comparison';
      if (currentRoute() === 'comparison') render();
    } catch (error) {
      stopTimer();
      const cancelled = error.name === 'AbortError' && controller.signal.reason !== 'timeout';
      state.running = null;
      state.searchError = cancelled ? '' : error.name === 'AbortError'
        ? 'The apps took longer than 3 minutes to respond. Try again, or try fewer listings per app.'
        : error.message;
      render({ focus: false });
    } finally {
      clearTimeout(timeoutId);
    }
  }

  function exportCsv() {
    const result = state.result;
    if (!result) return;
    const text = state.dataText.trim().toLowerCase();
    const rows = allProducts(result)
      .filter((p) => state.dataPlatform === 'all' || p.platform === state.dataPlatform)
      .filter((p) => !text || [p.name, p.brand, p.simple_name].some((v) => String(v || '').toLowerCase().includes(text)));

    // Cells starting with = + - @ would run as formulas in Excel, so they get a leading quote.
    const cell = (value) => {
      let s = String(value ?? '');
      if (/^[=+\-@]/.test(s)) s = `'${s}`;
      return `"${s.replace(/"/g, '""')}"`;
    };
    const header = ['App', 'Brand', 'Product', 'Standard name', 'Pack', 'Price (INR)', 'MRP (INR)', 'Paired'];
    const lines = [header.map(cell).join(',')].concat(rows.map((p) =>
      [PLATFORMS[p.platform], p.brand, p.name, p.simple_name, p.quantity, p.price, p.mrp, p.matched ? 'Yes' : 'No'].map(cell).join(',')));

    const blob = new Blob(['﻿' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = `price-matcher-${result.query.replace(/[^a-z0-9]+/gi, '-').toLowerCase()}.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  }

  document.addEventListener('click', (event) => {
    const target = event.target.closest('[data-action]');
    if (!target) return;
    const { action } = target.dataset;

    if (action === 'open') openSaved(target.dataset.id);
    else if (action === 'example') startSearch(target.dataset.q);
    else if (action === 'cancel') state.running?.controller.abort();
    else if (action === 'search-again') startSearch(state.result?.query ?? '', { forceRefresh: true });
    else if (action === 'sort') { state.sortKey = target.dataset.key; render({ focus: false }); }
    else if (action === 'pick-all') { state.picked = new Set(state.result.matched.map((_, i) => i)); render({ focus: false }); }
    else if (action === 'pick-none') { state.picked = new Set(); render({ focus: false }); }
    else if (action === 'data-platform') { state.dataPlatform = target.dataset.key; render({ focus: false }); }
    else if (action === 'export-csv') exportCsv();
  });

  document.addEventListener('change', (event) => {
    const target = event.target;
    if (target.dataset.action === 'pick') {
      const index = Number(target.dataset.index);
      if (target.checked) state.picked.add(index); else state.picked.delete(index);
      renderBasket();
    } else if (target.dataset.action === 'max-items') state.maxItems = Number(target.value);
    else if (target.dataset.action === 'refresh') state.refresh = target.checked;
  });

  document.addEventListener('input', (event) => {
    if (event.target.id === 'data-filter') {
      state.dataText = event.target.value;
      const result = state.result;
      const text = state.dataText.trim().toLowerCase();
      const rows = allProducts(result)
        .filter((p) => state.dataPlatform === 'all' || p.platform === state.dataPlatform)
        .filter((p) => !text || [p.name, p.brand, p.simple_name].some((v) => String(v || '').toLowerCase().includes(text)))
        .sort((a, b) => a.platform.localeCompare(b.platform) || String(a.name).localeCompare(String(b.name)));
      document.getElementById('data-table').innerHTML = dataTable(rows);
      document.getElementById('data-count').textContent = plural(rows.length, 'listing');
    } else if (event.target.id === 'query') {
      state.searchText = event.target.value;
    }
  });

  document.addEventListener('submit', (event) => {
    if (event.target.dataset.action === 'search-form') {
      event.preventDefault();
      startSearch(new FormData(event.target).get('query') || '');
    }
  });

  window.addEventListener('hashchange', () => render());

  // ---------- start ----------

  async function init() {
    render();
    const [config] = await Promise.allSettled([api('/api/config'), refreshSummaries()]);
    if (config.status === 'fulfilled') { state.config = config.value; state.maxItems = config.value.default_max_items || 20; }
    else state.loadError = config.reason.message;

    // Reopen the last comparison so a page reload doesn't lose it.
    const savedId = safeStorage((s) => s.getItem(LAST_RESULT_KEY)) || state.history[0]?.id;
    if (savedId && !state.result) {
      try { setResult(await api(`/api/history/${savedId}`)); } catch { /* it may have been deleted; start empty */ }
    }
    render({ focus: false });
  }

  init();
})();
