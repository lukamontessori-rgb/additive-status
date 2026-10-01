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
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from pipeline.common import PUBLISHED, ROOT, SITE_URL, REPO_URL, read_json
from pipeline.model import (
    ALLOWED_LIKE, JUR_ORDER, JURISDICTIONS, NOT_ALLOWED_LIKE, STATUSES,
)

WEB = ROOT / "web"
SITE_NAME = "Additive Status"
PLACES = "the EU, UK, US, Canada and Australia/New Zealand"
PLACES_SHORT = "EU, UK, US, Canada, Aus/NZ"
TAGLINE = "Is this food additive allowed? Official status in the EU, UK, US, Canada and Australia/New Zealand, side by side."

# Every ordered pair of jurisdictions gets an "allowed in A but not in B" page.
PAIRS = [(a, b) for a in JUR_ORDER for b in JUR_ORDER if a != b]

# Compact codes for badges, and the article used in running text.
JUR_CODE = {"eu": "EU", "gb": "UK", "us": "US", "ca": "CA", "anz": "AU/NZ"}
JUR_THE = {"eu": "the EU", "gb": "the UK (GB)", "us": "the US", "ca": "Canada", "anz": "Australia/New Zealand"}

TONE_ICON = {"ok": "✓", "warn": "!", "no": "✕", "unknown": "?"}

# Decorative only (aria-hidden); every class also has its text name next to it.
CLASS_ICON = {
    "Colours": "🎨", "Sweeteners": "🍬", "Preservatives": "🥫", "Antioxidants": "🛡️",
    "Emulsifiers, stabilisers, thickeners and gelling agents": "🥣", "Acidity regulators": "🍋",
    "Flour treatment agents": "🍞", "Sequestrants": "🔗", "Anticaking agents": "🧂",
    "Carriers and solvents": "💧", "Firming agents": "🥒", "Modified starches": "🌽",
    "Glazing agents": "✨", "Enzymes": "🧬",
}


GLOSSARY = [
    ("Food additive", "additive", "A substance added to food for a technological purpose — for example to colour, preserve, thicken, sweeten or stabilise it — and not normally eaten as a food by itself. Each jurisdiction defines the term in its own law, so the edges differ."),
    ("E-number", "e-number", "The code of an additive on the EU's list, such as E 330 for citric acid. Great Britain uses the same numbers. The numbers largely follow the international INS system."),
    ("INS number", "ins", "A number from the International Numbering System for Food Additives, maintained by the Codex Alimentarius Commission of the FAO and WHO. Labels in Australia and New Zealand show INS numbers without an E, for example “colour (102)”."),
    ("Positive list", "positive-list", "A system in which only the substances on the list may be used, and only in the foods and at the levels listed. The EU, Great Britain, Canada and Australia/New Zealand work this way."),
    ("Union list", "union-list", "The EU list of approved food additives, in Annex II of Regulation (EC) No 1333/2008. It applies in all EU countries and in Northern Ireland."),
    ("Consolidated text", "consolidated", "A version of an EU law with all amendments merged in, published for convenience. Only the texts in the Official Journal are legally authentic."),
    ("Quantum satis", "quantum-satis", "An EU term meaning that no maximum amount is set: use only as much as needed for the purpose, following good manufacturing practice."),
    ("GMP (good manufacturing practice)", "gmp", "Using no more of an additive than needed to achieve its purpose. “Permitted at GMP” means no number is set for the maximum level."),
    ("Maximum level", "maximum-level", "The highest amount of an additive allowed in a food, usually in milligrams per kilogram or per litre (mg/kg, mg/l)."),
    ("GRAS", "gras", "“Generally recognized as safe”: a US category. A substance that qualified experts generally recognise as safe for its intended use is exempt from the FDA's food additive approval. Companies may notify the FDA of a GRAS conclusion, but notification is voluntary, so many GRAS uses are not on any FDA list."),
    ("FEMA GRAS", "fema", "GRAS conclusions for flavouring substances made by the expert panel of the Flavor and Extract Manufacturers Association in the US."),
    ("Colour additive (US)", "color-additive", "In the US, colours need to be listed by the FDA before use (Title 21 of the Code of Federal Regulations, parts 73 and 74). They are not covered by GRAS."),
    ("FD&C colours", "fdc", "Synthetic colours certified by the FDA batch by batch, such as FD&C Red No. 40. FD&C refers to the Federal Food, Drug, and Cosmetic Act."),
    ("21 CFR", "cfr", "Title 21 of the US Code of Federal Regulations, which covers food and drugs. Section numbers such as 172.5 or 74.340 point to specific rules."),
    ("Federal Register final order", "federal-register", "The official US publication of a final rule or order, such as the revocation of a colour additive listing. It states when the change takes effect."),
    ("Delisted / revoked", "delisted", "A previous authorisation has been removed from the rules, so the substance may no longer be used as it was."),
    ("Phase-out", "phase-out", "An authorisation has been withdrawn or revoked, but a transition period is still running, so the substance may still be used until a set date."),
    ("Processing aid", "processing-aid", "A substance used while making a food for a technological purpose — for example to clarify a liquid — that does not do that job in the finished food. In Australia and New Zealand, processing aids are permitted under Standard 1.3.3 and Schedule 18, separately from food additives."),
    ("Schedule 15 and Schedule 16", "schedules", "Parts of the Australia New Zealand Food Standards Code. Schedule 15 lists which additives may be used in which foods; Schedule 16 lists additives and colourings permitted at GMP or to a maximum level."),
    ("Lists of Permitted Food Additives", "canada-lists", "Health Canada's 15 lists, each for one kind of additive (colours, preservatives, sweeteners and so on), with the foods, purposes and maximum levels allowed."),
    ("Great Britain (GB)", "gb", "England, Scotland and Wales. Since leaving the EU, Great Britain keeps its own additive rules. Northern Ireland follows EU rules."),
    ("CAS number", "cas", "A unique identifier for a chemical substance, assigned by the Chemical Abstracts Service. Useful for matching the same chemical across lists."),
    ("Colour Index number", "colour-index", "An identifier for dyes and pigments in the Colour Index International, for example C.I. 16035 for Allura Red AC."),
    ("ADI (acceptable daily intake)", "adi", "An estimate of the amount of a substance, per kilogram of body weight, that can be eaten every day over a lifetime without an appreciable health risk. Set by scientific bodies; not shown on this site."),
]

