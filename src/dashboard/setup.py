"""Onboarding wizard: Snexi → Constatimmo → Google OAuth."""

from __future__ import annotations

import asyncio
import json
import os

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required
from google_auth_oauthlib.flow import Flow

from dashboard import get_db
from dashboard.crypto import encrypt
from dashboard.models import UserCredentials

bp = Blueprint("setup", __name__)

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.readonly",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]


def _get_or_create_credentials(user_id: int) -> UserCredentials:
    db = get_db()
    creds = db.query(UserCredentials).filter_by(user_id=user_id).first()
    if not creds:
        creds = UserCredentials(user_id=user_id)
        db.add(creds)
        db.commit()
    return creds


# ---------------------------------------------------------------------------
# Step 1 — Snexi credentials
# ---------------------------------------------------------------------------

@bp.route("/snexi", methods=["GET", "POST"])
@login_required
def step1_snexi():
    db = get_db()
    creds = _get_or_create_credentials(current_user.id)
    error = None

    if request.method == "POST":
        username = request.form.get("snexi_username", "").strip()
        password = request.form.get("snexi_password", "")

        if not username or not password:
            error = "Identifiant et mot de passe requis."
        else:
            creds.snexi_username = username
            creds.snexi_password_enc = encrypt(password)
            db.commit()
            flash("Identifiants Snexi enregistrés.", "success")
            return redirect(url_for("setup.step2_constatimmo"))

    return render_template(
        "setup/step1_snexi.html",
        creds=creds,
        error=error,
        step=1,
    )


@bp.route("/snexi/test", methods=["POST"])
@login_required
def test_snexi():
    """AJAX endpoint: test Snexi credentials without saving."""
    from dashboard.sync_runner import test_snexi_connection

    username = request.form.get("snexi_username", "").strip()
    password = request.form.get("snexi_password", "")

    if not username or not password:
        return jsonify({"ok": False, "message": "Identifiants manquants."})

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(test_snexi_connection(username, password))
    finally:
        loop.close()

    return jsonify(result)


# ---------------------------------------------------------------------------
# Step 2 — Constatimmo credentials
# ---------------------------------------------------------------------------

@bp.route("/constatimmo", methods=["GET", "POST"])
@login_required
def step2_constatimmo():
    db = get_db()
    creds = _get_or_create_credentials(current_user.id)
    error = None

    if request.method == "POST":
        username = request.form.get("constatimmo_username", "").strip()
        password = request.form.get("constatimmo_password", "")

        if not username or not password:
            error = "Identifiant et mot de passe requis."
        else:
            creds.constatimmo_username = username
            creds.constatimmo_password_enc = encrypt(password)
            db.commit()
            flash("Identifiants Constatimmo enregistrés.", "success")
            return redirect(url_for("setup.step3_google"))

    return render_template(
        "setup/step2_constatimmo.html",
        creds=creds,
        error=error,
        step=2,
    )


@bp.route("/constatimmo/test", methods=["POST"])
@login_required
def test_constatimmo():
    """AJAX endpoint: test Constatimmo credentials without saving."""
    from dashboard.sync_runner import test_constatimmo_connection

    username = request.form.get("constatimmo_username", "").strip()
    password = request.form.get("constatimmo_password", "")

    if not username or not password:
        return jsonify({"ok": False, "message": "Identifiants manquants."})

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(test_constatimmo_connection(username, password))
    finally:
        loop.close()

    return jsonify(result)


# ---------------------------------------------------------------------------
# Step 3 — Google OAuth
# ---------------------------------------------------------------------------

def _build_flow() -> Flow:
    client_id = current_app.config["GOOGLE_CLIENT_ID"]
    client_secret = current_app.config["GOOGLE_CLIENT_SECRET"]
    redirect_uri = current_app.config["GOOGLE_REDIRECT_URI"]

    client_config = {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }
    flow = Flow.from_client_config(client_config, scopes=GOOGLE_SCOPES)
    flow.redirect_uri = redirect_uri
    return flow


@bp.route("/google", methods=["GET", "POST"])
@login_required
def step3_google():
    db = get_db()
    creds = _get_or_create_credentials(current_user.id)

    if request.method == "POST":
        # Manual calendar ID entry (skip OAuth, useful for dev/testing)
        os_cal = request.form.get("google_calendar_os_id", "").strip()
        odm_cal = request.form.get("google_calendar_odm_id", "").strip()
        creds.google_calendar_os_id = os_cal or "primary"
        creds.google_calendar_odm_id = odm_cal or "primary"
        db.commit()

        from dashboard.models import User
        user = db.get(User, current_user.id)
        user.setup_complete = True
        db.commit()
        flash("Configuration terminée ! Vous pouvez lancer votre première synchronisation.", "success")
        return redirect(url_for("dashboard.home"))

    has_google = bool(current_app.config.get("GOOGLE_CLIENT_ID"))
    return render_template(
        "setup/step3_google.html",
        creds=creds,
        step=3,
        has_google_oauth=has_google,
    )


