"""
gunicorn config for running Nova on a single, vertically-scaled instance.

Run with:
    gunicorn -c gunicorn_conf.py app:app

Threaded workers (not sync/single-threaded) are the right choice here
because the app's slow path — the YouTube lookup — is I/O bound
(network wait, not CPU). Threads let one worker process serve many
concurrent requests while some of them are blocked on that network
call, so you get real concurrency gains from more CPU/RAM on the box
without needing multiple machines.

Everything is tunable via env vars so you can turn the dial up as you
move to a bigger instance, e.g.:
    WEB_CONCURRENCY=8 THREADS=8 gunicorn -c gunicorn_conf.py app:app
"""
import multiprocessing
import os

_cpu_count = multiprocessing.cpu_count()

# workers: CPU-bound rule of thumb is (2 * cores) + 1. We cap it because
# this app is mostly I/O bound (threads handle that concurrency), so we
# don't need as many separate processes as a CPU-heavy service would.
workers = int(os.environ.get("WEB_CONCURRENCY", min((2 * _cpu_count) + 1, 8)))

# threads per worker: handles concurrent I/O-bound requests (the YouTube
# scrape) without needing a process per in-flight request.
threads = int(os.environ.get("THREADS", 4))

worker_class = "gthread"
worker_connections = 1000

bind = f"{os.environ.get('HOST', '0.0.0.0')}:{os.environ.get('PORT', 8000)}"

timeout = int(os.environ.get("GUNICORN_TIMEOUT", 30))
graceful_timeout = 20
keepalive = 5

# Recycle workers periodically to bound memory growth over long uptimes
# on a single long-lived instance.
max_requests = int(os.environ.get("MAX_REQUESTS", 2000))
max_requests_jitter = int(os.environ.get("MAX_REQUESTS_JITTER", 200))

accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info").lower()
