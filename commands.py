"""
Nova's command router.

Design goals (this is the piece that makes the assistant "actually
usable" instead of a demo that only handles two phrases):

1. Never dead-end. Every input produces a response — recognized
   commands do the right thing, everything else falls back to a live
   web search instead of a 400/500.
2. Two response "shapes":
     - {"type": "speak", "message": "..."}            -> spoken aloud,
       nothing opens (time, math, small talk, help).
     - {"type": "open", "message": "...", "url": "..."} -> spoken as a
       confirmation, then the URL opens in a new tab (YouTube, Gmail,
       search, common sites).
3. Easy to extend: add a (matcher, handler) pair to COMMANDS. Nothing
   else in the app needs to change — the frontend's command-chip list
   is even generated from this file via /api/commands.
"""
from __future__ import annotations

import ast
import operator
import re
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------

@dataclass
class CommandResult:
    type: str  # "speak" | "open"
    message: str
    url: Optional[str] = None

    def to_dict(self) -> dict:
        d = {"success": True, "type": self.type, "message": self.message}
        if self.url:
            d["url"] = self.url
        return d


class CommandError(Exception):
    """Raised when input is present but structurally invalid."""


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

_FILLER_PREFIXES = [
    # Wake-word prefixes only strip when something follows them — "hey
    # nova" on its own should still be treated as a greeting, not
    # stripped down to an empty command.
    r"^(hey|ok|okay|yo)\s+nova[,]?\s+(?=\S)",
    r"^nova[,]?\s+(?=\S)",
    r"^(please|could you|can you|would you)\s+(?=\S)",
    r"^(i want to|i'd like to|i need to)\s+(?=\S)",
]


def normalize(raw: str) -> str:
    cmd = raw.strip().lower()
    cmd = re.sub(r"\s+", " ", cmd)
    for pattern in _FILLER_PREFIXES:
        cmd = re.sub(pattern, "", cmd)
    return cmd.strip()


# ---------------------------------------------------------------------------
# Site shortcuts for "open <site>"
# ---------------------------------------------------------------------------

SITE_SHORTCUTS = {
    "google": "https://www.google.com",
    "maps": "https://maps.google.com",
    "google maps": "https://maps.google.com",
    "twitter": "https://twitter.com",
    "x": "https://x.com",
    "facebook": "https://www.facebook.com",
    "instagram": "https://www.instagram.com",
    "amazon": "https://www.amazon.com",
    "netflix": "https://www.netflix.com",
    "spotify": "https://open.spotify.com",
    "wikipedia": "https://www.wikipedia.org",
    "github": "https://github.com",
    "reddit": "https://www.reddit.com",
    "linkedin": "https://www.linkedin.com",
    "whatsapp": "https://web.whatsapp.com",
    "calendar": "https://calendar.google.com",
    "drive": "https://drive.google.com",
    "news": "https://news.google.com",
}

_SITE_ALTERNATION = "|".join(sorted((re.escape(k) for k in SITE_SHORTCUTS), key=len, reverse=True))
_OPEN_SITE_RE = re.compile(rf"^open (?:the )?({_SITE_ALTERNATION})$")


# ---------------------------------------------------------------------------
# Safe arithmetic ("what is 12 plus 8", "calculate 15% of 200")
# ---------------------------------------------------------------------------

_WORD_TO_SYMBOL = [
    (r"\bplus\b", "+"),
    (r"\bminus\b", "-"),
    (r"\btimes\b", "*"),
    (r"\bmultiplied by\b", "*"),
    (r"\bdivided by\b", "/"),
    (r"\bover\b", "/"),
]

_ALLOWED_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
}


def _safe_eval(expr: str) -> float:
    node = ast.parse(expr, mode="eval").body
    return _eval_node(node)


