"""Entry point to run the Calendar Sync dashboard."""

import os
import sys
from pathlib import Path

# Ensure src/ is on the path (works both from project root and when installed)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

_ROOT = Path(__file__).parent


def _load_env_file(path: Path) -> bool:
    """Load a .env file into os.environ (existing vars are never overwritten)."""
    if not path.exists():
        return False
    try:
        from dotenv import load_dotenv
        load_dotenv(path, override=False)
    except ImportError:
        # python-dotenv not installed — simple manual parser
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))
    return True


# 1. Load .env.dashboard first (dashboard-specific keys take priority)
if _load_env_file(_ROOT / ".env.dashboard"):
    print("[CONFIG] Loaded .env.dashboard")
else:
    print("[CONFIG] No .env.dashboard found — copy .env.dashboard.example to create one.")

# 2. Load .env as fallback (standalone CLI config — already on every machine)
if _load_env_file(_ROOT / ".env"):
    print("[CONFIG] Loaded .env (CLI config as fallback)")

# 3. Bridge variable name differences between .env and .env.dashboard.
#    .env uses GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET
#    .env.dashboard / app factory uses GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET
#    If the dashboard vars are still missing, copy them from the CLI vars.
_BRIDGE = {
    "GOOGLE_CLIENT_ID": "GOOGLE_OAUTH_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET": "GOOGLE_OAUTH_CLIENT_SECRET",
}
for dashboard_key, cli_key in _BRIDGE.items():
    if not os.environ.get(dashboard_key) and os.environ.get(cli_key):
        os.environ[dashboard_key] = os.environ[cli_key]
        print(f"[CONFIG] {dashboard_key} sourced from {cli_key} (.env)")

# 4. Validate DASHBOARD_SECRET_KEY early — generate one if missing/invalid.
#    crypto.get_fernet() does this lazily, but doing it here gives a clear
#    startup message before any request hits.
def _ensure_secret_key() -> None:
    from dashboard.crypto import get_fernet
    try:
        get_fernet()  # triggers auto-generation + persistence if needed
        print("[CONFIG] DASHBOARD_SECRET_KEY OK")
    except Exception as e:
        print(f"[CONFIG] DASHBOARD_SECRET_KEY error: {e}")

_ensure_secret_key()

from dashboard import create_app

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    print(f"Starting Calendar Sync dashboard on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=debug, use_reloader=False)