def _make_state(user_id: int, code_verifier: str) -> str:
    """Pack user_id + code_verifier into a signed, URL-safe state string.

    The state travels back from Google in the redirect URL, so both pieces
    of data survive even when the Flask session cookie is lost (e.g. browser
    accesses the server via LAN IP but the redirect comes back on 127.0.0.1).

    Format (base64url, no padding):
        base64url( "<user_id>|<code_verifier>" ) + "." + hmac_sig[:16]
    """
    import base64
    import hashlib
    import hmac as _hmac

    secret = current_app.config["SECRET_KEY"]
    if isinstance(secret, str):
        secret = secret.encode()

    payload = f"{user_id}|{code_verifier}"
    payload_b64 = base64.urlsafe_b64encode(payload.encode()).rstrip(b"=").decode()
    sig = _hmac.new(secret, payload_b64.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{payload_b64}.{sig}"


def _parse_state(state: str) -> tuple[int | None, str | None]:
    """Decode and verify a state string. Returns (user_id, code_verifier) or (None, None)."""
    import base64
    import hashlib
    import hmac as _hmac

    try:
        payload_b64, sig = state.rsplit(".", 1)
        secret = current_app.config["SECRET_KEY"]
        if isinstance(secret, str):
            secret = secret.encode()
        expected = _hmac.new(secret, payload_b64.encode(), hashlib.sha256).hexdigest()[:16]
        if not _hmac.compare_digest(sig, expected):
            return None, None
        # Re-pad and decode
        padding = 4 - len(payload_b64) % 4
        payload = base64.urlsafe_b64decode(payload_b64 + "=" * (padding % 4)).decode()
        user_id_str, verifier = payload.split("|", 1)
        return int(user_id_str), verifier
    except Exception:
        return None, None


@bp.route("/google/auth")
@login_required
def google_auth():
    """Redirect to Google OAuth consent screen."""
    if not current_app.config.get("GOOGLE_CLIENT_ID"):
        flash("Google OAuth non configuré sur ce serveur.", "danger")
        return redirect(url_for("setup.step3_google"))

    import base64
    import hashlib
    import secrets as _secrets

    # PKCE: generate verifier + SHA-256 challenge
    code_verifier = _secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    # Pack user_id + verifier into the state so the callback is session-independent
    state = _make_state(current_user.id, code_verifier)

    flow = _build_flow()
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        state=state,
        code_challenge=code_challenge,
        code_challenge_method="S256",
    )
    return redirect(auth_url)


@bp.route("/google/callback")
def google_callback():
    """Handle Google OAuth callback, store refresh token."""
    import os as _os
    _os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

    returned_state = request.args.get("state", "")

    # Decode user_id + code_verifier from the signed state — no session needed
    user_id, code_verifier = _parse_state(returned_state)

    if not user_id:
        flash("Session OAuth invalide. Recommencez.", "danger")
        return redirect(url_for("setup.step3_google"))

    flow = _build_flow()
    try:
        flow.fetch_token(
            authorization_response=request.url,
            state=returned_state,
            code_verifier=code_verifier,
        )
    except Exception as e:
        err = str(e)
        if "SSL" in err or "certificate" in err.lower():
            import urllib3, requests as _req
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            _s = _req.Session()
            _s.verify = False
            flow.fetch_token(
                authorization_response=request.url,
                state=returned_state,
                code_verifier=code_verifier,
                session=_s,
            )
        else:
            raise

    token_data = flow.credentials
    refresh_token = token_data.refresh_token

    db = get_db()
    creds = db.query(UserCredentials).filter_by(user_id=user_id).first()
    if not creds:
        creds = UserCredentials(user_id=user_id)
        db.add(creds)

    creds.google_refresh_token_enc = encrypt(refresh_token or "")
    db.commit()

    # Fetch user's calendars to let them pick (uses httplib2 which already has SSL disabled)
    try:
        import httplib2
        from google.oauth2.credentials import Credentials as GCredentials
        from googleapiclient.discovery import build

        gcreds = GCredentials(
            token=token_data.token,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=current_app.config["GOOGLE_CLIENT_ID"],
            client_secret=current_app.config["GOOGLE_CLIENT_SECRET"],
        )
        _http = httplib2.Http(disable_ssl_certificate_validation=True)
        service = build("calendar", "v3", credentials=gcreds, http=_http)
        calendar_list = service.calendarList().list().execute()
        calendars = [
            {"id": c["id"], "summary": c.get("summary", c["id"])}
            for c in calendar_list.get("items", [])
        ]
        session["google_calendars"] = calendars
    except Exception as e:
        flash(f"Connecté à Google, mais impossible de lister les calendriers: {e}", "warning")
        session["google_calendars"] = []

    flash("Compte Google connecté avec succès !", "success")
    return redirect(url_for("setup.step3_google"))


@bp.route("/google/calendars")
@login_required
def google_calendars_json():
    """Return list of user's Google Calendars as JSON (for dynamic select)."""
    calendars = session.get("google_calendars", [])
    return jsonify(calendars)


@bp.route("/skip")
@login_required
def skip_setup():
    """Allow skipping setup (mark complete) — useful for partial configs."""
    db = get_db()
    from dashboard.models import User
    user = db.get(User, current_user.id)
    user.setup_complete = True
    db.commit()
    flash("Configuration passée. Vous pourrez compléter vos accès dans les Paramètres.", "info")
    return redirect(url_for("dashboard.home"))
