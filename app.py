"""MBTION quotation generator - Flask app (login -> form -> PDF quotation -> logout).

Built to run on Render (Docker) and to fail politely: every error becomes a clear message for the
user (JSON for the form, a friendly page for the browser), and the full details go to the server log
together with a short reference id (ref) so a problem can be found quickly in Render -> Logs.
"""
import hashlib
import hmac
import io
import logging
import math
import os
import re
import secrets
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse

from flask import (Flask, flash, g, jsonify, redirect, render_template, request, send_file,
                   send_from_directory, session, url_for, abort)
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

try:  # optional: read settings from a .env file (local runs)
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ---------- logging (stdout -> Render "Logs") ----------
_level = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
logging.basicConfig(level=_level, stream=sys.stdout, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("quotation")

import pdfconv  # noqa: E402  (after logging is configured)
from docxfill import count_custom_slots, fill_docx, list_terms  # noqa: E402
from pdfconv import PdfError, docx_to_pdf  # noqa: E402
from quotation import (CAPACITY_MAX, CUSTOM_TERM_LEN, CUSTOM_TERMS_MAX, DEFAULTS, GST_PERCENT,  # noqa: E402
                       build_values, clean, compute_pricing, parse_amount, parse_custom_terms)

BASE = Path(__file__).resolve().parent
TEMPLATE = BASE / "docx_template" / "quotation_template.docx"
PDF_MIME = "application/pdf"
STATIC = BASE / "static"
# Render sets RENDER=true, Vercel sets VERCEL -> HTTPS only cookies + proxy headers are trusted there
ON_HOST = bool(os.environ.get("VERCEL") or os.environ.get("RENDER"))
IST = timezone(timedelta(hours=5, minutes=30))          # servers run in UTC; quotation dates should be Indian time
INT_RE = re.compile(r"^[0-9]{1,4}$")


# ---------- configuration (never crash on a bad setting - report it instead) ----------
def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        log.warning("%s has an invalid value, using %s", name, default)
        return default


ADMIN_USER = (os.environ.get("ADMIN_USERNAME") or "admin").strip() or "admin"
_pw = os.environ.get("ADMIN_PASSWORD", "")
_pw_hash = os.environ.get("ADMIN_PASSWORD_HASH", "").strip()
CONFIG_PROBLEMS = []          # shown on the login page so the owner knows what to fix in the Render dashboard

if _pw_hash:
    ADMIN_HASH = _pw_hash
    try:
        check_password_hash(ADMIN_HASH, "x")
    except (ValueError, TypeError):
        log.error("ADMIN_PASSWORD_HASH is not a valid werkzeug hash.")
        CONFIG_PROBLEMS.append("Login is not set up correctly: ADMIN_PASSWORD_HASH is invalid. "
                               "Use ADMIN_PASSWORD instead, or generate a new hash (see .env.example).")
        ADMIN_HASH = None
elif _pw:
    ADMIN_HASH = generate_password_hash(_pw)
elif ON_HOST:
    ADMIN_HASH = None
    log.error("ADMIN_PASSWORD is not set - login is disabled until it is added in the host's Environment settings.")
    CONFIG_PROBLEMS.append("Login is not set up yet. The site owner must add ADMIN_USERNAME and ADMIN_PASSWORD "
                           "in Render -> Environment, then redeploy.")
else:
    ADMIN_HASH = generate_password_hash("admin123")
    log.warning("Using default login admin / admin123 - set ADMIN_PASSWORD in .env before real use.")

_secret = (os.environ.get("SECRET_KEY") or "").strip()
if not _secret:
    _seed = _pw_hash or _pw
    if _seed:       # stable across restarts / free-plan sleep, so people are not logged out every time
        _secret = hmac.new(b"mbtion-quotation-session-v1", f"{ADMIN_USER}\0{_seed}".encode(), hashlib.sha256).hexdigest()
        log.warning("SECRET_KEY not set - using a key derived from the admin login. Set SECRET_KEY for best security.")
    else:
        _secret = secrets.token_hex(32)
        log.warning("SECRET_KEY not set - logins will reset every time the server restarts.")
elif len(_secret) < 16:
    log.warning("SECRET_KEY is very short - use a long random value.")

app = Flask(__name__)
app.config.update(
    SECRET_KEY=_secret,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE") == "1" or ON_HOST,   # HTTPS only
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    MAX_CONTENT_LENGTH=64 * 1024,
)
if ON_HOST or os.environ.get("TRUST_PROXY") == "1":
    # Render sits behind a proxy: use its headers for the real client IP / https
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)


