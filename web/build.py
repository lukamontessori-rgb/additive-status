"""Build the static website from data/published/ into dist/.

Usage: python -m web.build [--data PATH] [--out DIR]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import shutil
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

from jinja2 import Environment, FileSystemLoader, select_autoescape

from pipeline.common import PUBLISHED, ROOT, SITE_URL, REPO_URL, read_json
from pipeline.model import (
    ALLOWED_LIKE, JUR_ORDER, JURISDICTIONS, NOT_ALLOWED_LIKE, STATUSES,
)

WEB = ROOT / "web"
SITE_NAME = "Additive Status"
TAGLINE = "Food additive rules in the EU, UK, US and Canada — side by side"

# Pairs of jurisdictions we build "allowed in A but not in B" pages for.
PAIRS = [("us", "eu"), ("eu", "us"), ("gb", "eu"), ("eu", "gb"),
         ("ca", "eu"), ("eu", "ca"), ("us", "ca"), ("ca", "us"),
         ("us", "gb"), ("gb", "us")]


def site_base() -> tuple[str, str]:
    """Return (origin, base_path) from SITE_URL, e.g. ('https://x.github.io', '/repo')."""
    p = urlparse(SITE_URL.rstrip("/"))
    return f"{p.scheme}://{p.netloc}", p.path.rstrip("/")


ORIGIN, BASE = site_base()


def url(path: str) -> str:
    """Site-relative URL with the base path (for links inside pages)."""
    if not path.startswith("/"):
        path = "/" + path
    return BASE + path


def abs_url(path: str) -> str:
    return ORIGIN + url(path)


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:80] or "item"


def fmt_date(value: str | None) -> str:
    if not value:
        return "unknown"
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            d = date.fromisoformat(value[:10])
        except ValueError:
            return value
    return d.strftime("%-d %B %Y")


def allowed(st: str) -> bool:
    return st in ALLOWED_LIKE


def not_allowed(st: str) -> bool:
    return st in NOT_ALLOWED_LIKE


ARTICLE = {"EU": "the EU", "UK (GB)": "the UK (GB)", "US": "the US"}


def summary_sentence(a: dict) -> str:
    """Plain factual one-line answer built only from the status data."""
    groups = defaultdict(list)
    for j in JUR_ORDER:
        st = a["jur"].get(j, {}).get("status", "unknown")
        groups[st].append(JURISDICTIONS[j]["short"])
    name = a["name"] + (f" ({a['e']})" if a.get("e") else "")
    parts = []

    def join(xs):
        xs = [ARTICLE.get(x, x) for x in xs]
        return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]

    if groups.get("authorised"):
        parts.append(f"authorised in {join(groups['authorised'])}")
    if groups.get("phase_out"):
        parts.append(f"being phased out in {join(groups['phase_out'])}")
    na = groups.get("not_authorised", [])
    if na:
        parts.append(f"not authorised in {join(na)}")
    if groups.get("prohibited"):
        parts.append(f"prohibited in {join(groups['prohibited'])}")
    if groups.get("delisted"):
        parts.append(f"delisted in {join(groups['delisted'])}")
    if groups.get("listed_noreg"):
        parts.append("in the FDA inventory without a cited regulation (US)")
    if groups.get("not_listed"):
        parts.append(f"not on the list in {join(groups['not_listed'])}, which does not cover every permitted substance")
    if not parts:
        return f"We have no status data for {name}."
    s = f"{name} is " + "; ".join(parts) + "."
    return s[0].upper() + s[1:]


def differs(a: dict) -> bool:
    sts = [a["jur"].get(j, {}).get("status", "unknown") for j in JUR_ORDER]
    return any(allowed(s) for s in sts) and any(not_allowed(s) for s in sts)


def ld_json(obj) -> str:
    """JSON for <script type=application/ld+json>, safe against '</script>'."""
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


def ld_breadcrumbs(items) -> str:
    elems = [{"@type": "ListItem", "position": 1, "name": "Home", "item": abs_url("/")}]
    for i, (label, href) in enumerate(items, start=2):
        e = {"@type": "ListItem", "position": i, "name": label}
        if href:
            e["item"] = abs_url(href)
        elems.append(e)
    return ld_json({"@context": "https://schema.org", "@type": "BreadcrumbList",
                    "itemListElement": elems})


class Builder:
    def __init__(self, data_path: Path, out: Path):
        self.data = read_json(data_path)
        if not self.data:
            raise SystemExit(f"No published data at {data_path}")
        self.changelog = read_json(data_path.parent / "changelog.json",
                                   {"entries": [], "tracking_since": None})
        self.out = out
        self.pages: list[tuple[str, str, float]] = []  # (path, lastmod, priority)
        self.env = Environment(
            loader=FileSystemLoader(WEB / "templates"),
            autoescape=select_autoescape(["html", "xml"]),
            trim_blocks=True, lstrip_blocks=True,
        )
        self.additives = sorted(self.data["additives"], key=lambda a: a.get("sort", a["id"]))
        self.by_id = {a["id"]: a for a in self.additives}
        for a in self.additives:
            a["summary"] = summary_sentence(a)
            a["differs"] = differs(a)
        self.asset = {}
        g = self.env.globals
        g.update(
            url=url, abs_url=abs_url, SITE_NAME=SITE_NAME, TAGLINE=TAGLINE,
            JUR=JURISDICTIONS, JUR_ORDER=JUR_ORDER, STATUSES=STATUSES,
            fmt_date=fmt_date, REPO_URL=REPO_URL, ld_json=ld_json,
            ld_breadcrumbs=ld_breadcrumbs, asset=lambda n: self.asset[n],
            data_version=self.data.get("data_version"),
            generated_at=self.data.get("generated_at"),
            sources=self.data.get("sources", {}),
            quality=self.data.get("quality", []),
            EU_HISTORY=self.data.get("eu_history") or {},
            CLASSES=[],
            TINY={"authorised": "Yes", "phase_out": "Ending", "not_authorised": "No",
                  "prohibited": "No", "delisted": "No", "not_listed": "?", "listed_noreg": "?",
                  "unknown": "–"},
        )

    # ------------------------------------------------------------ helpers
    def write(self, path: str, html: str, *, priority: float = 0.5,
              lastmod: str | None = None, sitemap: bool = True) -> None:
        """path is site-relative like '/additive/e129/' or '/404.html'."""
        target = self.out / (path.lstrip("/") + ("index.html" if path.endswith("/") else ""))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html, encoding="utf-8")
        if sitemap:
            self.pages.append((path, (lastmod or self.data.get("generated_at", ""))[:10], priority))

    def render(self, template: str, **ctx) -> str:
        return self.env.get_template(template).render(**ctx)

    def page(self, path: str, template: str, *, priority: float = 0.5,
             lastmod: str | None = None, sitemap: bool = True, **ctx) -> None:
        html = self.render(template, path=path, **ctx)
        self.write(path, html, priority=priority, lastmod=lastmod, sitemap=sitemap)

    def copy_static(self) -> None:
        dst = self.out / "static"
        dst.mkdir(parents=True, exist_ok=True)
        for f in sorted((WEB / "static").iterdir()):
            if f.is_file():
                body = f.read_bytes()
                h = hashlib.sha256(body).hexdigest()[:10]
                name = f"{f.stem}.{h}{f.suffix}" if f.suffix in (".css", ".js") else f.name
                (dst / name).write_bytes(body)
                self.asset[f.name] = url(f"/static/{name}")

    # ------------------------------------------------------------ pages
    def build(self) -> None:
        if self.out.exists():
            shutil.rmtree(self.out)
        self.out.mkdir(parents=True)
        self.copy_static()
        self.build_search_index()
        compare = self.build_compare_pages()
        classes = self.build_class_pages()
        self.build_additive_pages(compare)
        self.build_list_page()
        self.build_changes_page()
        self.build_about_page()
        self.build_data_page()
        self.build_home(compare, classes)
        self.page("/404.html", "404.html", page_title="Page not found", sitemap=False, noindex=True)
        self.build_sitemap_robots()

    def build_search_index(self) -> None:
        rows = []
        for a in self.additives:
            rows.append({
                "i": a["id"], "e": a.get("e") or "", "n": a["name"],
                "k": ([a["former_e"]] if a.get("former_e") else []) + a.get("aka", [])[:12], "c": a.get("cas", [])[:3],
                "s": [a["jur"].get(j, {}).get("status", "unknown") for j in JUR_ORDER],
            })
        body = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
        h = hashlib.sha256(body.encode()).hexdigest()[:10]
        name = f"search.{h}.json"
        (self.out / "static").mkdir(exist_ok=True)
        (self.out / "static" / name).write_text(body, encoding="utf-8")
        self.env.globals["SEARCH_INDEX_URL"] = url(f"/static/{name}")

    def compare_sets(self) -> list[dict]:
        out = []
        for a_j, b_j in PAIRS:
            items = [a for a in self.additives
                     if allowed(a["jur"].get(a_j, {}).get("status", ""))
                     and not_allowed(a["jur"].get(b_j, {}).get("status", ""))]
            if not items:
                continue
            A, B = JURISDICTIONS[a_j]["short"], JURISDICTIONS[b_j]["short"]
            the = lambda j: "" if j == "ca" else "the "
            out.append({
                "a": a_j, "b": b_j, "items": items,
                "path": f"/compare/allowed-in-{a_j}-not-{b_j}/",
                "title": f"Food additives allowed in {the(a_j)}{A} but not in {the(b_j)}{B}",
                "short": f"Allowed in {A}, not in {B}",
            })
        return out

    def build_compare_pages(self) -> list[dict]:
        sets = self.compare_sets()
        for s in sets:
            self.page(s["path"], "compare.html", priority=0.8, page_title=s["title"], s=s,
                      description=f"{len(s['items'])} food additives that are allowed in "
                                  f"{JURISDICTIONS[s['a']]['name_in']} but not in {JURISDICTIONS[s['b']]['name_in']}, "
                                  "with the official status and source for each.")
        self.page("/compare/", "compare_index.html", priority=0.8,
                  page_title="Where food additive rules differ: EU, UK, US and Canada", sets=sets,
                  description="Lists of food additives that are allowed in one of the EU, UK, US and Canada but not in another.")
        return sets

    def build_class_pages(self) -> list[dict]:
        groups = defaultdict(list)
        for a in self.additives:
            for c in a.get("classes", []):
                groups[c].append(a)
        classes = []
        for c, items in sorted(groups.items(), key=lambda kv: kv[0].lower()):
            if len(items) < 2:
                continue
            path = f"/class/{slug(c)}/"
            classes.append({"name": c, "path": path, "items": items, "count": len(items)})
            self.page(path, "class.html", priority=0.6,
                      page_title=f"{c}: food additive status in the EU, UK, US and Canada",
                      c={"name": c, "items": items},
                      description=f"Legal status of {len(items)} food additives in the class '{c}', compared across the EU, UK (GB), US and Canada.")
        self.env.globals["CLASSES"] = classes
        return classes

    def related(self, a: dict) -> list[dict]:
        cls = set(a.get("classes", []))
        pool = [b for b in self.additives if b["id"] != a["id"] and cls & set(b.get("classes", []))]

        def num(x):
            m = re.search(r"\d+", x.get("e") or "")
            return int(m.group()) if m else 99999
        return sorted(pool, key=lambda b: abs(num(b) - num(a)))[:8]

    def build_additive_pages(self, compare: list[dict]) -> None:
        for a in self.additives:
            in_sets = [s for s in compare if a in s["items"]]
            changes = [e for e in self.changelog.get("entries", []) if e.get("id") == a["id"]]
            title_bits = f"{a['e']} {a['name']}" if a.get("e") else a["name"]
            self.page(f"/additive/{a['id']}/", "additive.html", priority=0.7, lastmod=a.get("updated"),
                      a=a, related=self.related(a), in_sets=in_sets, changes=changes,
                      page_title=f"{title_bits}: is it allowed in the EU, UK, US and Canada?",
                      description=a["summary"] + " Official sources and conditions for each.")

    def build_list_page(self) -> None:
        self.page("/additives/", "list.html", priority=0.9,
                  page_title="All food additives: status table for the EU, UK, US and Canada",
                  additives=self.additives,
                  description=f"Search, filter and compare {len(self.additives)} food additives by legal status in the EU, UK (GB), US and Canada.")

    def build_changes_page(self) -> None:
        entries = sorted(self.changelog.get("entries", []), key=lambda e: e.get("date", ""), reverse=True)
        self.page("/changes/", "changes.html", priority=0.7,
                  page_title="Changes in food additive status: weekly checks and EU history since 2013",
                  entries=entries, by_id=self.by_id, eu_events=self.data.get("eu_history_events", []),
                  tracking_since=self.changelog.get("tracking_since"),
                  description="Status changes for food additives detected in the official EU, UK, US and Canadian sources, with dates.")

    def build_about_page(self) -> None:
        self.page("/about/", "about.html", priority=0.4,
                  page_title="Methods, sources and limits",
                  description="How Additive Status collects, matches and checks official food additive data, what each status means, and what the site does not cover.")

    def build_data_page(self) -> None:
        dl = self.out / "downloads"
        dl.mkdir(parents=True, exist_ok=True)
        (dl / "additives.json").write_text(
            json.dumps(self.data_for_download(), ensure_ascii=False, indent=1), encoding="utf-8")
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "e_number", "name", "other_names", "cas", "classes"] +
                   [f"status_{j}" for j in JUR_ORDER] + [f"detail_{j}" for j in JUR_ORDER] +
                   ["page_url"])
        for a in self.additives:
            w.writerow([a["id"], a.get("e") or "", a["name"], "; ".join(a.get("aka", [])),
                        "; ".join(a.get("cas", [])), "; ".join(a.get("classes", []))] +
                       [a["jur"].get(j, {}).get("status", "unknown") for j in JUR_ORDER] +
                       [a["jur"].get(j, {}).get("headline", "") for j in JUR_ORDER] +
                       [abs_url(f"/additive/{a['id']}/")])
        (dl / "additives.csv").write_text(buf.getvalue(), encoding="utf-8")
        sizes = {n: (dl / n).stat().st_size for n in ("additives.json", "additives.csv")}
        self.page("/data/", "data.html", priority=0.5, page_title="Download the food additive status data",
                  sizes=sizes, count=len(self.additives),
                  description="Download the combined EU, UK, US and Canada food additive status dataset as CSV or JSON, with sources and licences.")

    def data_for_download(self) -> dict:
        keep = ("id", "e", "name", "aka", "cas", "classes", "jur", "wikidata", "updated")
        return {
            "generated_at": self.data.get("generated_at"),
            "data_version": self.data.get("data_version"),
            "licence_note": "Compiled from EU, UK, US, Canadian and Wikidata sources; see 'sources' for each source's licence and required attribution.",
            "sources": self.data.get("sources", {}),
            "statuses": {k: {"label": v[0], "meaning": v[3]} for k, v in STATUSES.items()},
            "additives": [{k: a[k] for k in keep if k in a} for a in self.additives],
        }

    def build_home(self, compare, classes) -> None:
        counts = defaultdict(int)
        for a in self.additives:
            for j in JUR_ORDER:
                if allowed(a["jur"].get(j, {}).get("status", "")):
                    counts[j] += 1
        diff_count = sum(1 for a in self.additives if a["differs"])
        examples = [self.by_id[i] for i in ("e171", "e129", "e127", "us-potassium-bromate", "e951",
                                            "us-brominated-vegetable-oil", "e621") if i in self.by_id]
        recent = sorted(self.changelog.get("entries", []), key=lambda e: e.get("date", ""), reverse=True)[:6]
        self.page("/", "home.html", priority=1.0,
                  page_title=f"{SITE_NAME}: is this food additive allowed in the EU, UK, US or Canada?",
                  home=True, compare=compare, classes=classes, counts=counts,
                  total=len(self.additives), diff_count=diff_count,
                  examples=examples, recent=recent, by_id=self.by_id,
                  description="Search any E-number or additive name and see its official status in the EU, UK (GB), US and Canada side by side, with links to the sources.")

    def build_sitemap_robots(self) -> None:
        lines = ['<?xml version="1.0" encoding="UTF-8"?>',
                 '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
        for path, lastmod, prio in self.pages:
            lines.append(f"  <url><loc>{abs_url(path)}</loc>"
                         + (f"<lastmod>{lastmod}</lastmod>" if lastmod else "")
                         + f"<priority>{prio:.1f}</priority></url>")
        lines.append("</urlset>")
        (self.out / "sitemap.xml").write_text("\n".join(lines) + "\n", encoding="utf-8")
        robots = f"User-agent: *\nAllow: /\n\nSitemap: {abs_url('/sitemap.xml')}\n"
        (self.out / "robots.txt").write_text(robots, encoding="utf-8")
        (self.out / ".nojekyll").write_text("", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(PUBLISHED / "additives.json"))
    ap.add_argument("--out", default=str(ROOT / "dist"))
    args = ap.parse_args()
    b = Builder(Path(args.data), Path(args.out))
    b.build()
    print(f"Built {len(b.pages)} pages into {args.out}")


if __name__ == "__main__":
    main()
