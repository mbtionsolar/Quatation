"""Run:  python -m unittest discover -s tests -v      (from the quotation_app folder)

Checks that bad input / failures give clear messages instead of crashing the server.
The real-PDF test is skipped automatically when LibreOffice is not installed.
"""
import importlib.util
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.update(SECRET_KEY="test-secret-" + "x" * 20, ADMIN_USERNAME="admin", ADMIN_PASSWORD="pw-12345",
                  PDF_WARMUP="0", LOG_LEVEL="CRITICAL")
os.environ.pop("RENDER", None)

import app as A  # noqa: E402
import pdfconv  # noqa: E402

FORM = {
    "project_type": "Commercial", "customer_name": "Test Customer", "customer_address": "Surat, Gujarat",
    "module_qty": "25", "module_wp": "615", "capacity_kw": "15.375", "module_make": "WAAREE",
    "inverter_make": "Solaryaan", "inverter_kw": "15", "acdb_make": "Havells", "dcdb_make": "Havells",
    "cable_make": "Polycab", "cable_ac_mm": "6", "cable_dc_mm": "4", "earth_make": "Polycab", "earth_mm": "6",
    "la_make": "Polytech", "structure_make": "Apollo", "structure_height": "6/4", "conduit_make": "Polycab",
    "pricing_mode": "total_to_rate", "total_cost": "523000", "warranty_panel": "WAAREE 615Wp",
    "warranty_inverter": "Solaryaan 15 KW", "contact_name": "JANAK", "contact_mobile": "8320565108",
    "terms": ["1", "2"],
}


def logged_in_client():
    c = A.app.test_client()
    c.get("/login")
    with c.session_transaction() as s:
        tok = s["csrf"]
    r = c.post("/login", data={"username": "admin", "password": "pw-12345", "csrf_token": tok})
    assert r.status_code == 302, r.status_code
    c.get("/quotation")
    with c.session_transaction() as s:
        return c, s["csrf"]


def _running(pid):
    """True if the process still runs. A killed process waiting to be reaped (zombie) counts as stopped."""
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False
    except OSError:                       # no /proc (macOS / Windows): fall back to a plain signal check
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


class Base(unittest.TestCase):
    def setUp(self):
        A._fails.clear()
        self.c, self.tok = logged_in_client()

    def post_form(self, **over):
        d = dict(FORM, csrf_token=self.tok)
        d.update(over)
        return self.c.post("/generate", data=d)


class HealthAndPages(Base):
    def test_healthz(self):
        self.assertEqual(self.c.get("/healthz").data, b"ok")

    def test_readyz_reports_checks(self):
        r = self.c.get("/readyz")
        self.assertIn(r.status_code, (200, 503))
        self.assertEqual(set(r.get_json()) - {"status"}, {"template", "pdf_engine", "login_configured"})

    def test_404_html_and_json(self):
        r = self.c.get("/nope")
        self.assertEqual(r.status_code, 404)
        self.assertIn(b"Page not found", r.data)
        r = self.c.post("/api/nope", headers={"X-CSRFToken": self.tok})
        self.assertEqual((r.status_code, r.is_json), (404, True))

    def test_favicon_and_robots(self):
        self.assertEqual(self.c.get("/favicon.ico").status_code, 200)
        self.assertEqual(self.c.get("/robots.txt").status_code, 200)

    def test_quotation_page_renders(self):
        self.assertEqual(self.c.get("/quotation").status_code, 200)


