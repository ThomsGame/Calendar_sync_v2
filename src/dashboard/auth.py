"""Authentication routes: login, register, logout."""

from __future__ import annotations

import bcrypt
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from dashboard import get_db
from dashboard.models import User

bp = Blueprint("auth", __name__)


@bp.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.home"))
    return redirect(url_for("auth.login"))


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.home"))

    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        db = get_db()
        user = db.query(User).filter_by(email=email).first()

        if user and user.is_active and bcrypt.checkpw(password.encode(), user.password_hash.encode()):
            login_user(user, remember=request.form.get("remember") == "on")
            next_page = request.args.get("next")
            if not user.setup_complete:
                return redirect(url_for("setup.step1_snexi"))
            return redirect(next_page or url_for("dashboard.home"))
        else:
            error = "Email ou mot de passe invalide."

    return render_template("login.html", error=error)


@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.home"))

    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        if not email or not password:
            error = "Email et mot de passe requis."
        elif password != confirm:
            error = "Les mots de passe ne correspondent pas."
        elif len(password) < 8:
            error = "Le mot de passe doit contenir au moins 8 caractères."
        else:
            db = get_db()
            existing = db.query(User).filter_by(email=email).first()
            if existing:
                error = "Un compte avec cet email existe déjà."
            else:
                pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
                user = User(email=email, password_hash=pw_hash)
                db.add(user)
                db.commit()
                login_user(user)
                flash("Compte créé ! Configurez maintenant vos accès.", "success")
                return redirect(url_for("setup.step1_snexi"))

    return render_template("register.html", error=error)


@bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Vous êtes déconnecté.", "info")
    return redirect(url_for("auth.login"))
