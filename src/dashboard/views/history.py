"""Sync history: list of runs and per-run event detail."""

from __future__ import annotations

from flask import Blueprint, abort, render_template
from flask_login import current_user, login_required

from dashboard import get_db
from dashboard.models import SyncEvent, SyncRun

bp = Blueprint("history", __name__)


@bp.route("/")
@login_required
def index():
    db = get_db()
    runs = (
        db.query(SyncRun)
        .filter_by(user_id=current_user.id)
        .order_by(SyncRun.started_at.desc())
        .limit(100)
        .all()
    )
    return render_template("history.html", runs=runs)


@bp.route("/<int:run_id>")
@login_required
def detail(run_id: int):
    db = get_db()
    run: SyncRun | None = db.query(SyncRun).filter_by(id=run_id, user_id=current_user.id).first()
    if not run:
        abort(404)

    events = (
        db.query(SyncEvent)
        .filter_by(run_id=run_id)
        .order_by(SyncEvent.date, SyncEvent.start_time)
        .all()
    )

    return render_template("history_detail.html", run=run, events=events)
