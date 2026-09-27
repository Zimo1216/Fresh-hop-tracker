# Seattle Fresh Hop Beer Tracker

Tracks fresh hop beer releases from Seattle-area breweries. Shows what's
currently on draft today, and a calendar of upcoming/past releases.

## How it works

- **Data model / status logic**: matches the spec in `fresh-hop-tracker-prompt.md`.
  A release starts as `upcoming`, becomes `on_draft` on its release date, and
  after a configurable number of days (default **21**) with no confirming new
  info it's marked `unknown_end` and drops off the Today list (it stays visible
  in the calendar).
- **Multiple releases per brewery**: all are kept and shown side by side (not
  replaced by the newest one).
- **Two independent refresh tracks**, merged into the same table and labeled
  in the UI (`source_type`: Untappd / IG search / Manual):
  - **Track A — Untappd** (`backend/untappd_scraper.py`): direct HTML parsing
    of a brewery's public Untappd beer list (`untappd.com/<slug>/beer`,
    sorted newest-first). No LLM, no search API — just a keyword match
    (`fresh hop` / `wet hop` / `harvest ale`) against beer name/style/
    description, plus the page's own "Added" date. This is the authoritative
    on_draft source: a beer found here needs no IG/news corroboration.
    Requires an **Untappd URL** configured per brewery (Breweries tab) —
    these are *not* auto-guessed (a wrong match would misattribute data to
    the wrong brewery), so only some seed breweries have one pre-filled;
    add more by pasting a brewery's `.../beer` Untappd URL into the table.
  - **Track B — Instagram search** (`backend/scraper.py`): Tavily + Claude,
    narrowed to a single Instagram-restricted search per brewery, scoped to
    content published on/after **2026-09-01**. Only for *upcoming*
    announcements that haven't shown up on Untappd yet.
    - **Grounding**: search results can mention *other* breweries (e.g. a
      roundup article). Before a release is written to the DB, code (not
      the model) re-checks the brewery's name against the actual fetched
      content of its cited `source_url` — the model's own `evidence_snippet`
      is display-only and is never trusted as proof; a hallucinated quote
      can't pass. An unmatched or missing `source_url` is an automatic
      reject. See `backend/tests/test_regression.py` for the two real bad
      records this was hardened against — run it with
      `python -m unittest backend.tests.test_regression -v`.
    - **Staleness**: each search result's own `published_date` (from
      Tavily) is checked in code before the result ever reaches Claude —
      old content (e.g. a years-old Instagram post) is filtered out
      up front, independent of whatever release_date the model might
      later claim for it.
    - **Quota conservation**: a brewery searched within the last
      `skip_recent_days` (default 6, editable in the Breweries tab) is
      skipped unless you check "Force re-search all".
  - **Merge**: the same beer showing up on both tracks (e.g. an IG teaser
    later confirmed on Untappd) is fuzzy-matched by brewery + normalized
    beer name and merged into one record — Untappd's finding wins and
    upgrades the record to `on_draft`, never the other way around.
- **Seed brewery list**: `data/seed_breweries.json`, ~31 well-known
  Seattle-area craft breweries. Editable any time from the Breweries tab in
  the app (or by editing the JSON before first run) — "Deactivate" stops a
  brewery from being searched without deleting its history.
- **Manual entry**: the Today tab has a "+ Add a release manually" form for
  filling in anything the automated pipeline misses; manual entries go
  through the same status logic as scraped ones.
- Only releases dated on/after **2026-08-01** (Untappd Added-date floor) are
  collected, per the current spec.
- **Admin vs. guest access**: read-only endpoints (Today's List, Calendar,
  brewery listing) are open to anyone. Refresh, manual entry, and all
  brewery/settings edits require an admin key — see below. A visitor with
  no key only ever sees Today's List and Calendar, with a "Data last
  updated" line; they can't see or trigger Refresh at all.

## Setup

