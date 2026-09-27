import json
import re
from datetime import date, datetime, timedelta

import libsql_client

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS breweries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    city TEXT,
    website TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    last_refreshed_at TEXT,
    untappd_url TEXT,
    last_untappd_checked_at TEXT
);

CREATE TABLE IF NOT EXISTS releases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brewery_name TEXT NOT NULL,
    beer_name TEXT NOT NULL,
    release_date TEXT NOT NULL,
    source_url TEXT,
    evidence_snippet TEXT,
    source_type TEXT NOT NULL DEFAULT 'instagram',
    status TEXT NOT NULL DEFAULT 'upcoming',
    discovered_at TEXT NOT NULL,
    last_confirmed_at TEXT NOT NULL,
    UNIQUE(brewery_name, beer_name, release_date)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

# Higher number = more trusted source; used when deciding whether a new
# fuzzy-matched finding is allowed to overwrite an existing record.
_SOURCE_PRIORITY = {"instagram": 0, "manual": 1, "untappd": 2}

_PUNCT_RE = re.compile(r"[^a-z0-9 ]")


def _normalize_beer_name(name: str) -> str:
    name = (name or "").lower()
    name = _PUNCT_RE.sub(" ", name)
    return re.sub(r"\s+", " ", name).strip()


class _CursorLike:
    """Just enough of sqlite3's cursor surface for the rest of this file:
    .fetchall() / .fetchone() over already-materialized dict rows, and
    .lastrowid from the last INSERT."""

    __slots__ = ("_rows", "lastrowid")

    def __init__(self, rows, lastrowid):
        self._rows = rows
        self.lastrowid = lastrowid

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _ConnWrapper:
    """Shim around libsql_client.ClientSync so the rest of this file can
    keep using the exact sqlite3-shaped calls it always has —
    conn.execute(sql, params).fetchall(), row["col"], conn.commit(),
    conn.close() — unchanged, whether talking to a local SQLite file or a
    remote Turso database. Both go through libsql_client; only the URL
    passed to get_conn() differs, verified against a local `file:` URL
    (2026-09-27) to behave identically to the remote `libsql://` one for
    every SQL feature this file uses (params, ALTER TABLE, ON CONFLICT
    upserts, batch()).
    """

    __slots__ = ("_client",)

    def __init__(self, client):
        self._client = client

    def execute(self, sql, params=()):
        rs = self._client.execute(sql, list(params) if params else None)
        rows = [row.asdict() for row in rs.rows]
        return _CursorLike(rows, rs.last_insert_rowid)

    def executescript(self, script):
        # Only ever called with SCHEMA below: simple `CREATE TABLE ...;`
        # statements, no semicolons inside string/default-value literals,
        # so a naive split is safe. batch() runs them atomically.
        stmts = [s.strip() for s in script.split(";") if s.strip()]
        self._client.batch(stmts)

    def commit(self):
        pass  # libsql_client commits each execute()/batch() as it goes

    def close(self):
        self._client.close()


def get_conn():
    if config.TURSO_DATABASE_URL:
        client = libsql_client.create_client_sync(
            url=config.TURSO_DATABASE_URL, auth_token=config.TURSO_AUTH_TOKEN or None
        )
    else:
        config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        client = libsql_client.create_client_sync(url=f"file:{config.DB_PATH}")
    return _ConnWrapper(client)


def init_db():
    conn = get_conn()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
        _migrate_schema(conn)
        _seed_breweries_if_empty(conn)
        _seed_settings_if_empty(conn)
    finally:
        conn.close()


def _try_add_column(conn, table, column, coltype):
    """ALTER TABLE ADD COLUMN, ignoring the "already exists" case.

    Deliberately not PRAGMA table_info(...)-based: that's a SQLite-session
    concept, and rather than assume it behaves identically over libSQL's
    remote (Turso) protocol, just attempt the ALTER and swallow the specific
    "duplicate column" error SQLite/libSQL both raise when it already
    exists — verified (2026-09-27) this is the same message text libsql_client
    surfaces locally, and ALTER TABLE ADD COLUMN itself is core SQL any
    SQLite-compatible engine supports, so there's nothing Turso-specific to
    be uncertain about here.
    """
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
    except Exception as e:
        if "duplicate column" not in str(e).lower():
            raise


def _migrate_schema(conn):
    """Add columns introduced after the initial release to existing DBs.

    CREATE TABLE IF NOT EXISTS above only creates missing tables, not
    missing columns on tables that already exist, so new columns need an
    explicit ALTER TABLE.
    """
    _try_add_column(conn, "releases", "evidence_snippet", "TEXT")
    _try_add_column(conn, "releases", "source_type", "TEXT NOT NULL DEFAULT 'instagram'")
    _try_add_column(conn, "breweries", "last_refreshed_at", "TEXT")
    _try_add_column(conn, "breweries", "untappd_url", "TEXT")
    _try_add_column(conn, "breweries", "last_untappd_checked_at", "TEXT")
    conn.commit()


