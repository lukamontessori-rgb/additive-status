"""Parse Annex II of the consolidated Regulation (EC) No 1333/2008 (EUR-Lex HTML).

Annex II has five parts:
  A  introduction          B  list of all additives (E-number, name)
  C  groups of additives   D  food categories
  E  authorised additives and conditions of use per food category

We read B (the Union list), C (group membership) and E (which additives,
directly or through a group, have at least one authorised use), plus the
amending-act markers (►M1 … ►M125) that EUR-Lex prints next to changed
entries.
"""
from __future__ import annotations

import re
from collections import OrderedDict, defaultdict

from lxml import html as lhtml

from pipeline.common import RAW, read_json
from pipeline.htmltable import cell_text, table_to_grid
from pipeline.names import e_display, e_id, e_parts, e_sort

PART_RE = re.compile(r"^PART\s+([A-E])\b", re.I)
GROUP_RE = re.compile(r"\bGroup\s+(I|II|III|IV|V)\b(?![a-z])", re.I)
CAT_RE = re.compile(r"^(\d{2}(?:\.\d{1,2}){0,3})\.?\s+(\S.{2,})$")
MARK_RE = re.compile(r"►\s*(M\d+|A\d+|C\d+)")
E_CELL_RE = re.compile(r"^E[\s ]*\d{3,4}")
B_SECTIONS = {"1": "Colours", "2": "Sweeteners", "3": "Additives other than colours and sweeteners"}


def _linear(doc):
    """Yield ('p', text) and ('table', element) in document order, skipping nested tables."""
    body = doc.find(".//body")
    if body is None:
        body = doc
    for el in body.iter():
        if not isinstance(el.tag, str):
            continue
        tag = el.tag.lower()
        if tag == "table":
            if el.xpath("ancestor::table"):
                continue
            yield "table", el
        elif tag in ("p", "h1", "h2", "h3", "h4", "div") and not el.xpath("ancestor::table"):
            if tag == "div" and el.xpath(".//p|.//table|.//div"):
                continue
            t = re.sub(r"\s+", " ", el.text_content()).strip()
            if t:
                yield "p", t


def amendment_list(doc) -> dict[str, str]:
    """Map marker (M1…) -> amending act title from the header list."""
    out = {}
    for tr in doc.xpath("//table[.//*[contains(text(),'►M1')] or .//*[contains(text(),'M1')]]//tr"):
        cells = [re.sub(r"\s+", " ", c.text_content()).strip() for c in tr.xpath("./td|./th")]
        if len(cells) >= 2:
            m = re.match(r"^►?\s*(M\d+|A\d+|C\d+)$", cells[0])
            if m and m.group(1) not in out:
                out[m.group(1)] = cells[1]
    return out


