"""Download every registered source into data/raw/<source>/.

Rules this script follows:
* Only public URLs, no credentials, no cookies, no browser automation.
* robots.txt is checked before every request; a disallowed URL is skipped.
* A failed or suspicious download never overwrites the last good file.
* Every file gets provenance in meta.json (URL, time, size, SHA-256).

Usage:  python -m pipeline.fetch [source_id ...]
Exit code is 0 unless a *critical* source has never been fetched successfully.
"""
from __future__ import annotations

import re
import sys
import traceback
from pathlib import Path

from pipeline.common import (
    PIPELINE, RAW, get_with_retry, load_sources, now_iso, read_json,
    robots_allowed, session, sha256_bytes, write_json,
)

# Text that shows up on bot-challenge / error pages instead of real content.
CHALLENGE_MARKERS = [
    b"Request Rejected", b"captcha", b"CAPTCHA", b"Access Denied",
    b"Please enable JavaScript", b"cf-browser-verification", b"awswaf",
]


class FetchError(Exception):
    pass


def looks_like_challenge(body: bytes) -> bool:
    head = body[:20000]
    return len(body) < 50000 and any(m in head for m in CHALLENGE_MARKERS)


def checked_get(sess, url: str, *, min_bytes: int = 200, **kw):
    if not robots_allowed(sess, url):
        raise FetchError(f"robots.txt disallows {url}")
    r = get_with_retry(sess, url, **kw)
    if r.status_code != 200:
        raise FetchError(f"HTTP {r.status_code} for {url}")
    body = r.content
    if len(body) < min_bytes:
        raise FetchError(f"Response too small ({len(body)} bytes) for {url}")
    if looks_like_challenge(body):
        raise FetchError(f"Response looks like a bot-challenge page for {url}")
    return r


def store(src_dir: Path, filename: str, body: bytes, url: str, final_url: str,
          content_type: str, meta: dict, extra: dict | None = None) -> bool:
    """Write file if content changed. Returns True if changed."""
    files = meta.setdefault("files", {})
    old = files.get(filename, {})
    digest = sha256_bytes(body)
    changed = old.get("sha256") != digest or not (src_dir / filename).exists()
    if changed:
        src_dir.mkdir(parents=True, exist_ok=True)
        tmp = src_dir / (filename + ".tmp")
        tmp.write_bytes(body)
        tmp.replace(src_dir / filename)
    entry = {
        "url": url,
        "final_url": final_url,
        "content_type": content_type,
        "bytes": len(body),
        "sha256": digest,
        "fetched_at": now_iso(),
        "content_changed_at": now_iso() if changed else old.get("content_changed_at"),
    }
    if extra:
        entry.update(extra)
    files[filename] = entry
    return changed


# ---------------------------------------------------------------- fetchers

def fetch_http(sess, sid, cfg, src_dir, meta):
    r = checked_get(sess, cfg["url"])
    return [store(src_dir, cfg["file"], r.content, cfg["url"], r.url,
                  r.headers.get("content-type", ""), meta)]


def fetch_http_multi(sess, sid, cfg, src_dir, meta):
    changed = []
    errors = []
    for key, page in cfg["pages"].items():
        url = cfg["base"] + page
        try:
            r = checked_get(sess, url, min_bytes=5000)
            changed.append(store(src_dir, f"{key}.html", r.content, url, r.url,
                                 r.headers.get("content-type", ""), meta))
        except Exception as e:  # keep going; report at the end
            errors.append(f"{key}: {e}")
    if errors:
        raise FetchError("; ".join(errors))
    return changed


def fetch_fsa_paged(sess, sid, cfg, src_dir, meta):
    import json
    items, offset, size = [], 0, int(cfg.get("page_size", 500))
    top_meta = None
    for _ in range(100):  # hard stop
        url = f"{cfg['url']}?_limit={size}&_offset={offset}"
        r = checked_get(sess, url, min_bytes=20)
        data = r.json()
        top_meta = top_meta or data.get("meta")
        page = data.get("items", [])
        items.extend(page)
        if len(page) < size:
            break
        offset += size
    else:
        raise FetchError("Paging did not terminate")
    if not items:
        raise FetchError("No items returned")
    body = json.dumps({"meta": top_meta, "items": items}, ensure_ascii=False,
                      indent=1, sort_keys=True).encode("utf-8")
    return [store(src_dir, cfg["file"], body, cfg["url"], cfg["url"],
                  "application/json", meta, {"items": len(items)})]


CELEX_RE = re.compile(r"0?2008R1333-(\d{8})")