```bash
cd "fresh-hop-tracker"
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and fill in:
- `ANTHROPIC_API_KEY` — used by Track B to extract structured release info
  from Instagram search results.
- `TAVILY_API_KEY` — used by Track B to search the web. Get a free key at
  https://tavily.com.

Both keys are required for Track B. **Track A (Untappd) needs neither** —
it's plain HTTP + HTML parsing, so it works even without any API keys
configured, as long as breweries have an Untappd URL set.

- `ADMIN_KEY` — a long random string that gates Refresh and all management
  endpoints; a fresh one was already generated for you in `.env` when this
  feature was added. Visit the site once as
  `http://127.0.0.1:5050/?admin_key=<the value from .env>` to unlock admin
  mode on your own browser — the key is verified against the server, then
  remembered in that browser's localStorage (not shared with visitors), and
  stripped from the visible URL right after. To rotate it, change the value
  in `.env`, restart the server, and re-visit with the new key (the old one
  stops working immediately). This is a lightweight single-shared-secret
  gate, not a full login system — good enough to keep casual visitors from
  triggering paid API calls, not bank-grade security.

### Database: local file vs. Turso (persistent cloud)

By default the app stores data in a local SQLite file (`data/fresh_hop.db`).
That's fine for local dev, but **on a host with an ephemeral filesystem
(e.g. Render's free tier), that file — and every release you've ever
refreshed — gets wiped on every deploy, restart, or sleep/wake cycle.**

To persist data instead, point the app at a free [Turso](https://turso.tech)
(cloud SQLite-compatible, libSQL) database:

1. Install the CLI and sign up/log in:
   ```bash
   curl -sSfL https://get.tur.so/install.sh | bash
   turso auth signup   # or: turso auth login
   ```
2. Create a database and get its connection details:
   ```bash
   turso db create fresh-hop-tracker
   turso db show fresh-hop-tracker --url        # -> TURSO_DATABASE_URL
   turso db tokens create fresh-hop-tracker      # -> TURSO_AUTH_TOKEN
   ```
3. Put both values in `.env`:
   ```
   TURSO_DATABASE_URL=libsql://fresh-hop-tracker-<your-username>.turso.io
   TURSO_AUTH_TOKEN=<the long token string>
   ```
4. If you already have data in the local file worth keeping, copy it over
   once:
   ```bash
   python scripts/migrate_to_turso.py
   ```

That's it — `backend/db.py` picks up the two env vars automatically and
talks to Turso instead of the local file; nothing else in the app changes.
**Leave both blank** to keep using the local file (this is also what
happens automatically if you forget to set one of the two).

**Deploying this yourself?** Whoever runs this app in production needs to
set `TURSO_DATABASE_URL` and `TURSO_AUTH_TOKEN` (plus `ANTHROPIC_API_KEY`,
`TAVILY_API_KEY`, `ADMIN_KEY`) as actual environment variables on that host
— `.env` is gitignored and never gets deployed with the code. On Render:
Dashboard → your service → Environment → add each one. Do **not** set
`APP_ENV=local` there — its absence is what makes the server bind `0.0.0.0`
(see `backend/config.py`).

## Run

```bash
source venv/bin/activate
python run.py
```

Then open http://127.0.0.1:5050 in your browser.

## Notes / limitations

- **Why `libsql-client` and not the other Turso Python packages**: `libsql`/
  `libsql-experimental` (the sqlite3-drop-in one) needs a compiled Rust
  extension with no prebuilt wheel for this project's Python version —
  it failed to build locally from source. `libsql-client` is pure Python
  (`py3-none-any` wheel), so it installs reliably everywhere, including
  Render's build environment. It also transparently supports a local
  `file:` URL, so `backend/db.py` uses the exact same client/code path for
  both local dev and Turso — there's no separate "local mode" to drift out
  of sync with the real one.
- **Untappd blocks plain `requests`**: Untappd sits behind Cloudflare bot
  management that TLS-fingerprints Python's `requests`/urllib3 and serves it
  a JS challenge page, even with a convincing User-Agent header (verified —
  plain `curl` and `curl_cffi` with `impersonate="chrome"` both get real
  content). `untappd_scraper.py` uses `curl_cffi` for this reason; don't
  switch it back to `requests`.
- Keyword matching only catches beers whose Untappd name/style/description
  literally says "fresh hop"/"wet hop"/"harvest ale" — a beer marketed as
  fresh hop elsewhere but entered on Untappd under a plain series name
  (e.g. just "Field To Ferment SABRO 2026") will be missed. Use manual entry
  for known gaps like this.
- Instagram has no public API for this use case, so Track B relies on web
  search results mentioning brewery posts rather than scraping Instagram
  directly; coverage depends on what Tavily's index has.
- There is no reliable "sold out" signal anywhere, so the app follows the
  spec's fallback: a release simply expires off the Today list after the
  configurable window, but its history stays in the calendar.
- The expiration window and skip-recent window are both adjustable in the
  Breweries tab.