def _eval_node(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_OPS:
        return _ALLOWED_OPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_OPS:
        return _ALLOWED_OPS[type(node.op)](_eval_node(node.operand))
    raise CommandError("unsupported expression")


def _format_number(n: float) -> str:
    if float(n).is_integer():
        return str(int(n))
    return f"{n:.4g}"


def handle_math(cmd: str, tz: Optional[str]) -> CommandResult:
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*%\s*of\s*(-?\d+(?:\.\d+)?)", cmd)
    if m:
        pct, base = float(m.group(1)), float(m.group(2))
        result = (pct / 100) * base
        return CommandResult("speak", f"{_format_number(pct)}% of {_format_number(base)} is {_format_number(result)}.")

    m = re.search(r"(\d+(?:\.\d+)?)\s*percent of\s*(\d+(?:\.\d+)?)", cmd)
    if m:
        pct, base = float(m.group(1)), float(m.group(2))
        result = (pct / 100) * base
        return CommandResult("speak", f"{_format_number(pct)}% of {_format_number(base)} is {_format_number(result)}.")

    expr = cmd
    for pattern, symbol in _WORD_TO_SYMBOL:
        expr = re.sub(pattern, symbol, expr)
    expr = re.sub(r"[^0-9+\-*/().\s]", "", expr).strip()
    if not expr or not re.search(r"\d", expr):
        raise CommandError("no expression found")

    try:
        result = _safe_eval(expr)
    except ZeroDivisionError:
        return CommandResult("speak", "I can't divide by zero.")
    except Exception:
        raise CommandError("could not evaluate expression")

    return CommandResult("speak", f"That's {_format_number(result)}.")


# ---------------------------------------------------------------------------
# Time / date (uses the browser-reported IANA timezone when available)
# ---------------------------------------------------------------------------

def _now(tz: Optional[str]) -> datetime:
    if tz and ZoneInfo is not None:
        try:
            return datetime.now(ZoneInfo(tz))
        except Exception:
            pass
    return datetime.utcnow()


def handle_time(cmd: str, tz: Optional[str]) -> CommandResult:
    now = _now(tz)
    return CommandResult("speak", f"It's {now.strftime('%-I:%M %p')} right now.")


def handle_date(cmd: str, tz: Optional[str]) -> CommandResult:
    now = _now(tz)
    return CommandResult("speak", f"Today is {now.strftime('%A, %B %-d, %Y')}.")


# ---------------------------------------------------------------------------
# Small talk / help
# ---------------------------------------------------------------------------

def handle_greeting(cmd: str, tz: Optional[str]) -> CommandResult:
    return CommandResult("speak", "Hey there! What can I help you with?")


def handle_how_are_you(cmd: str, tz: Optional[str]) -> CommandResult:
    return CommandResult("speak", "Running smoothly, thanks for asking. What do you need?")


def handle_identity(cmd: str, tz: Optional[str]) -> CommandResult:
    return CommandResult("speak", "I'm Nova, your voice assistant. I can search YouTube, draft emails, open sites, do quick math, and more.")


def handle_thanks(cmd: str, tz: Optional[str]) -> CommandResult:
    return CommandResult("speak", "You're welcome!")


def handle_help(cmd: str, tz: Optional[str]) -> CommandResult:
    return CommandResult(
        "speak",
        "Try things like: play a song on YouTube, email someone, open google, "
        "what's 12 times 4, what time is it, or search for something.",
    )


# ---------------------------------------------------------------------------
# YouTube
# ---------------------------------------------------------------------------

_YOUTUBE_STRIP_PATTERNS = [
    "open youtube and search for",
    "open youtube and search",
    "open youtube and play",
    "search youtube for",
    "open youtube",
    "on youtube",
    "and play",
    "play",
]


def handle_youtube(cmd: str, tz: Optional[str], *, video_id_lookup: Callable[[str], Optional[str]]) -> CommandResult:
    q = cmd
    for phrase in _YOUTUBE_STRIP_PATTERNS:
        q = q.replace(phrase, " ")
    q = re.sub(r"\s+", " ", q).strip()

    if not q:
        raise CommandError("no search term given")

    vid = video_id_lookup(q)
    if not vid:
        # Graceful fallback: still useful even if scraping failed / was empty.
        search_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(q)}"
        return CommandResult("open", f"Here's what I found for {q} on YouTube.", search_url)

    target = f"https://www.youtube.com/embed/{vid}?autoplay=1&mute=1"
    return CommandResult("open", f"Playing {q}.", target)


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

_EMAIL_LEAD_RE = re.compile(r'^(please\s+)?(open\s+)?(gmail|email|mail|message)\s*')
_EMAIL_RECIP_LEAD_RE = re.compile(r'^(update\s+to|to|send\s+to|and\s+update\s+to)\s*')
_EMAIL_BODY_SPLIT_RE = re.compile(r'\b(type|write|saying|message|content|with body)\b')


def handle_email(cmd: str, tz: Optional[str]) -> CommandResult:
    clean = _EMAIL_LEAD_RE.sub("", cmd).strip()
    clean = re.sub(r"\bcom(a|and|mand)?\b", "com", clean)

    parts = _EMAIL_BODY_SPLIT_RE.split(clean)
    recip_part = parts[0].strip()
    recip_part = _EMAIL_RECIP_LEAD_RE.sub("", recip_part).strip()

    body = parts[-1].strip() if len(parts) > 1 else ""

    to = ""
    if recip_part:
        c = recip_part.replace(" at ", "@").replace(" dot ", ".").replace(" ", "")
        c = re.sub(r"[^a-zA-Z0-9@._%-]", "", c)
        to = c if "@" in c else f"{c}@gmail.com"

    base = "https://mail.google.com/mail/u/0/?view=cm&fs=1"
    params = urllib.parse.urlencode({"to": to, "body": body})
    target = f"{base}&{params}"
    label = f"Drafting an email to {to}." if to else "Opening a new email draft."
    return CommandResult("open", label, target)


