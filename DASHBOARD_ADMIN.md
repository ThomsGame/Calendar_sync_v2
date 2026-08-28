# Dashboard — Administrator Guide

This guide covers everything needed to install, configure, and operate the Calendar Sync dashboard as a server administrator.

---

## Table of contents

1. [Architecture overview](#1-architecture-overview)
2. [Requirements](#2-requirements)
3. [Installation](#3-installation)
4. [Google Cloud project setup](#4-google-cloud-project-setup)
5. [Configuration reference](#5-configuration-reference)
   - [.env.dashboard](#envdashboard)
   - [.env (CLI fallback)](#env-cli-fallback)
6. [First run](#6-first-run)
7. [Running in production](#7-running-in-production)
   - [systemd service](#systemd-service)
   - [Process manager (supervisor)](#process-manager-supervisor)
8. [Admin panel](#8-admin-panel)
9. [Database](#9-database)
10. [Security model](#10-security-model)
11. [Logs](#11-logs)
12. [Scheduler](#12-scheduler)
13. [Updating the application](#13-updating-the-application)
14. [Troubleshooting](#14-troubleshooting)

---

## 1. Architecture overview

```
Browser (user)
     │
     ▼
Flask app (run_dashboard.py)
     │
     ├── auth.py          — login / register / logout
     ├── setup.py         — 3-step onboarding wizard (Snexi → Constatimmo → Google OAuth)
     ├── views/
     │   ├── dashboard.py — home, manual sync trigger, status polling
     │   ├── settings.py  — update credentials and flags
     │   ├── history.py   — past sync runs and per-run event lists
     │   ├── email_drafts.py — list of Gmail drafts created
     │   └── admin.py     — admin-only: all users, all runs, trigger sync for any user
     │
     ├── sync_runner.py   — bridges DB credentials → calendar_sync engine
     ├── scheduler.py     — APScheduler: daily sync at 07:00 Paris time
     ├── crypto.py        — Fernet encryption/decryption for stored secrets
     └── models.py        — SQLAlchemy models (User, UserCredentials, SyncRun, SyncEvent, EmailDraft)
          │
          ▼
      SQLite (dashboard.db)
          │
          ▼
     calendar_sync engine  (scrapers → filters → Google Calendar API → Gmail API)
```

All user credentials (Snexi password, Constatimmo password, Google refresh token) are encrypted at rest with a Fernet key stored in `.env.dashboard`. The key is never sent to any external service.

---

## 2. Requirements

- Python **3.11+**
- [`uv`](https://github.com/astral-sh/uv) (recommended) or `pip`
- [Playwright](https://playwright.dev/) with the Chromium browser (for scraping)
- A **Google Cloud project** with Calendar and Gmail APIs enabled (see §4)
- A machine with network access to `snexi.fr` and `constatonline.constatimmo.com`

The dashboard has been tested on Debian 11 / Ubuntu 22.04 / WSL2.

---

## 3. Installation

```bash
# Clone the repository
git clone ssh://git@scm.clubmed.com:10022/... cal_sync
cd cal_sync

# Install Python dependencies
uv sync

# Install Playwright's Chromium browser
uv run playwright install chromium

# Copy and edit the dashboard config file
cp .env.dashboard.example .env.dashboard
nano .env.dashboard
```

See §5 for the full variable reference.

---

## 4. Google Cloud project setup

The dashboard uses OAuth 2.0. You need a **Web application** OAuth client (not Desktop — that is for the CLI tool).

### 4.1 Create a project (skip if one already exists)

1. Go to [console.cloud.google.com](https://console.cloud.google.com).
2. Click **Select a project → New Project**, give it a name, click **Create**.

### 4.2 Enable APIs

In the left menu → **APIs & Services → Library**, search for and enable:

- **Google Calendar API**
- **Gmail API**

### 4.3 Configure the OAuth consent screen

1. Go to **APIs & Services → OAuth consent screen**.
2. Choose **External** (unless your organisation uses Google Workspace and you want to restrict to internal users).
3. Fill in:
   - **App name** — e.g. "Calendar Sync"
   - **User support email** — your email
   - **Developer contact** — your email
4. Click **Save and Continue**.
5. On **Scopes**, click **Add or Remove Scopes** and add:
   - `https://www.googleapis.com/auth/calendar`
   - `https://www.googleapis.com/auth/gmail.compose`
   - `https://www.googleapis.com/auth/gmail.readonly`
   - `openid`
   - `https://www.googleapis.com/auth/userinfo.email`
6. Click **Save and Continue**.
7. On **Test users** (only required while the app is in Testing mode), add the Google accounts that will use the dashboard.
8. Click **Back to Dashboard**.

> To allow any Google account to sign in (not just test users), you need to publish the app. For internal use, staying in Testing mode with explicit test users is simpler and does not require Google's review.

### 4.4 Create OAuth credentials

1. Go to **APIs & Services → Credentials → Create Credentials → OAuth client ID**.
2. Application type: **Web application**.
3. Name: e.g. "Calendar Sync Dashboard".
4. Under **Authorised redirect URIs**, add the callback URL:
   - `http://localhost:5000/setup/google/callback` (for local/LAN use)
   - `https://yourdomain.com/setup/google/callback` (if publicly hosted)
5. Click **Create**. Copy the **Client ID** and **Client Secret** — you will need them in §5.

---

## 5. Configuration reference

### .env.dashboard

This is the main configuration file for the dashboard. Copy `.env.dashboard.example` and fill in the values.

```ini
# ── Flask session encryption ────────────────────────────────────────────────
# Random hex string used to sign session cookies.
# Generate with: python3 -c "import secrets; print(secrets.token_hex(32))"
FLASK_SECRET_KEY=<random 64-char hex>

# ── Fernet key for credential encryption ───────────────────────────────────
# Used to encrypt Snexi, Constatimmo, and Google refresh tokens in the DB.
# Generated automatically on first run if missing.
# WARNING: if you lose or change this key, all stored credentials become
# unreadable and every user must reconnect their accounts.
# Generate with: python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
DASHBOARD_SECRET_KEY=<Fernet key>

# ── Database ────────────────────────────────────────────────────────────────
# SQLite (default) or PostgreSQL (prefix: postgresql://user:pass@host/db)
DATABASE_URL=sqlite:///dashboard.db

# ── Bootstrap admin user ────────────────────────────────────────────────────
# Created automatically on first startup. Change the password immediately.
# Only read at startup; changing these values later has no effect.
ADMIN_EMAIL=admin@yourdomain.com
ADMIN_PASSWORD=ChangeMe123!

# ── Google OAuth ────────────────────────────────────────────────────────────
# From your Google Cloud Console — Web application OAuth client.
GOOGLE_CLIENT_ID=<client_id>.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-<secret>
GOOGLE_REDIRECT_URI=http://localhost:5000/setup/google/callback

# ── Server ──────────────────────────────────────────────────────────────────
FLASK_DEBUG=false
PORT=5000
```

### .env (CLI fallback)

If `.env` is present in the project root (the standard CLI configuration file), `run_dashboard.py` loads it as a fallback. `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET` from `.env` are automatically bridged to `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` if the dashboard vars are not set.

This means a machine that already runs the CLI tool can start the dashboard without duplicating the Google credentials.

---

## 6. First run

```bash
cd cal_sync
uv run python run_dashboard.py
```

On startup the following happens:

1. `.env.dashboard` is loaded, then `.env` as a fallback.
2. `GOOGLE_CLIENT_ID` is bridged from `GOOGLE_OAUTH_CLIENT_ID` if needed.
3. `DASHBOARD_SECRET_KEY` is validated. If missing or invalid, a new Fernet key is **generated and saved** back to `.env.dashboard`. This is safe on a fresh install but will break all stored credentials on an existing database — see §10.
4. The Flask app is created, all database tables are created (SQLite file is created if needed).
5. The admin user is created if `ADMIN_EMAIL` and `ADMIN_PASSWORD` are set and no user with that email exists yet.
6. The APScheduler starts the daily sync job at 07:00 Paris time.
7. Flask starts listening on `0.0.0.0:5000`.

Expected startup output:

```
[CONFIG] Loaded .env.dashboard
[CONFIG] Loaded .env (CLI config as fallback)
[CONFIG] DASHBOARD_SECRET_KEY OK
Starting Calendar Sync dashboard on http://localhost:5000
 * Serving Flask app 'dashboard'
 * Running on http://127.0.0.1:5000
 * Running on http://192.168.x.x:5000
```

Open `http://localhost:5000` (or the LAN IP shown) in a browser.

**First steps after starting:**
1. Log in with the `ADMIN_EMAIL` / `ADMIN_PASSWORD` you configured.
2. Change the admin password immediately — go to your browser's dev tools or use the SQLite CLI (see §9) since the dashboard has no password-change UI yet.
3. The admin account is marked `setup_complete=True` automatically and skips the wizard.

---

## 7. Running in production

Running `uv run python run_dashboard.py` directly is fine for development but the process dies when the shell session ends. Use a process manager for production.

### systemd service

Create `/etc/systemd/system/cal-sync-dashboard.service`:

```ini
[Unit]
Description=Calendar Sync Dashboard
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/path/to/cal_sync
ExecStart=/path/to/cal_sync/.venv/bin/python run_dashboard.py
Restart=on-failure
RestartSec=10

# Load environment from the project files
# (run_dashboard.py handles loading .env.dashboard and .env itself)
EnvironmentFile=-/path/to/cal_sync/.env.dashboard

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable cal-sync-dashboard
sudo systemctl start cal-sync-dashboard
sudo systemctl status cal-sync-dashboard
```

View logs:

```bash
sudo journalctl -u cal-sync-dashboard -f
```

### Process manager (supervisor)

If you prefer Supervisor, install it and create `/etc/supervisor/conf.d/cal_sync.conf`:

```ini
[program:cal_sync_dashboard]
command=/path/to/cal_sync/.venv/bin/python run_dashboard.py
directory=/path/to/cal_sync
autostart=true
autorestart=true
stderr_logfile=/var/log/cal_sync/dashboard.err.log
stdout_logfile=/var/log/cal_sync/dashboard.out.log
user=youruser
```

```bash
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl start cal_sync_dashboard
```

### Reverse proxy (nginx)

If the dashboard needs to be accessible over the internet or on a custom domain, put nginx in front:

```nginx
server {
    listen 80;
    server_name yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

Update `GOOGLE_REDIRECT_URI` in `.env.dashboard` to match the public URL and add that URI to the list of **Authorised redirect URIs** in Google Cloud Console.

---

## 8. Admin panel

The admin panel is at `/admin` and is only accessible to users with `is_admin=True`.

### Overview page (`/admin`)

Shows:

- A table of all registered users with their email, creation date, active status, and whether setup is complete.
- Global statistics: total runs, error runs, currently running syncs, total calendar events created.
- The 10 most recent error runs across all users.

### User detail (`/admin/user/<id>`)

Shows:

- The user's account status.
- Their stored credential status (username set? password set? Google connected?).
- Their last 50 sync runs with status and counts.
- A **Lancer une synchronisation** button to manually trigger a sync for that user (uses the server-side env vars for OAuth credentials, same as the scheduler).

### Run detail (`/admin/run/<id>`)

Shows every calendar event created or updated during that specific run, with date, time, source, address, and Google event ID.

### Granting admin rights

There is no UI for this. Use the SQLite CLI:

```bash
cd cal_sync
sqlite3 dashboard.db "UPDATE users SET is_admin=1 WHERE email='user@example.com';"
```

### Deactivating a user

```bash
sqlite3 dashboard.db "UPDATE users SET is_active=0 WHERE email='user@example.com';"
```

The user will not be able to log in, and the daily scheduler will skip them (it only syncs users where `is_active=1 AND setup_complete=1`).

---

## 9. Database

The default database is a SQLite file at `dashboard.db` in the project root.

### Schema summary

| Table | Description |
|---|---|
| `users` | User accounts (email, bcrypt hash, is_admin, is_active, setup_complete) |
| `user_credentials` | Per-user encrypted credentials and settings (1 row per user) |
| `sync_runs` | One row per sync execution (status, counts, error message) |
| `sync_events` | Individual calendar events from each run (linked to sync_runs) |
| `email_drafts` | Gmail drafts created during sync runs |

### Backup

```bash
# SQLite: copy the file
cp dashboard.db dashboard.db.bak

# Or use sqlite3's backup API
sqlite3 dashboard.db ".backup '/backups/dashboard_$(date +%Y%m%d).db'"
```

### Switching to PostgreSQL

Change `DATABASE_URL` in `.env.dashboard`:

```ini
DATABASE_URL=postgresql://user:password@localhost:5432/cal_sync
```

Install the driver:

```bash
uv add psycopg2-binary
```

The tables are created automatically by SQLAlchemy on startup.

---

## 10. Security model

### Credential encryption

Every sensitive value stored in the database is encrypted with **Fernet** (AES-128-CBC + HMAC-SHA256). The key is stored in `DASHBOARD_SECRET_KEY` inside `.env.dashboard`.

**Critical:** if `DASHBOARD_SECRET_KEY` is lost or changed:
- All stored Snexi passwords, Constatimmo passwords, and Google refresh tokens become unreadable.
- Every user will get a decryption error on their next sync.
- Recovery requires each user to go through the setup wizard again to re-enter their credentials.

**Protect the key:**
- Never commit `.env.dashboard` to version control (it is in `.gitignore`).
- Back up the file separately from the database.
- On a fresh install, if `DASHBOARD_SECRET_KEY` is missing, the app generates a new one and appends it to `.env.dashboard`. This is safe only if the database is also empty.

### Passwords

User account passwords are hashed with **bcrypt** (12 rounds). They are never stored in plain text.

### Google refresh tokens

The Google OAuth refresh token is what gives the app access to a user's calendar and Gmail. It is:
- Stored encrypted in `user_credentials.google_refresh_token_enc`.
- Used to obtain short-lived access tokens at sync time.
- Never logged or transmitted anywhere except to `oauth2.googleapis.com`.

Users can revoke access at any time via [myaccount.google.com/permissions](https://myaccount.google.com/permissions). A revoked token will cause the next sync to fail with an authentication error; the user then needs to reconnect Google from Settings.

### Network access

The dashboard makes outbound connections to:
- `accounts.google.com` — OAuth token exchange
- `oauth2.googleapis.com` — token refresh
- `www.googleapis.com` — Calendar and Gmail API calls
- `snexi.fr` — scraping (via Playwright/Chromium)
- `constatonline.constatimmo.com` — scraping

It does not accept inbound connections from the internet unless you put it behind a reverse proxy (see §7).

---

## 11. Logs

The application uses **loguru** for structured logging. All output goes to stdout.

Log levels:

| Level | Examples |
|---|---|
| `INFO` | Sync started/finished, events created, token refreshed |
| `WARNING` | Detail page not found (Snexi inline panel), token refresh retried |
| `ERROR` | Scraper login failed, Google API error, database error |
| `DEBUG` | Browser launched, frame detected |

When running under systemd, logs are captured by journald:

```bash
sudo journalctl -u cal-sync-dashboard -f
# Filter to errors only:
sudo journalctl -u cal-sync-dashboard -p err
```

When running directly or with supervisor, redirect stdout to a file and use `tail -f`.

---

## 12. Scheduler

The background scheduler (`APScheduler`) starts automatically with the Flask app and runs the daily sync at **07:00 Europe/Paris**.

### What it does

At 07:00 it:
1. Queries all users where `is_active=1 AND setup_complete=1`.
2. Calls `run_sync_for_user()` for each user sequentially.
3. Logs success or error per user.

Each user's sync runs in the same process thread. If one user's scraper hangs, it will block subsequent users. The default scraper timeout is 25 seconds per calendar frame.

### Changing the schedule time

Edit `src/dashboard/scheduler.py`:

```python
_scheduler.add_job(
    ...
    trigger=CronTrigger(hour=7, minute=0, timezone="Europe/Paris"),
    ...
)
```

Change `hour` and `minute` to any values (0–23, 0–59) and restart the dashboard.

### Checking the scheduler status

There is no UI for the scheduler. Check the logs for `[SCHEDULER]` lines:

```
[SCHEDULER] Background scheduler started — daily sync at 07:00 Paris time.
[SCHEDULER] Daily sync started for 2 users.
[SCHEDULER] Syncing user user@example.com ...
[SCHEDULER] User user@example.com → run_id=42
[SCHEDULER] Daily sync complete.
```

---

## 13. Updating the application

```bash
cd cal_sync
git pull origin main
uv sync                              # install any new dependencies
sudo systemctl restart cal-sync-dashboard
```

The database schema is managed by SQLAlchemy's `create_all()` — it creates new tables and columns that do not exist, but does **not** apply destructive migrations. If a model change drops a column, you need to handle that manually with `sqlite3`.

---

## 14. Troubleshooting

### Dashboard starts but users cannot log in

Check that `FLASK_SECRET_KEY` in `.env.dashboard` is set and not changing between restarts. If it changes, all session cookies are invalidated.

### "Fernet key invalid" error on startup

The `DASHBOARD_SECRET_KEY` in `.env.dashboard` is not a valid Fernet key. Either:
- Delete the line — the app will generate a new one (but **all stored credentials will become unreadable**).
- Restore the key from your backup.

### Sync fails for all users with "invalid_grant"

All Google refresh tokens have been revoked (e.g. the OAuth consent screen was unpublished, or a Google security event triggered revocation). Each user must reconnect their Google account.

### Admin panel returns 403

The logged-in user does not have `is_admin=1`. See §8 for the SQL command to grant admin rights.

### The scheduler is not running

Look for `[SCHEDULER] Background scheduler started` in the startup logs. If missing, the APScheduler may have failed to start. Common causes:
- The process was started with `FLASK_DEBUG=true` — in debug mode Flask uses a reloader which starts the process twice; the scheduler guard (`if _scheduler is not None: return`) prevents double-start, but in rare cases the reloader can interfere. Always run with `FLASK_DEBUG=false` in production.
- `apscheduler` is not installed — run `uv sync`.

### Port 5000 is already in use

Another process is using port 5000. Find and stop it:

```bash
ss -tlnp | grep 5000
# or:
fuser 5000/tcp
```

Or change the port in `.env.dashboard`:

```ini
PORT=8080
```

And update `GOOGLE_REDIRECT_URI` and your reverse proxy config to match.

### SQLite "database is locked" errors

This happens when multiple processes try to write to the SQLite file at the same time (e.g. the scheduler and a manual sync running simultaneously). The connection is configured with `check_same_thread=False` which handles thread-level access, but two separate processes would still conflict. Ensure only one instance of the dashboard runs at a time.

For higher concurrency, migrate to PostgreSQL (see §9).
