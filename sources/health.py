"""
sources/health.py — honest per-poll source status.

Sources used to swallow every network error and return [], so a source
that was blocked (403), rate-limited (429) or timing out looked identical
to "no new listings" — and was recorded as a success.

Now each source records every request it makes through `fetch()` (or
`record()` for non-HTTP work), and `classify()` turns the outcomes into:

    healthy        requests succeeded and parsed (possibly 0 listings)
    degraded       some requests failed, responses were malformed, or a
                   source has returned nothing for many polls in a row
    blocked        403 / challenge page / proxy refusal on every request
    rate_limited   429 on every failing request
    timeout        every request timed out
    auth_config    credentials missing or rejected (401, bad IMAP login)
    disabled       turned off in settings
    unverified     never produced a listing yet, so the parser is unproven

`healthy` describes this poll only. A scheduler interval is not a
discovery-latency guarantee: how fast a listing is seen depends on the
source publishing it and on per-source polling budgets.
"""
from __future__ import annotations

import re
import threading
from typing import Optional

import requests

OK, EMPTY, BLOCKED, RATE_LIMITED, TIMEOUT = "ok", "empty", "blocked", "rate_limited", "timeout"
NETWORK, HTTP_ERROR, MALFORMED, AUTH = "network", "http_error", "malformed", "auth"

HEALTHY, DEGRADED, S_BLOCKED, S_RATE_LIMITED, S_TIMEOUT = (
    "healthy", "degraded", "blocked", "rate_limited", "timeout")
AUTH_CONFIG, DISABLED, UNVERIFIED = "auth_config", "disabled", "unverified"

EMPTY_POLLS_BEFORE_DEGRADED = 6

_CHALLENGE_RE = re.compile(
    r"(captcha|cf-chl|challenge-platform|access denied|are you a robot|"
    r"unusual traffic|request blocked|blocked by)", re.IGNORECASE)


class PollReport:
    def __init__(self, source_id: str):
        self.source_id = source_id
        self.attempts: list[dict] = []
        self.retry_after_s: Optional[float] = None
        self._lock = threading.Lock()

    def record(self, label: str, outcome: str, status: Optional[int] = None,
               detail: str = "") -> None:
        with self._lock:
            self.attempts.append({"label": label, "outcome": outcome,
                                  "status": status, "detail": detail[:200]})

    def parsed(self, label: str, n: int) -> None:
        """Call after a successful fetch was parsed: n items found."""
        self.record(label, OK if n > 0 else EMPTY, 200, f"{n} items")

    def malformed(self, label: str, detail: str = "") -> None:
        self.record(label, MALFORMED, 200, detail or "unparseable response")

    def fetch(self, label: str, url: str, *, session=None, **kw):
        """GET `url`, classifying failure. Returns the Response on HTTP 200
        with a non-challenge body, else None (the outcome is recorded).
        The caller must then call parsed()/malformed() for the body."""
        getter = session.get if session is not None else requests.get
        try:
            r = getter(url, **kw)
        except requests.Timeout:
            self.record(label, TIMEOUT)
            return None
        except requests.exceptions.ProxyError as e:
            self.record(label, BLOCKED, detail=f"proxy refused: {e}"[:200])
            return None
        except requests.RequestException as e:
            self.record(label, NETWORK, detail=type(e).__name__)
            return None
        s = r.status_code
        if s == 200:
            head = (r.text[:4000] if "html" in (r.headers.get("Content-Type") or "")
                    else "")
            if head and _CHALLENGE_RE.search(head):
                self.record(label, BLOCKED, 200, "challenge / bot-check page")
                return None
            return r
        if s == 429:
            ra = r.headers.get("Retry-After")
            try:
                self.retry_after_s = max(self.retry_after_s or 0, float(ra))
            except (TypeError, ValueError):
                pass
            # keep the provider's message: "monthly quota exhausted" and
            # "rate limit exceeded" need different backoff
            self.record(label, RATE_LIMITED, s, (getattr(r, "text", "") or "")[:160])
        elif s in (401, 407):
            self.record(label, AUTH, s)
        elif s == 403:
            self.record(label, BLOCKED, s)
        else:
            self.record(label, HTTP_ERROR, s)
        return None


