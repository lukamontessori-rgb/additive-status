"""Parse Annex II of the consolidated Regulation (EC) No 1333/2008.

Annex II has five parts:
  A  introduction          B  list of all additives (E-number, name)
  C  groups of additives   D  food categories
  E  authorised additives and conditions of use per food category

We read B (the Union list), C (group membership) and E (which additives,
directly or through a group, have at least one currently applicable use).
EUR-Lex marks amended passages with ▼M<n>; the header table maps each
marker to the amending regulation, which gives a dated "last changed by".
"""
from __future__ import annotations

import re
from collections import OrderedDict, defaultdict
from datetime import date

from lxml import html as lhtml

from pipeline.common import RAW, read_json, read_raw
from pipeline.htmltable import table_to_grid
from pipeline.names import e_display, e_id, e_parts, e_sort

PART_RE = re.compile(r"^PART\s+([A-E])$", re.I)
GROUP_RE = re.compile(r"^Group\s+(I|II|III|IV)\b", re.I)
MARK_RE = re.compile(r"[►▼]\s*(M\d+|A\d+|C\d+)")
E_CELL_RE = re.compile(r"^E[\s ]*\d{3,4}")
FOOTREF_RE = re.compile(r"\(\*\s*(\d+)\s*\)")
B_SECTIONS = {"1": "Colours", "2": "Sweeteners", "3": "Additives other than colours and sweeteners"}
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], start=1)}
DATE_RE = re.compile(r"(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})", re.I)


def parse_date(text: str) -> date | None:
    m = DATE_RE.search(text or "")
    if not m:
        return None
    return date(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1)))


def _linear(doc):
    """Yield ('p', text) and ('table', element) in document order (top-level tables only)."""
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


def amendment_list(doc) -> dict[str, dict]:
    """Marker (M1 …) -> {title, act, adopted, oj_date, url}."""
    out = {}
    for t in doc.xpath("//table")[:6]:
        for row in table_to_grid(t):
            if len(row) < 2:
                continue
            m = re.match(r"^►?\s*(M\d+)$", row[0].strip())
            if not m or m.group(1) in out:
                continue
            title = re.sub(r"\s+", " ", row[1].replace("Amended by:", "")).strip()
            act = None
            am = re.search(r"(Regulation|Directive|Decision)\s+\((EU|EC|EEC)\)\s+(No\s+)?(\d+)/(\d+)", title, re.I)
            url = None
            if am:
                n1, n2 = am.group(4), am.group(5)
                year, num = (n1, n2) if len(n1) == 4 and int(n1) > 1950 else (n2, n1)
                kind = am.group(1).lower()
                act = f"{am.group(1).title()} ({am.group(2).upper()}) {'No ' if am.group(3) else ''}{n1}/{n2}"
                if kind in ("regulation", "directive", "decision"):
                    eli = {"regulation": "reg", "directive": "dir", "decision": "dec"}[kind]
                    url = f"https://eur-lex.europa.eu/eli/{eli}/{year}/{int(num)}/oj"
            adopted = parse_date(title)
            oj = row[4].strip() if len(row) > 4 else ""
            dm = re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", oj)
            out[m.group(1)] = {
                "title": title, "act": act or title, "url": url,
                "adopted": adopted.isoformat() if adopted else None,
                "oj_date": f"{dm.group(3)}-{int(dm.group(2)):02d}-{int(dm.group(1)):02d}" if dm else None,
            }
    return out


def range_key(text: str) -> str:
    """Normalise an E-number range label, e.g. 'E 200 – 213' -> 'e200-213'."""
    t = MARK_RE.sub("", text).lower()
    t = t.split(":")[0]
    t = re.sub(r"[–—−]", "-", t)
    t = re.sub(r"\band\b", ",", t)
    t = re.sub(r"[^0-9a-z,;\-]", "", t)
    t = t.replace(";", ",")
    return t


def expand_range(text: str, listed_keys) -> set[str]:
    """Fallback numeric expansion of 'E 338-341, E 343 and E 450-452'."""
    keys = set()
    t = re.sub(r"[–—−]", "-", MARK_RE.sub("", text))
    lm = re.fullmatch(r"\s*E\s*(\d{3,4})([a-z])((?:\s*,\s*[a-z])+)\s*", t)
    if lm:
        letters = [lm.group(2)] + re.findall(r"[a-z]", lm.group(3))
        return {e_id(int(lm.group(1)), L) for L in letters}
    for part in re.split(r",|;|\band\b", t):
        nums = re.findall(r"(\d{3,4})([a-z]?)", part)
        if len(nums) == 2 and "-" in part:
            lo, hi = int(nums[0][0]), int(nums[1][0])
            for k in listed_keys:
                m = re.match(r"e(\d+)", k)
                if m and lo <= int(m.group(1)) <= hi:
                    keys.add(k)
        elif len(nums) == 1:
            p = e_parts("E " + nums[0][0] + nums[0][1])
            if p:
                keys.add(e_id(*p))
    return keys


