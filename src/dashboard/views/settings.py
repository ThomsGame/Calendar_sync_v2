"""User settings: update credentials and feature flags."""

from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from dashboard import get_db
from dashboard.crypto import decrypt, encrypt
from dashboard.models import UserCredentials

bp = Blueprint("settings", __name__)


def _get_or_create_creds(user_id: int) -> UserCredentials:
    db = get_db()
    creds = db.query(UserCredentials).filter_by(user_id=user_id).first()
    if not creds:
        creds = UserCredentials(user_id=user_id)
        db.add(creds)
        db.commit()
    return creds


@bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    db = get_db()
    creds = _get_or_create_creds(current_user.id)

    if request.method == "POST":
        section = request.form.get("section")

        if section == "snexi":
            username = request.form.get("snexi_username", "").strip()
            password = request.form.get("snexi_password", "")
            if username:
                creds.snexi_username = username
            if password:
                creds.snexi_password_enc = encrypt(password)
            db.commit()
            flash("Identifiants Snexi mis à jour.", "success")

        elif section == "constatimmo":
            username = request.form.get("constatimmo_username", "").strip()
            password = request.form.get("constatimmo_password", "")
            if username:
                creds.constatimmo_username = username
            if password:
                creds.constatimmo_password_enc = encrypt(password)
            db.commit()
            flash("Identifiants Constatimmo mis à jour.", "success")

        elif section == "google":
            os_cal = request.form.get("google_calendar_os_id", "").strip()
            odm_cal = request.form.get("google_calendar_odm_id", "").strip()
            creds.google_calendar_os_id = os_cal or "primary"
            creds.google_calendar_odm_id = odm_cal or "primary"
            db.commit()
            flash("Paramètres Google Calendar mis à jour.", "success")

        elif section == "flags":
            creds.snexi_enrich_details = request.form.get("snexi_enrich") == "on"
            creds.constatimmo_enrich_details = request.form.get("constatimmo_enrich") == "on"
            creds.dry_run = request.form.get("dry_run") == "on"
            db.commit()
            flash("Options mises à jour.", "success")

        return redirect(url_for("settings.index"))

    # Mask stored passwords — show placeholder if set
    snexi_pw_set = bool(creds.snexi_password_enc)
    constatimmo_pw_set = bool(creds.constatimmo_password_enc)
    google_connected = bool(creds.google_refresh_token_enc)

    return render_template(
        "settings.html",
        creds=creds,
        snexi_pw_set=snexi_pw_set,
        constatimmo_pw_set=constatimmo_pw_set,
        google_connected=google_connected,
    )
