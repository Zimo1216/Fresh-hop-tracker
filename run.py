"""Entry point: `python run.py` starts the local server at http://127.0.0.1:5050"""
from dotenv import load_dotenv

load_dotenv()

from backend.app import create_app  # noqa: E402
from backend import config  # noqa: E402

if __name__ == "__main__":
    app = create_app()
    print(f"Fresh Hop Tracker running at http://127.0.0.1:{config.FLASK_PORT}")
    # threaded=True so a long-running /api/refresh doesn't block Today/Calendar page loads
    app.run(host="127.0.0.1", port=config.FLASK_PORT, debug=True, use_reloader=False, threaded=True)
