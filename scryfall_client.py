#!/usr/bin/env python3
"""
scryfall_client.py
=======================================================================
Scryfall card-data fetching and local JSON caching, split out of
vibecodedCurveSim.py (see CHANGELOG.md) to keep that file focused on
the simulation engine itself. This module knows nothing about Card
objects, mana costs, or the turn loop -- it only ever hands back the
raw Scryfall JSON dict for a card name, using a local on-disk cache so
repeat runs don't hit the network for cards already seen.

Used by vibecodedCurveSim.py's `_build_card_lookup` (STEP 7), which
imports `load_cache`, `save_cache`, `fetch_card_data`, and `_emit`
directly from here.
"""
import json
import sys
import time
from pathlib import Path
from typing import Optional, Callable

try:
    import scrython
    from scrython.base import ScryfallError
except ImportError:
    # We only hard-fail on this once the user actually needs to hit the
    # network (see fetch_card_data). Importing scrython is deferred-ish
    # so that --help works even without the dependency installed, but
    # we still attempt the import up front to fail fast with a clear
    # message in the common case.
    scrython = None
    ScryfallError = Exception


def load_cache(cache_path: Path) -> dict:
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_cache(cache_path: Path, cache: dict) -> None:
    cache_path.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")


# Scryfall's documented limit is <10 requests/second. scrython does
# NOT enforce this itself -- an earlier version of this comment
# claimed it did; that was untested and wrong. Verified the hard way:
# a ~96-card decklist with no throttling blew through the limit in
# well under a second and got most of the deck (including basics and
# Sol Ring) rejected as 429s, which fetch_card_data was then
# misreporting as "card not found." SCRYFALL_MIN_REQUEST_INTERVAL
# keeps us safely under that limit; the retry/backoff below is a
# safety net for if we still get rate-limited anyway.
SCRYFALL_MIN_REQUEST_INTERVAL = 0.15   # ~6.6 req/s, comfortably under 10/s
SCRYFALL_RATE_LIMIT_BACKOFF = 60        # seconds -- matches Scryfall's own message
SCRYFALL_MAX_RETRIES = 1

_last_scryfall_request_time = 0.0


def _throttle_scryfall_request() -> None:
    """Block until SCRYFALL_MIN_REQUEST_INTERVAL seconds have passed
    since the last outbound Scryfall request."""
    global _last_scryfall_request_time
    elapsed = time.monotonic() - _last_scryfall_request_time
    if elapsed < SCRYFALL_MIN_REQUEST_INTERVAL:
        time.sleep(SCRYFALL_MIN_REQUEST_INTERVAL - elapsed)
    _last_scryfall_request_time = time.monotonic()


def _is_rate_limit_error(exc: Exception) -> bool:
    return "rate-limited" in str(exc).lower() or "rate limit" in str(exc).lower()


def _emit(message: str, log_fn: Optional[Callable[[str], None]] = None, file=None) -> None:
    """
    Send one line of progress/log output either to a caller-supplied
    callback or to `print()` (the CLI's normal behavior, `file`
    defaulting to stdout). `log_fn` is None everywhere except when the
    web app's background job thread passes one in, so it can push each
    line to a live per-job queue (for its SSE progress stream) instead
    of these functions writing directly to the process-wide stdout/
    stderr -- which matters because `contextlib.redirect_stdout`-style
    capture is NOT safe to use from a background thread (it patches
    the one global `sys.stdout` for the whole process, not just the
    calling thread).
    """
    if log_fn is not None:
        log_fn(message)
    else:
        print(message, file=file or sys.stdout)


def _scryfall_named(log_fn: Optional[Callable[[str], None]] = None, **kwargs):
    """
    One throttled `scrython.cards.Named` call. If Scryfall responds
    with an actual rate-limit error (as opposed to a genuine no-match),
    wait out SCRYFALL_RATE_LIMIT_BACKOFF and retry once before giving
    up -- distinguishing this from a real miss matters because the
    caller (fetch_card_data) falls back to a fuzzy-name search on any
    ScryfallError, which is the wrong response to a 429.
    """
    last_exc = None
    for attempt in range(SCRYFALL_MAX_RETRIES + 1):
        _throttle_scryfall_request()
        try:
            return scrython.cards.Named(**kwargs)
        except ScryfallError as exc:
            last_exc = exc
            if _is_rate_limit_error(exc) and attempt < SCRYFALL_MAX_RETRIES:
                _emit(f"  [fetch_card_data] Rate-limited by Scryfall; waiting "
                      f"{SCRYFALL_RATE_LIMIT_BACKOFF}s before retrying...", log_fn, file=sys.stderr)
                time.sleep(SCRYFALL_RATE_LIMIT_BACKOFF)
                continue
            raise
    raise last_exc


def fetch_card_data(name: str, cache: dict, verbose: bool = False,
                     log_fn: Optional[Callable[[str], None]] = None) -> Optional[dict]:
    """
    Return the raw Scryfall JSON dict for `name`, using the on-disk
    cache when possible and only hitting the network for cards we
    haven't seen before. Returns None if the card genuinely can't be
    found (NOT if we were rate-limited -- see _scryfall_named).

    Trusts `exact=`/`fuzzy=` results as-is (no extra name-matching
    validation here -- see CHANGELOG.md Pass 5). scrython's `exact=`
    search matches a card's `flavor_name` as well as its mechanical
    `name`, which is correct, intended Scryfall behavior: Universes
    Beyond crossovers reprint existing cards under new flavor names
    (e.g. "Minwu, Rebellion Strategist" is a Final Fantasy: Through
    the Ages flavor name for the mechanically-identical "Mangara, the
    Diplomat"), and `exact=` resolving that is the correct outcome,
    not a bug to guard against.

    `verbose` logs BOTH outcomes -- a cache hit and a fresh network
    fetch -- not just fresh fetches. A cache-hit-only skip here would
    make verbose mode go silent for any decklist that's been run
    before (i.e. nearly always, once `scryfall_cache.json` has built
    up), which defeats the point of a progress log.
    """
    cache_key = name.lower()
    if cache_key in cache:
        if verbose:
            _emit(f"  [fetch_card_data] {name!r} already cached, skipping network fetch", log_fn)
        return cache[cache_key]

    if scrython is None:
        raise RuntimeError(
            "The 'scrython' package is required to fetch card data. "
            "Install it with:  pip install scrython"
        )

    try:
        result = _scryfall_named(log_fn, exact=name)
    except ScryfallError:
        try:
            result = _scryfall_named(log_fn, fuzzy=name)  # fall back for typos/abbreviations
        except ScryfallError as exc:
            _emit(f"  [fetch_card_data] Could not find card {name!r} on "
                  f"Scryfall: {exc}", log_fn, file=sys.stderr)
            return None

    raw = dict(result._scryfall_data)
    cache[cache_key] = raw

    if verbose:
        _emit(f"  [fetch_card_data] Fetched {raw.get('name', name)!r} from Scryfall", log_fn)

    return raw
