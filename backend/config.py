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
FLASK_PORT = int(os.environ.get("FLASK_PORT", "5050"))

# Gates refresh/manual-entry/brewery-and-settings-edit endpoints. Read-only
# endpoints (today/calendar/brewery listing) stay open to anyone. Must be
# set for admin features to work at all — see require_admin() in app.py.
ADMIN_KEY = os.environ.get("ADMIN_KEY", "")