def _asset_version():
    """Cache-busting value for CSS/JS links (changes whenever a static file changes)."""
    try:
        return int(max(p.stat().st_mtime for p in STATIC.rglob("*") if p.is_file() and p.suffix in (".css", ".js")))
    except (OSError, ValueError):
        return 1


app.jinja_env.globals["asset_v"] = os.environ.get("VERCEL_GIT_COMMIT_SHA", "")[:8] or _asset_version()


# ---------- Word template (validated, cached, never crashes a page) ----------
class TemplateProblem(Exception):
    """The Word template is missing or damaged."""


_tpl = {"mtime": None, "terms": [], "slots": 0}
_tpl_lock = threading.Lock()


def template_info():
    """(terms, extra-condition slots) read from the Word template; re-read when the file changes."""
    try:
        mtime = TEMPLATE.stat().st_mtime
    except OSError as exc:
        log.error("Word template not found at %s: %s", TEMPLATE, exc)
        raise TemplateProblem("missing") from exc
    with _tpl_lock:
        if _tpl["mtime"] != mtime:
            try:
                terms, slots = list_terms(TEMPLATE), count_custom_slots(TEMPLATE)
            except Exception as exc:
                log.exception("Word template could not be read")
                raise TemplateProblem("unreadable") from exc
            if not terms:
                log.error("Word template has no Terms & Conditions table")
                raise TemplateProblem("no terms")
            _tpl.update(mtime=mtime, terms=terms, slots=slots)
        return _tpl["terms"], _tpl["slots"]


# ---------- request lifecycle ----------
def wants_json():
    return request.path.startswith(("/generate", "/api/")) or request.is_json


@app.before_request
def begin_request():
    g.rid = uuid.uuid4().hex[:8]


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def csrf_protect():
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        sent = request.form.get("csrf_token") or request.headers.get("X-CSRFToken") or ""
        expected = session.get("csrf", "")
        # compare as bytes: str comparison raises TypeError on non-ASCII input
        if not expected or not secrets.compare_digest(sent.encode("utf-8", "ignore"), expected.encode()):
            abort(400, description="csrf")


@app.after_request
def secure_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    resp.headers.setdefault("Content-Security-Policy", (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; "
        "manifest-src 'self'; worker-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'self'; object-src 'none'"))
    if request.is_secure:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    if hasattr(g, "rid"):
        resp.headers.setdefault("X-Request-ID", g.rid)
    if not request.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-store"      # back button must not reveal pages after logout
    return resp


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
    """Only allow same-site relative paths after login (blocks //evil.com and /\\evil.com)."""
    if (target and target.startswith("/") and not target.startswith("//") and "\\" not in target
            and not re.search(r"[\x00-\x1f]", target) and not urlparse(target).netloc):
        return target
    return None


# ---------- friendly errors ----------
TITLES = {400: "Request problem", 401: "Please login", 403: "Not allowed", 404: "Page not found",
          405: "Not allowed", 408: "Request timed out", 413: "Request too large", 429: "Too many attempts",
          500: "Something went wrong", 502: "Server is busy", 503: "Temporarily unavailable"}
MESSAGES = {400: "The request could not be understood. Please go back and try again.",
            403: "You do not have permission to open this page.",
            404: "The page you are looking for does not exist.",
            405: "This action is not allowed here.",
            408: "The request took too long. Please try again.",
            413: "The data sent was too large. Please shorten the text and try again.",
            429: "Too many requests. Please wait a little and try again."}