GENERIC = {"acid", "acids", "sodium", "potassium", "calcium", "ammonium", "magnesium", "and", "salts",
           "salt", "its", "the", "of", "esters", "ester", "with", "from"}


def _stems(text: str) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z]{3,}", (text or "").lower()):
        if w in GENERIC:
            continue
        out.add(w[:-1] if w.endswith("s") and len(w) > 4 else w)
    return out


def filter_by_name(keys: set[str], row_name: str, listed: dict) -> set[str]:
    """Keep only range members whose Part B name shares a word with the row's name."""
    rs = _stems(row_name)
    if not rs:
        return keys
    kept = set()
    for k in keys:
        base = re.match(r"(e\d+[a-z]?)", k).group(1)
        nm = (listed.get(k) or listed.get(base) or {}).get("name", "")
        ms = _stems(nm)
        if ms & rs or any(a.startswith(b) or b.startswith(a) for a in ms for b in rs if min(len(a), len(b)) >= 4):
            kept.add(k)
    return kept or keys


def group_by_span(e_cell: str, groups: dict) -> str | None:
    """Match 'E 338-452' to the Part C group whose members run from E 338 to E 452."""
    nums = [int(n) for n in re.findall(r"(\d{3,4})", e_cell)]
    if len(nums) != 2:
        return None
    lo, hi = nums
    for g, members in groups.items():
        if not g.startswith("R:"):
            continue
        mn = [int(re.match(r"e(\d+)", m).group(1)) for m in members]
        if mn and min(mn) == lo and max(mn) == hi:
            return g
    return None


def period_applies(restriction: str, today: date) -> bool:
    """False if a 'Period of application' clause has ended or not yet started."""
    if "period of application" not in restriction.lower():
        return True
    low = restriction.lower()
    ok = True
    um = re.search(r"until\s+(\d{1,2}\s+\w+\s+\d{4})", low)
    if um:
        d = parse_date(um.group(1))
        if d and d < today:
            ok = False
    fm = re.search(r"from\s+(\d{1,2}\s+\w+\s+\d{4})", low)
    if fm:
        d = parse_date(fm.group(1))
        if d and d > today:
            ok = False
    return ok


