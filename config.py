"""
Central configuration for Nova.

Everything here is overridable via environment variables so the same
image/codebase can be tuned for a bigger box (more workers, bigger cache,
looser rate limits) without touching code — that's the "scale vertically"
lever: turn these knobs up as you move to a bigger instance.
"""
import os


def _bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


class Config:
    # --- Server ---
    HOST = os.environ.get("HOST", "0.0.0.0")
    PORT = _int("PORT", 8000)
    DEBUG = _bool("DEBUG", False)

    # --- External calls (YouTube scrape, etc.) ---
    HTTP_TIMEOUT_SECONDS = _int("HTTP_TIMEOUT_SECONDS", 6)
    HTTP_MAX_RETRIES = _int("HTTP_MAX_RETRIES", 2)
    HTTP_POOL_MAXSIZE = _int("HTTP_POOL_MAXSIZE", 50)

    # --- In-memory TTL cache for repeated lookups ---
    CACHE_TTL_SECONDS = _int("CACHE_TTL_SECONDS", 600)  # 10 minutes
    CACHE_MAX_ITEMS = _int("CACHE_MAX_ITEMS", 500)

    # --- Simple per-IP rate limiting (protects the single instance
    #     from being overwhelmed / from getting the outbound YouTube
    #     scrape IP-banned) ---
    RATE_LIMIT_ENABLED = _bool("RATE_LIMIT_ENABLED", True)
    RATE_LIMIT_MAX_REQUESTS = _int("RATE_LIMIT_MAX_REQUESTS", 30)
    RATE_LIMIT_WINDOW_SECONDS = _int("RATE_LIMIT_WINDOW_SECONDS", 60)

    # --- Logging ---
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
