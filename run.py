"""Entry point: `python run.py` starts the server.

Locally this is http://127.0.0.1:5050. On a host like Render, PORT is
injected by the platform — config.HOST/config.FLASK_PORT pick that up
automatically (see backend/config.py), so nothing here needs to change
between environments.
"""
from dotenv import load_dotenv

load_dotenv()

from backend.app import create_app  # noqa: E402
from backend import config  # noqa: E402

if __name__ == "__main__":
    app = create_app()
    print(f"Fresh Hop Tracker running at http://{config.HOST}:{config.FLASK_PORT}")
    # debug=True only locally — a public deploy must not run Flask's
    # interactive debugger (it allows arbitrary code execution if ever hit).
    # threaded=True so a long-running /api/refresh doesn't block Today/Calendar page loads.
    is_local = config.HOST == "127.0.0.1"
    app.run(host=config.HOST, port=config.FLASK_PORT, debug=is_local, use_reloader=False, threaded=True)