class Security(unittest.TestCase):
    def setUp(self):
        A._fails.clear()

    def test_non_ascii_csrf_does_not_crash(self):
        c = A.app.test_client()
        c.get("/login")
        r = c.post("/api/calc", json={}, headers={"X-CSRFToken": "टोकन"})
        self.assertEqual(r.status_code, 401)            # not logged in -> clear "session expired", not a 500
        self.assertTrue(r.get_json()["login"])

    def test_missing_csrf_on_login_redirects(self):
        r = A.app.test_client().post("/login", data={"username": "admin", "password": "x"})
        self.assertEqual(r.status_code, 302)

    def test_open_redirect_blocked(self):
        for bad in ("//evil.com", "/\\evil.com", "https://evil.com", "/ok\r\nX: y"):
            self.assertIsNone(A.safe_next(bad), bad)
        self.assertEqual(A.safe_next("/quotation"), "/quotation")

    def test_login_rate_limit(self):
        c = A.app.test_client()
        c.get("/login")
        with c.session_transaction() as s:
            tok = s["csrf"]
        codes = [c.post("/login", data={"username": "admin", "password": "bad", "csrf_token": tok}).status_code
                 for _ in range(7)]
        self.assertEqual(codes[:5], [200] * 5)
        self.assertEqual(codes[5:], [429, 429])
        A._fails.clear()

    def test_login_wrong_then_right(self):
        c = A.app.test_client()
        c.get("/login")
        with c.session_transaction() as s:
            tok = s["csrf"]
        r = c.post("/login", data={"username": "admin", "password": "bad", "csrf_token": tok})
        self.assertIn(b"Invalid username or password", r.data)
        r = c.post("/login?next=/quotation", data={"username": "admin", "password": "pw-12345", "csrf_token": tok})
        self.assertEqual((r.status_code, r.headers["Location"]), (302, "/quotation"))