def parse_html(body: bytes) -> dict:
    doc = lhtml.fromstring(body)
    acts = amendment_list(doc)
    in_annex2 = False
    part = None
    b_section = None
    group = None
    category = None
    listed: "OrderedDict[str, dict]" = OrderedDict()
    groups: dict[str, set[str]] = defaultdict(set)
    uses: dict[str, set[str]] = defaultdict(set)          # key -> set(category)
    group_uses: dict[str, set[str]] = defaultdict(set)    # group -> set(category)
    use_rows: dict[str, list[dict]] = defaultdict(list)
    categories: "OrderedDict[str, str]" = OrderedDict()
    seen_parts = []

    for kind, item in _linear(doc):
        if kind == "p":
            t = item
            if re.fullmatch(r"ANNEX\s+II", t, re.I):
                in_annex2 = True
                part = None
                continue
            if re.fullmatch(r"ANNEX\s+(III|IV|V)", t, re.I):
                if in_annex2:
                    in_annex2 = False
                    part = None
                continue
            if not in_annex2:
                continue
            m = PART_RE.match(t)
            if m and len(t) < 12:
                part = m.group(1).upper()
                seen_parts.append(part)
                group = None
                category = None
                continue
            if part == "B":
                m = re.match(r"^(\d)\.\s+(Colours|Sweeteners|Additives other)", t, re.I)
                if m:
                    b_section = m.group(1)
            if part == "C":
                m = re.match(r"^\(?\d+\)?\s*Group\s+(I|II|III|IV|V)\b", t, re.I) or re.match(r"^Group\s+(I|II|III|IV|V)\b", t, re.I)
                if m:
                    group = m.group(1).upper()
            continue

        if not in_annex2 or part is None:
            continue
        grid = table_to_grid(item)
        if part == "B":
            for row in grid:
                cells = [c for c in row if c]
                if len(cells) < 2 or not E_CELL_RE.match(cells[0]):
                    continue
                p = e_parts(cells[0])
                if not p:
                    continue
                key = e_id(*p)
                name = MARK_RE.sub("", cells[1]).strip()
                name = re.sub(r"\s*\(\*+\d*\)\s*$", "", name).strip()
                marks = sorted(set(MARK_RE.findall(" ".join(row))))
                if key not in listed:
                    listed[key] = {"key": key, "e": e_display(*p), "sort": e_sort(*p), "name": name,
                                   "section": B_SECTIONS.get(b_section or "", ""), "marks": marks,
                                   "note": " ".join(row[2:]).strip() if len(row) > 2 else ""}
        elif part == "C":
            for row in grid:
                cells = [c for c in row if c]
                if not cells:
                    continue
                gm = GROUP_RE.search(cells[0]) if len(cells) == 1 else None
                if gm:
                    group = gm.group(1).upper()
                    continue
                if group and E_CELL_RE.match(cells[0]):
                    p = e_parts(cells[0])
                    if p:
                        groups[group].add(e_id(*p))
                        # E 160a includes E 160a(i)… : also register the base key.
                        groups[group].add(e_id(p[0], p[1]))
        elif part == "E":
            for row in grid:
                cells = [c.strip() for c in row]
                nonempty = [c for c in cells if c]
                if not nonempty:
                    continue
                # Category heading rows: "01.1 Unflavoured pasteurised …" (often in one merged cell)
                joined = " ".join(dict.fromkeys(nonempty))
                cm = CAT_RE.match(MARK_RE.sub("", joined).strip())
                if cm and not any(E_CELL_RE.match(c) for c in nonempty) and not GROUP_RE.search(nonempty[0]):
                    category = cm.group(1)
                    categories.setdefault(category, cm.group(2)[:200])
                    continue
                # Some tables put the category number in the first column of every row.
                if re.fullmatch(r"\d{2}(?:\.\d{1,2}){0,3}", nonempty[0]) and len(nonempty) > 1:
                    category = nonempty[0]
                    categories.setdefault(category, "")
                    nonempty = nonempty[1:]
                if category is None:
                    continue
                first = MARK_RE.sub("", nonempty[0]).strip()
                gm = GROUP_RE.match(first)
                if gm:
                    group_uses[gm.group(1).upper()].add(category)
                    continue
                if E_CELL_RE.match(first):
                    # a cell can hold a range like "E 200 – 213"
                    keys = set()
                    rng = re.match(r"^E\s*(\d{3,4})\s*[–-]\s*(?:E\s*)?(\d{3,4})$", first)
                    if rng:
                        lo, hi = int(rng.group(1)), int(rng.group(2))
                        keys = {k for k in listed if lo <= int(re.match(r"e(\d+)", k).group(1)) <= hi}
                    else:
                        p = e_parts(first)
                        if p:
                            keys = {e_id(*p), e_id(p[0], p[1])}
                    for k in keys:
                        uses[k].add(category)
                    if len(use_rows[first]) < 400:
                        use_rows[first].append({"cat": category, "cells": nonempty[1:5]})

    # resolve group uses onto members
    for g, cats in group_uses.items():
        for k in groups.get(g, ()):
            uses[k] |= cats

    return {
        "listed": listed, "groups": {g: sorted(v) for g, v in groups.items()},
        "uses": {k: sorted(v) for k, v in uses.items()},
        "group_uses": {g: sorted(v) for g, v in group_uses.items()},
        "categories": categories, "acts": acts, "parts_seen": seen_parts,
        "use_rows": use_rows,
    }


def parse_all() -> dict:
    f = RAW / "eu_annex2" / "annex2.html"
    meta = read_json(RAW / "eu_annex2" / "meta.json", {}) or {}
    out = parse_html(f.read_bytes())
    out["meta"] = meta
    out["celex"] = (meta.get("files", {}).get("annex2.html", {}) or {}).get("celex")
    return out


def eu_uses_for(parsed: dict, key: str) -> list[str]:
    """Categories where an additive (or its base E-number) has a use."""
    cats = set(parsed["uses"].get(key, []))
    m = re.match(r"(e\d+[a-z]?)-", key)
    if m:
        cats |= set(parsed["uses"].get(m.group(1), []))
    return sorted(cats)


if __name__ == "__main__":
    out = parse_all()
    print("parts", out["parts_seen"], "listed", len(out["listed"]), "with uses",
          sum(1 for k in out["listed"] if eu_uses_for(out, k)), "categories", len(out["categories"]),
          "groups", {g: len(v) for g, v in out["groups"].items()})
