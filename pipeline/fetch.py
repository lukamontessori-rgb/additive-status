"""Download every registered source into data/raw/<source>/.

Rules this script follows:
* Only public URLs, no credentials, no cookies, no browser automation.
* robots.txt is checked before every request; a disallowed URL is skipped.
* Bot-protection challenges are reported, never bypassed.
* A failed or suspicious download never overwrites the last good file.
* Every file gets provenance in meta.json (URL, time, size, SHA-256).
* Snapshots are stored gzip-compressed (deterministic, so unchanged data
  gives an unchanged file).

Usage:  python -m pipeline.fetch [source_id ...]
"""
from __future__ import annotations

import gzip
import json
import re
import sys
import traceback
from pathlib import Path

from pipeline.common import (
    RAW, get_with_retry, load_sources, now_iso, read_json,
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
    if r.status_code == 202 or r.headers.get("x-amzn-waf-action"):
        raise FetchError(f"Bot-protection challenge (HTTP {r.status_code}) at {url}; not bypassed")
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
    """Write filename.gz if the content changed. Returns True if changed."""
    files = meta.setdefault("files", {})
    old = files.get(filename, {})
    digest = sha256_bytes(body)
    target = src_dir / (filename + ".gz")
    changed = old.get("sha256") != digest or not target.exists()
    if changed:
        src_dir.mkdir(parents=True, exist_ok=True)
        tmp = src_dir / (filename + ".gz.tmp")
        tmp.write_bytes(gzip.compress(body, compresslevel=9, mtime=0))
        tmp.replace(target)
        if (src_dir / filename).exists():
            (src_dir / filename).unlink()
    entry = {
        "url": url,
        "final_url": final_url,
        "content_type": content_type,
        "bytes": len(body),
        "stored_as": filename + ".gz",
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
    items.sort(key=lambda it: json.dumps(it.get("@id", ""), sort_keys=True))
    body = json.dumps({"meta": top_meta, "items": items}, ensure_ascii=False,
                      indent=1, sort_keys=True).encode("utf-8")
    return [store(src_dir, cfg["file"], body, cfg["url"], cfg["url"],
                  "application/json", meta, {"items": len(items)})]


def discover_latest_celex(sess, cfg) -> tuple[str, str]:
    """Find the newest consolidated version via the Publications Office SPARQL endpoint."""
    base = cfg["celex_base"]
    q = ('PREFIX cdm: <http://publications.europa.eu/ontology/cdm#> '
         'SELECT DISTINCT ?c WHERE { ?w cdm:resource_legal_id_celex ?c . '
         f'FILTER(STRSTARTS(STR(?c), "{base}-")) }}')
    url = cfg["sparql_endpoint"]
    if not robots_allowed(sess, url):
        raise FetchError(f"robots.txt disallows {url}")
    r = get_with_retry(sess, url, params={"query": q, "format": "application/sparql-results+json"},
                       headers={"Accept": "application/sparql-results+json"})
    if r.status_code != 200:
        raise FetchError(f"SPARQL HTTP {r.status_code}")
    found = set()
    for b in r.json()["results"]["bindings"]:
        v = b["c"]["value"]
        if v.startswith(base + "-") and re.fullmatch(r"\d{8}", v[len(base) + 1:]):
            found.add(v)
    if not found:
        raise FetchError(f"No consolidated versions found for {base}")
    latest = max(found, key=lambda c: c[-8:])
    return latest, ",".join(sorted(c[-8:] for c in found))


def download_cellar(sess, celex: str, cfg) -> tuple[bytes, str]:
    """Download the English XHTML/HTML manifestation from the Cellar REST service."""
    url = cfg["cellar_url"].format(celex=celex)
    headers = {"Accept": "application/xhtml+xml, text/html;q=0.9", "Accept-Language": "eng"}
    if not robots_allowed(sess, url):
        raise FetchError(f"robots.txt disallows {url}")
    r = get_with_retry(sess, url, headers=headers, timeout=300)
    if r.status_code == 300:
        links = re.findall(r'href="([^"]+/resource/cellar/[^"]+)"', r.text)
        links = [l for l in links if "pdf" not in l.lower()]
        if not links:
            raise FetchError(f"Cellar returned 300 with no usable links for {celex}")
        r = get_with_retry(sess, links[0], headers=headers, timeout=300)
    if r.status_code != 200:
        raise FetchError(f"Cellar HTTP {r.status_code} for {celex}")
    if looks_like_challenge(r.content):
        raise FetchError("Cellar response looks like a challenge page")
    return r.content, r.url


def fetch_eurlex_latest(sess, sid, cfg, src_dir, meta):
    celex, all_versions = discover_latest_celex(sess, cfg)
    must = cfg.get("must_contain", "").encode()
    errors: list[str] = []
    body, final = None, None
    try:
        body, final = download_cellar(sess, celex, cfg)
        if must and must not in body:
            errors.append(f"cellar document lacks {must!r} ({len(body)} bytes)")
            body = None
    except Exception as e:
        errors.append(f"cellar: {e}")
    if body is None:
        url = cfg["html_url"].format(celex=celex)
        try:
            r = checked_get(sess, url, min_bytes=100000, timeout=300)
            if must and must not in r.content:
                raise FetchError(f"EUR-Lex page lacks {must!r}")
            body, final = r.content, r.url
        except Exception as e:
            errors.append(f"eur-lex: {e}")
    if body is None or len(body) < 100000:
        raise FetchError("; ".join(errors) or "document too small")
    return [store(src_dir, cfg["file"], body, cfg["cellar_url"].format(celex=celex), final,
                  "text/html", meta, {"celex": celex, "versions_seen": all_versions,
                                      "notes": errors})]


def fetch_fr_api(sess, sid, cfg, src_dir, meta):
    """Federal Register API: FDA final rules touching the listed 21 CFR parts."""
    fields = ["title", "document_number", "publication_date", "effective_on", "dates", "action",
              "html_url", "abstract", "cfr_references", "type"]
    docs = {}
    for part in cfg["parts"]:
        page = 1
        while page <= 20:
            params = [("conditions[agencies][]", "food-and-drug-administration"),
                      ("conditions[type][]", "RULE"),
                      ("conditions[cfr][title]", "21"),
                      ("conditions[cfr][part]", str(part)),
                      ("conditions[publication_date][gte]", cfg["since"]),
                      ("order", "newest"), ("per_page", "100"), ("page", str(page))]
            params += [("fields[]", f) for f in fields]
            url = cfg["endpoint"]
            if not robots_allowed(sess, url):
                raise FetchError(f"robots.txt disallows {url}")
            r = get_with_retry(sess, url, params=params, headers={"Accept": "application/json"})
            if r.status_code != 200:
                raise FetchError(f"Federal Register API HTTP {r.status_code} (part {part})")
            data = r.json()
            for d in data.get("results", []):
                docs.setdefault(d["document_number"], d)
            if not data.get("next_page_url"):
                break
            page += 1
    if not docs:
        raise FetchError("Federal Register API returned no documents")
    body = json.dumps(sorted(docs.values(), key=lambda d: d["document_number"]), ensure_ascii=False,
                      indent=1, sort_keys=True).encode("utf-8")
    return [store(src_dir, cfg["file"], body, cfg["endpoint"], cfg["endpoint"], "application/json",
                  meta, {"documents": len(docs)})]


FETCHERS = {
    "fr_api": fetch_fr_api,
    "http": fetch_http,
    "http_multi": fetch_http_multi,
    "fsa_paged": fetch_fsa_paged,
    "eurlex_latest": fetch_eurlex_latest,
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
