"""Parse Wikidata SPARQL results: E-number -> labels, aliases, CAS."""
from __future__ import annotations

from pipeline.common import RAW, read_json
from pipeline.names import cas_valid, e_id, e_parts


def parse_all() -> dict:
    data = read_json(RAW / "wikidata" / "enumbers.json")
    by_e: dict[str, dict] = {}
    if not data:
        return {"by_e": by_e, "rows": 0}
    rows = data.get("results", {}).get("bindings", [])
    for b in rows:
        v = lambda k: b.get(k, {}).get("value", "")  # noqa: E731
        raw_e = v("enumber")
        p = e_parts(raw_e if raw_e.upper().startswith("E") else "E" + raw_e)
        if not p:
            continue
        key = e_id(*p)
        qid = v("item").rsplit("/", 1)[-1]
        rec = by_e.setdefault(key, {"qids": [], "labels": [], "aliases": [], "cas": []})
        if qid and qid not in rec["qids"]:
            rec["qids"].append(qid)
        lab = v("label")
        if lab and lab not in rec["labels"]:
            rec["labels"].append(lab)
        for a in v("aliases").split("|"):
            a = a.strip()
            if a and a not in rec["aliases"] and len(a) <= 80:
                rec["aliases"].append(a)
        c = v("cas").strip()
        if c and cas_valid(c) and c not in rec["cas"]:
            rec["cas"].append(c)
    return {"by_e": by_e, "rows": len(rows)}