# Line icons (24x24, stroke = currentColor). Decorative: always aria-hidden.
ICONS = {
    "dice": '<rect x="4" y="4" width="16" height="16" rx="4"/><circle cx="9" cy="9" r="1.2" fill="currentColor"/><circle cx="15" cy="15" r="1.2" fill="currentColor"/><circle cx="15" cy="9" r="1.2" fill="currentColor"/><circle cx="9" cy="15" r="1.2" fill="currentColor"/>',
    "tag": '<path d="M3.5 12.2V5a1.5 1.5 0 0 1 1.5-1.5h7.2l8.3 8.3a1.5 1.5 0 0 1 0 2.1l-7.1 7.1a1.5 1.5 0 0 1-2.1 0Z"/><circle cx="8.5" cy="8.5" r="1.6"/>',
    "spark": '<path d="M12 3v4M12 17v4M3 12h4M17 12h4"/><path d="M12 8.5 13.4 10.6 15.5 12 13.4 13.4 12 15.5 10.6 13.4 8.5 12 10.6 10.6Z" fill="currentColor"/>',
    "scale": '<path d="M12 4v16M7 20h10M5 7h14"/><path d="M5 7 2.5 13a3 3 0 0 0 5 0Z"/><path d="M19 7l-2.5 6a3 3 0 0 0 5 0Z"/>',
    "shuffle": '<path d="M3 7h3.5c4 0 6.5 10 11 10H21M3 17h3.5c1.6 0 2.9-1.5 4-3.4M14 9.4C15 7.9 16 7 17.5 7H21"/><path d="m18.5 4.5 2.5 2.5-2.5 2.5M18.5 14.5l2.5 2.5-2.5 2.5"/>',
    "cart": '<path d="M3 4h2.2l2.2 11h10.4l2-8H6.3"/><circle cx="9" cy="19" r="1.4"/><circle cx="17" cy="19" r="1.4"/>',
    "factory": '<path d="M3 20V10l5 3V10l5 3V6h3l1 4h4v10Z"/><path d="M7 16h2M12 16h2M17 16h1"/>',
    "news": '<rect x="3.5" y="5" width="14" height="14" rx="2"/><path d="M17.5 9h3v8a2 2 0 0 1-2 2M7 9h7M7 12.5h7M7 16h4"/>',
    "cap": '<path d="m2.5 9.5 9.5-4.5 9.5 4.5-9.5 4.5Z"/><path d="M6.5 11.5v4.2c1.4 1.4 3.4 2.3 5.5 2.3s4.1-.9 5.5-2.3v-4.2M21.5 9.5v5"/>',
    "download": '<path d="M12 4v11M7.5 10.5 12 15l4.5-4.5M5 19.5h14"/>',
    "read": '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/>',
    "puzzle": '<path d="M8 4h3.5a1.5 1.5 0 0 1 3 0H18v3.5a1.5 1.5 0 0 1 0 3V14h-3.5a1.5 1.5 0 0 0-3 0H8v-3.5a1.5 1.5 0 0 0 0-3Z"/><path d="M8 14v6h10v-6"/>',
    "check": '<circle cx="12" cy="12" r="8.5"/><path d="m8.3 12.3 2.5 2.5 5-5.2"/>',
    "rocket": '<path d="M12.5 15.5 8.5 11.5c1.8-4.5 5-7.5 11-8-.5 6-3.5 9.2-8 11Z"/><path d="M8.5 11.5 5 12l-1.5 3 4-1M12.5 15.5 12 19l-3 1.5 1-4"/><circle cx="15" cy="9" r="1.4"/>',
    "file": '<path d="M6 3.5h8l4 4V20a.5.5 0 0 1-.5.5h-11A.5.5 0 0 1 6 20Z"/><path d="M14 3.5v4h4M9 12h6M9 15.5h6"/>',
    "braces": '<path d="M9 4c-2 0-2.5 1-2.5 3v2c0 1.5-.8 2.5-2 3 1.2.5 2 1.5 2 3v2c0 2 .5 3 2.5 3M15 4c2 0 2.5 1 2.5 3v2c0 1.5.8 2.5 2 3-1.2.5-2 1.5-2 3v2c0 2-.5 3-2.5 3"/>',
    "lock": '<rect x="5" y="10.5" width="14" height="10" rx="2"/><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5"/>',
    "flask": '<path d="M9.5 3.5h5M10.5 3.5v6L5 18.5a1.5 1.5 0 0 0 1.3 2.2h11.4a1.5 1.5 0 0 0 1.3-2.2L13.5 9.5v-6"/><path d="M7.5 14.5h9"/>',
    "link": '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
    "trophy": '<path d="M8 4h8v5a4 4 0 0 1-8 0Z"/><path d="M8 6H5a3 3 0 0 0 3 4M16 6h3a3 3 0 0 1-3 4M12 13v4M8.5 20h7M10 17h4"/>',
    "pause": '<path d="M9 6v12M15 6v12"/>',
    "play": '<path d="M8 5.5v13l10-6.5Z" fill="currentColor"/>',
    "arrow": '<path d="M5 12h14M13 6l6 6-6 6"/>',
    "up": '<path d="M12 19V5M6 11l6-6 6 6"/>',
}