class BadInput(Base):
    def test_weird_digits_in_terms_and_highlight(self):
        for field in ("terms", "highlight"):
            r = self.post_form(**{field: ["²", "٣", "9" * 5000, "-1"]})
            self.assertIn(r.status_code, (200, 400), field)
            self.assertTrue(r.is_json or r.mimetype == "application/pdf")

    def test_absurd_numbers_are_validation_errors(self):
        for cap in ("1e-30", "1e30", "nan", "inf", "-5", "0", "abc", "9" * 200):
            r = self.post_form(capacity_kw=cap)
            self.assertEqual(r.status_code, 400, cap)
            self.assertIn("capacity_kw", r.get_json()["errors"], cap)

    def test_weird_qty_and_mobile(self):
        r = self.post_form(module_qty="²", module_wp="١٢٣", contact_mobile="٠٠٠٠٠٠٠٠٠٠")
        self.assertEqual(r.status_code, 400)
        self.assertEqual({"module_qty", "module_wp", "contact_mobile"} - set(r.get_json()["errors"]), set())

    def test_no_terms_selected(self):
        r = self.post_form(terms=[])
        self.assertEqual(r.status_code, 400)
        self.assertIn("terms", r.get_json()["errors"])

    def test_calc_endpoint_bad_bodies(self):
        h = {"X-CSRFToken": self.tok}
        self.assertEqual(self.c.post("/api/calc", json=[1, 2], headers=h).status_code, 400)
        self.assertEqual(self.c.post("/api/calc", data="not json", headers=h).status_code, 400)
        r = self.c.post("/api/calc", json={"mode": "total_to_rate", "capacity_kw": "1e-30", "total_cost": "1000"}, headers=h)
        self.assertEqual(r.status_code, 200)
        self.assertIn("error", r.get_json())
        r = self.c.post("/api/calc", json={"mode": "total_to_rate", "capacity_kw": "15.375", "total_cost": "523000"}, headers=h)
        self.assertEqual(r.get_json()["total_cost"], "5,23,000")

    def test_request_too_large(self):
        r = self.c.post("/generate", data=b"x" * 70000,
                        headers={"X-CSRFToken": self.tok, "Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual((r.status_code, r.is_json), (413, True))


class Failures(Base):
    def test_pdf_engine_busy_gives_503_json(self):
        with mock.patch.object(A, "docx_to_pdf", side_effect=pdfconv.PdfError("The server is busy.")):
            r = self.post_form()
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.get_json()["error"], "The server is busy.")
        self.assertEqual(r.headers.get("Retry-After"), "5")

    def test_unexpected_crash_gives_json_with_ref(self):
        with mock.patch.object(A, "docx_to_pdf", side_effect=RuntimeError("boom")):
            r = self.post_form()
        self.assertEqual(r.status_code, 500)
        self.assertIn("ref:", r.get_json()["error"])
        self.assertNotIn("boom", r.get_json()["error"])           # internals never leak

    def test_missing_template(self):
        with mock.patch.object(A, "TEMPLATE", ROOT / "docx_template" / "missing.docx"):
            self.assertEqual(self.post_form().status_code, 500)
            self.assertIn("template", self.post_form().get_json()["error"].lower())
            page = self.c.get("/quotation")
            self.assertEqual(page.status_code, 500)
            self.assertIn(b"template", page.data.lower())
            self.assertEqual(self.c.get("/readyz").status_code, 503)

    def test_damaged_template(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "bad.docx"
            bad.write_bytes(b"this is not a zip file")
            with mock.patch.object(A, "TEMPLATE", bad):
                self.assertEqual(self.c.get("/quotation").status_code, 500)
                self.assertEqual(self.post_form().status_code, 500)

    def test_session_expired_on_generate_is_401_json(self):
        c = A.app.test_client()
        r = c.post("/generate", data={"x": "1"})
        self.assertEqual((r.status_code, r.is_json), (401, True))


class LoginNotConfigured(unittest.TestCase):
    def test_host_without_password_shows_setup_message(self):
        env = {k: v for k, v in os.environ.items() if k not in ("ADMIN_PASSWORD", "ADMIN_PASSWORD_HASH", "SECRET_KEY")}
        env["RENDER"] = "true"
        with mock.patch.dict(os.environ, env, clear=True):
            spec = importlib.util.spec_from_file_location("app_nopw", ROOT / "app.py")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        c = mod.app.test_client()
        r = c.get("/login")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"ADMIN_PASSWORD", r.data)
        with c.session_transaction() as s:
            tok = s["csrf"]
        r = c.post("/login", data={"username": "admin", "password": "admin123", "csrf_token": tok})
        self.assertEqual(r.status_code, 503)                        # default password must NOT work on a host
        self.assertEqual(c.get("/healthz").status_code, 200)        # deploy still passes the health check


class PdfEngine(unittest.TestCase):
    def test_busy_lock_gives_polite_error(self):
        with mock.patch.object(pdfconv, "SOFFICE", "/bin/true"), mock.patch.object(pdfconv, "LOCK_WAIT", 1):
            pdfconv._lock.acquire()
            try:
                with self.assertRaisesRegex(pdfconv.PdfError, "busy"):
                    pdfconv.docx_to_pdf(b"x")
            finally:
                pdfconv._lock.release()

    def test_timeout_kills_child_processes(self):
        with tempfile.TemporaryDirectory() as d:
            pidfile = Path(d) / "child.pid"
            fake = Path(d) / "fake-soffice"
            fake.write_text(f"#!/bin/sh\nsleep 60 &\necho $! > {pidfile}\nsleep 60\n")
            fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
            with mock.patch.object(pdfconv, "SOFFICE", str(fake)), mock.patch.object(pdfconv, "TIMEOUT", 2):
                with self.assertRaisesRegex(pdfconv.PdfError, "too long"):
                    pdfconv.docx_to_pdf(b"x")
            time.sleep(0.3)
            self.assertFalse(_running(int(pidfile.read_text())), "soffice child process was left running")

    def test_engine_missing(self):
        with mock.patch.object(pdfconv, "SOFFICE", None):
            with self.assertRaisesRegex(pdfconv.PdfError, "not installed"):
                pdfconv.docx_to_pdf(b"x")

    def test_garbage_output_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d) / "fake-soffice"
            fake.write_text('#!/bin/sh\nfor last; do :; done\nout=$(dirname "$last")\nhead -c 5000 /dev/zero > "$out/quotation.pdf"\n')
            fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
            with mock.patch.object(pdfconv, "SOFFICE", str(fake)):
                with self.assertRaisesRegex(pdfconv.PdfError, "failed"):
                    pdfconv.docx_to_pdf(b"x")


@unittest.skipUnless(pdfconv.available(), "LibreOffice not installed")
class RealPdf(Base):
    def test_full_quotation_pdf(self):
        r = self.post_form(customer_name="ગુજરાત Test / Customer", terms=["1", "2", "3"], highlight=["2"],
                           custom_term_1="Extra condition one", custom_hl_1="1")
        self.assertEqual(r.status_code, 200, r.data[:200])
        self.assertEqual(r.mimetype, "application/pdf")
        self.assertTrue(r.data.startswith(b"%PDF"))
        self.assertRegex(r.headers["Content-Disposition"], r'filename="?Quotation_Test_Customer_\d\d-\d\d-\d{4}\.pdf')


if __name__ == "__main__":
    unittest.main()
