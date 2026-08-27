# SETUP_GUIDE.md — Full Test & Deployment Guide

This document is written for an agent or developer setting up and testing the
Calendar Sync project on a machine that has:
- A real browser (required for the Google OAuth first-time consent)
- Valid Snexi and Constatimmo credentials
- Access to the Google Cloud project (client_id / client_secret)

---

## 1. Prerequisites

- Python 3.11+
- `uv` package manager (`pip install uv` or see https://docs.astral.sh/uv/)
- Git
- Chromium or Chrome installed (Playwright will download its own copy)

---

## 2. Clone and install

```bash
git clone https://github.com/ThomsGame/Calendar_sync_v2.git
cd Calendar_sync_v2

# Create venv and install all dependencies
uv venv
uv pip install -e ".[dev]"

# Install Playwright's Chromium browser
uv run playwright install chromium
```

---

## 3. Configure credentials

```bash
cp .env.example .env
```

Edit `.env` with real values:

```dotenv
# Snexi
SNEXI_URL=https://snexi.fr/portail
SNEXI_USERNAME=<your_snexi_username>
SNEXI_PASSWORD=<your_snexi_password>
SNEXI_HEADLESS=true

# Constatimmo
CONSTATIMMO_URL=https://constatonline.constatimmo.com
CONSTATIMMO_USERNAME=<your_constatimmo_email>
CONSTATIMMO_PASSWORD=<your_constatimmo_password>
CONSTATIMMO_USER_DATA_DIR=./.browser/constatimmo
CONSTATIMMO_HEADLESS=true

# Google Calendar — OAuth2 user-consent flow
GOOGLE_CALENDAR_ID=primary
GOOGLE_OAUTH_CLIENT_ID=<your_google_oauth_client_id>
GOOGLE_OAUTH_CLIENT_SECRET=<your_google_oauth_client_secret>
GOOGLE_TOKEN_PATH=./token.json
GOOGLE_CALENDAR_OS_ID=primary
GOOGLE_CALENDAR_ODM_ID=primary
GOOGLE_COLOR_SNEXI=5
GOOGLE_COLOR_CONSTATIMMO=11

# Feature flags
DRY_RUN=false
SNEXI_ENRICH_DETAILS=true
CONSTATIMMO_ENRICH_DETAILS=true
```

---

## 4. Generate the Google OAuth token (one-time, requires browser)

This step must be done **once** on a machine with a real browser. The resulting
`token.json` is then reused for all future runs (it auto-refreshes).

```bash
uv run python -c "
from calendar_sync.config import load_settings
from calendar_sync.sync.google_calendar import _load_or_refresh_credentials
s = load_settings()
creds = _load_or_refresh_credentials(s)
print('Token OK:', creds.valid if creds else False)
"
```

What happens:
1. Script detects no `token.json` exists.
2. It opens a browser window at `accounts.google.com`.
3. Log in with the Google account that owns the target calendar.
4. Click **Allow** on the consent screen (Calendar + Gmail scopes).
5. `token.json` is saved to `./token.json` automatically.

If the browser cannot open automatically (headless server), the script prints a
URL. Open it manually, complete the login, and paste the authorization code back
when prompted.

**Important**: `token.json` contains a refresh token — keep it secret, do not
commit it to git (it is already in `.gitignore`).

---

## 5. Test scrapers in isolation

### 5a. Snexi

```bash
uv run python << 'EOF'
import asyncio
from loguru import logger
import sys
logger.remove()
logger.add(sys.stderr, format="{time:HH:mm:ss} | {level} | {message}", level="DEBUG")
from calendar_sync.config import load_settings
from calendar_sync.scrapers.base import BrowserManager
from calendar_sync.scrapers.snexi import login_snexi

async def test():
    s = load_settings()
    s = s.model_copy(update={"snexi_enrich_details": False})
    mgr = BrowserManager(headless=True)
    await mgr.launch()
    page = await mgr.new_page()
    events = await login_snexi(page, s)
    logger.info(f"Snexi: {len(events)} events extracted")
    for e in events[:5]:
        logger.info(f"  {e.text[:70]} | date={e.date} start={e.start_time}")
    await mgr.close()

asyncio.run(test())
EOF
```

**Expected**: 50–150 events extracted across 4 calendar weeks.

### 5b. Constatimmo

```bash
uv run python << 'EOF'
import asyncio, sys
from loguru import logger
logger.remove()
logger.add(sys.stderr, format="{time:HH:mm:ss} | {level} | {message}", level="DEBUG")
from calendar_sync.config import load_settings
from calendar_sync.scrapers.constatimmo import login_constatimmo

async def test():
    s = load_settings()
    s = s.model_copy(update={"constatimmo_enrich_details": False})
    events = await login_constatimmo(s)
    logger.info(f"Constatimmo: {len(events)} events extracted")
    for e in events[:5]:
        logger.info(f"  {e.text[:70]} | odm={e.odm_number}")

asyncio.run(test())
EOF
```

**Expected**: 5–20 ODM events (Sortie/Entrée ODM).

### 5c. Google Calendar read access

```bash
uv run python << 'EOF'
from calendar_sync.config import load_settings
from calendar_sync.sync.google_calendar import get_google_service
s = load_settings()
svc = get_google_service(s)
if svc:
    result = svc.events().list(calendarId='primary', maxResults=5).execute()
    print(f"Google Calendar OK — {len(result.get('items',[]))} recent events visible")
else:
    print("ERROR: could not build Google service")
EOF
```

**Expected**: "Google Calendar OK" with a count of events.

---

## 6. Run the full standalone sync

### Dry run first (nothing written to Google Calendar)

```bash
uv run calendar-sync --dry-run
```

Check the output for:
- `[SNEXI] N events after enrichment`
- `[CONSTATIMMO] N events fetched`
- `[FILTER] Kept: N`
- `[DRY_RUN] Would create: ...` lines for each event

### Real run

```bash
uv run calendar-sync
```

Check Google Calendar — events should appear with the correct color:
- Snexi OS events: color 5 (banana/yellow)
- Constatimmo ODM events: color 11 (tomato/red)

### Sync-only (re-sync from cache, no scraping)

```bash
uv run calendar-sync --sync-only
```

**Expected**: uses `appointments.cache.json` if less than 2 hours old.

---

## 7. Unit tests

```bash
uv run pytest tests/ -v
```

**Expected**: 20/20 passed.

---

## 8. Dashboard setup and test

### 8a. Generate dashboard secrets

```bash
# Flask session secret
python3 -c "import secrets; print(secrets.token_hex(32))"

# Fernet encryption key for stored credentials
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 8b. Create `.env.dashboard`

```bash
cp .env.dashboard.example .env.dashboard
```

Edit with real values:

```dotenv
FLASK_SECRET_KEY=<output of token_hex above>
DASHBOARD_SECRET_KEY=<output of Fernet.generate_key() above>
DATABASE_URL=sqlite:///dashboard.db
ADMIN_EMAIL=your_admin@email.com
ADMIN_PASSWORD=<strong_password>
GOOGLE_CLIENT_ID=<same as GOOGLE_OAUTH_CLIENT_ID in .env>
GOOGLE_CLIENT_SECRET=<same as GOOGLE_OAUTH_CLIENT_SECRET in .env>
GOOGLE_REDIRECT_URI=http://localhost:5000/setup/google/callback
FLASK_DEBUG=false
PORT=5000
```

### 8c. Load env and start dashboard

```bash
export $(grep -v '^#' .env.dashboard | xargs)
uv run python run_dashboard.py
```

Open `http://localhost:5000` in your browser.

### 8d. Dashboard route checklist

Verify each route works in the browser:

| Route | Expected |
|---|---|
| `GET /` | Redirects to `/login` |
| `GET /login` | Login form rendered |
| `GET /register` | Registration form rendered |
| `POST /register` | Creates account, redirects to `/setup/snexi` |
| `POST /login` (valid) | Redirects to `/dashboard` |
| `POST /login` (invalid) | Shows "Email ou mot de passe invalide." |
| `GET /dashboard` | Stats cards, "Synchroniser maintenant" button |
| `GET /setup/snexi` | Step 1 form with "Tester la connexion" button |
| `GET /setup/constatimmo` | Step 2 form with "Tester la connexion" button |
| `GET /setup/google` | Step 3 with OAuth button or manual calendar ID entry |
| `POST /setup/snexi/test` (AJAX) | Returns `{"ok": true, "message": "...", "event_count": N}` |
| `POST /setup/constatimmo/test` (AJAX) | Returns `{"ok": true, "message": "...", "event_count": N}` |
| `POST /sync/trigger` | Returns `{"ok": true}`, starts sync in background |
| `GET /sync/status` | Returns JSON with `status`, `events_created`, etc. |
| `GET /history/` | Table of sync runs |
| `GET /history/<run_id>` | Per-run event table |
| `GET /settings/` | All settings sections |
| `GET /drafts/` | Email draft log |
| `GET /admin/` (admin only) | Global stats, user table, error log |
| `GET /admin/user/<id>` (admin) | User detail with sync history |
| `POST /admin/trigger/<id>` (admin) | Triggers sync for that user |
| `GET /logout` | Logs out, redirects to `/login` |

### 8e. Test the connection test buttons

In the setup wizard:
1. Enter Snexi credentials and click **Tester la connexion**
   - Should show green: "Connexion réussie — N événements trouvés."
   - This actually runs the Playwright scraper (takes ~30s)
2. Enter Constatimmo credentials and click **Tester la connexion**
   - Same: should show N events found
3. Step 3 Google: enter calendar IDs manually or use OAuth

### 8f. Test a dashboard sync

1. Complete setup wizard
2. Go to Dashboard → click **Synchroniser maintenant**
3. The banner "Synchronisation en cours..." appears
4. After ~3 minutes (with enrichment), banner disappears and stats update
5. Check Google Calendar for new events

---

## 9. Email drafts

The email draft module (`src/calendar_sync/email/`) is currently a stub.
When the email drafting code is ported/added, it should:

1. Use `get_gmail_service(settings)` from `calendar_sync.sync.google_calendar`
2. Create drafts via `service.users().drafts().create(userId='me', body={...}).execute()`
3. Save `EmailDraft` records to the DB using the `run_id` from the current sync run

The dashboard `/drafts/` route is already wired — it just needs the DB records populated.

---

## 10. Known issues and limitations

| Issue | Status | Notes |
|---|---|---|
| Snexi detail enrichment (address, digicode etc.) | Intermittent | Inline panel timing — events are extracted correctly, enrichment is best-effort |
| `token.json` required on machine with browser | By design | Standard OAuth2 pattern; one-time setup |
| Constatimmo persistent browser context causes 403 | Fixed | Now uses fresh context per run |
| Google API SSL verification in corporate environments | Workaround | `httplib2` with `disable_ssl_certificate_validation=True` |
| APScheduler double-start in Flask debug mode | Fixed | `use_reloader=False` in `run_dashboard.py` |
| Email draft module | Stub | Placeholder routes exist; logic needs porting |

---

## 11. File structure reference

```
Calendar_sync_v2/
├── .env.example                  # Copy to .env, fill credentials
├── .env.dashboard.example        # Copy to .env.dashboard for Flask app
├── pyproject.toml                # Dependencies + entry points
├── run_dashboard.py              # Start the Flask dashboard
├── src/
│   ├── calendar_sync/            # Core automation engine
│   │   ├── config.py             # Settings loaded from .env
│   │   ├── main.py               # CLI entry point (calendar-sync)
│   │   ├── models/appointment.py # Pydantic Appointment model
│   │   ├── scrapers/
│   │   │   ├── base.py           # BrowserManager (Playwright)
│   │   │   ├── snexi.py          # Snexi portal scraper
│   │   │   └── constatimmo.py    # Constatimmo portal scraper
│   │   ├── filters/business.py   # Event classification + filtering
│   │   ├── sync/google_calendar.py # Google Calendar OAuth2 + sync
│   │   ├── utils/helpers.py      # Date/text utilities
│   │   └── email/                # Gmail draft module (stub)
│   └── dashboard/                # Flask web dashboard
│       ├── __init__.py           # App factory
│       ├── models.py             # SQLAlchemy DB models
│       ├── crypto.py             # Fernet credential encryption
│       ├── auth.py               # Login/register/logout routes
│       ├── setup.py              # 3-step onboarding wizard
│       ├── sync_runner.py        # Bridge: DB creds → sync engine
│       ├── scheduler.py          # APScheduler daily sync at 07:00
│       ├── views/
│       │   ├── dashboard.py      # Main view + sync trigger
│       │   ├── history.py        # Sync run history
│       │   ├── settings.py       # User settings
│       │   ├── admin.py          # Admin panel
│       │   └── email_drafts.py   # Email draft log
│       ├── templates/            # Jinja2 HTML templates
│       └── static/               # CSS + JS
└── tests/                        # Unit tests (20 passing)
    ├── test_filters.py
    ├── test_helpers.py
    └── test_models.py
```