def icon(name: str, size: int = 18) -> Markup:
    body = ICONS.get(name, "")
    return Markup(f'<svg class="ico" aria-hidden="true" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
                  f'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')


# Functional classes shown as element-style tiles (symbol, hue) - a nod to the lab theme.
CLASS_SYMBOL = {
    "Colours": ("Co", 350), "Sweeteners": ("Sw", 300), "Preservatives": ("Pr", 25), "Antioxidants": ("Ao", 200),
    "Emulsifiers, stabilisers, thickeners and gelling agents": ("Em", 250), "Acidity regulators": ("Ac", 55),
    "Flour treatment agents": ("Fl", 35), "Sequestrants": ("Sq", 180), "Anticaking agents": ("An", 160),
    "Carriers and solvents": ("Cs", 210), "Firming agents": ("Fi", 140), "Modified starches": ("St", 45),
    "Glazing agents": ("Gl", 270), "Enzymes": ("En", 120),
}


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


def status_of(a: dict, j: str) -> str:
    return a["jur"].get(j, {}).get("status", "unknown")


def tone(st: str) -> str:
    return STATUSES.get(st, STATUSES["unknown"])[2]


def join_words(xs: list[str]) -> str:
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


def label(a: dict) -> str:
    return f"{a['name']} ({a['e']})" if a.get("e") else a["name"]


def summary_sentence(a: dict) -> str:
    """Plain factual one-line answer built only from the status data."""
    groups = defaultdict(list)
    for j in JUR_ORDER:
        groups[status_of(a, j)].append(j)
    parts = []

    def places(js):
        return join_words([JUR_THE[j] for j in js])

    if groups.get("authorised"):
        parts.append(f"authorised in {places(groups['authorised'])}")
    if groups.get("phase_out"):
        parts.append(f"being phased out in {places(groups['phase_out'])}")
    if groups.get("not_authorised"):
        parts.append(f"not authorised in {places(groups['not_authorised'])}")
    if groups.get("prohibited"):
        parts.append(f"prohibited in {places(groups['prohibited'])}")
    if groups.get("delisted"):
        parts.append(f"delisted in {places(groups['delisted'])}")
    if groups.get("listed_noreg"):
        parts.append("in the FDA inventory without a cited regulation (US)")
    if groups.get("not_listed"):
        nl = groups["not_listed"]
        parts.append(f"not on the list in {places(nl)} "
                     + ("(that list does not cover every permitted substance)" if len(nl) == 1
                        else "(those lists do not cover every permitted substance)"))
    if not parts:
        return f"We have no status data for {label(a)}."
    return f"{label(a)}: " + "; ".join(parts) + "."


def differs(a: dict) -> bool:
    sts = [status_of(a, j) for j in JUR_ORDER]
    return any(allowed(s) for s in sts) and any(not_allowed(s) for s in sts)


def ld_json(obj) -> str:
    """JSON for <script type=application/ld+json>, safe against '</script>'."""
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


def ld_breadcrumbs(items) -> str:
    elems = [{"@type": "ListItem", "position": 1, "name": "Home", "item": abs_url("/")}]
    for i, (lbl, href) in enumerate(items, start=2):
        e = {"@type": "ListItem", "position": i, "name": lbl}
        if href:
            e["item"] = abs_url(href)
        elems.append(e)
    return ld_json({"@context": "https://schema.org", "@type": "BreadcrumbList",
                    "itemListElement": elems})


# ---------------------------------------------------------------- charts
def esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def bar_chart(rows: list[tuple[str, str, int]], total: int, *, title: str, unit: str) -> Markup:
    """Horizontal single-series bar chart in HTML/CSS (readable at any width).

    rows: (key, label, value). Direct value labels; hover/focus tooltips via data-tip;
    the same numbers are also offered as a table next to the chart."""
    step = 50 if total <= 250 else 100 if total <= 600 else 200
    ticks = list(range(0, total + 1, step))
    out = [f'<div class="hbars" role="list" aria-label="{esc(title)}">']
    out.append('<div class="hb-grid" aria-hidden="true">' + "".join(
        f'<span style="left:{100 * t / total:.2f}%"><i>{t}</i></span>' for t in ticks) + "</div>")
    for key, lbl, v in rows:
        pct = 100 * v / total if total else 0
        tip = f"{lbl}: {v} of {total} {unit} ({round(pct)}%)"
        out.append(f'<div class="hb-row" role="listitem" tabindex="0" data-tip="{esc(tip)}" data-key="{key}">'
                   f'<span class="hb-lbl">{esc(lbl)}</span>'
                   f'<span class="hb-track"><span class="hb-bar" style="width:{pct:.2f}%"></span>'
                   f'<span class="hb-val" style="left:{pct:.2f}%">{v}</span></span></div>')
    out.append("</div>")
    return Markup("".join(out))