_REPORTS: dict[str, PollReport] = {}
_REPORTS_LOCK = threading.Lock()


def begin(source_id: str) -> PollReport:
    rep = PollReport(source_id)
    with _REPORTS_LOCK:
        _REPORTS[source_id] = rep
    return rep


def report(source_id: str) -> PollReport:
    """The report for the poll in progress (a throwaway one if a source is
    called outside poll_once, e.g. from a test or the CLI)."""
    with _REPORTS_LOCK:
        rep = _REPORTS.get(source_id)
        if rep is None:
            rep = _REPORTS[source_id] = PollReport(source_id)
        return rep


def classify(rep: Optional[PollReport], n_listings: int, *, error: Optional[str] = None,
             consecutive_empty: int = 0, ever_succeeded: bool = True) -> tuple[str, str]:
    """(status, reason) for one poll."""
    if error:
        return DEGRADED, f"source crashed: {error}"[:300]
    attempts = rep.attempts if rep else []
    if not attempts:
        # Source does local work only (inbox, imports) or isn't instrumented.
        if n_listings > 0:
            return HEALTHY, f"{n_listings} listings"
        return (HEALTHY if ever_succeeded else UNVERIFIED), "no requests made"
    good = [a for a in attempts if a["outcome"] in (OK, EMPTY)]
    bad = [a for a in attempts if a["outcome"] not in (OK, EMPTY)]
    n_req = len(attempts)
    if not bad:
        if n_listings > 0 or any(a["outcome"] == OK for a in good):
            return HEALTHY, f"{len(good)}/{n_req} requests ok, {n_listings} listings"
        if not ever_succeeded:
            return UNVERIFIED, "responses valid but no listing has ever been parsed"
        if consecutive_empty >= EMPTY_POLLS_BEFORE_DEGRADED:
            return DEGRADED, (f"valid but empty responses for {consecutive_empty} "
                              "polls in a row — possible format change")
        return HEALTHY, f"{n_req} requests ok, 0 new listings"
    kinds = {a["outcome"] for a in bad}
    summary = ", ".join(sorted(f"{k}×{sum(1 for a in bad if a['outcome'] == k)}"
                               for k in kinds))
    if good:
        return DEGRADED, f"{len(good)}/{n_req} requests ok; failures: {summary}"
    if RATE_LIMITED in kinds:
        return S_RATE_LIMITED, f"all {n_req} requests failed: {summary}"
    if BLOCKED in kinds:
        return S_BLOCKED, f"all {n_req} requests failed: {summary}"
    if AUTH in kinds:
        return AUTH_CONFIG, f"credentials rejected: {summary}"
    if kinds == {TIMEOUT}:
        return S_TIMEOUT, f"all {n_req} requests timed out"
    return DEGRADED, f"all {n_req} requests failed: {summary}"


def request_counts(rep: Optional[PollReport]) -> tuple[int, int]:
    attempts = rep.attempts if rep else []
    return len(attempts), sum(1 for a in attempts if a["outcome"] in (OK, EMPTY))


def fetch_feed(rep: PollReport, label: str, urls: list[str], **kw):
    """Try RSS/Atom URLs in order; return the first parsed feed that has
    entries (recorded as ok), else None. Valid-but-empty and malformed
    responses are recorded distinctly."""
    import feedparser
    for i, url in enumerate(urls):
        r = rep.fetch(f"{label}#{i}", url, **kw)
        if r is None:
            continue
        feed = feedparser.parse(r.content)
        if feed.entries:
            rep.parsed(f"{label}#{i}", len(feed.entries))
            return feed
        if feed.bozo:
            rep.malformed(f"{label}#{i}", "not a valid RSS/Atom feed")
        else:
            rep.parsed(f"{label}#{i}", 0)
            return feed
    return None
