"""Entry point: `python run.py` starts the server.

Locally this is http://127.0.0.1:5050. On a host like Render, PORT is
injected by the platform — config.HOST/config.FLASK_PORT pick that up
automatically (see backend/config.py), so nothing here needs to change
between environments.
"""
import os

from dotenv import load_dotenv

load_dotenv()

from backend.app import create_app  # noqa: E402
from backend import config  # noqa: E402

if __name__ == "__main__":
    app = create_app()

    # Print the raw env reads alongside what they resolved to, so a deploy
    # log makes it obvious *why* the server bound where it did — this is
    # what would have caught the last deploy running stale/unpushed code
    # immediately instead of needing a round trip to diagnose.
    print(
        f"[boot] env PORT={os.environ.get('PORT')!r} FLASK_PORT={os.environ.get('FLASK_PORT')!r} "
        f"APP_ENV={os.environ.get('APP_ENV')!r} -> IS_LOCAL_DEV={config.IS_LOCAL_DEV}"
    )
    print(f"Fresh Hop Tracker running at http://{config.HOST}:{config.FLASK_PORT} (this is what's passed to app.run)")

    # debug=True only for a confirmed local dev run — a public deploy must
    # not run Flask's interactive debugger (it allows arbitrary code
    # execution if ever hit). threaded=True so a long-running /api/refresh
    # doesn't block Today/Calendar page loads.
    app.run(host=config.HOST, port=config.FLASK_PORT, debug=config.IS_LOCAL_DEV, use_reloader=False, threaded=True)
