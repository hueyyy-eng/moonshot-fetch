"""Shared plumbing: HTTP with pacing/retries, per-source status, time helpers, rounding."""
from __future__ import annotations

import json
import logging
import math
import os
import signal
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import requests

log = logging.getLogger("moonshot")

SGT = timezone(timedelta(hours=8))
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_sgt_str(dt: Optional[datetime] = None) -> str:
    dt = dt or now_utc()
    return dt.astimezone(SGT).strftime("%Y-%m-%d %H:%M SGT")


def today_utc_midnight_ts() -> int:
    d = now_utc()
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


def r1(x):  return None if x is None else round(float(x), 1)
def r2(x):  return None if x is None else round(float(x), 2)


def safe_div(a, b):
    try:
        if a is None or b in (None, 0):
            return None
        return a / b
    except Exception:
        return None


def drop_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


class Status:
    """Per-source outcome the cloud scan reads to decide carry-forward. Never raises."""

    def __init__(self):
        self.sources: dict[str, dict[str, Any]] = {}
        self.started = now_utc()

    def ok(self, src: str, **counts):
        self.sources[src] = {"state": "ok", **counts}
        log.info("OK   %s %s", src, counts)

    def partial(self, src: str, note: str, **counts):
        self.sources[src] = {"state": "partial", "note": note, **counts}
        log.warning("PART %s %s %s", src, note, counts)

    def fail(self, src: str, err: Any, **counts):
        self.sources[src] = {"state": "fail", "error": str(err)[:300], **counts}
        log.error("FAIL %s %s", src, str(err)[:300])

    def get(self, src: str) -> str:
        return self.sources.get(src, {}).get("state", "missing")

    def as_dict(self) -> dict:
        return {
            "generated_at_utc": now_utc().isoformat(timespec="seconds"),
            "generated_at_sgt": now_sgt_str(),
            "runtime_s": int((now_utc() - self.started).total_seconds()),
            "sources": self.sources,
        }


