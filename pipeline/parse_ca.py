"""Parse Health Canada's 15 Lists of Permitted Food Additives.

Output: {normalised_name: record} where record =
  {name, names, lists: [{no, title, url, items: [..], purposes: [..], foods: n}], notes}
"""
from __future__ import annotations

import re
from collections import OrderedDict

from pipeline.common import RAW, load_sources, raw_exists, read_json, read_raw
from pipeline.htmltable import parse, table_to_grid
from pipeline.names import norm_name

LIST_TITLES = {
    "01": "List of Permitted Anticaking Agents",
    "02": "List of Permitted Flour Treatment Agents",
    "03": "List of Permitted Food Colours",
    "04": "List of Permitted Emulsifying, Gelling, Stabilizing or Thickening Agents",
    "05": "List of Permitted Food Enzymes",
    "06": "List of Permitted Firming Agents",
    "07": "List of Permitted Glazing Agents",
    "08": "List of Permitted Food Additives with Other Purposes of Use",
    "09": "List of Permitted Sweeteners",
    "10": "List of Permitted Acidity Regulators and Acid-Reacting Materials",
    "11": "List of Permitted Preservatives",
    "12": "List of Permitted Sequestering Agents",
    "13": "List of Permitted Starch-Modifying Agents",
    "14": "List of Permitted Yeast Foods",
    "15": "List of Permitted Carrier or Extraction Solvents",
}
CLASS_FOR_LIST = {
    "01": "Anticaking agents", "02": "Flour treatment agents", "03": "Colours",
    "04": "Emulsifiers, stabilisers, thickeners and gelling agents", "05": "Enzymes",
    "06": "Firming agents", "07": "Glazing agents", "08": None, "09": "Sweeteners",
    "10": "Acidity regulators", "11": "Preservatives", "12": "Sequestrants",
    "13": "Modified starches", "14": None, "15": "Carriers and solvents",
}
ITEM_RE = re.compile(r"^[A-Z]{1,2}\.\d+[A-Za-z]?$")


def _col(header: list[str], *keys) -> int | None:
    for i, h in enumerate(header):
        hl = h.lower()
        if any(k in hl for k in keys):
            return i
    return None


def clean_additive_name(raw: str) -> str:
    name = raw.split("\n")[0]
    name = re.sub(r"\s*Footnote\s*[\w*†‡]*", "", name)   # footnote link text
    name = re.sub(r"[\*†‡]+", "", name)
    name = re.sub(r"\s*\((?:see|refer)[^)]*\)", "", name, flags=re.I)
    return re.sub(r"\s+", " ", name).strip(" ,;:")


def parse_page(key: str, body: bytes, url: str) -> list[dict]:
    doc = parse(body)
    rows_out = []
    for table in doc.xpath("//table"):
        grid = table_to_grid(table)
        if len(grid) < 2:
            continue
        # header = first row containing an 'additive' column
        h_idx = None
        for i, row in enumerate(grid[:4]):
            if any("additive" in c.lower() for c in row):
                h_idx = i
                break
        if h_idx is None:
            continue
        header = grid[h_idx]
        c_item = _col(header, "item")
        c_add = _col(header, "additive")
        c_food = _col(header, "permitted in", "food")
        if c_food == c_add:
            c_food = None
        c_purpose = _col(header, "purpose")
        c_max = _col(header, "maximum", "level")
        if c_add is None:
            continue
        for row in grid[h_idx + 1:]:
            if len(row) <= c_add:
                continue
            item = row[c_item].strip() if c_item is not None and c_item < len(row) else ""
            if item and not ITEM_RE.match(item):
                item = item.split("\n")[0].strip()
            name = clean_additive_name(row[c_add])
            if not name or name.lower().startswith(("column", "food additive")):
                continue
            foods = row[c_food] if c_food is not None and c_food < len(row) else ""
            rows_out.append({
                "list": key, "item": item, "name": name,
                "foods": foods,
                "purpose": row[c_purpose] if c_purpose is not None and c_purpose < len(row) else "",
                "max": row[c_max] if c_max is not None and c_max < len(row) else "",
                "url": url,
            })
    return rows_out


def parse_all() -> dict:
    cfg = load_sources()["ca_lists"]
    meta = read_json(RAW / "ca_lists" / "meta.json", {}) or {}
    records: "OrderedDict[str, dict]" = OrderedDict()
    per_list = {}
    for key, page in cfg["pages"].items():
        if not raw_exists("ca_lists", f"{key}.html"):
            raise FileNotFoundError(f"ca_lists/{key}.html")
        url = cfg["base"] + page
        rows = parse_page(key, read_raw("ca_lists", f"{key}.html"), url)
        per_list[key] = len(rows)
        for r in rows:
            k = norm_name(r["name"])
            if not k:
                continue
            rec = records.setdefault(k, {"name": r["name"], "names": [], "lists": {}})
            if r["name"] not in rec["names"]:
                rec["names"].append(r["name"])
            L = rec["lists"].setdefault(key, {"no": int(key), "title": LIST_TITLES[key], "url": url,
                                              "items": [], "purposes": [], "food_rows": 0})
            if r["item"] and r["item"] not in L["items"]:
                L["items"].append(r["item"])
            for p in re.split(r"\n|;", r["purpose"] or ""):
                p = re.sub(r"^\([a-z0-9.]+\)\s*", "", p.strip()).strip()
                if p and not re.fullmatch(r"\(?[a-z0-9.]+\)?", p) and p.lower() != "n/a" and p not in L["purposes"]:
                    L["purposes"].append(p)
            if r["foods"]:
                L["food_rows"] += 1
    for rec in records.values():
        rec["lists"] = sorted(rec["lists"].values(), key=lambda x: x["no"])
        rec["classes"] = sorted({CLASS_FOR_LIST[f"{L['no']:02d}"] for L in rec["lists"]
                                 if CLASS_FOR_LIST[f"{L['no']:02d}"]})
    return {"records": records, "rows_per_list": per_list, "meta": meta}


if __name__ == "__main__":
    out = parse_all()
    print(out["rows_per_list"], len(out["records"]))
