"""
Nova — voice-driven action agent.

Vertical-scaling notes (see README.md for the full writeup):
  * A single `requests.Session` with a pooled HTTPAdapter reuses TCP/TLS
    connections across requests instead of opening a new one every call.
  * A short-TTL in-memory cache avoids re-scraping YouTube for repeat
    queries — the most common source of latency in this app.
  * A per-IP sliding-window limiter protects the process (and the
    outbound YouTube IP reputation) from being hammered by one client.
  * gunicorn_conf.py runs multiple threaded workers sized off CPU count,
    so the same code takes advantage of a bigger box without changes.
  * Structured logging + /health + /metrics give you what you need to
    actually watch the instance as you scale it up.
"""
import logging
import re
import time
import uuid

import requests
from flask import Flask, g, jsonify, render_template, request
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from commands import CommandError, list_commands, route_command
from config import Config
from utils.cache import TTLCache
from utils.rate_limit import RateLimiter

# ---------------------------------------------------------------------------
# App & logging
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.config.from_object(Config)

logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("nova")

# ---------------------------------------------------------------------------
# Shared resources: pooled HTTP session, cache, rate limiter
# ---------------------------------------------------------------------------

_session = requests.Session()
_retry = Retry(
    total=Config.HTTP_MAX_RETRIES,
    backoff_factor=0.3,
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=("GET",),
)
_adapter = HTTPAdapter(
    max_retries=_retry,
    pool_connections=Config.HTTP_POOL_MAXSIZE,
    pool_maxsize=Config.HTTP_POOL_MAXSIZE,
)
_session.mount("https://", _adapter)
_session.mount("http://", _adapter)

_yt_cache = TTLCache(max_items=Config.CACHE_MAX_ITEMS, ttl_seconds=Config.CACHE_TTL_SECONDS)
_limiter = RateLimiter(Config.RATE_LIMIT_MAX_REQUESTS, Config.RATE_LIMIT_WINDOW_SECONDS)

_START_TIME = time.monotonic()
_request_count = 0
_error_count = 0

_VIDEO_ID_RE = re.compile(r'"videoId":"([^"]+)"')


def get_video_id(query: str):
    """Look up a YouTube video id for a search query, cached and pooled."""
    cached = _yt_cache.get(query)
    if cached is not None:
        return cached or None

    try:
        resp = _session.get(
            "https://www.youtube.com/results",
            params={"search_query": query},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=Config.HTTP_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        ids = _VIDEO_ID_RE.findall(resp.text)
        vid = ids[0] if ids else None
    except requests.RequestException as exc:
        log.warning("YouTube lookup failed for %r: %s", query, exc)
        vid = None

    _yt_cache.set(query, vid or "")
    return vid


# ---------------------------------------------------------------------------
# Request lifecycle: correlation id + basic access logging
# ---------------------------------------------------------------------------

@app.before_request
def _start_timer():
    global _request_count
    _request_count += 1
    g.request_id = uuid.uuid4().hex[:8]
    g.start_time = time.monotonic()


@app.after_request
def _log_request(response):
    duration_ms = (time.monotonic() - g.get("start_time", time.monotonic())) * 1000
    log.info(
        "req_id=%s method=%s path=%s status=%s duration_ms=%.1f",
        g.get("request_id", "-"),
        request.method,
        request.path,
        response.status_code,
        duration_ms,
    )
    response.headers["X-Request-Id"] = g.get("request_id", "-")
    return response


def _client_key() -> str:
    # Respect a proxy-supplied header if present (typical in front of a
    # single vertically-scaled instance behind nginx/ALB), else fall back
    # to the direct peer address.
    fwd = request.headers.get("X-Forwarded-For")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr or "unknown"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/", methods=["GET"])
def home():
    return render_template("index.html")


@app.route("/api/commands", methods=["GET"])
def api_commands():
    """Backs the frontend's example-command chips, so the UI and the
    router can never drift out of sync."""
    return jsonify({"commands": list_commands()})


@app.route("/agent", methods=["POST"])
def ai_agent_router():
    global _error_count

    if Config.RATE_LIMIT_ENABLED:
        key = _client_key()
        if not _limiter.allow(key):
            retry_after = _limiter.retry_after(key)
            resp = jsonify({
                "success": False,
                "message": "Too many requests — please slow down a moment.",
            })
            resp.status_code = 429
            resp.headers["Retry-After"] = str(retry_after)
            return resp

    payload = request.get_json(silent=True)
    if not payload or ("command" not in payload and "text_command" not in payload):
        return jsonify({"success": False, "message": "Missing 'command' in request body."}), 400

    cmd_raw = payload.get("command") or payload.get("text_command")
    if not isinstance(cmd_raw, str) or not cmd_raw.strip():
        return jsonify({"success": False, "message": "Command must be a non-empty string."}), 400

    tz = payload.get("timezone") if isinstance(payload.get("timezone"), str) else None

    try:
        result = route_command(cmd_raw, tz, video_id_lookup=get_video_id)
        return jsonify(result.to_dict())
    except CommandError as exc:
        _error_count += 1
        log.info("command error for %r: %s", cmd_raw, exc)
        return jsonify({"success": False, "message": "I couldn't understand that command."}), 422
    except Exception:
        _error_count += 1
        log.exception("unhandled error routing command %r", cmd_raw)
        return jsonify({"success": False, "message": "Something went wrong on my end."}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "uptime_seconds": round(time.monotonic() - _START_TIME, 1)})


@app.route("/metrics", methods=["GET"])
def metrics():
    return jsonify({
        "uptime_seconds": round(time.monotonic() - _START_TIME, 1),
        "requests_total": _request_count,
        "errors_total": _error_count,
        "youtube_cache": _yt_cache.stats(),
    })


@app.errorhandler(404)
def not_found(_e):
    return jsonify({"success": False, "message": "Not found."}), 404


@app.errorhandler(500)
def server_error(_e):
    return jsonify({"success": False, "message": "Internal server error."}), 500


if __name__ == "__main__":
    app.run(host=Config.HOST, port=Config.PORT, debug=Config.DEBUG)