def error_response(status, message, **extra):
    """JSON for the form / API calls, a friendly page for the browser. Never raises."""
    ref = getattr(g, "rid", None)
    if wants_json():
        resp = jsonify(error=message, **extra)
        resp.status_code = status
        if status in (429, 502, 503):
            resp.headers["Retry-After"] = "5"
        return resp
    try:
        home = url_for("quotation" if session.get("user") else "login")
        page = render_template("error.html", status=status, title=TITLES.get(status, "Error"), message=message,
                               ref=ref if (status >= 500 and ref and ref not in message) else None, home_url=home,
                               button="Back to quotation form" if session.get("user") else "Go to login")
        return page, status
    except Exception:       # even the error page must never fail
        log.exception("Could not render error page")
        return f"{TITLES.get(status, 'Error')}: {message}", status, {"Content-Type": "text/plain; charset=utf-8"}


@app.errorhandler(HTTPException)
def handle_http(exc):
    code = exc.code or 500
    if code == 400 and exc.description == "csrf":
        if wants_json():
            if not session.get("user"):
                return jsonify(error="Session expired. Please login again.", login=url_for("login")), 401
            return error_response(400, "Security token expired. Please refresh the page and try again.")
        flash("Your session expired. Please login again.", "warning")
        return redirect(url_for("login"))
    if code >= 500:
        log.error("[%s] HTTP %s on %s %s", g.get("rid"), code, request.method, request.path)
    return error_response(code, MESSAGES.get(code, "Something went wrong. Please try again."))


@app.errorhandler(TemplateProblem)
def handle_template(exc):
    log.error("[%s] Word template problem: %s", g.get("rid"), exc)
    return error_response(500, "The quotation Word template is missing or damaged. Please contact the administrator "
                               f"(ref: {g.get('rid')}).")


@app.errorhandler(Exception)
def handle_unexpected(exc):
    rid = g.get("rid", "-")
    log.exception("[%s] Unhandled error on %s %s", rid, request.method, request.path)
    return error_response(500, f"Something went wrong on the server. Please try again. (ref: {rid})")


# ---------- login rate limit (in memory; one worker on Render) ----------
MAX_FAILS, WINDOW = 5, 300
_fails, _fails_lock = {}, threading.Lock()


def _client_ip():
    return request.remote_addr or "unknown"


def _wait_seconds(ip):
    now = time.time()
    with _fails_lock:
        if len(_fails) > 2000:                      # keep memory bounded
            for k in [k for k, v in _fails.items() if not v or now - v[-1] > WINDOW]:
                _fails.pop(k, None)
        recent = [t for t in _fails.get(ip, []) if now - t < WINDOW]
        _fails[ip] = recent
        return int(WINDOW - (now - recent[0])) + 1 if len(recent) >= MAX_FAILS else 0


def _record_fail(ip):
    with _fails_lock:
        _fails.setdefault(ip, []).append(time.time())


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


@app.get("/favicon.ico")
def favicon():
    return send_from_directory(STATIC / "img", "favicon.png", mimetype="image/png")


@app.get("/robots.txt")
def robots():
    return "User-agent: *\nDisallow: /\n", 200, {"Content-Type": "text/plain"}


@app.get("/healthz")
def healthz():
    """Light check used by Render (must always answer fast)."""
    return "ok", 200, {"Content-Type": "text/plain", "Cache-Control": "no-store"}


@app.get("/readyz")
def readyz():
    """Deeper check you can open in a browser to see what is wrong after a deploy."""
    try:
        template_info()
        template_ok = True
    except TemplateProblem:
        template_ok = False
    checks = {"template": template_ok, "pdf_engine": pdfconv.available(), "login_configured": not CONFIG_PROBLEMS}
    ok = all(checks.values())
    return jsonify(status="ok" if ok else "degraded", **checks), (200 if ok else 503)


