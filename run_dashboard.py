"""Entry point to run the Calendar Sync dashboard."""

import os
import sys
from pathlib import Path

# Ensure src/ is on the path (works both from project root and when installed)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

# Auto-load .env.dashboard if it exists and python-dotenv is available.
# This means you can just run `uv run python run_dashboard.py` without
# manually exporting every variable first.
_env_file = Path(__file__).parent / ".env.dashboard"
if _env_file.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_file, override=False)  # override=False: real env vars win
        print(f"[CONFIG] Loaded environment from {_env_file}")
    except ImportError:
        # dotenv not installed — fall back to manual parsing
        with open(_env_file) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                os.environ.setdefault(key, val)
        print(f"[CONFIG] Loaded environment from {_env_file} (manual parser)")
else:
    print(f"[CONFIG] No .env.dashboard found — using environment variables as-is.")
    print(f"[CONFIG] Copy .env.dashboard.example to .env.dashboard and fill in values.")

from dashboard import create_app

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    print(f"Starting Calendar Sync dashboard on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=debug, use_reloader=False)
