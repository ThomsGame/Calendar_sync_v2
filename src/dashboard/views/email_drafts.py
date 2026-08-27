"""Email drafts view — shows Gmail drafts created during sync runs."""

from __future__ import annotations

from flask import Blueprint, render_template
from flask_login import current_user, login_required

from dashboard import get_db
from dashboard.models import EmailDraft

bp = Blueprint("drafts", __name__)


@bp.route("/")
@login_required
def index():
    db = get_db()
    drafts = (
        db.query(EmailDraft)
        .filter_by(user_id=current_user.id)
        .order_by(EmailDraft.created_at.desc())
        .limit(100)
        .all()
    )
    return render_template("email_drafts.html", drafts=drafts)
