"""HTTP helper with on-disk caching, retries and polite rate limiting."""

import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .config import USER_AGENT

CACHE_DIR = os.environ.get(
    "LAND_ANALYZER_CACHE", os.path.join(os.path.expanduser("~"), ".cache", "spokane-land")
)
CACHE_TTL_S = 24 * 3600
MIN_INTERVAL_S = 0.15  # per-host spacing between requests

_host_lock = threading.Lock()
_host_last = {}


class HttpError(RuntimeError):
    pass


def _throttle(url):
    host = urllib.parse.urlsplit(url).netloc
    with _host_lock:
        wait = _host_last.get(host, 0) + MIN_INTERVAL_S - time.monotonic()
        _host_last[host] = time.monotonic() + max(wait, 0)
    if wait > 0:
        time.sleep(wait)


def _cache_path(key):
    return os.path.join(CACHE_DIR, hashlib.sha256(key.encode()).hexdigest()[:32])


def get_text(url, params=None, headers=None, use_cache=True, timeout=45, retries=3, post=False):
    body = None
    if params and post:
        body = urllib.parse.urlencode(params).encode()
    elif params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    path = _cache_path(url + "\n" + (body or b"").decode())
    if use_cache and os.path.exists(path) and time.time() - os.path.getmtime(path) < CACHE_TTL_S:
        with open(path, encoding="utf-8") as f:
            return f.read()

    hdrs = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if body is not None:
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    hdrs.update(headers or {})
    last_err = None
    for attempt in range(retries + 1):
        _throttle(url)
        try:
            req = urllib.request.Request(url, data=body, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                text = resp.read().decode("utf-8", errors="replace")
            if use_cache:
                os.makedirs(CACHE_DIR, exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
            return text
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (400, 401, 403, 404):
                break
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_err = e
        time.sleep(2 ** attempt)
    raise HttpError(f"{'POST' if body else 'GET'} {url[:160]} failed: {last_err}")


def get_json(url, params=None, headers=None, use_cache=True, post=False):
    text = get_text(url, params, headers, use_cache, post=post)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None
    if data is None or (isinstance(data, dict) and "error" in data):
        _forget(url, params, post)  # don't cache failures
        detail = data["error"] if data else text[:200]
        raise HttpError(f"Service error from {url[:120]}: {detail}")
    return data


def _forget(url, params, post):
    if params and post:
        key = url + "\n" + urllib.parse.urlencode(params)
    else:
        key = (url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params) if params else url) + "\n"
    try:
        os.remove(_cache_path(key))
    except OSError:
        pass
