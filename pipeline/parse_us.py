"""Parse FDA's Substances Added to Food inventory download.

The '.xls' download is an HTML table. Cells use Excel text formulas
(=T("74.340")) and HTML line breaks; regulation citations are spread over
columns 'Reg col01..06' (colour additives), 'Reg add01..20' (additives),
'Reg prohibited189' and 'Reg Administrative'. Columns about standards of
identity ('regs Labeling & Standards') are not authorisations and are ignored.
"""
from __future__ import annotations

import csv
import html as htmlmod
import io
import re

from lxml import html as lxml_html

from pipeline.common import RAW, read_json, read_raw
from pipeline.htmltable import parse as parse_html, table_to_grid
from pipeline.names import find_cas, norm_name, title_case_chem

SECTION_RE = re.compile(r"\b(\d{2,3})\.(\d{1,4})\b")
SUFFIX_RE = re.compile(r"\s*--\s*(PROHIBITED WITH EXCEPTIONS|PROHIBITED|DELISTED|NLFG)\s*$", re.I)


def read_grid(body: bytes) -> list[list[str]]:
    if body[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        import xlrd
        book = xlrd.open_workbook(file_contents=body)
        sh = book.sheet_by_index(0)
        return [[str(sh.cell_value(r, c)) for c in range(sh.ncols)] for r in range(sh.nrows)]
    if body[:2] == b"PK":
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(body), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        return [[("" if v is None else str(v)) for v in row] for row in ws.iter_rows(values_only=True)]
    text = body.decode("utf-8", errors="replace")
    if "<table" in text.lower():
        # keep raw cell HTML so <br /> separators survive
        doc = parse_html(body)
        best = []
        for t in doc.xpath("//table"):
            rows = []
            for tr in t.xpath(".//tr"):
                cells = []
                for td in tr.xpath("./td|./th"):
                    inner = (td.text or "") + "".join(
                        lxml_html.tostring(c, encoding="unicode") for c in td)
                    cells.append(inner)
                rows.append(cells)
            if len(rows) > len(best):
                best = rows
        return best
    dialect = csv.Sniffer().sniff(text[:5000], delimiters=",\t;")
    return [row for row in csv.reader(io.StringIO(text), dialect)]


def clean(cell: str) -> str:
    s = cell or ""
    m = re.fullmatch(r'\s*=T\("(.*)"\)\s*', s)
    if m:
        s = m.group(1)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    for _ in range(3):  # the FDA file double-escapes entities (&amp;diams;)
        u = htmlmod.unescape(s)
        if u == s:
            break
        s = u
    return s.strip()


def split_list(cell: str) -> list[str]:
    s = clean(cell).replace("♦", "\n")
    return [x.strip(" ,;") for x in re.split(r"\n", s) if x.strip(" ,;")]


def find_header(grid):
    for i, row in enumerate(grid[:30]):
        low = [clean(c).lower() for c in row]
        if any(c == "substance" for c in low):
            return i
    raise ValueError("FDA file: no header row containing 'Substance'")


FOOD_COLOUR_MAX = 1000
INDIRECT_182 = {70, 90, 99}
# 21 CFR 73 Subpart A sections that allow the colour only in animal feed or pet food
FEED_ONLY_73 = {35, 37, 50, 185, 275, 295, 315, 352, 355}  # 73.1–73.999 and 74.101–74.999 are food uses; 73.1xxx+/74.1xxx+ are drugs, cosmetics, devices


def classify(sections: list[tuple[int, int]], flags: set[str], fema: bool,
             effects: list[str] | None = None) -> tuple[str, str]:
    """Return (status, headline) from 21 CFR sections and inventory flags."""
    parts = {p for p, _ in sections}
    if "PROHIBITED" in flags or "PROHIBITED WITH EXCEPTIONS" in flags or 189 in parts:
        if "PROHIBITED WITH EXCEPTIONS" in flags:
            return "prohibited", "Prohibited in food, with exceptions (21 CFR 189)"
        return "prohibited", "Prohibited from use in human food (21 CFR 189)"
    if "DELISTED" in flags:
        return "delisted", "Colour additive delisted (21 CFR 81)"
    food_col_74 = [s for p, s in sections if p == 74 and s < FOOD_COLOUR_MAX]
    food_col_73 = [s for p, s in sections if p == 73 and s < FOOD_COLOUR_MAX]
    if (food_col_73 or food_col_74) and set(food_col_73) <= FEED_ONLY_73 and not food_col_74 \
            and not parts & {172, 173, 180, 182, 184}:
        secs = ", ".join(f"73.{x}" for x in sorted(food_col_73))
        return "not_authorised", f"Not listed as a colour for human food (listed for animal feed only, 21 CFR {secs})"
    food_col_73 = [s for s in food_col_73 if s not in FEED_ONLY_73]
    # Parts 181 and 182 also hold food-contact (packaging) sanctions: 181.22-181.32 (packaging
    # materials) and 182.70/182.90/182.99 (migration from paper, pesticide adjuvants). Only the
    # remaining sections are direct food uses.
    direct = {p for p, x in sections if p not in (181, 182)
              or (p == 181 and x in (33, 34)) or (p == 182 and x not in INDIRECT_182)}
    indirect_only = bool(parts & {181, 182}) and not (direct & {181, 182})
    parts = (parts - {181, 182}) | (direct & {181, 182})
    labels = []
    colour_first = effects is None or any("color" in e.lower() for e in effects)
    other = []
    # GRAS first: a substance that is GRAS (182/184) and also cited in 172 (often only for a
    # narrow use, e.g. MSG and 172.320) is best described by its GRAS status.
    for part, text in ((184, "Affirmed as GRAS (21 CFR 184)"), (182, "Listed as GRAS (21 CFR 182)"),
                       (172, "Approved food additive (21 CFR 172)"), (173, "Approved secondary direct food additive (21 CFR 173)"),
                       (180, "Interim food additive (21 CFR 180)"), (181, "Prior-sanctioned (21 CFR 181)")):
        if part in parts:
            other.append(text)
    if other and not colour_first:
        labels.extend(other)
    if food_col_74:
        labels.append("Certified colour additive for food (21 CFR " + ", ".join(f"74.{x}" for x in food_col_74) + ")")
    if food_col_73:
        labels.append("Colour additive for food, exempt from certification (21 CFR " + ", ".join(f"73.{x}" for x in food_col_73) + ")")
    if colour_first:
        labels.extend(other)
    if labels:
        return "authorised", labels[0]
    if "NLFG" in flags:
        return "delisted", "No longer considered GRAS by the FEMA expert panel (flavouring)"
    if fema:
        return "authorised", "Flavouring considered GRAS by the FEMA expert panel"
    if parts & {73, 74} and not (food_col_73 or food_col_74) and colour_first:
        return "not_authorised", "Colour additive listed only for drugs, cosmetics or devices, not for food"
    if parts == {81} or (parts and parts <= {81, 70, 71}):
        return "delisted", "Colour additive listing terminated (21 CFR 81)"
    if parts & {175, 176, 177, 178, 186} or indirect_only:
        return "not_listed", "Listed only for indirect (food-contact) uses"
    return "listed_noreg", "In FDA's inventory, but no regulation is cited"


def parse_grid(grid: list[list[str]]) -> tuple[list[dict], list[str]]:
    h = find_header(grid)
    header = [clean(c) for c in grid[h]]
    idx = {n: i for i, n in enumerate(header)}

    def ci(*names):
        for n in names:
            for k, i in idx.items():
                if k.lower() == n.lower():
                    return i
        return None

    c_cas, c_name, c_other = ci("CAS Reg No (or other ID)"), ci("Substance"), ci("Other Names")
    c_effect, c_fema, c_fema_status = ci("Used for (Technical Effect)"), ci("FEMA No"), ci("FEMA status")
    reg_cols = [i for n, i in idx.items() if n.lower().startswith("reg ")]
    records = []
    for row in grid[h + 1:]:
        if c_name is None or len(row) <= c_name:
            continue
        raw_name = clean(row[c_name])
        if not raw_name:
            continue
        cell = lambda i: clean(row[i]) if i is not None and i < len(row) else ""  # noqa: E731
        flags = set()
        m = SUFFIX_RE.search(raw_name)
        name = raw_name
        if m:
            flags.add(m.group(1).upper())
            name = raw_name[:m.start()].strip()
        fema_status = cell(c_fema_status)
        if "no longer fema gras" in fema_status.lower():
            flags.add("NLFG")
        sections = []
        for i in reg_cols:
            for mm in SECTION_RE.finditer(cell(i)):
                p, s = int(mm.group(1)), int(mm.group(2))
                if 2 <= p <= 199 and (p, s) not in sections:
                    sections.append((p, s))
        fema = bool(cell(c_fema))
        effects_raw = split_list(row[c_effect]) if c_effect is not None and c_effect < len(row) else []
        status, headline = classify(sections, flags, fema, effects_raw)
        other = []
        for o in split_list(row[c_other]) if c_other is not None and c_other < len(row) else []:
            if o and o.upper() != name.upper() and o not in other:
                other.append(o)
        effects = [e.strip().capitalize() for e in split_list(row[c_effect]) if e.strip()] if c_effect is not None and c_effect < len(row) else []
        cas = find_cas(cell(c_cas))
        ci_numbers = sorted({n for o in other + [name] for n in re.findall(r"\bC\.?\s?I\.?\s*(?:NO\.?\s*)?(\d{5})\b", o.upper())})
        records.append({
            "name": name, "display": title_case_chem(name), "other_names": other[:40],
            "cas": cas, "id_raw": cell(c_cas), "effects": effects,
            "cfr": [f"{p}.{s}" for p, s in sections], "flags": sorted(flags),
            "fema": cell(c_fema) or None, "fema_status": fema_status or None,
            "colour_index": ci_numbers,
            "status": status, "headline": headline, "key": norm_name(name),
        })
    return records, header


def parse_all() -> dict:
    meta = read_json(RAW / "us_fda_substances" / "meta.json", {}) or {}
    grid = read_grid(read_raw("us_fda_substances", "substances.xls"))
    records, header = parse_grid(grid)
    note = clean(grid[0][0]) if grid and grid[0] else ""
    m = re.search(r"Last updated (\d{1,2})/(\d{1,2})/(\d{4})", note)
    updated = f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}" if m else None
    return {"records": records, "header": header, "meta": meta, "inventory_updated": updated}


if __name__ == "__main__":
    from collections import Counter
    out = parse_all()
    print(len(out["records"]), out["inventory_updated"])
    print(Counter(r["status"] for r in out["records"]))
    for r in out["records"]:
        if r["name"] in ("FD&C RED NO. 3", "FD&C RED NO. 40", "TITANIUM DIOXIDE", "POTASSIUM BROMATE", "ORANGE B",
                         "ASPARTAME", "CALCIUM CYCLAMATE", "VANILLIN", "D&C RED NO. 6"):
            print(r["name"], r["cfr"], r["flags"], r["status"], r["headline"], r["colour_index"], r["effects"][:3])
