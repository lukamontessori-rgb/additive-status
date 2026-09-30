"""Shared helpers: paths, HTTP session, hashing, robots.txt checks."""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.robotparser
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
PIPELINE = ROOT / "pipeline"
DATA = ROOT / "data"
RAW = DATA / "raw"
CURATED = DATA / "curated"
PUBLISHED = DATA / "published"
INTERIM = DATA / "interim"

SITE_URL = os.environ.get("SITE_URL", "https://example.github.io/additive-status")
REPO_URL = os.environ.get("REPO_URL", "https://github.com/")

USER_AGENT = os.environ.get(
    "FETCH_USER_AGENT",
    f"AdditiveStatusBot/1.0 (+{REPO_URL}; weekly open-data refresh)",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_sources() -> dict:
    with open(PIPELINE / "sources.yml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, obj, indent: int | None = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent, sort_keys=False)
        f.write("\n")
    tmp.replace(path)


def read_raw(sid: str, filename: str) -> bytes:
    """Read a raw snapshot; snapshots are stored gzip-compressed (filename.gz)."""
    import gzip
    gz = RAW / sid / (filename + ".gz")
    if gz.exists():
        return gzip.decompress(gz.read_bytes())
    plain = RAW / sid / filename
    if plain.exists():
        return plain.read_bytes()
    raise FileNotFoundError(str(gz))


def raw_exists(sid: str, filename: str) -> bool:
    return (RAW / sid / (filename + ".gz")).exists() or (RAW / sid / filename).exists()


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "en",
    })
    return s


_robots_cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}


def robots_allowed(sess: requests.Session, url: str) -> bool:
    """Return True if robots.txt allows our user agent to fetch url.

    If robots.txt cannot be read (network error, 5xx) we treat the site as
    allowing access only when the file is simply missing (404), which is the
    standard interpretation. Other failures return False so we never guess.
    """
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    if base not in _robots_cache:
        rp = urllib.robotparser.RobotFileParser()
        try:
            r = sess.get(base + "/robots.txt", timeout=30)
            if r.status_code == 404:
                rp.parse([])
            elif r.ok:
                rp.parse(r.text.splitlines())
            else:
                rp = None
        except requests.RequestException:
            rp = None
        _robots_cache[base] = rp
    rp = _robots_cache[base]
    if rp is None:
        return False
    return rp.can_fetch(USER_AGENT, url)


def get_with_retry(sess: requests.Session, url: str, *, tries: int = 3,
                   timeout: int = 120, **kw) -> requests.Response:
    last_exc: Exception | None = None
    for i in range(tries):
        try:
            r = sess.get(url, timeout=timeout, **kw)
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {r.status_code}", response=r)
            return r
        except requests.RequestException as e:  # noqa: PERF203
            last_exc = e
            time.sleep(5 * (i + 1))
    assert last_exc is not None
    raise last_exc
