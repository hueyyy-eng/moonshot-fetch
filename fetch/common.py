"""Shared plumbing: HTTP with pacing/retries, per-source status, time helpers, rounding."""
from __future__ import annotations

import json
import logging
import math
import os
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

    def __init__(self, timeout: float = 25.0):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept": "application/json, text/html;q=0.8, */*;q=0.5"})
        self.timeout = timeout
        self.min_gap: dict[str, float] = defaultdict(float)  # host -> seconds between calls
        self.last: dict[str, float] = defaultdict(float)
        self.calls: dict[str, int] = defaultdict(int)

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
            self._wait(host)
            self.calls[host] += 1
            try:
                r = self.s.get(url, params=params, headers=headers, timeout=self.timeout)
                if r.status_code == 429 or 500 <= r.status_code < 600:
                    last_err = f"HTTP {r.status_code}"
                    time.sleep(8 if r.status_code == 429 else 3 * (attempt + 1))
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
                time.sleep(2 * (attempt + 1))
        raise requests.RequestException(f"{url[:120]} failed after retries: {last_err}")

    def post_json(self, url: str, body: dict, *, retries: int = 1):
        host = url.split("/")[2]
        last_err: Any = None
        for attempt in range(retries + 1):
            self._wait(host)
            self.calls[host] += 1
            try:
                r = self.s.post(url, json=body, timeout=self.timeout)
                if r.status_code == 429 or 500 <= r.status_code < 600:
                    last_err = f"HTTP {r.status_code}"
                    time.sleep(6)
                    continue
                r.raise_for_status()
                return r.json()
            except (requests.ConnectionError, requests.Timeout) as e:
                last_err = e
                time.sleep(2)
        raise requests.RequestException(f"POST {url[:120]} failed: {last_err}")


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