def breakdown_chart(rows: list[tuple[str, str, dict]], total: int, *, title: str) -> Markup:
    """Stacked horizontal bars per place: allowed / not allowed / unclear (HTML/CSS).

    rows: (key, label, {"yes": n, "no": n, "unk": n}). Status colours always come with a legend
    and direct labels; small segments rely on the tooltip and the table view."""
    segs = (("yes", "allowed", "ok"), ("no", "not allowed", "no"), ("unk", "unclear", "unk"))
    out = [f'<div class="sbars" role="list" aria-label="{esc(title)}">',
           '<p class="sb-legend" aria-hidden="true">' + "".join(
               f'<span><i class="sw sw-{t}"></i>{lbl.capitalize()}</span>' for _, lbl, t in segs) + "</p>"]
    for key, lbl, c in rows:
        aria = f"{lbl}: " + ", ".join(f"{c[k]} {name}" for k, name, _ in segs)
        out.append(f'<div class="sb-row" role="listitem" tabindex="0" aria-label="{esc(aria)}" data-tip="{esc(aria)}">'
                   f'<span class="sb-lbl" aria-hidden="true">{esc(lbl)}</span><span class="sb-track" aria-hidden="true">')
        for k, name, t in segs:
            v = c[k]
            if not v:
                continue
            pct = 100 * v / total if total else 0
            tip = f"{lbl}: {v} of {total} {name} ({round(pct)}%)"
            show = pct >= 7
            out.append(f'<span class="sb-seg sb-{t}" style="width:{pct:.2f}%" data-tip="{esc(tip)}">'
                       f'{v if show else ""}</span>')
        out.append("</span></div>")
    out.append("</div>")
    return Markup("".join(out))


