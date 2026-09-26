import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "fresh_hop.db"
SEED_BREWERIES_PATH = BASE_DIR / "data" / "seed_breweries.json"

# First date we start trusting/collecting fresh hop release info. Used as
# the Untappd "Added" date floor (Track A).
DATA_START_DATE = "2026-08-01"

# Track B (Instagram/Tavily) is scoped narrower: it's only for *upcoming*
# announcements, so it only searches/accepts content from this date on.
IG_SEARCH_START_DATE = "2026-09-01"

# Default number of days a beer stays in the "on_draft" / today list
# after its release date, before it's considered unknown_end.
DEFAULT_EXPIRATION_DAYS = 21

# Default number of days a brewery is skipped on refresh after Track B
# (Tavily/Claude) last actually searched it, to conserve Tavily quota.
# Overridden with the "force re-search" option in the UI. Track A (Untappd)
# has no quota concern (just polite HTTP throttling) so it always runs.
DEFAULT_SKIP_RECENT_DAYS = 6

# Delay between consecutive Untappd requests, to avoid tripping anti-bot
# rate limiting.
UNTAPPD_REQUEST_DELAY_SECONDS = 1.5

FRESH_HOP_KEYWORDS = ["fresh hop", "wet hop", "harvest ale"]

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")

# PORT is the convention hosts like Render inject automatically (a dynamic
# value, e.g. 10000+ — never assume it's fixed); FLASK_PORT is this app's
# own local override (.env). PORT wins when both are set. `or` (not a
# second .get default) so an empty-string PORT doesn't win over FLASK_PORT.
FLASK_PORT = int(os.environ.get("PORT") or os.environ.get("FLASK_PORT") or "5050")

# Local dev must opt IN to 127.0.0.1 by setting APP_ENV=local (run.py's own
# .env does this) — every other case, including a failed/ambiguous check,
# defaults to 0.0.0.0. Getting this backwards is exactly what silently
# broke the Render deploy before: a "look for cloud signals, else assume
# local" check defaults to the unreachable address the moment its cloud
# signal doesn't fire for whatever reason (stale deploy, renamed env var,
# different host). Binding 0.0.0.0 locally is harmless either way.
IS_LOCAL_DEV = os.environ.get("APP_ENV", "").strip().lower() == "local"
HOST = "127.0.0.1" if IS_LOCAL_DEV else "0.0.0.0"

# Gates refresh/manual-entry/brewery-and-settings-edit endpoints. Read-only
# endpoints (today/calendar/brewery listing) stay open to anyone. Must be
# set for admin features to work at all — see require_admin() in app.py.
ADMIN_KEY = os.environ.get("ADMIN_KEY", "")
