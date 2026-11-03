"""gunicorn settings (used by the Dockerfile and the Procfile).

ONE worker on purpose: PDF creation uses LibreOffice (heavy), and the free Render plan has 512 MB RAM.
Threads let several people use the form at the same time; PDFs are made one after another (see pdfconv.py).
"""
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"     # Render gives the port in $PORT
workers = 1
threads = 4
timeout = 120                  # must be longer than PDF_LOCK_WAIT + PDF_TIMEOUT (30 + 60)
graceful_timeout = 30
keepalive = 5
worker_tmp_dir = "/dev/shm" if os.path.isdir("/dev/shm") else None   # avoids false "worker timeout" in Docker
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info").lower()
