"""Parse the FSA Regulated Products register (food additive authorisations)."""
from __future__ import annotations

import json

from pipeline.common import RAW, read_json, read_raw
from pipeline.names import e_display, e_id, e_parts


def labels(v) -> list[str]:
    """Collect prefLabel/label/code strings from nested JSON-LD-ish values."""
    out = []
    if v is None:
        return out
    if isinstance(v, str):
        return [v]
    if isinstance(v, list):
        for x in v:
            out.extend(labels(x))
        return out
    if isinstance(v, dict):
        for k in ("prefLabel", "label", "name", "code"):
            if k in v:
                out.extend(labels(v[k]))
                break
    return out


def first(v, default=""):
    ls = labels(v)
    return ls[0] if ls else default


def parse_all() -> dict:
    data = json.loads(read_raw("uk_fsa", "authorisations.json"))
    meta = read_json(RAW / "uk_fsa" / "meta.json", {}) or {}
    records = {}
    unparsed = []
    for it in data.get("items", []):
        code = first(it.get("idCode"))
        p = e_parts(code)
        if not p:
            unparsed.append(code or it.get("@id", "?"))
            continue
        key = e_id(*p)
        subst = it.get("regulatedSubstance") or []
        name = first(subst)
        groups = []
        for s in subst if isinstance(subst, list) else [subst]:
            groups.extend(labels((s or {}).get("memberOf")))
        phase = first(it.get("phase"), "Unknown")
        nations = []
        for jd in it.get("jurisdictionDetail") or []:
            nations.extend(labels(jd.get("jurisdiction") if isinstance(jd, dict) else jd))
        terms = labels(it.get("termsOfAuthorisation"))
        rec = records.setdefault(key, {
            "key": key, "e": e_display(*p), "name": name, "groups": [], "phases": [],
            "nations": [], "terms": [], "last_modified": None, "notes": [], "url": it.get("@id"),
        })
        for g in groups:
            if g not in rec["groups"]:
                rec["groups"].append(g)
        if phase not in rec["phases"]:
            rec["phases"].append(phase)
        for n in nations:
            if n not in rec["nations"]:
                rec["nations"].append(n)
        for t in terms:
            if t not in rec["terms"]:
                rec["terms"].append(t)
        lm = it.get("lastModified")
        if isinstance(lm, str) and (rec["last_modified"] is None or lm > rec["last_modified"]):
            rec["last_modified"] = lm
        note = it.get("updateNote")
        if isinstance(note, str) and note.strip() and note.strip() not in rec["notes"]:
            rec["notes"].append(note.strip())
    return {"records": records, "unparsed": unparsed, "meta": meta,
            "items": len(data.get("items", []))}


def status_of(rec: dict) -> str:
    ph = [p.lower() for p in rec["phases"]]
    if any("authoris" in p or "authoriz" in p for p in ph):
        return "authorised"
    if any("withdrawn" in p or "revoked" in p or "expired" in p for p in ph):
        return "delisted"
    return "unknown"


if __name__ == "__main__":
    out = parse_all()
    print(out["items"], len(out["records"]), out["unparsed"][:10])
