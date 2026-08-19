# Nova — Voice Assistant

A small Flask app that turns spoken (or typed) commands into actions: play
something on YouTube, draft a Gmail email, open a site, do quick math, tell
you the time — with the assistant speaking its replies back to you.

## What changed from the original version

**Frontend** — full redesign, split into `templates/index.html`,
`static/css/style.css`, `static/js/app.js` (previously one file with
inline `<style>`/`<script>`):
- Calmer, professional palette (single accent color, near-black surface)
  instead of the multi-color glow/blob look, with a light-theme toggle.
- A real audio visualizer: it uses `getUserMedia` + `AnalyserNode` to draw
  actual mic amplitude on a `<canvas>`, not a canned CSS loop.
- The assistant **talks back** via `SpeechSynthesis` — replies are spoken,
  not just opened as a tab.
- A typed-command fallback input, so it's usable even without mic access,
  without HTTPS, or in browsers without `SpeechRecognition` (Firefox, Safari).
- A conversation log, toast notifications for errors/rate-limits, a live
  backend-health dot, and command chips that are fetched from the backend
  (`/api/commands`) instead of hardcoded, so the UI can't drift out of sync
  with what the router actually supports.
- Push-to-talk via holding <kbd>Space</kbd>.

**Backend** — split into `app.py`, `commands.py`, `config.py`,
`utils/cache.py`, `utils/rate_limit.py`:
- **Fixed a crash**: the original `/agent` handler only set `msg`/`target`
  inside its `if`/`elif` branches, so any command that wasn't about YouTube
  or email raised an `UnboundLocalError` → 500. The router now always
  returns a response, falling back to a web search for anything it doesn't
  recognize.
- Recognizes many more intents: opening common sites, web search, math
  (including percentages), time/date (using the browser's IANA timezone),
  small talk, and a help command — see `commands.py`'s `COMMANDS` list.
- **Vertical-scaling groundwork** (see below).

## Running it

```bash
pip install -r requirements.txt

# Dev server
python app.py

# Production (multi-worker, see gunicorn_conf.py)
gunicorn -c gunicorn_conf.py app:app
```

Then open `http://localhost:8000`. Voice input requires Chrome/Edge and
either `localhost` or HTTPS (the browser's `SpeechRecognition` requirement,
not this app's) — the typed-input fallback works everywhere.

Copy `.env.example` to `.env` (or just export the variables) to tune any of
the settings below; every one has a working default.

## Scaling this vertically

The brief was to scale the backend *vertically* (get more out of one
instance) rather than horizontally (more instances), so:

| Lever | Where | Why |
|---|---|---|
| Threaded, multi-worker gunicorn | `gunicorn_conf.py` | Workers default to `min(2×cores+1, 8)`, threads default to 4. The slow path (YouTube scrape) is I/O-bound, so threads let one process serve many concurrent requests while some are waiting on the network — that's where a bigger box actually pays off. |
| Pooled HTTP connections | `app.py` (`requests.Session` + `HTTPAdapter`) | Reuses TCP/TLS connections across requests instead of a new handshake every call, plus automatic retries on transient 5xx/429. |
| In-memory TTL cache | `utils/cache.py` | Repeat voice queries are common ("play that again"); a 10-minute cache avoids re-scraping YouTube for the same query. Thread-safe, bounded size (LRU eviction). |
| Per-IP rate limiting | `utils/rate_limit.py` | Protects one process from being starved by a single client, and protects the outbound scrape from getting the box's IP rate-limited by YouTube. In-process by design — swap the dict for Redis if this ever needs to run as multiple replicas. |
| Worker recycling | `gunicorn_conf.py` (`max_requests`) | Bounds memory growth on a long-lived single instance. |
| Structured logging + `/health` + `/metrics` | `app.py` | You need visibility to know *when* to scale the box up; every request is logged with a duration and a correlation id, and `/metrics` exposes cache hit rate and request/error counts. |

All of the above are env-var tunable (`.env.example`) so the same code runs
comfortably on a small box or a large one without edits.

## Supported commands

Ask for `GET /api/commands` for the live list (also what powers the chips
in the UI), or see `commands.py`. Examples:

- "Open YouTube and play \<song\>"
- "Email \<name\> at gmail.com saying \<message\>"
- "Open github" / "open google maps" / "open twitter" / …
- "Search for \<anything\>"
- "What's 18 times 4" / "what's 15 percent of 200"
- "What time is it" / "what's today's date"
- "What can you do" / "who are you"
- Anything else falls back to a Google search instead of failing.
