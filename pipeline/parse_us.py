"""Parse FDA's Substances Added to Food inventory download.

The download is served as '.xls' but may be a real Excel file, an HTML table
or delimited text, so the format is sniffed from the bytes.
"""
from __future__ import annotations

import csv
import io
import re

from pipeline.common import RAW, read_json
from pipeline.htmltable import parse as parse_html, table_to_grid
from pipeline.names import find_cas, norm_name, title_case_chem

SECTION_RE = re.compile(r"\b(\d{2,3})\.(\d{1,4})\b")


def read_grid(body: bytes) -> list[list[str]]:
    if body[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        import xlrd
        book = xlrd.open_workbook(file_contents=body)
        sh = book.sheet_by_index(0)
        return [[str(sh.cell_value(r, c)).strip() for c in range(sh.ncols)] for r in range(sh.nrows)]
    if body[:2] == b"PK":
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(body), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        return [[("" if v is None else str(v)).strip() for v in row] for row in ws.iter_rows(values_only=True)]
    text = body.decode("utf-8", errors="replace")
    if "<table" in text.lower():
        doc = parse_html(body)
        tables = doc.xpath("//table")
        grid: list[list[str]] = []
        for t in tables:
            g = table_to_grid(t)
            if len(g) > len(grid):
                grid = g
        return grid
    dialect = csv.Sniffer().sniff(text[:5000], delimiters=",\t;")
    return [row for row in csv.reader(io.StringIO(text), dialect)]


def find_header(grid):
    for i, row in enumerate(grid[:30]):
        low = [c.lower() for c in row]
        if any(c.strip() == "substance" or c.startswith("substance") for c in low):
            return i
    raise ValueError("FDA file: no header row containing 'Substance'")


def col(header, *keys, exclude=()):
    for i, h in enumerate(header):
        hl = h.lower()
        if any(k in hl for k in keys) and not any(x in hl for x in exclude):
            return i
    return None


def classify(sections: list[tuple[int, int]], flags: set[str]) -> tuple[str, str]:
    """Return (status, headline) from 21 CFR sections and inventory flags."""
    parts = {p for p, _ in sections}
    if "PROHIBITED" in flags or 189 in parts:
        return "prohibited", "Prohibited from use in human food (21 CFR 189)"
    if "DELISTED" in flags or any(p == 81 and s in (10, 30) for p, s in sections):
        return "delisted", "Colour additive delisted (21 CFR 81)"
    if "NLFG" in flags:
        return "delisted", "No longer considered GRAS by the FEMA expert panel (flavouring)"
    labels = []
    if 74 in parts:
        labels.append("Certified colour additive (21 CFR 74)")
    if 73 in parts:
        labels.append("Colour additive exempt from certification (21 CFR 73)")
    if 172 in parts:
        labels.append("Approved direct food additive (21 CFR 172)")
    if 173 in parts:
        labels.append("Secondary direct food additive (21 CFR 173)")
    if 180 in parts:
        labels.append("Interim food additive (21 CFR 180)")
    if 184 in parts:
        labels.append("Affirmed as GRAS (21 CFR 184)")
    if 182 in parts:
        labels.append("Listed as GRAS (21 CFR 182)")
    if 181 in parts:
        labels.append("Prior-sanctioned (21 CFR 181)")
    if labels:
        return "authorised", labels[0]
    if "GRASN" in flags:
        return "authorised", "GRAS notice with no questions from FDA"
    if "FEMA" in flags:
        return "authorised", "Flavouring considered GRAS by the FEMA expert panel"
    if parts & {175, 176, 177, 178, 186}:
        return "not_listed", "Listed only for indirect (food-contact) use"
    return "authorised", "Listed in FDA's Substances Added to Food inventory"


def parse_all() -> dict:
    f = RAW / "us_fda_substances" / "substances.xls"
    meta = read_json(RAW / "us_fda_substances" / "meta.json", {}) or {}
    grid = read_grid(f.read_bytes())
    h = find_header(grid)
    header = [c.strip() for c in grid[h]]
    c_cas = col(header, "cas")
    c_name = col(header, "substance", exclude=("other",))
    c_other = col(header, "other name")
    c_effect = col(header, "technical effect", "used for")
    c_cfr = col(header, "21 cfr", "cfr", "reg")
    if c_name is None:
        raise ValueError(f"FDA file: no Substance column in {header}")
    records = []
    for row in grid[h + 1:]:
        if len(row) <= c_name or not row[c_name].strip():
            continue
        cell = lambda i: row[i].strip() if i is not None and i < len(row) else ""  # noqa: E731
        name_raw = cell(c_name)
        cfr_raw = cell(c_cfr)
        whole = " | ".join(row).upper()
        flags = set()
        if "PROHIBITED" in whole:
            flags.add("PROHIBITED")
        if "DELISTED" in whole:
            flags.add("DELISTED")
        if "NLFG" in whole or "NO LONGER FEMA GRAS" in whole:
            flags.add("NLFG")
        if re.search(r"\bGRN\b|GRAS NOTICE", whole):
            flags.add("GRASN")
        if "FEMA" in whole:
            flags.add("FEMA")
        sections = []
        for m in SECTION_RE.finditer(cfr_raw):
            p, s = int(m.group(1)), int(m.group(2))
            if 70 <= p <= 190:
                sections.append((p, s))
        status, headline = classify(sections, flags)
        effects = [e.strip().title() for e in re.split(r",|;|\n", cell(c_effect)) if e.strip()]
        other = [o.strip() for o in re.split(r";|\n|\|", cell(c_other)) if o.strip()]
        cas = find_cas(cell(c_cas))
        records.append({
            "name": name_raw,
            "display": title_case_chem(name_raw),
            "other_names": other,
            "cas": cas,
            "id_raw": cell(c_cas),
            "effects": effects,
            "cfr": [f"{p}.{s}" for p, s in sections],
            "cfr_raw": cfr_raw,
            "flags": sorted(flags),
            "status": status,
            "headline": headline,
            "key": norm_name(name_raw),
        })
    return {"records": records, "header": header, "meta": meta}


if __name__ == "__main__":
    out = parse_all()
    print(out["header"], len(out["records"]))