def _seed_breweries_if_empty(conn):
    """Insert any brewery from the seed file that isn't already in the DB.

    Runs on every startup (not just first run), so adding a brewery to
    data/seed_breweries.json and restarting picks it up even for an existing
    database. Breweries the user removed stay removed (INSERT OR IGNORE only
    adds names that aren't present at all).
    """
    if not config.SEED_BREWERIES_PATH.exists():
        return
    with open(config.SEED_BREWERIES_PATH, "r", encoding="utf-8") as f:
        seed = json.load(f)
    for b in seed:
        conn.execute(
            "INSERT OR IGNORE INTO breweries (name, city, website, active, untappd_url) VALUES (?, ?, ?, 1, ?)",
            (b.get("name"), b.get("city"), b.get("website"), b.get("untappd_url")),
        )
        # If the brewery row already existed without an untappd_url (e.g. it
        # predates this field), backfill it from the seed file, but never
        # overwrite a URL someone already set via the UI.
        if b.get("untappd_url"):
            conn.execute(
                "UPDATE breweries SET untappd_url = ? WHERE name = ? AND (untappd_url IS NULL OR untappd_url = '')",
                (b.get("untappd_url"), b.get("name")),
            )
    conn.commit()


def _seed_settings_if_empty(conn):
    defaults = {
        "expiration_days": config.DEFAULT_EXPIRATION_DAYS,
        "skip_recent_days": config.DEFAULT_SKIP_RECENT_DAYS,
    }
    for key, default in defaults.items():
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if row is None:
            conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, str(default)))
    conn.commit()


def _get_int_setting(key, default):
    conn = get_conn()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return int(row["value"]) if row is not None else default
    finally:
        conn.close()


def _set_int_setting(key, value: int):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
        conn.commit()
    finally:
        conn.close()


def get_expiration_days():
    return _get_int_setting("expiration_days", config.DEFAULT_EXPIRATION_DAYS)


def set_expiration_days(days: int):
    _set_int_setting("expiration_days", days)


def get_skip_recent_days():
    return _get_int_setting("skip_recent_days", config.DEFAULT_SKIP_RECENT_DAYS)


def set_skip_recent_days(days: int):
    _set_int_setting("skip_recent_days", days)


def get_last_refresh_completed_at():
    """Server-side timestamp of the last completed refresh, shown to
    everyone (including guests with no admin key) as a data-freshness
    indicator without exposing who/when triggered it beyond that."""
    conn = get_conn()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = 'last_refresh_completed_at'").fetchone()
        return row["value"] if row is not None else None
    finally:
        conn.close()


def set_last_refresh_completed_at():
    conn = get_conn()
    try:
        now = datetime.utcnow().isoformat()
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('last_refresh_completed_at', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (now,),
        )
        conn.commit()
    finally:
        conn.close()


def compute_status(release_date_str: str, expiration_days: int) -> str:
    today = date.today()
    release_date = datetime.strptime(release_date_str, "%Y-%m-%d").date()
    if release_date > today:
        return "upcoming"
    days_since = (today - release_date).days
    if days_since <= expiration_days:
        return "on_draft"
    return "unknown_end"


def recompute_all_statuses():
    """Re-derive status for every release from release_date + expiration window."""
    expiration_days = get_expiration_days()
    conn = get_conn()
    try:
        rows = conn.execute("SELECT id, release_date FROM releases").fetchall()
        for row in rows:
            new_status = compute_status(row["release_date"], expiration_days)
            conn.execute("UPDATE releases SET status = ? WHERE id = ?", (new_status, row["id"]))
        conn.commit()
    finally:
        conn.close()