# ---------------------------------------------------------------------------
# Generic "open <site>"
# ---------------------------------------------------------------------------

def handle_open_site(cmd: str, tz: Optional[str]) -> CommandResult:
    m = _OPEN_SITE_RE.match(cmd)
    if not m:
        raise CommandError("no matching site")
    site = m.group(1)
    url = SITE_SHORTCUTS[site]
    return CommandResult("open", f"Opening {site}.", url)


# ---------------------------------------------------------------------------
# Web search fallback
# ---------------------------------------------------------------------------

_SEARCH_LEAD_RE = re.compile(r"^(search( the web)?( for)?|google|look up|find)\s+")


def handle_search(cmd: str, tz: Optional[str]) -> CommandResult:
    q = _SEARCH_LEAD_RE.sub("", cmd).strip()
    if not q:
        raise CommandError("no search term")
    url = f"https://www.google.com/search?q={urllib.parse.quote(q)}"
    return CommandResult("open", f"Here's what I found for {q}.", url)


def handle_fallback(cmd: str, tz: Optional[str]) -> CommandResult:
    url = f"https://www.google.com/search?q={urllib.parse.quote(cmd)}"
    return CommandResult("open", "I don't have a specific action for that, so here's a search instead.", url)


# ---------------------------------------------------------------------------
# Registry — order matters, first match wins
# ---------------------------------------------------------------------------

@dataclass
class Command:
    name: str
    matcher: Callable[[str], bool]
    handler: Callable
    example: str
    description: str
    needs_lookup: bool = False


def _contains_word(cmd: str, *words: str) -> bool:
    return any(re.search(rf"\b{re.escape(w)}\b", cmd) for w in words)


COMMANDS: list[Command] = [
    Command(
        "youtube",
        lambda c: "youtube" in c,
        handle_youtube,
        "Open YouTube and play lo-fi beats",
        "Search and play a video on YouTube",
        needs_lookup=True,
    ),
    Command(
        "email",
        lambda c: bool(re.search(r"\b(gmail|email|mail)\b", c)),
        handle_email,
        "Email jane at gmail.com saying running late",
        "Draft a Gmail email to someone",
    ),
    Command(
        "open_site",
        lambda c: bool(_OPEN_SITE_RE.match(c)),
        handle_open_site,
        "Open GitHub",
        "Open a common site (Google, Maps, GitHub, ...)",
    ),
    Command(
        "search",
        lambda c: bool(_SEARCH_LEAD_RE.match(c)),
        handle_search,
        "Search for the nearest coffee shop",
        "Run a web search",
    ),
    Command(
        "math",
        lambda c: bool(re.search(r"\d", c)) and _contains_word(
            c, "plus", "minus", "times", "divided", "percent", "calculate", "what's", "what is"
        ),
        handle_math,
        "What is 18 times 4",
        "Quick arithmetic, spoken back to you",
    ),
    Command(
        "time",
        lambda c: _contains_word(c, "time") and not _contains_word(c, "timezone"),
        handle_time,
        "What time is it",
        "Current time in your timezone",
    ),
    Command(
        "date",
        lambda c: _contains_word(c, "date", "today"),
        handle_date,
        "What's today's date",
        "Current date",
    ),
    Command(
        "identity",
        lambda c: _contains_word(c, "your name") or "who are you" in c,
        handle_identity,
        "Who are you",
        "Learn what Nova can do",
    ),
    Command(
        "how_are_you",
        lambda c: "how are you" in c,
        handle_how_are_you,
        "How are you",
        "Small talk",
    ),
    Command(
        "help",
        lambda c: _contains_word(c, "help") or "what can you do" in c,
        handle_help,
        "What can you do",
        "List example commands",
    ),
    Command(
        "thanks",
        lambda c: _contains_word(c, "thanks", "thank"),
        handle_thanks,
        "Thanks Nova",
        "Say thanks",
    ),
    Command(
        "greeting",
        lambda c: _contains_word(c, "hello", "hi", "hey"),
        handle_greeting,
        "Hey Nova",
        "Say hello",
    ),
]


def route_command(raw_command: str, tz: Optional[str], *, video_id_lookup: Callable[[str], Optional[str]]) -> CommandResult:
    if not raw_command or not raw_command.strip():
        raise CommandError("empty command")

    cmd = normalize(raw_command)

    if not cmd:
        # Wake-word-only utterance (e.g. just "hey nova") — greet rather
        # than falling through to an empty search.
        return handle_greeting(cmd, tz)

    for entry in COMMANDS:
        try:
            if entry.matcher(cmd):
                if entry.needs_lookup:
                    return entry.handler(cmd, tz, video_id_lookup=video_id_lookup)
                return entry.handler(cmd, tz)
        except CommandError:
            continue

    return handle_fallback(cmd, tz)


def list_commands() -> list[dict]:
    return [{"example": c.example, "description": c.description} for c in COMMANDS]
