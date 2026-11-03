"""Word (.docx) bytes -> PDF bytes using headless LibreOffice.

The design is still made in Word (docx_template/quotation_template.docx); LibreOffice only
prints it to PDF, so layout, fonts, colours and tables stay the same.
Needs `soffice` on the server (the Dockerfile installs it).

Hardened for small hosts (Render free plan):
  * only one LibreOffice runs at a time (low memory); a request that has to wait gives up politely
  * LibreOffice runs in its own process group, so a timeout kills soffice.bin too (no orphans eating RAM)
  * the result must really be a PDF, otherwise a clear error is raised
"""
import logging
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path

log = logging.getLogger("quotation.pdf")


def _env_int(name, default, lo=1, hi=600):
    try:
        return max(lo, min(hi, int(os.environ.get(name, default))))
    except (TypeError, ValueError):
        log.warning("%s has an invalid value, using %s", name, default)
        return default


SOFFICE = shutil.which("soffice") or shutil.which("libreoffice")
TIMEOUT = _env_int("PDF_TIMEOUT", 60)            # seconds LibreOffice may take for one PDF
LOCK_WAIT = _env_int("PDF_LOCK_WAIT", 30)        # seconds a request waits for the previous PDF to finish
_lock = threading.Lock()


class PdfError(RuntimeError):
    """Message is written for the end user (safe to show)."""


def available():
    return SOFFICE is not None


def _kill_tree(proc):
    """Kill LibreOffice and everything it started (soffice is a wrapper script around soffice.bin)."""
    try:
        if hasattr(os, "killpg"):
            os.killpg(proc.pid, signal.SIGKILL)
        else:                                    # Windows (local testing)
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _convert(data):
    with tempfile.TemporaryDirectory(prefix="quote_") as tmp:
        tmp = Path(tmp)
        src = tmp / "quotation.docx"
        src.write_bytes(data)
        profile = (tmp / "profile").as_uri()
        cmd = [SOFFICE, f"-env:UserInstallation={profile}", "--headless", "--norestore", "--nolockcheck",
               "--convert-to", "pdf:writer_pdf_Export", "--outdir", str(tmp), str(src)]
        env = dict(os.environ, HOME=str(tmp), SAL_USE_VCLPLUGIN="svp")
        extra = {"start_new_session": True} if os.name == "posix" else {}
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, **extra)
        except OSError as exc:
            log.error("Could not start LibreOffice: %s", exc)
            raise PdfError("PDF engine could not be started on this server. Please try again in a minute.")
        try:
            try:
                _out, err = proc.communicate(timeout=TIMEOUT)
            except subprocess.TimeoutExpired:
                _kill_tree(proc)
                proc.communicate()
                log.error("LibreOffice timed out after %ss", TIMEOUT)
                raise PdfError("PDF creation took too long. Please try again.")
        finally:
            _kill_tree(proc)                     # also clears a soffice.bin that outlived its launcher
        out = tmp / "quotation.pdf"
        if proc.returncode != 0 or not out.exists():
            log.error("LibreOffice failed (code %s): %s", proc.returncode, (err or b"")[-500:].decode("utf-8", "replace"))
            raise PdfError("PDF creation failed. Please try again.")
        pdf = out.read_bytes()
        if len(pdf) < 1000 or not pdf.startswith(b"%PDF"):
            log.error("LibreOffice produced an invalid PDF (%d bytes)", len(pdf))
            raise PdfError("PDF creation failed. Please try again.")
        return pdf


def docx_to_pdf(data: bytes) -> bytes:
    if not SOFFICE:
        raise PdfError("PDF engine (LibreOffice) is not installed on this server.")
    if not _lock.acquire(timeout=LOCK_WAIT):
        raise PdfError("The server is busy making another quotation. Please try again in a few seconds.")
    try:
        t0 = time.monotonic()
        pdf = _convert(data)
        log.info("PDF ready: %d KB in %.1fs", len(pdf) // 1024, time.monotonic() - t0)
        return pdf
    finally:
        _lock.release()


def warm_up(template_path):
    """Convert the template once in the background so the first real quotation is not slow."""
    def run():
        try:
            docx_to_pdf(Path(template_path).read_bytes())
            log.info("PDF engine warm-up done")
        except Exception as exc:                # never let warm-up break the app
            log.warning("PDF engine warm-up skipped: %s", exc)
    if available():
        threading.Thread(target=run, name="pdf-warmup", daemon=True).start()