def upsert_fresh_hop_release(brewery_name, beer_name, release_date_str, source_url, evidence_snippet, source_type):
    """Insert or merge a release from one of the tracks (source_type is
    'untappd', 'instagram', or 'manual').

    Two dedup paths:
      1. Exact match (brewery + beer_name + release_date): refresh the
         existing row's evidence/source/confirmed_at in place.
      2. Fuzzy match (brewery + normalized beer_name, any release_date):
         handles the same beer showing up on different tracks with
         different dates (e.g. an IG teaser's guessed date vs. Untappd's
         actual Added date). The higher-_SOURCE_PRIORITY_ finding wins and
         overwrites release_date/source/evidence/source_type; a
         lower-priority finding (e.g. an IG mention arriving after Untappd
         already confirmed the beer) only touches last_confirmed_at and
         never downgrades an already-confirmed record.

    Returns (kind, release_id) where kind is 'inserted', 'updated', 'merged'
    (fuzzy match upgraded to this source), or 'kept_higher_priority' (fuzzy
    match found but this source is lower priority, so left as-is).
    """
    now = datetime.utcnow().isoformat()
    expiration_days = get_expiration_days()
    status = compute_status(release_date_str, expiration_days)
    normalized_target = _normalize_beer_name(beer_name)

    conn = get_conn()
    try:
        exact = conn.execute(
            "SELECT id FROM releases WHERE brewery_name = ? AND beer_name = ? AND release_date = ?",
            (brewery_name, beer_name, release_date_str),
        ).fetchone()
        if exact:
            conn.execute(
                "UPDATE releases SET source_url = COALESCE(?, source_url), "
                "evidence_snippet = COALESCE(?, evidence_snippet), "
                "last_confirmed_at = ?, status = ? WHERE id = ?",
                (source_url, evidence_snippet, now, status, exact["id"]),
            )
            conn.commit()
            return "updated", exact["id"]

        candidates = conn.execute(
            "SELECT id, beer_name, source_type FROM releases WHERE brewery_name = ?", (brewery_name,)
        ).fetchall()
        fuzzy = next(
            (c for c in candidates if _normalize_beer_name(c["beer_name"]) == normalized_target and normalized_target),
            None,
        )
        if fuzzy:
            existing_priority = _SOURCE_PRIORITY.get(fuzzy["source_type"], 0)
            new_priority = _SOURCE_PRIORITY.get(source_type, 0)
            if new_priority >= existing_priority:
                conn.execute(
                    "UPDATE releases SET release_date = ?, source_url = ?, evidence_snippet = ?, "
                    "source_type = ?, last_confirmed_at = ?, status = ? WHERE id = ?",
                    (release_date_str, source_url, evidence_snippet, source_type, now, status, fuzzy["id"]),
                )
                conn.commit()
                return "merged", fuzzy["id"]
            else:
                conn.execute("UPDATE releases SET last_confirmed_at = ? WHERE id = ?", (now, fuzzy["id"]))
                conn.commit()
                return "kept_higher_priority", fuzzy["id"]

        cur = conn.execute(
            "INSERT INTO releases (brewery_name, beer_name, release_date, source_url, evidence_snippet, "
            "source_type, status, discovered_at, last_confirmed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (brewery_name, beer_name, release_date_str, source_url, evidence_snippet, source_type, status, now, now),
        )
        conn.commit()
        return "inserted", cur.lastrowid
    finally:
        conn.close()


def list_today_releases():
    recompute_all_statuses()
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM releases WHERE status = 'on_draft' ORDER BY release_date DESC, brewery_name ASC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_all_releases():
    recompute_all_statuses()
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM releases ORDER BY release_date ASC, brewery_name ASC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_breweries(active_only=False):
    conn = get_conn()
    try:
        if active_only:
            rows = conn.execute("SELECT * FROM breweries WHERE active = 1 ORDER BY name ASC").fetchall()
        else:
            rows = conn.execute("SELECT * FROM breweries ORDER BY name ASC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def add_brewery(name, city=None, website=None):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO breweries (name, city, website, active) VALUES (?, ?, ?, 1)",
            (name, city, website),
        )
        conn.commit()
    finally:
        conn.close()


def set_brewery_active(brewery_id, active: bool):
    conn = get_conn()
    try:
        conn.execute("UPDATE breweries SET active = ? WHERE id = ?", (1 if active else 0, brewery_id))
        conn.commit()
    finally:
        conn.close()


def touch_brewery_refreshed(brewery_id):
    """Mark a brewery as having been actually searched just now (used to
    skip it on the next refresh while data is still considered fresh)."""
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE breweries SET last_refreshed_at = ? WHERE id = ?",
            (datetime.utcnow().isoformat(), brewery_id),
        )
        conn.commit()
    finally:
        conn.close()


def touch_brewery_untappd_checked(brewery_id):
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE breweries SET last_untappd_checked_at = ? WHERE id = ?",
            (datetime.utcnow().isoformat(), brewery_id),
        )
        conn.commit()
    finally:
        conn.close()


def set_brewery_untappd_url(brewery_id, untappd_url):
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE breweries SET untappd_url = ? WHERE id = ?",
            (untappd_url or None, brewery_id),
        )
        conn.commit()
    finally:
        conn.close()


def delete_brewery(brewery_id):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM breweries WHERE id = ?", (brewery_id,))
        conn.commit()
    finally:
        conn.close()
