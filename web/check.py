"""Crawl the built site in dist/ and fail on broken structure.

Checks: every internal link and asset resolves; each page has one <h1>,
a unique <title>, a meta description and a correct canonical URL;
JSON-LD parses; sitemap and pages agree; no orphan pages.

Usage: python -m web.check dist
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import unquote, urlparse

from lxml import etree, html

from web.build import BASE, ORIGIN, abs_url


def page_path(dist: Path, f: Path) -> str:
    rel = f.relative_to(dist).as_posix()
    if rel.endswith("index.html"):
        rel = rel[: -len("index.html")]
    return "/" + rel


def resolve(dist: Path, href: str) -> Path | None:
    p = urlparse(href)
    if p.scheme or p.netloc:
        if f"{p.scheme}://{p.netloc}" != ORIGIN:
            return None  # external
    path = unquote(p.path)
    if not path.startswith(BASE + "/") and path != BASE:
        return dist / "__outside_base__"
    rel = path[len(BASE):].lstrip("/")
    target = dist / rel
    if rel == "" or rel.endswith("/"):
        target = target / "index.html"
    return target


def main(dist_dir: str) -> int:
    dist = Path(dist_dir)
    errors: list[str] = []
    warnings: list[str] = []
    titles = Counter()
    inbound = defaultdict(int)
    pages = {}
    for f in sorted(dist.rglob("*.html")):
        doc = html.fromstring(f.read_bytes())
        path = page_path(dist, f)
        pages[path] = f
        t = doc.findtext(".//title") or ""
        if not t.strip():
            errors.append(f"{path}: empty <title>")
        titles[t] += 1
        h1 = doc.findall(".//h1")
        if len(h1) != 1:
            errors.append(f"{path}: {len(h1)} <h1> elements")
        noindex = bool(doc.xpath('//meta[@name="robots" and contains(@content,"noindex")]'))
        if not noindex:
            desc = doc.xpath('//meta[@name="description"]/@content')
            if not desc or len(desc[0]) < 50:
                errors.append(f"{path}: missing or short meta description")
            elif len(desc[0]) > 320:
                warnings.append(f"{path}: long meta description ({len(desc[0])})")
            canon = doc.xpath('//link[@rel="canonical"]/@href')
            if canon != [abs_url(path)]:
                errors.append(f"{path}: canonical {canon} != {abs_url(path)}")
        for s in doc.xpath('//script[@type="application/ld+json"]/text()'):
            try:
                json.loads(s)
            except ValueError as e:
                errors.append(f"{path}: bad JSON-LD ({e})")
        main_el = doc.find(".//main")
        if main_el is None or len(main_el.text_content().strip()) < 80:
            errors.append(f"{path}: main content is empty or too short")
        for attr, xp in (("href", "//a[@href]"), ("href", "//link[@href]"), ("src", "//script[@src]"), ("src", "//img[@src]")):
            for node in doc.xpath(xp):
                href = node.get(attr)
                if href.startswith(("mailto:", "#", "javascript:")):
                    continue
                target = resolve(dist, href)
                if target is None:
                    continue
                if not target.exists():
                    errors.append(f"{path}: broken link {href}")
                elif target.suffix == ".html" and target != f:
                    inbound[page_path(dist, target)] += 1
        for img in doc.xpath("//img[not(@alt)]"):
            errors.append(f"{path}: <img> without alt")
    for t, n in titles.items():
        if n > 1:
            errors.append(f"duplicate <title> on {n} pages: {t!r}")
    # sitemap
    sm = dist / "sitemap.xml"
    if not sm.exists():
        errors.append("sitemap.xml missing")
    else:
        tree = etree.parse(str(sm))
        locs = [e.text for e in tree.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]
        listed = set()
        for loc in locs:
            tgt = resolve(dist, loc)
            if tgt is None or not tgt.exists():
                errors.append(f"sitemap URL does not exist: {loc}")
            else:
                listed.add(page_path(dist, tgt))
        for path in pages:
            if path in ("/404.html",):
                continue
            if path not in listed:
                errors.append(f"page not in sitemap: {path}")
            if path != "/" and inbound[path] == 0:
                errors.append(f"orphan page (no internal links to it): {path}")
    if not (dist / "robots.txt").exists():
        errors.append("robots.txt missing")
    for w in warnings[:50]:
        print("WARN ", w)
    for e in errors[:200]:
        print("ERROR", e)
    print(f"Checked {len(pages)} pages: {len(errors)} errors, {len(warnings)} warnings")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "dist"))