def discover_latest_celex(sess, cfg) -> tuple[str, str]:
    found: set[str] = set()
    notes = []
    for url in cfg.get("discovery_urls", []):
        try:
            r = checked_get(sess, url, min_bytes=1000)
            found.update(CELEX_RE.findall(r.text))
        except Exception as e:
            notes.append(f"{url}: {e}")
    if not found and cfg.get("sparql_endpoint"):
        q = ('PREFIX cdm: <http://publications.europa.eu/ontology/cdm#> '
             'SELECT DISTINCT ?c WHERE { ?w cdm:resource_legal_id_celex ?c . '
             'FILTER(STRSTARTS(STR(?c), "02008R1333-")) }')
        try:
            r = get_with_retry(sess, cfg["sparql_endpoint"],
                               params={"query": q, "format": "application/sparql-results+json"},
                               headers={"Accept": "application/sparql-results+json"})
            if r.ok:
                for b in r.json()["results"]["bindings"]:
                    m = CELEX_RE.search(b["c"]["value"])
                    if m:
                        found.add(m.group(1))
            else:
                notes.append(f"sparql HTTP {r.status_code}")
        except Exception as e:
            notes.append(f"sparql: {e}")
    if not found:
        raise FetchError("Could not discover consolidated versions: " + "; ".join(notes))
    latest = max(found)
    return f"{cfg['celex_base']}-{latest}", ",".join(sorted(found))


def fetch_eurlex_latest(sess, sid, cfg, src_dir, meta):
    celex, all_versions = discover_latest_celex(sess, cfg)
    url = cfg["html_url"].format(celex=celex)
    r = checked_get(sess, url, min_bytes=200000, timeout=300)
    text = r.content
    # Sanity: the page must really be the consolidated regulation with Annex II.
    if b"ANNEX II" not in text and b"Annex II" not in text:
        raise FetchError("Downloaded EUR-Lex page has no Annex II")
    return [store(src_dir, cfg["file"], text, url, r.url,
                  r.headers.get("content-type", ""), meta,
                  {"celex": celex, "versions_seen": all_versions})]


def fetch_sparql(sess, sid, cfg, src_dir, meta):
    query = (PIPELINE / cfg["query_file"]).read_text(encoding="utf-8")
    url = cfg["endpoint"]
    if not robots_allowed(sess, url):
        raise FetchError(f"robots.txt disallows {url}")
    r = get_with_retry(sess, url, params={"query": query},
                       headers={"Accept": "application/sparql-results+json"}, timeout=180)
    if r.status_code != 200:
        raise FetchError(f"SPARQL HTTP {r.status_code}")
    data = r.json()
    n = len(data.get("results", {}).get("bindings", []))
    if n < 100:
        raise FetchError(f"SPARQL returned only {n} rows")
    import json
    # Sort rows so the file only changes when the data changes.
    rows = sorted(data["results"]["bindings"], key=lambda b: json.dumps(b, sort_keys=True))
    data["results"]["bindings"] = rows
    body = json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True).encode("utf-8")
    return [store(src_dir, cfg["file"], body, url, url, "application/json", meta, {"rows": n})]


FETCHERS = {
    "http": fetch_http,
    "http_multi": fetch_http_multi,
    "fsa_paged": fetch_fsa_paged,
    "eurlex_latest": fetch_eurlex_latest,
    "sparql": fetch_sparql,
}


def run(ids: list[str] | None = None) -> int:
    sources = load_sources()
    sess = session()
    summary = {}
    exit_code = 0
    for sid, cfg in sources.items():
        if ids and sid not in ids:
            continue
        src_dir = RAW / sid
        meta_path = src_dir / "meta.json"
        meta = read_json(meta_path, {}) or {}
        meta["title"] = cfg["title"]
        meta["publisher"] = cfg["publisher"]
        meta["licence"] = cfg["licence"]
        meta["last_attempt"] = now_iso()
        try:
            changed = FETCHERS[cfg["kind"]](sess, sid, cfg, src_dir, meta)
            meta["last_success"] = now_iso()
            meta["last_error"] = None
            summary[sid] = "changed" if any(changed) else "unchanged"
        except Exception as e:
            meta["last_error"] = f"{type(e).__name__}: {e}"
            meta["last_error_at"] = now_iso()
            summary[sid] = f"ERROR {e}"
            traceback.print_exc()
            if cfg.get("critical") and not meta.get("last_success"):
                exit_code = 1
        write_json(meta_path, meta)
    width = max(len(k) for k in summary) if summary else 0
    for k, v in summary.items():
        print(f"{k.ljust(width)}  {v}")
    write_json(RAW / "fetch-summary.json", {"at": now_iso(), "results": summary})
    return exit_code


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:] or None))