# ---------- pages ----------
@app.get("/")
def index():
    return redirect(url_for("quotation" if session.get("user") else "login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user"):
        return redirect(url_for("quotation"))
    status = 200
    if request.method == "POST":
        ip = _client_ip()
        wait = _wait_seconds(ip)
        if CONFIG_PROBLEMS or not ADMIN_HASH:
            status = 503
        elif wait:
            flash(f"Too many failed attempts. Please try again in {math.ceil(wait / 60)} minute(s).", "danger")
            status = 429
        else:
            user = request.form.get("username", "").strip()[:200]
            password = request.form.get("password", "")[:500]
            user_ok = secrets.compare_digest(user.encode(), ADMIN_USER.encode())
            try:
                pass_ok = check_password_hash(ADMIN_HASH, password)      # always checked -> no timing hint
            except (ValueError, TypeError):
                log.exception("Password check failed - is ADMIN_PASSWORD_HASH valid?")
                pass_ok = False
            if user_ok and pass_ok:
                session.clear()
                session["user"] = ADMIN_USER
                session.permanent = True
                with _fails_lock:
                    _fails.pop(ip, None)
                return redirect(safe_next(request.args.get("next")) or url_for("quotation"))
            _record_fail(ip)
            log.warning("Failed login from %s", ip)
            flash("Invalid username or password.", "danger")
    return render_template("login.html", config_problems=CONFIG_PROBLEMS), status


@app.get("/quotation")
@login_required
def quotation():
    terms, slots = template_info()
    return render_template("form.html", d=DEFAULTS, gst=GST_PERCENT, terms=terms,
                           custom_slots=min(CUSTOM_TERMS_MAX, slots), custom_len=CUSTOM_TERM_LEN)


@app.post("/api/calc")
@login_required
def api_calc():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="Invalid request."), 400
    mode = data.get("mode")
    if mode not in ("total_to_rate", "rate_to_total"):
        return jsonify(error="Invalid mode.")
    try:
        capacity = parse_amount(str(data.get("capacity_kw", "")), "Project capacity", hi=CAPACITY_MAX)
    except ValueError as exc:
        return jsonify(error=str(exc))
    out, errors = compute_pricing(mode, capacity, {k: str(v) for k, v in data.items()})
    return jsonify(error=next(iter(errors.values()))) if errors else jsonify(out)


def _ints(values, lo, hi):
    """Safe int() for form values (ASCII digits only), limited to lo..hi."""
    return sorted({int(x) for x in values if INT_RE.match(x or "") and lo <= int(x) <= hi})


@app.post("/generate")
@login_required
def generate():
    values, errors = build_values(request.form)
    terms, slots = template_info()
    chosen = _ints(request.form.getlist("terms"), 1, len(terms))
    custom_count = sum(1 for i in range(1, CUSTOM_TERMS_MAX + 1) if values.get(f"custom_term_{i}"))
    if not chosen and not custom_count:
        errors["terms"] = "Select at least one Terms & Conditions item or add an extra condition."
    if custom_count > slots:
        errors["custom_term_1"] = f"Only {slots} extra condition(s) fit in the Word template."
    if errors:
        return jsonify(errors=errors), 400
    wanted = set(_ints(request.form.getlist("highlight"), 1, len(terms)))
    custom_items, _ = parse_custom_terms(request.form)
    try:
        data = fill_docx(TEMPLATE, values, keep_terms=chosen, custom_count=custom_count,
                         highlight_terms=wanted & set(chosen),
                         highlight_custom={k for k, (_t, hl) in enumerate(custom_items, 1) if hl})
    except Exception as exc:
        log.exception("[%s] Filling the Word template failed", g.rid)
        raise TemplateProblem("fill failed") from exc
    try:
        pdf = docx_to_pdf(data)
    except PdfError as exc:
        log.warning("[%s] PDF conversion failed: %s", g.rid, exc)
        return error_response(503, str(exc))
    slug = re.sub(r"[^A-Za-z0-9]+", "_", clean(values["customer_name"])).strip("_")[:40] or "Customer"
    filename = f"Quotation_{slug}_{datetime.now(IST):%d-%m-%Y}.pdf"
    return send_file(io.BytesIO(pdf), as_attachment=True, download_name=filename, mimetype=PDF_MIME)


@app.post("/logout")
def logout():
    session.clear()
    return render_template("logout.html")


@app.get("/logout")
def logout_get():
    return redirect(url_for("index"))


# PDF engine warm-up (first LibreOffice start is slow on a small free server)
if os.environ.get("PDF_WARMUP", "1") == "1" and TEMPLATE.exists():
    pdfconv.warm_up(TEMPLATE)

if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=_env_int("PORT", 5000), debug=False)
