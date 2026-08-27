"""Entry point to run the Calendar Sync dashboard."""

import os
import sys

# Ensure src/ is on the path (works both from project root and when installed)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from dashboard import create_app

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    print(f"Starting Calendar Sync dashboard on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=debug, use_reloader=False)