def parse_html(body: bytes, today: date | None = None) -> dict:
    today = today or date.today()
    doc = lhtml.fromstring(body)
    acts = amendment_list(doc)
    in_annex2 = False
    part = None
    b_section = None
    group = None
    listed: "OrderedDict[str, dict]" = OrderedDict()
    groups: dict[str, set[str]] = defaultdict(set)       # "I".."IV" and range labels
    uses: dict[str, set[str]] = defaultdict(set)         # key -> categories (current)
    expired: dict[str, set[str]] = defaultdict(set)      # key -> categories only in expired/future rows
    use_marks: dict[str, set[str]] = defaultdict(set)    # key -> markers of rows that mention it
    group_uses: dict[str, set[str]] = defaultdict(set)
    categories: "OrderedDict[str, str]" = OrderedDict()
    footnotes_b: dict[str, str] = {}
    parts_seen = []
    unresolved: dict[str, int] = defaultdict(int)

    for kind, item in _linear(doc):
        if kind == "p":
            t = item
            if re.fullmatch(r"ANNEX\s+II", t, re.I):
                in_annex2, part = True, None
                continue
            if re.fullmatch(r"ANNEX\s+(III|IV|V)", t, re.I):
                if in_annex2:
                    in_annex2, part = False, None
                continue
            if not in_annex2:
                continue
            m = PART_RE.match(t)
            if m:
                part = m.group(1).upper()
                parts_seen.append(part)
                group = None
                continue
            if part == "B":
                m = re.match(r"^(\d)\.\s+(Colours|Sweeteners|Additives other)", t, re.I)
                if m:
                    b_section = m.group(1)
                fm = re.match(r"^(?:[►▼]\s*M\d+\s*)?\(\*\s*(\d+)\s*\)\s*(.+?)\s*◄?$", t)
                if fm:
                    footnotes_b[fm.group(1)] = fm.group(2)
            if part == "C":
                m = re.match(r"^\(\d\)\s*Group\s+(I|II|III|IV)\b", t, re.I)
                if m:
                    group = m.group(1).upper()
                    continue
                if re.match(r"^\(5\)", t):
                    group = None
                    continue
                m = re.match(r"^\(([a-z](?:\.\d)?|s\.\d)\.?\)\s*(E\s*\d.*)$", t)
                if m:
                    group = "R:" + range_key(m.group(2))
                    continue
                if re.fullmatch(r"\(([a-z](?:\.\d)?|s\.\d)\.?\)", t):
                    group = "PENDING"
                    continue
                m = re.match(r"^(E\s*\d{3}\s*[–—-]\s*\d{3}.*)$", t)
                if m and group in (None, "PENDING"):
                    group = "R:" + range_key(m.group(1))
                    continue
            continue

        if not in_annex2 or part is None:
            continue
        grid = table_to_grid(item)
        if part == "B":
            marker = None
            for row in grid:
                cells = [c.strip() for c in row]
                if cells and MARK_RE.match(cells[0]) and len(set(cells)) == 1:
                    marker = MARK_RE.match(cells[0]).group(1)
                    continue
                if len(cells) < 2 or not E_CELL_RE.match(cells[0]):
                    continue
                p = e_parts(cells[0])
                if not p:
                    continue
                key = e_id(*p)
                raw_name = MARK_RE.sub("", cells[1]).strip()
                refs = FOOTREF_RE.findall(raw_name)
                name = FOOTREF_RE.sub("", raw_name)
                pm = re.search(r"\s*\((\d+)\)\s*$", name)
                if pm and not re.search(r"\(\d+\)\s*stearate", name):
                    refs.append("p" + pm.group(1))
                    name = name[:pm.start()].strip()
                name = re.sub(r"\s+", " ", name).strip()
                if key not in listed:
                    listed[key] = {"key": key, "e": e_display(*p), "sort": e_sort(*p), "name": name,
                                   "section": B_SECTIONS.get(b_section or "", ""), "marker": marker,
                                   "footrefs": refs}
        elif part == "C":
            for row in grid:
                cells = [c.strip() for c in row if c.strip()]
                if not cells or group in (None, "PENDING"):
                    continue
                if E_CELL_RE.match(cells[0]):
                    p = e_parts(cells[0])
                    if p:
                        groups[group].add(e_id(*p))
                        groups[group].add(e_id(p[0], p[1]))
        elif part == "E":
            marker = None
            header_seen = False
            for row in grid:
                cells = [c.strip() for c in row]
                if not header_seen:
                    if cells and cells[0].lower().startswith("category"):
                        header_seen = True
                        continue
                if len(cells) < 3:
                    continue
                cat = cells[0]
                rest = cells[1:]
                if not re.fullmatch(r"\d{1,2}(?:\.\d{1,2}){0,3}", cat):
                    mm = MARK_RE.match(cat)
                    if mm and len(set(cells)) == 1:
                        marker = mm.group(1)
                    continue
                if MARK_RE.match(rest[0]) and len(set(rest)) <= 2:
                    marker = MARK_RE.match(rest[0]).group(1)
                    continue
                if len(set(rest)) == 1:  # category title row
                    categories.setdefault(cat, MARK_RE.sub("", rest[0]).strip()[:200])
                    continue
                e_cell = MARK_RE.sub("", rest[0]).strip()
                name = rest[1] if len(rest) > 1 else ""
                if re.match(r"^\(\d+\)\s*:", name) or MARK_RE.match(name):
                    continue  # footnote text or marker spilling over a rowspan
                restriction = rest[4] if len(rest) > 4 else ""
                applies = period_applies(restriction, today)
                gm = GROUP_RE.match(e_cell)
                if gm:
                    if applies:
                        group_uses[gm.group(1).upper()].add(cat)
                    continue
                if not E_CELL_RE.match(e_cell):
                    continue
                keys: set[str] = set()
                rk = "R:" + range_key(e_cell)
                if rk in groups:
                    keys = set(groups[rk])
                elif re.search(r"\d{3,4}\s*[a-z]?\s*[-–—]\s*(E\s*)?\d{3,4}|,|\band\b", e_cell):
                    alias = group_by_span(e_cell, groups)
                    if alias:
                        keys = set(groups[alias])
                    else:
                        keys = filter_by_name(expand_range(e_cell, listed.keys()), name, listed)
                        unresolved[e_cell] += 1
                else:
                    p = e_parts(e_cell)
                    if p:
                        keys = {e_id(*p), e_id(p[0], p[1])}
                for k in keys:
                    (uses if applies else expired)[k].add(cat)
                    if marker:
                        use_marks[k].add(marker)

    for g, cats in group_uses.items():
        for k in groups.get(g, ()):
            uses[k] |= cats

    full = re.sub(r"\s+", " ", doc.text_content())
    b_start = full.find("LIST OF ALL ADDITIVES")
    c_start = full.find("DEFINITIONS OF GROUPS OF ADDITIVES")
    part_b_text = full[b_start:c_start] if 0 <= b_start < c_start else ""
    for m in re.finditer(r"\(\s*\*\s*(\d+)\s*\)\s*([A-Z][a-z][^◄]{25,600}?)(?=\s*◄|\s*[►▼]M\d|\s*\d\.\s+(?:Colours|Sweeteners|Additives)|$)", part_b_text):
        if not re.match(r"E \d", m.group(2)):
            footnotes_b.setdefault(m.group(1), m.group(2).strip())
    for m in re.finditer(r"(?<![\w*])\((\d)\)\s*((?:authorised until|Period of application)[^.◄]*\.?)", part_b_text):
        footnotes_b.setdefault("p" + m.group(1), m.group(2).strip())
    notes = {}
    for k, r in listed.items():
        texts = [footnotes_b[n] for n in r["footrefs"] if n in footnotes_b]
        if texts:
            notes[k] = texts
    annex3 = set()
    a3 = [m.start() for m in re.finditer(r"ANNEX III", full)]
    a4 = [m.start() for m in re.finditer(r"ANNEX IV", full)]
    if a3:
        start = a3[-1]
        end = next((x for x in a4 if x > start), len(full))
        for m in re.finditer(r"E\s*\d{3,4}[a-z]?(?:\s*\((?:i|ii|iii|iv|v)\))?", full[start:end]):
            p = e_parts(m.group(0))
            if p:
                annex3.add(e_id(*p))
                annex3.add(e_id(p[0], p[1]))
    return {
        "listed": listed,
        "groups": {g: sorted(v) for g, v in groups.items()},
        "uses": {k: sorted(v) for k, v in uses.items()},
        "expired_only": {k: sorted(v - uses.get(k, set())) for k, v in expired.items() if v - uses.get(k, set())},
        "use_marks": {k: sorted(v) for k, v in use_marks.items()},
        "group_uses": {g: sorted(v) for g, v in group_uses.items()},
        "categories": categories, "acts": acts, "parts_seen": parts_seen,
        "notes": notes, "unresolved_ranges": dict(unresolved), "annex3": sorted(annex3),
    }


