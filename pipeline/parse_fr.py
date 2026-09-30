"""Find FDA revocation orders in Federal Register final rules.

A document counts as a revocation when it is a final rule/order whose title
says it revokes, repeals or removes a listing or use. The substance is found
by looking for an FDA inventory name inside the title. The effective date is
taken from the DATES text (objection and comment deadlines are ignored),
falling back to the API's effective_on field.
"""
from __future__ import annotations

import json
import re
from datetime import date

from pipeline.common import RAW, read_json, read_raw
from pipeline.names import norm_name

REVOKE_RE = re.compile(r"\b(revok\w*|revocation|repeal\w*|remov\w*)\b", re.I)
MONTH_DATE_RE = re.compile(r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),\s+(\d{4})")
MONTHS = {m: i for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August",
                                      "September", "October", "November", "December"], start=1)}


def effective_date(doc: dict) -> date | None:
    text = doc.get("dates") or ""
    sentences = re.split(r"(?<=[.;])\s+", text)
    keep = [s for s in sentences if not re.search(r"objection|hearing|comment|submit", s, re.I)]
    found = []
    for s in keep:
        for m in MONTH_DATE_RE.finditer(s):
            found.append(date(int(m.group(3)), MONTHS[m.group(1)], int(m.group(2))))
    if found:
        return min(found)
    eo = doc.get("effective_on")
    if eo:
        try:
            return date.fromisoformat(eo[:10])
        except ValueError:
            return None
    return None


def parse_all() -> dict:
    meta = read_json(RAW / "us_fr_revocations" / "meta.json", {}) or {}
    docs = json.loads(read_raw("us_fr_revocations", "rules.json"))
    revocations = []
    for d in docs:
        title = d.get("title") or ""
        if not REVOKE_RE.search(title):
            continue
        if re.search(r"\bpropos", title, re.I) or re.search(r"proposed", d.get("action") or "", re.I):
            continue
        revocations.append({
            "document_number": d["document_number"], "title": title,
            "norm_title": " " + norm_name(title) + " ",
            "publication_date": d.get("publication_date"),
            "effective": (effective_date(d) or None),
            "url": d.get("html_url"), "action": d.get("action"),
        })
    return {"revocations": revocations, "documents": len(docs), "meta": meta}


def match_revocation(rec: dict, revocations: list[dict]) -> dict | None:
    """Return the newest revocation whose title names this FDA record."""
    names = [rec["key"]] + [norm_name(o) for o in rec.get("other_names", [])[:10]]
    names = [n for n in names if len(n) >= 8 or (len(n) >= 5 and re.search(r"\d", n))]
    hits = [r for r in revocations if any(f" {n} " in r["norm_title"] for n in names)]
    if not hits:
        return None
    return max(hits, key=lambda r: r["publication_date"] or "")
