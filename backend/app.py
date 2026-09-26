import secrets
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from . import config, db
from .scraper import RefreshError, run_refresh

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

app = Flask(__name__, static_folder=str(FRONTEND_DIR), static_url_path="")


def require_admin(view):
    """Gate for management endpoints (refresh, manual entry, brewery/settings
    edits) — read-only endpoints (today/calendar/brewery listing) stay open.
    Compares the X-Admin-Key header against ADMIN_KEY from the environment
    using a constant-time comparison. Fails closed: if ADMIN_KEY isn't
    configured on the server at all, every admin request is rejected rather
    than silently allowed through.
    """

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not config.ADMIN_KEY:
            return jsonify({"ok": False, "error": "Server has no ADMIN_KEY configured"}), 500
        provided = request.headers.get("X-Admin-Key", "")
        if not provided or not secrets.compare_digest(provided, config.ADMIN_KEY):
            return jsonify({"ok": False, "error": "Unauthorized"}), 401
        return view(*args, **kwargs)

    return wrapped


@app.route("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/api/admin/verify", methods=["POST"])
@require_admin
def api_admin_verify():
    return jsonify({"ok": True})


@app.route("/api/today")
def api_today():
    return jsonify(db.list_today_releases())


@app.route("/api/calendar")
def api_calendar():
    return jsonify(db.list_all_releases())


_EMPTY_SUMMARY = {
    "breweries_total": 0,
    "untappd_checked": 0,
    "ig_searched": 0,
    "ig_skipped": 0,
    "inserted": 0,
    "updated": 0,
    "merged": 0,
    "discarded_ungrounded": 0,
    "discarded_stale_source": 0,
    "errors": [],
    "details": [],
}


@app.route("/api/refresh", methods=["POST"])
@require_admin
def api_refresh():
    payload = request.get_json(silent=True) or {}
    force = bool(payload.get("force", False))
    try:
        summary = run_refresh(force=force)
        return jsonify({"ok": True, "summary": summary})
    except RefreshError as e:
        return jsonify({"ok": False, "error": str(e), "summary": _EMPTY_SUMMARY}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"Unexpected error: {e}", "summary": _EMPTY_SUMMARY}), 500


@app.route("/api/releases", methods=["POST"])
@require_admin
def api_add_release():
    """Manual entry: fills in gaps the automated refresh pipeline misses.
    Goes through the same dedup/status logic as scraped releases.
    """
    payload = request.get_json(force=True) or {}
    brewery_name = (payload.get("brewery_name") or "").strip()
    beer_name = (payload.get("beer_name") or "").strip()
    release_date = (payload.get("release_date") or "").strip()
    source_url = (payload.get("source_url") or "").strip() or None

    if not brewery_name or not beer_name or not release_date:
        return jsonify({"ok": False, "error": "brewery_name, beer_name and release_date are required"}), 400
    try:
        datetime.strptime(release_date, "%Y-%m-%d")
    except ValueError:
        return jsonify({"ok": False, "error": "release_date must be in YYYY-MM-DD format"}), 400

    kind, _ = db.upsert_fresh_hop_release(brewery_name, beer_name, release_date, source_url, None, "manual")
    db.add_brewery(brewery_name)  # so it shows up in the Breweries list too
    return jsonify({"ok": True, "result": kind})


@app.route("/api/breweries", methods=["GET"])
def api_list_breweries():
    return jsonify(db.list_breweries())


@app.route("/api/breweries", methods=["POST"])
@require_admin
def api_add_brewery():
    payload = request.get_json(force=True) or {}
    name = (payload.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "name is required"}), 400
    db.add_brewery(name, payload.get("city"), payload.get("website"))
    return jsonify({"ok": True})


@app.route("/api/breweries/<int:brewery_id>", methods=["DELETE"])
@require_admin
def api_delete_brewery(brewery_id):
    db.delete_brewery(brewery_id)
    return jsonify({"ok": True})


@app.route("/api/breweries/<int:brewery_id>/active", methods=["POST"])
@require_admin
def api_toggle_brewery(brewery_id):
    payload = request.get_json(force=True) or {}
    db.set_brewery_active(brewery_id, bool(payload.get("active", True)))
    return jsonify({"ok": True})


@app.route("/api/breweries/<int:brewery_id>/untappd_url", methods=["POST"])
@require_admin
def api_set_untappd_url(brewery_id):
    payload = request.get_json(force=True) or {}
    url = (payload.get("untappd_url") or "").strip()
    if url and "untappd.com" not in url:
        return jsonify({"ok": False, "error": "That doesn't look like an untappd.com URL"}), 400
    db.set_brewery_untappd_url(brewery_id, url or None)
    return jsonify({"ok": True})


@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    return jsonify({
        "expiration_days": db.get_expiration_days(),
        "skip_recent_days": db.get_skip_recent_days(),
        "data_start_date": config.DATA_START_DATE,
        "last_refresh_completed_at": db.get_last_refresh_completed_at(),
    })


@app.route("/api/settings", methods=["POST"])
@require_admin
def api_set_settings():
    payload = request.get_json(force=True) or {}
    if "expiration_days" in payload:
        try:
            days = int(payload["expiration_days"])
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "expiration_days must be an integer"}), 400
        db.set_expiration_days(days)
    if "skip_recent_days" in payload:
        try:
            days = int(payload["skip_recent_days"])
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "skip_recent_days must be an integer"}), 400
        db.set_skip_recent_days(days)
    return jsonify({"ok": True})


def create_app():
    db.init_db()
    return app


if __name__ == "__main__":
    # Prefer `python run.py` (loads .env first); this is a fallback for
    # running the module directly.
    create_app()
    app.run(host=config.HOST, port=config.FLASK_PORT, debug=config.IS_LOCAL_DEV)
