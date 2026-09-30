"""Parse Regulation (EU) No 231/2012 (specifications for food additives).

Used only for identifiers and synonyms that help match additives across
jurisdictions: synonyms, CAS numbers (where given), EINECS numbers and
Colour Index numbers.
"""
from __future__ import annotations

import re

from lxml import html as lhtml

from pipeline.common import RAW, read_json, read_raw
from pipeline.htmltable import table_to_grid
from pipeline.names import e_id, e_parts, find_cas
from pipeline.parse_eu import MARK_RE, _linear

HEAD_RE = re.compile(r"^E\s?(\d{3,4})\s?([a-z]?)\s*(?:\((i|ii|iii|iv|v|vi|vii|viii|ix|x)\))?\s+(.+)$")


def parse_html(body: bytes) -> dict:
    doc = lhtml.fromstring(body)
    entries: dict[str, dict] = {}
    cur = None
    for kind, item in _linear(doc):
        if kind == "p":
            t = MARK_RE.sub("", item).strip()
            m = HEAD_RE.match(t)
            if m and re.match(r"^[A-Z0-9(\-][A-Z0-9 ,'\-′()]{3}", m.group(4)) and len(t) < 160:
                key = e_id(int(m.group(1)), m.group(2) or "", m.group(3) or "")
                title = m.group(4).strip()
                cur = entries.setdefault(key, {"key": key, "title": title, "synonyms": [], "cas": [],
                                               "einecs": [], "colour_index": [], "chem_names": []})
            continue
        if cur is None:
            continue
        for row in table_to_grid(item):
            if len(row) < 2:
                continue
            label = MARK_RE.sub("", row[0]).strip().lower()
            value = MARK_RE.sub("", row[1]).strip()
            if not value or value.lower() == label:
                continue
            if label.startswith("synonym"):
                for s in re.split(r";|\n", value):
                    s = s.strip(" .,")
                    if 2 < len(s) < 90 and s not in cur["synonyms"]:
                        cur["synonyms"].append(s)
            elif label.startswith("cas"):
                for c in find_cas(value):
                    if c not in cur["cas"]:
                        cur["cas"].append(c)
            elif label.startswith("einecs") or label.startswith("ec number"):
                for e in re.findall(r"\b\d{3}-\d{3}-\d\b", value):
                    if e not in cur["einecs"]:
                        cur["einecs"].append(e)
            elif label.startswith("colour index"):
                for n in re.findall(r"\b\d{5}\b", value):
                    if n not in cur["colour_index"]:
                        cur["colour_index"].append(n)
            elif label.startswith("chemical name"):
                for s in value.split("\n"):
                    s = re.sub(r"^(I|II|III|IV|V)\s+", "", s).strip()
                    if 3 < len(s) < 120 and s not in cur["chem_names"]:
                        cur["chem_names"].append(s)
    return entries


def parse_all() -> dict:
    meta = read_json(RAW / "eu_specs" / "meta.json", {}) or {}
    return {"entries": parse_html(read_raw("eu_specs", "specs.html")), "meta": meta}


if __name__ == "__main__":
    out = parse_all()["entries"]
    print(len(out))
    for k in ("e129", "e100", "e171", "e200", "e471", "e951", "e160a-i"):
        print(k, out.get(k))