class Http:
    """requests.Session with per-host pacing, 429/5xx backoff and a hard per-call timeout."""

    def __init__(self, timeout: float = 25.0, budget_s: Optional[float] = None):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept": "application/json, text/html;q=0.8, */*;q=0.5"})
        self.timeout = timeout
        self.min_gap: dict[str, float] = defaultdict(float)  # host -> seconds between calls
        self.last: dict[str, float] = defaultdict(float)
        self.calls: dict[str, int] = defaultdict(int)
        self.n429: dict[str, int] = defaultdict(int)          # rate-limit answers per host (shown in status.json)
        self.t0 = time.time()
        # Time limits. 1 Oct: a GeckoTerminal 429 storm made every call wait 20-90s and then succeed, so the run
        # sat silently in discovery until GitHub killed the job at 120 min and nothing was written. Now:
        # `deadline` = hard end of all network calls for the run; `stage_end` = the current stage's own limit.
        # Past either, calls raise OutOfTime at once (no network), stages keep what they already have, and the
        # snapshot is still written.
        self.deadline: Optional[float] = (self.t0 + budget_s) if budget_s else None
        self.stage_end: Optional[float] = None
        self.hard_cap = timeout * 2.4                          # wall-clock cap per attempt, even if bytes trickle in

    def elapsed_min(self) -> float:
        return (time.time() - self.t0) / 60.0

    def _limit(self) -> Optional[float]:
        ends = [t for t in (self.deadline, self.stage_end) if t]
        return min(ends) if ends else None

    def out_of_time(self) -> bool:
        lim = self._limit()
        return lim is not None and time.time() >= lim

    def _check_time(self, url: str) -> None:
        if self.out_of_time():
            raise OutOfTime(f"time limit reached — skipped {url[:80]}")

    def _sleep(self, secs: float) -> None:
        """Back-off sleep that never runs past the time limit."""
        lim = self._limit()
        if lim is not None:
            secs = min(secs, max(0.0, lim - time.time()))
        if secs > 0:
            time.sleep(secs)

    def _send(self, fn, *args, **kw):
        """One HTTP attempt with a hard wall-clock cap (SIGALRM, main thread only)."""
        if threading.current_thread() is not threading.main_thread() or not hasattr(signal, "setitimer"):
            return fn(*args, **kw)

        def _boom(signum, frame):
            raise requests.Timeout(f"no complete answer within {int(self.hard_cap)}s")
        prev = signal.signal(signal.SIGALRM, _boom)
        signal.setitimer(signal.ITIMER_REAL, self.hard_cap)
        try:
            return fn(*args, **kw)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, prev)

    def pace(self, host: str, per_minute: float):
        self.min_gap[host] = 60.0 / per_minute

    def _wait(self, host: str):
        gap = self.min_gap.get(host, 0.0)
        if gap:
            dt = time.time() - self.last[host]
            if dt < gap:
                time.sleep(gap - dt)
        self.last[host] = time.time()

    def get(self, url: str, *, params=None, headers=None, retries: int = 2, json_out: bool = True):
        host = url.split("/")[2]
        last_err: Any = None
        for attempt in range(retries + 1):
            self._check_time(url)
            self._wait(host)
            self.calls[host] += 1
            try:
                r = self._send(self.s.get, url, params=params, headers=headers, timeout=self.timeout)
                if r.status_code == 429 or 500 <= r.status_code < 600:
                    last_err = f"HTTP {r.status_code}"
                    if r.status_code == 429:
                        self.n429[host] += 1
                        # GitHub-hosted runners share egress IPs, so a public API's per-IP bucket is shared with
                        # strangers: back off progressively (20s, 35s, 50s...) and honour Retry-After when longer.
                        try:
                            ra = float(r.headers.get("Retry-After") or 0)
                        except ValueError:
                            ra = 0.0
                        wait = min(90.0, max(ra, 20.0 + 15.0 * attempt))
                    else:
                        wait = 3.0 * (attempt + 1)
                    self._sleep(wait)
                    continue
                if r.status_code == 404:
                    return None
                if r.status_code >= 400:
                    raise requests.HTTPError(f"HTTP {r.status_code} for {url[:120]}: {r.text[:120]}")
                if not json_out:
                    return r.text
                try:
                    return r.json()
                except ValueError:
                    txt = r.text[:200].lower()
                    if "cloudflare" in txt or "turnstile" in txt or "challenge" in txt or "<html" in txt:
                        raise BlockedError(f"non-JSON (challenge page?) from {host}")
                    raise
            except (requests.ConnectionError, requests.Timeout) as e:
                last_err = e
                self._sleep(2 * (attempt + 1))
        raise requests.RequestException(f"{url[:120]} failed after retries: {last_err}")

    def post_json(self, url: str, body: dict, *, retries: int = 1):
        host = url.split("/")[2]
        last_err: Any = None
        for attempt in range(retries + 1):
            self._check_time(url)
            self._wait(host)
            self.calls[host] += 1
            try:
                r = self._send(self.s.post, url, json=body, timeout=self.timeout)
                if r.status_code == 429 or 500 <= r.status_code < 600:
                    last_err = f"HTTP {r.status_code}"
                    if r.status_code == 429:
                        self.n429[host] += 1
                    self._sleep(6)
                    continue
                r.raise_for_status()
                return r.json()
            except (requests.ConnectionError, requests.Timeout) as e:
                last_err = e
                self._sleep(2)
        raise requests.RequestException(f"POST {url[:120]} failed: {last_err}")


class OutOfTime(requests.RequestException):
    """The run's (or the stage's) time limit has passed; no call was made."""


class BlockedError(Exception):
    """A host answered with something other than data (bot challenge, HTML error page)."""


def load_json(path: str, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def dump_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")


def log10_ratio(a, b) -> Optional[float]:
    try:
        if a and b and a > 0 and b > 0:
            return abs(math.log10(a / b))
    except Exception:
        pass
    return None
