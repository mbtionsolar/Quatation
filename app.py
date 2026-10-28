"""MBTION quotation generator - Flask app (login -> form -> PDF quotation -> logout)."""
import io
import os
import re
import secrets
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse

from flask import (Flask, abort, flash, jsonify, make_response, redirect, render_template, request,
                   send_file, send_from_directory, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

try:  # optional: read settings from a .env file
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from docxfill import count_custom_slots, fill_docx, list_terms
from pdfconv import PdfError, docx_to_pdf
from quotation import (CUSTOM_TERM_LEN, CUSTOM_TERMS_MAX, DEFAULTS, GST_PERCENT, build_values, clean,
                       compute_pricing, parse_amount, parse_custom_terms)

BASE = Path(__file__).resolve().parent
TEMPLATE = BASE / "docx_template" / "quotation_template.docx"
PDF_MIME = "application/pdf"
STATIC = BASE / "static"
# Vercel / Render always serve over HTTPS -> secure cookies switch on automatically there
ON_HOST = bool(os.environ.get("VERCEL") or os.environ.get("RENDER"))

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE") == "1" or ON_HOST,   # HTTPS only
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    MAX_CONTENT_LENGTH=64 * 1024,
)

ADMIN_USER = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_HASH = os.environ.get("ADMIN_PASSWORD_HASH") or generate_password_hash(
    os.environ.get("ADMIN_PASSWORD", "admin123"))
if not (os.environ.get("ADMIN_PASSWORD") or os.environ.get("ADMIN_PASSWORD_HASH")):
    print("WARNING: using default login admin / admin123 - set ADMIN_PASSWORD in .env before real use.")
if not os.environ.get("SECRET_KEY"):
    print("NOTE: SECRET_KEY not set - logins will reset every time the server restarts.")


def _asset_version():
    """Cache-busting value for CSS/JS links (changes whenever a static file changes)."""
    try:
        return int(max(p.stat().st_mtime for p in STATIC.rglob("*") if p.is_file() and p.suffix in (".css", ".js")))
    except (OSError, ValueError):
        return 1


app.jinja_env.globals["asset_v"] = os.environ.get("VERCEL_GIT_COMMIT_SHA", "")[:8] or _asset_version()

_terms_cache = {"mtime": None, "terms": []}


def get_terms():
    """Terms & Conditions text read from the Word template (re-read if the template is edited)."""
    mtime = TEMPLATE.stat().st_mtime
    if _terms_cache["mtime"] != mtime:
        _terms_cache.update(mtime=mtime, terms=list_terms(TEMPLATE))
    return _terms_cache["terms"]


# ---------- security helpers ----------
def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def csrf_protect():
    if request.method == "POST":
        sent = request.form.get("csrf_token") or request.headers.get("X-CSRFToken") or ""
        if not secrets.compare_digest(sent, session.get("csrf", "")):
            abort(400)


@app.after_request
def secure_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    resp.headers.setdefault("Content-Security-Policy", (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; "
        "manifest-src 'self'; worker-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'self'; object-src 'none'"))
    if not request.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-store"      # back button must not reveal pages after logout
    return resp


def wants_json():
    return request.path.startswith(("/generate", "/api/"))


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("user"):
            if wants_json():
                return jsonify(error="Session expired. Please login again.", login=url_for("login")), 401
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapper


def safe_next(target):
    if target and target.startswith("/") and not target.startswith("//") and not urlparse(target).netloc:
        return target
    return None


@app.errorhandler(400)
def bad_request(_e):
    if wants_json():
        return jsonify(error="Session expired or invalid request. Please refresh the page."), 400
    flash("Your session expired. Please login again.", "warning")
    return redirect(url_for("login"))


# ---------- PWA + health (no login needed) ----------
@app.get("/manifest.webmanifest")
def manifest():
    resp = send_from_directory(STATIC, "manifest.webmanifest", mimetype="application/manifest+json")
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


@app.get("/sw.js")
def service_worker():
    resp = send_from_directory(STATIC / "js", "sw.js", mimetype="application/javascript")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


@app.get("/healthz")
def healthz():
    return "ok", 200, {"Content-Type": "text/plain", "Cache-Control": "no-store"}


# ---------- pages ----------
@app.get("/")
def index():
    return redirect(url_for("quotation" if session.get("user") else "login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user"):
        return redirect(url_for("quotation"))
    if request.method == "POST":
        user = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user_ok = secrets.compare_digest(user.encode(), ADMIN_USER.encode())
        pass_ok = check_password_hash(ADMIN_HASH, password)      # always checked -> no timing hint
        if user_ok and pass_ok:
            session.clear()
            session["user"] = ADMIN_USER
            session.permanent = True
            return redirect(safe_next(request.args.get("next")) or url_for("quotation"))
        flash("Invalid username or password.", "danger")
    return render_template("login.html")


@app.get("/quotation")
@login_required
def quotation():
    return render_template("form.html", d=DEFAULTS, gst=GST_PERCENT, terms=get_terms(),
                           custom_slots=min(CUSTOM_TERMS_MAX, count_custom_slots(TEMPLATE)), custom_len=CUSTOM_TERM_LEN)


@app.post("/api/calc")
@login_required
def api_calc():
    data = request.get_json(silent=True) or {}
    mode = data.get("mode")
    try:
        capacity = parse_amount(str(data.get("capacity_kw", "")), "Project capacity")
    except ValueError as exc:
        return jsonify(error=str(exc))
    if mode not in ("total_to_rate", "rate_to_total"):
        return jsonify(error="Invalid mode.")
    out, errors = compute_pricing(mode, capacity, {k: str(v) for k, v in data.items()})
    return jsonify(error=next(iter(errors.values()))) if errors else jsonify(out)


@app.post("/generate")
@login_required
def generate():
    values, errors = build_values(request.form)
    total_terms = len(get_terms())
    chosen = sorted({int(x) for x in request.form.getlist("terms") if x.isdigit() and 1 <= int(x) <= total_terms})
    custom_count = sum(1 for i in range(1, CUSTOM_TERMS_MAX + 1) if values.get(f"custom_term_{i}"))
    if not chosen and not custom_count:
        errors["terms"] = "Select at least one Terms & Conditions item or add an extra condition."
    if errors:
        return jsonify(errors=errors), 400
    wanted = {int(x) for x in request.form.getlist("highlight") if x.isdigit()}
    custom_items, _ = parse_custom_terms(request.form)
    data = fill_docx(TEMPLATE, values, keep_terms=chosen, custom_count=custom_count,
                     highlight_terms=wanted & set(chosen),
                     highlight_custom={k for k, (_t, hl) in enumerate(custom_items, 1) if hl})
    try:
        pdf = docx_to_pdf(data)
    except PdfError as exc:
        app.logger.error("PDF conversion failed: %s", exc)
        return jsonify(error=str(exc)), 503
    slug = re.sub(r"[^A-Za-z0-9]+", "_", clean(values["customer_name"])).strip("_")[:40] or "Customer"
    filename = f"Quotation_{slug}_{datetime.now():%d-%m-%Y}.pdf"
    return send_file(io.BytesIO(pdf), as_attachment=True, download_name=filename, mimetype=PDF_MIME)


@app.post("/logout")
def logout():
    session.clear()
    return render_template("logout.html")


@app.get("/logout")
def logout_get():
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 5000)), debug=False)
