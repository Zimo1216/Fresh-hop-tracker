"""One-time migration: copy the local SQLite data (data/fresh_hop.db) into
a Turso database, preserving row ids so nothing is duplicated or renumbered.

The local data is worth keeping — as of 2026-09-27 it's 76 real releases
(mostly Untappd-confirmed on_draft beers plus a handful of grounded
Instagram teasers) built up over several rounds of fixing the grounding/
staleness checks, not throwaway test data. Regenerating it from scratch
would cost real Tavily/Claude credits for no benefit.

Usage:
    1. Set TURSO_DATABASE_URL and TURSO_AUTH_TOKEN in .env (get these from
       `turso db show <name> --url` and `turso db tokens create <name>`).
    2. source venv/bin/activate
    3. python scripts/migrate_to_turso.py

Safe to re-run: schema creation uses IF NOT EXISTS / ADD COLUMN, and row
copies use INSERT OR IGNORE keyed on the original ids, so running this
twice against the same Turso database won't duplicate anything.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

import libsql_client  # noqa: E402

from backend import config, db  # noqa: E402


def main():
    if not config.TURSO_DATABASE_URL or not config.TURSO_AUTH_TOKEN:
        print("TURSO_DATABASE_URL / TURSO_AUTH_TOKEN aren't set in your environment.")
        print("Set them in .env first (see .env.example), then re-run this script.")
        sys.exit(1)

    if not config.DB_PATH.exists():
        print(f"No local database found at {config.DB_PATH} — nothing to migrate.")
        sys.exit(0)

    print(f"Reading local data from {config.DB_PATH} ...")
    local = libsql_client.create_client_sync(url=f"file:{config.DB_PATH}")
    breweries = [row.asdict() for row in local.execute("SELECT * FROM breweries").rows]
    releases = [row.asdict() for row in local.execute("SELECT * FROM releases").rows]
    settings = [row.asdict() for row in local.execute("SELECT * FROM settings").rows]
    local.close()
    print(f"  found {len(breweries)} breweries, {len(releases)} releases, {len(settings)} settings rows")

    print(f"Connecting to Turso ({config.TURSO_DATABASE_URL}) and creating schema if needed...")
    db.init_db()  # targets Turso now, since the env vars above are set

    remote = db.get_conn()
    try:
        print("Copying breweries...")
        for b in breweries:
            remote.execute(
                "INSERT OR IGNORE INTO breweries "
                "(id, name, city, website, active, last_refreshed_at, untappd_url, last_untappd_checked_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    b["id"], b["name"], b["city"], b["website"], b["active"],
                    b["last_refreshed_at"], b["untappd_url"], b["last_untappd_checked_at"],
                ),
            )

        print("Copying releases...")
        for r in releases:
            remote.execute(
                "INSERT OR IGNORE INTO releases "
                "(id, brewery_name, beer_name, release_date, source_url, evidence_snippet, "
                "source_type, status, discovered_at, last_confirmed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    r["id"], r["brewery_name"], r["beer_name"], r["release_date"], r["source_url"],
                    r["evidence_snippet"], r["source_type"], r["status"], r["discovered_at"], r["last_confirmed_at"],
                ),
            )

        print("Copying settings...")
        for s in settings:
            remote.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (s["key"], s["value"]),
            )
        remote.commit()
    finally:
        remote.close()

    verify = db.get_conn()
    try:
        b_count = verify.execute("SELECT COUNT(*) c FROM breweries").fetchone()["c"]
        r_count = verify.execute("SELECT COUNT(*) c FROM releases").fetchone()["c"]
    finally:
        verify.close()
    print(f"Done. Turso now has {b_count} breweries and {r_count} releases.")


if __name__ == "__main__":
    main()