def parse_all(today: date | None = None) -> dict:
    meta = read_json(RAW / "eu_annex2" / "meta.json", {}) or {}
    out = parse_html(read_raw("eu_annex2", "annex2.html"), today)
    out["meta"] = meta
    out["celex"] = (meta.get("files", {}).get("annex2.html", {}) or {}).get("celex")
    return out


def eu_uses_for(parsed: dict, key: str) -> list[str]:
    """Categories where an additive (or its base E-number) has a current use."""
    cats = set(parsed["uses"].get(key, []))
    m = re.match(r"(e\d+[a-z]?)-", key)
    if m:
        cats |= set(parsed["uses"].get(m.group(1), []))
    return sorted(cats)


def last_change(parsed: dict, key: str) -> dict | None:
    """Most recent amending act that touched this additive's Part B entry or Part E rows."""
    marks = set(parsed["use_marks"].get(key, []))
    b = parsed["listed"].get(key, {}).get("marker")
    if b:
        marks.add(b)
    best = None
    for m in marks:
        a = parsed["acts"].get(m)
        if a and (a.get("oj_date") or a.get("adopted")):
            d = a.get("oj_date") or a.get("adopted")
            if best is None or d > best[0]:
                best = (d, m, a)
    if not best:
        return None
    return {"marker": best[1], **best[2]}


if __name__ == "__main__":
    out = parse_all()
    print("parts", out["parts_seen"], "listed", len(out["listed"]), "with uses",
          sum(1 for k in out["listed"] if eu_uses_for(out, k)), "categories", len(out["categories"]),
          "groups", {g: len(v) for g, v in out["groups"].items()})
    print("no uses:", [(k, out["listed"][k]["name"]) for k in out["listed"] if not eu_uses_for(out, k)])
    print("unresolved ranges:", out["unresolved_ranges"])
    print("notes:", out["notes"])
