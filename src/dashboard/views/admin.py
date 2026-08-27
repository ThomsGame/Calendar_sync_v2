"""Admin panel: all users, sync runs, errors, system overview."""

from __future__ import annotations

from flask import Blueprint, abort, render_template, request
from flask_login import current_user, login_required

from dashboard import get_db
from dashboard.models import SyncEvent, SyncRun, User, UserCredentials

bp = Blueprint("admin", __name__)


def _require_admin():
    if not current_user.is_authenticated or not current_user.is_admin:
        abort(403)


@bp.route("/")
@login_required
def index():
    _require_admin()
    db = get_db()

    users = db.query(User).order_by(User.created_at.desc()).all()

    # Global stats
    total_runs = db.query(SyncRun).count()
    error_runs = db.query(SyncRun).filter_by(status="error").count()
    running_runs = db.query(SyncRun).filter(SyncRun.status.in_(["running", "pending"])).count()
    total_events = db.query(SyncEvent).count()

    # Recent errors across all users
    recent_errors = (
        db.query(SyncRun)
        .filter_by(status="error")
        .order_by(SyncRun.started_at.desc())
        .limit(10)
        .all()
    )

    return render_template(
        "admin/index.html",
        users=users,
        total_runs=total_runs,
        error_runs=error_runs,
        running_runs=running_runs,
        total_events=total_events,
        recent_errors=recent_errors,
    )


@bp.route("/user/<int:user_id>")
@login_required
def user_detail(user_id: int):
    _require_admin()
    db = get_db()

    user = db.get(User, user_id)
    if not user:
        abort(404)

    runs = (
        db.query(SyncRun)
        .filter_by(user_id=user_id)
        .order_by(SyncRun.started_at.desc())
        .limit(50)
        .all()
    )
    creds = db.query(UserCredentials).filter_by(user_id=user_id).first()

    return render_template("admin/user_detail.html", user=user, runs=runs, creds=creds)


@bp.route("/run/<int:run_id>")
@login_required
def run_detail(run_id: int):
    _require_admin()
    db = get_db()

    run = db.get(SyncRun, run_id)
    if not run:
        abort(404)

    events = (
        db.query(SyncEvent)
        .filter_by(run_id=run_id)
        .order_by(SyncEvent.date, SyncEvent.start_time)
        .all()
    )

    return render_template("admin/run_detail.html", run=run, events=events)


@bp.route("/trigger/<int:user_id>", methods=["POST"])
@login_required
def admin_trigger_sync(user_id: int):
    """Admin-triggered sync for a specific user."""
    import threading
    _require_admin()

    def _run():
        from dashboard.sync_runner import run_sync_for_user
        run_sync_for_user(user_id, trigger="admin")

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    from flask import flash, redirect, url_for
    flash(f"Synchronisation lancée pour l'utilisateur #{user_id}.", "success")
    return redirect(url_for("admin.user_detail", user_id=user_id))
