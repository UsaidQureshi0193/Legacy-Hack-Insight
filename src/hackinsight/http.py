"""A small polite HTTP client: identifies itself, retries with back-off, pauses between calls."""
from __future__ import annotations

import time
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import config

_session: requests.Session | None = None
_last_call = 0.0


def session() -> requests.Session:
    global _session
    if _session is None:
        s = requests.Session()
        s.headers["User-Agent"] = config.USER_AGENT
        # 429 is NOT retried: a rate-limited API must be left alone (the caller defers the work).
        # respect_retry_after_header=False: a server "Retry-After: 3600" would otherwise freeze the run.
        retry = Retry(total=4, backoff_factor=2, status_forcelist=(500, 502, 503, 504),
                      allowed_methods=("GET",), respect_retry_after_header=False, raise_on_status=False)
        s.mount("https://", HTTPAdapter(max_retries=retry))
        _session = s
    return _session


def get(url: str, params: dict | None = None, timeout: int = 120) -> requests.Response:
    global _last_call
    wait = config.REQUEST_PAUSE_S - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    r = session().get(url, params=params, timeout=timeout)
    _last_call = time.monotonic()
    r.raise_for_status()
    return r


def download(url: str, dest: Path, params: dict | None = None) -> Path:
    """Stream to a temp file and rename, so a broken download never leaves a half file behind."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    r = get(url, params=params)
    tmp.write_bytes(r.content)
    tmp.replace(dest)
    return dest