def column_chart(rows: list[tuple[str, int]], *, title: str, unit: str) -> Markup:
    """Vertical single-series column chart in HTML/CSS (e.g. changes per year)."""
    vmax = max([v for _, v in rows] + [1])
    step = 1 if vmax <= 6 else 2 if vmax <= 12 else 5
    ymax = ((vmax + step - 1) // step) * step
    out = [f'<div class="vcols" role="group" aria-label="{esc(title)}">',
           '<div class="vc-grid" aria-hidden="true">' + "".join(
               f'<span style="bottom:{100 * t / ymax:.2f}%"><i>{t}</i></span>' for t in range(0, ymax + 1, step)) + "</div>",
           '<div class="vc-plot" role="list">']
    for lbl, v in rows:
        tip = f"{lbl}: {v} {unit if v != 1 else unit.rstrip('s')}"
        out.append(f'<div class="vc-col" role="listitem" tabindex="0" data-tip="{esc(tip)}" aria-label="{esc(tip)}">'
                   f'<span class="vc-bar" style="height:{100 * v / ymax:.2f}%">{f"<b>{v}</b>" if v else ""}</span>'
                   f'<span class="vc-lbl">{esc(lbl)}</span></div>')
    out.append("</div></div>")
    return Markup("".join(out))


def heat_level(v: int, vmax: int) -> int:
    """0 for zero, else 1..7 on a linear scale (sequential, one hue)."""
    if v <= 0 or vmax <= 0:
        return 0
    return max(1, min(7, 1 + round(6 * v / vmax)))


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
        for i, a in enumerate(self.additives):
            a["summary"] = summary_sentence(a)
            a["differs"] = differs(a)
            a["prev_id"] = self.additives[i - 1]["id"] if i else None
            a["next_id"] = self.additives[i + 1]["id"] if i + 1 < len(self.additives) else None
        self.asset = {}
        g = self.env.globals
        g.update(
            url=url, abs_url=abs_url, SITE_NAME=SITE_NAME, TAGLINE=TAGLINE, PLACES=PLACES,
            PLACES_SHORT=PLACES_SHORT, JUR=JURISDICTIONS, JUR_ORDER=JUR_ORDER, JUR_CODE=JUR_CODE,
            JUR_THE=JUR_THE, STATUSES=STATUSES, TONE_ICON=TONE_ICON, CLASS_ICON=CLASS_ICON,
            CLASS_SYMBOL=CLASS_SYMBOL, icon=icon, LEGAL_UPDATED="1 October 2026",
            fmt_date=fmt_date, REPO_URL=REPO_URL if REPO_URL and REPO_URL != "https://github.com/" else "",
            ld_json=ld_json, ld_breadcrumbs=ld_breadcrumbs, asset=lambda n: self.asset[n],
            data_version=self.data.get("data_version"),
            generated_at=self.data.get("generated_at"),
            sources=self.data.get("sources", {}),
            quality=self.data.get("quality", []),
            EU_HISTORY=self.data.get("eu_history") or {},
            CLASSES=[], status_of=status_of, tone=tone, total=len(self.additives),
            site_config=self.site_config(),
        )

    def site_config(self) -> dict:
        return {
            "jur": [{"k": j, "c": JUR_CODE[j], "s": JURISDICTIONS[j]["short"], "n": JURISDICTIONS[j]["name"],
                     "t": JUR_THE[j]} for j in JUR_ORDER],
            "st": {k: [v[0], v[1], v[2]] for k, v in STATUSES.items()},
            "allowed": sorted(ALLOWED_LIKE), "notAllowed": sorted(NOT_ALLOWED_LIKE),
        }

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
            elif f.is_dir():   # e.g. fonts/: copied as they are (referenced from the CSS)
                shutil.copytree(f, dst / f.name, dirs_exist_ok=True)
                for g in f.iterdir():
                    self.asset[f"{f.name}/{g.name}"] = url(f"/static/{f.name}/{g.name}")

    def allowed_counts(self) -> dict:
        counts = defaultdict(int)
        for a in self.additives:
            for j in JUR_ORDER:
                if allowed(status_of(a, j)):
                    counts[j] += 1
        return counts

    # ------------------------------------------------------------ pages
    def build(self) -> None:
        if self.out.exists():
            shutil.rmtree(self.out)
        self.out.mkdir(parents=True)
        self.copy_static()
        self.build_search_index()
        # Globals used by macros must exist before the first template is rendered
        # (imported macro modules are cached with a copy of the globals).
        self.env.globals["MATRIX"] = self.matrix(self.compare_sets())
        self.env.globals["CLASSES"] = self.class_groups()
        compare = self.build_compare_pages()
        classes = self.build_class_pages()
        self.build_additive_pages(compare)
        self.build_list_page()
        self.build_changes_page()
        self.build_tools()
        self.build_about_pages()
        self.build_data_page()
        self.build_home(compare, classes)
        self.page("/404.html", "404.html", page_title="Page not found", sitemap=False, noindex=True)
        self.build_sitemap_robots()

    def build_search_index(self) -> None:
        rows = []
        for a in self.additives:
            rows.append({
                "i": a["id"], "e": a.get("e") or "", "n": a["name"],
                "k": (["INS " + a["ins"]] if a.get("ins") else []) + a.get("aka", [])[:12], "c": a.get("cas", [])[:3],
                "x": ["e" + a["ins"].lower()] if a.get("ins") else [],
                "s": [status_of(a, j) for j in JUR_ORDER], "g": a.get("classes", [])[:3],
                "d": 1 if a["differs"] else 0,
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
            items = [a for a in self.additives if allowed(status_of(a, a_j)) and not_allowed(status_of(a, b_j))]
            out.append({
                "a": a_j, "b": b_j, "items": items,
                "path": f"/compare/allowed-in-{a_j}-not-{b_j}/",
                "title": f"Food additives allowed in {JUR_THE[a_j]} but not in {JUR_THE[b_j]}",
                "short": f"Allowed in {JUR_CODE[a_j]}, not in {JUR_CODE[b_j]}",
            })
        return out

    def matrix(self, sets: list[dict]) -> dict:
        cells = {(s["a"], s["b"]): s for s in sets}
        vmax = max([len(s["items"]) for s in sets] + [1])
        rows = []
        for a in JUR_ORDER:
            row = []
            for b in JUR_ORDER:
                if a == b:
                    row.append(None)
                else:
                    s = cells[(a, b)]
                    n = len(s["items"])
                    row.append({"a": a, "b": b, "n": n, "path": s["path"], "level": heat_level(n, vmax),
                                "tip": f"Allowed in {JUR_THE[a]}, not in {JUR_THE[b]}: {n} additive"
                                       + ("" if n == 1 else "s")})
            rows.append((a, row))
        return {"rows": rows, "max": vmax}

    def build_compare_pages(self) -> list[dict]:
        sets = self.compare_sets()
        for s in sets:
            n = len(s["items"])
            self.page(s["path"], "compare.html", priority=0.7, page_title=s["title"], s=s,
                      description=f"{n} food additive{'' if n == 1 else 's'} allowed in "
                                  f"{JURISDICTIONS[s['a']]['name_in']} but not in {JURISDICTIONS[s['b']]['name_in']}, "
                                  "with the official status and source for each.")
        self.page("/compare/", "compare_index.html", priority=0.8,
                  page_title="Compare food additive rules: EU, UK, US, Canada and Australia/NZ", sets=sets,
                  description="Pick two places and see which food additives are allowed in one but not the other: "
                              "the EU, UK (GB), US, Canada and Australia/New Zealand.")
        return sets

    def class_groups(self) -> list[dict]:
        groups = defaultdict(list)
        for a in self.additives:
            for c in a.get("classes", []):
                groups[c].append(a)
        classes = []
        for c, items in sorted(groups.items(), key=lambda kv: kv[0].lower()):
            if len(items) < 2:
                continue
            path = f"/class/{slug(c)}/"
            counts = {j: sum(1 for a in items if allowed(status_of(a, j))) for j in JUR_ORDER}
            classes.append({"name": c, "path": path, "items": items, "count": len(items),
                            "icon": CLASS_ICON.get(c, "🧪"), "counts": counts,
                            "differ": sum(1 for a in items if a["differs"])})
        return classes

    def build_class_pages(self) -> list[dict]:
        classes = self.env.globals["CLASSES"]
        for c in classes:
            self.page(c["path"], "class.html", priority=0.6,
                      page_title=f"{c['name']}: food additive status in the EU, UK, US, Canada and Australia/NZ",
                      c=c,
                      description=f"Legal status of {c['count']} food additives in the group '{c['name']}', "
                                  f"compared across {PLACES}.")
        return classes

    def related(self, a: dict) -> list[dict]:
        cls = set(a.get("classes", []))
        pool = [b for b in self.additives if b["id"] != a["id"] and cls & set(b.get("classes", []))]

        def num(x):
            m = re.search(r"\d+", x.get("e") or "")
            return int(m.group()) if m else 99999
        return sorted(pool, key=lambda b: abs(num(b) - num(a)))[:6]

    def build_additive_pages(self, compare: list[dict]) -> None:
        for a in self.additives:
            in_sets = [s for s in compare if a in s["items"]]
            changes = [e for e in self.changelog.get("entries", []) if e.get("id") == a["id"]]
            title_bits = f"{a['e']} {a['name']}" if a.get("e") else a["name"]
            self.page(f"/additive/{a['id']}/", "additive.html", priority=0.7, lastmod=a.get("updated"),
                      a=a, related=self.related(a), in_sets=in_sets, changes=changes,
                      prev=self.by_id.get(a["prev_id"]), next=self.by_id.get(a["next_id"]),
                      page_title=f"{title_bits}: is it allowed in the EU, UK, US, Canada, Australia and NZ?",
                      description=a["summary"] + " Official sources and conditions for each.")

    def build_list_page(self) -> None:
        self.page("/additives/", "list.html", priority=0.9,
                  page_title="All food additives: status table for the EU, UK, US, Canada and Australia/NZ",
                  additives=self.additives,
                  description=f"Search, filter and sort {len(self.additives)} food additives by legal status in "
                              f"{PLACES}.")

    def build_changes_page(self) -> None:
        entries = sorted(self.changelog.get("entries", []), key=lambda e: e.get("date", ""), reverse=True)
        events = self.data.get("eu_history_events", [])
        per_year = Counter(e["date"][:4] for e in events)
        first = int(((self.data.get("eu_history") or {}).get("first") or "2013")[:4])
        last = max([int(e["date"][:4]) for e in events] + [first, int((self.data.get("generated_at") or "2026")[:4])])
        years = [(str(y), per_year.get(str(y), 0)) for y in range(first, last + 1)]
        chart = column_chart(years, title="EU list changes per year", unit="changes") if events else None
        self.page("/changes/", "changes.html", priority=0.7,
                  page_title="Changes in food additive status: weekly checks and EU history since 2013",
                  entries=entries, by_id=self.by_id, eu_events=events, years=years, chart=chart,
                  tracking_since=self.changelog.get("tracking_since"),
                  description="Status changes for food additives detected in the official EU, UK, US, Canadian and "
                              "Australia/New Zealand sources, with dates, plus every EU list change since 2013.")

    def build_tools(self) -> None:
        self.page("/scan/", "scan.html", priority=0.8,
                  page_title="Ingredient label checker: find the additives in any ingredients list",
                  description="Paste an ingredients list and see every E-number or additive in it, with its status "
                              f"in {PLACES}. Works on your device; nothing is uploaded.")
        self.page("/quiz/", "quiz.html", priority=0.6,
                  page_title="Quiz: guess where each food additive is allowed",
                  description="Ten rounds, five places. Can you tell which food additives are allowed in the EU, UK, "
                              "US, Canada and Australia/New Zealand? Answers come from the official lists.")

    def build_about_pages(self) -> None:
        self.page("/about/", "about.html", priority=0.5,
                  page_title="About Additive Status: methods, sources and limits",
                  counts=self.allowed_counts(),
                  description="What Additive Status is, how it collects, matches and checks official food additive "
                              "data from five jurisdictions every week, what each status means and what it does not cover.")
        self.page("/legal/", "legal.html", priority=0.3,
                  page_title="Licences and legal notices",
                  description="Disclaimer, source licences and attribution, font licences, independence and honest-content "
                              "notices for Additive Status, with links to the privacy policy, terms and cookie page.")
        self.page("/privacy/", "privacy.html", priority=0.3,
                  page_title="Privacy policy",
                  description="Additive Status collects no personal data: no accounts, cookies, analytics, ads, emails or "
                              "third-party trackers. What is kept on your device, hosting, and your rights.")
        self.page("/terms/", "terms.html", priority=0.3,
                  page_title="Terms of use",
                  description="Terms of use for Additive Status: a free service with no fees or refunds, not legal advice, "
                              "acceptable use, content licences, warranty and liability.")
        self.page("/cookies/", "cookies.html", priority=0.3,
                  page_title="Cookies and storage",
                  description="Additive Status sets no cookies. See the few settings that can be kept on your device with "
                              "your permission, and delete them in one click.")
        self.page("/accessibility/", "accessibility.html", priority=0.3,
                  page_title="Accessibility statement",
                  description="How Additive Status aims to meet WCAG 2.2 AA: keyboard use, contrast, reduced motion, "
                              "screen readers, charts with tables, known limitations and how to report a problem.")
        terms = sorted(GLOSSARY, key=lambda t: t[0].lower())
        glossary_ld = [{"@type": "DefinedTerm", "name": t, "description": d, "url": abs_url("/glossary/") + "#" + k}
                       for t, k, d in terms]
        self.page("/glossary/", "glossary.html", priority=0.5, terms=terms, glossary_ld=glossary_ld,
                  page_title="Glossary: E-numbers, INS, GRAS, GMP and other food additive terms",
                  description="Plain-English meanings of the terms used on food additive lists: E-number, INS number, "
                              "CAS number, GRAS, GMP, quantum satis, positive list, processing aid and more.")

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
                       [status_of(a, j) for j in JUR_ORDER] +
                       [a["jur"].get(j, {}).get("headline", "") for j in JUR_ORDER] +
                       [abs_url(f"/additive/{a['id']}/")])
        (dl / "additives.csv").write_text(buf.getvalue(), encoding="utf-8")
        sizes = {n: (dl / n).stat().st_size for n in ("additives.json", "additives.csv")}
        self.page("/data/", "data.html", priority=0.5, page_title="Download the food additive status data",
                  sizes=sizes, count=len(self.additives),
                  description=f"Download the combined food additive status dataset for {PLACES} as CSV or JSON, "
                              "with sources and licences.")

    def data_for_download(self) -> dict:
        keep = ("id", "e", "ins", "name", "aka", "cas", "classes", "jur", "updated")
        return {
            "generated_at": self.data.get("generated_at"),
            "data_version": self.data.get("data_version"),
            "licence_note": "Compiled from EU, UK, US, Canadian and Australia/New Zealand official sources; "
                            "see 'sources' for each source's licence and required attribution.",
            "jurisdictions": {j: JURISDICTIONS[j]["name"] for j in JUR_ORDER},
            "sources": self.data.get("sources", {}),
            "statuses": {k: {"label": v[0], "meaning": v[3]} for k, v in STATUSES.items()},
            "additives": [{k: a[k] for k in keep if k in a} for a in self.additives],
        }

    # ------------------------------------------------------------ facts
    def facts(self, counts: dict, sets: list[dict]) -> list[dict]:
        """'Did you know?' cards. Every sentence is computed from the published data."""
        out = []
        total = len(self.additives)

        def add(text, a=None, href=None, kind="fact"):
            out.append({"text": text, "href": href or (url(f"/additive/{a['id']}/") if a else None), "kind": kind})

        # 1. Well-known differences
        wanted = ["e171", "e127", "us-potassium-bromate", "us-brominated-vegetable-oil", "e123", "e952",
                  "e129", "e102", "us-azodicarbonamide", "e173", "e128", "e131", "e161g", "e1519", "e586",
                  "us-citrus-red-no-2", "e925", "e284", "e239", "e999"]
        for i in wanted:
            a = self.by_id.get(i)
            if not a or not a["differs"]:
                continue
            yes = [j for j in JUR_ORDER if allowed(status_of(a, j))]
            no = [j for j in JUR_ORDER if not_allowed(status_of(a, j))]
            add(f"{label(a)}: allowed in {join_words([JUR_THE[j] for j in yes])}, "
                f"but not in {join_words([JUR_THE[j] for j in no])}.", a, kind="differs")

        # 2. Counts per jurisdiction (superlatives only "of the additives tracked here")
        if counts:
            ranked = sorted(JUR_ORDER, key=lambda j: counts[j])
            most, fewest = ranked[-1], ranked[0]
            if counts[most] > counts[ranked[-2]]:
                add(f"Of the {total} additives tracked here, {JUR_THE[most]} allows {counts[most]}, more than any of "
                    f"the other four places.", href=url("/additives/") + f"?allow={most}", kind="count")
            if counts[fewest] < counts[ranked[1]]:
                add(f"Of the {total} additives tracked here, {JUR_THE[fewest]} allows {counts[fewest]}, the fewest "
                    f"of the five places.", href=url("/additives/") + f"?allow={fewest}", kind="count")
        everywhere = [a for a in self.additives if all(allowed(status_of(a, j)) for j in JUR_ORDER)]
        if everywhere:
            add(f"{len(everywhere)} of the {total} additives tracked here are allowed in all five places.",
                href=url("/additives/") + "?allow=" + ",".join(JUR_ORDER), kind="count")
        diff = [a for a in self.additives if a["differs"]]
        add(f"{len(diff)} additives are allowed in at least one of the five places and not allowed in another.",
            href=url("/additives/") + "?differ=1", kind="count")
        biggest = max(sets, key=lambda s: len(s["items"]), default=None)
        if biggest and biggest["items"]:
            add(f"The biggest one-way gap: {len(biggest['items'])} additives are allowed in {JUR_THE[biggest['a']]} "
                f"but not in {JUR_THE[biggest['b']]}.", href=url(biggest["path"]), kind="count")

        # 3. Phase-outs and processing aids
        for a in self.additives:
            ph = [j for j in JUR_ORDER if status_of(a, j) == "phase_out"]
            if ph:
                heads = "; ".join(h for h in (a["jur"][j].get("headline") or "" for j in ph) if h)
                tail = f": {heads[0].lower() + heads[1:]}" if heads else ""
                add(f"{label(a)} is being phased out in {join_words([JUR_THE[j] for j in ph])}{tail}.", a, kind="phase")
        for a in self.additives:
            r = a["jur"].get("anz", {})
            if "processing aid" in (r.get("headline") or "") and a["id"].startswith("us-"):
                add(f"In Australia and New Zealand, {a['name']} is not a permitted food additive, but Schedule 18 of "
                    f"the Food Standards Code lists it as a processing aid.", a, kind="aid")

        # 4. EU history
        ev = self.data.get("eu_history_events", [])
        hist = self.data.get("eu_history") or {}
        if ev and hist.get("versions"):
            added = sum(1 for e in ev if e["change"].startswith("Added"))
            removed = sum(1 for e in ev if e["change"].startswith("Removed"))
            def n_was(n):
                return f"{n} additive was" if n == 1 else f"{n} additives were"
            add(f"Across {hist['versions']} consolidated versions of the EU list since {hist['first'][:4]}, "
                f"{n_was(added)} added and {n_was(removed)} removed.", href=url("/changes/#eu-history"), kind="history")
        # 6. Class facts
        by_class = defaultdict(list)
        for a in self.additives:
            for c in a.get("classes", []):
                by_class[c].append(a)
        for c in ("Colours", "Sweeteners", "Preservatives"):
            items = by_class.get(c, [])
            if len(items) >= 5:
                d = sum(1 for a in items if a["differs"])
                add(f"{d} of the {len(items)} {c.lower()} tracked here are allowed in some of the five places but "
                    f"not in others.", href=url(f"/class/{slug(c)}/"), kind="class")
        return out

    def build_home(self, compare, classes) -> None:
        counts = self.allowed_counts()
        total = len(self.additives)
        diff_count = sum(1 for a in self.additives if a["differs"])
        everywhere = sum(1 for a in self.additives if all(allowed(status_of(a, j)) for j in JUR_ORDER))
        breakdown = {}
        for j in JUR_ORDER:
            sts = [status_of(a, j) for a in self.additives]
            breakdown[j] = {"yes": sum(1 for x in sts if allowed(x)), "no": sum(1 for x in sts if not_allowed(x))}
            breakdown[j]["unk"] = total - breakdown[j]["yes"] - breakdown[j]["no"]
        chart = breakdown_chart([(j, JURISDICTIONS[j]["short"], breakdown[j]) for j in JUR_ORDER], total,
                                title=f"Status of the {total} additives tracked here, in each place")
        examples = [self.by_id[i] for i in ("e171", "e129", "e127", "us-potassium-bromate", "e951",
                                            "us-brominated-vegetable-oil", "e621", "e102") if i in self.by_id]
        recent = sorted(self.changelog.get("entries", []), key=lambda e: e.get("date", ""), reverse=True)[:5]
        eu_recent = self.data.get("eu_history_events", [])[:5]
        # Hero "chromatography" lanes: the dot height encodes the status, the colour is the
        # additive's own dye colour where it has one (decorative; the same data is in the cards).
        dyes = {"e171": "#eef1ff", "e129": "#ff3d5e", "e127": "#ff5fa2", "e102": "#ffd23f", "e951": "#7fe3ff",
                "e621": "#c9d4ff", "us-potassium-bromate": "#b39dff", "us-brominated-vegetable-oil": "#ffb347"}
        height = {"ok": 0.14, "warn": 0.36, "unknown": 0.6, "no": 0.86}
        lab = []
        for a in examples:
            sts = [status_of(a, j) for j in JUR_ORDER]
            lab.append({"i": a["id"], "e": a.get("e") or "", "n": a["name"], "s": sts,
                        "y": [height[tone(x)] for x in sts], "dye": dyes.get(a["id"], "#8fa8ff"),
                        "l": [STATUSES[x][1] for x in sts], "t": [tone(x) for x in sts]})
        story = self.story(total, breakdown, everywhere, diff_count)
        marquee = self.marquee(examples)
        self.page("/", "home.html", priority=1.0,
                  page_title=f"{SITE_NAME}: is this food additive allowed in the EU, UK, US, Canada or Australia?",
                  home=True, compare=compare, classes=classes, counts=counts, chart=chart, breakdown=breakdown, lab=lab,
                  story=story, marquee=marquee,
                  diff_count=diff_count, everywhere=everywhere, facts=self.facts(counts, compare),
                  examples=examples, recent=recent, eu_recent=eu_recent, by_id=self.by_id,
                  description="Search any E-number or additive name and see its official status in the EU, UK (GB), "
                              "US, Canada and Australia/New Zealand side by side, with links to the sources. "
                              "Free, no ads, no tracking.")

    def story(self, total: int, breakdown: dict, everywhere: int, diff_count: int) -> dict:
        """Data for the home page's scroll story: one dot per additive, sorted into piles step by step.

        Every number and sentence is computed from the published data, so the story is true by
        construction. The text is rendered server-side (readable without JavaScript); the dots are
        drawn by motion.js from the compact `rows` list."""
        code = {"ok": "o", "warn": "w", "no": "n", "unknown": "u"}
        rows = []
        for a in self.additives:
            sts = [status_of(a, j) for j in JUR_ORDER]
            groups = ["a" if allowed(s) else "n" if not_allowed(s) else "u" for s in sts]
            summ = "a" if all(g == "a" for g in groups) else "d" if a["differs"] else "r"
            rows.append([a["id"], (a["e"] + " " if a.get("e") else "") + a["name"],
                         "".join(code[tone(s)] for s in sts), "".join(groups) + summ])
        steps = [{"key": "start", "tab": "Start", "title": f"{total} additives",
                  "text": f"Every dot is one of the {total} food additives tracked here. "
                          "Keep scrolling and they sort themselves, one place at a time.",
                  "piles": []}]
        prev = None
        for k, j in enumerate(JUR_ORDER):
            b = breakdown[j]
            text = (f"{b['yes']} allowed, {b['no']} not allowed"
                    + (f" and {b['unk']} unclear" if b["unk"] else "") + ".")
            if prev is not None:
                moved = sum(1 for r in rows if r[3][k] != r[3][k - 1])
                text += f" {moved} additives changed pile compared with {JURISDICTIONS[prev]['short']}."
            if b["unk"]:
                text += " Unclear means not on a list that does not cover everything, or no data. It does not mean banned."
            steps.append({"key": j, "tab": JUR_CODE[j], "title": JURISDICTIONS[j]["name"], "text": text,
                          "piles": [["a", "Allowed", b["yes"]], ["n", "Not allowed", b["no"]], ["u", "Unclear", b["unk"]]]})
            prev = j
        rest = total - everywhere - diff_count
        steps.append({"key": "sum", "tab": "All five", "title": "All five together",
                      "text": f"{everywhere} are allowed in all five places. {diff_count} are allowed in at least one place "
                              f"and not allowed in another. The other {rest} are unclear somewhere, or allowed nowhere.",
                      "piles": [["a", "Allowed in all five", everywhere], ["d", "Allowed in one, not in another", diff_count],
                                ["r", "Unclear somewhere, or allowed nowhere", rest]]})
        return {"rows": rows, "steps": steps, "total": total}

    def marquee(self, examples: list[dict]) -> dict:
        """Two bands of real additives for the moving ticker: famous ones whose status differs between
        places, and ones allowed everywhere."""
        seen = {a["id"] for a in examples}
        differs = [a for a in examples if a["differs"]]
        differs += [a for a in self.additives if a["differs"] and a.get("e") and a["id"] not in seen][:14 - len(differs)]
        same = [a for a in self.additives if a.get("e") and not a["differs"]
                and all(allowed(status_of(a, j)) for j in JUR_ORDER)]
        step = max(1, len(same) // 14)
        same = same[::step][:14]

        def item(a: dict) -> dict:
            sts = [status_of(a, j) for j in JUR_ORDER]
            return {"id": a["id"], "e": a.get("e") or "", "n": a["name"].split(",")[0].split("/")[0].strip(),
                    "t": [tone(s) for s in sts]}
        return {"big": [item(a) for a in differs], "chips": [item(a) for a in same + differs[:4]]}

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
