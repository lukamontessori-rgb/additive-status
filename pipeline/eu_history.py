"""Build an EU status history from the consolidated versions of Regulation (EC) No 1333/2008.

The Union list in Annex II applies from 1 June 2013, so only consolidated
versions from that date on are used. Each version is downloaded once from the
Publications Office (Cellar), parsed with the same parser as the current text,
and reduced to one status code per E-number:

  A  authorised (at least one food category, or Annex III only)
  N  on the list but not authorised in food (medicines only, or authorisation ended)
  X  not on the list

Per-version codes are stored in data/history/eu_versions.json, so the history
can be rebuilt without downloading again. Versions that fail the same sanity
checks as the current text are skipped and reported.

Usage: python -m pipeline.eu_history [--max N]
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date

from pipeline import parse_eu
from pipeline.common import DATA, RAW, load_sources, now_iso, read_json, session, write_json
from pipeline.fetch import download_cellar

HISTORY = DATA / "history" / "eu_versions.json"
FIRST_DATE = "20130601"
PARSER_VERSION = "1"


def codes_for(parsed: dict, day: date) -> dict[str, str]:
    out = {}
    for k in parsed["listed"]:
        cats = parse_eu.eu_uses_for(parsed, k)
        notes = " ".join(parsed["notes"].get(k, [])).lower()
        if cats:
            out[k] = "A"
        elif "not authorised in the food categories" in notes:
            out[k] = "N"
        elif "authorised until" in notes:
            import re
            m = re.search(r"authorised until (\d{1,2} \w+ \d{4})", notes)
            d = parse_eu.parse_date(m.group(1)) if m else None
            out[k] = "N" if d and d < day else "A"
        else:
            out[k] = "A"  # Annex III only, or conditions set elsewhere
    return out


def sane(parsed: dict) -> bool:
    n = len(parsed["listed"])
    with_use = sum(1 for k in parsed["listed"] if parse_eu.eu_uses_for(parsed, k))
    return 250 <= n <= 450 and with_use >= 0.8 * n and not (set("BCE") - set(parsed["parts_seen"]))


def run(max_new: int | None = None) -> int:
    cfg = load_sources()["eu_annex2"]
    meta = read_json(RAW / "eu_annex2" / "meta.json", {}) or {}
    f = (meta.get("files") or {}).get("annex2.html") or {}
    versions = [v for v in (f.get("versions_seen") or "").split(",") if v and v >= FIRST_DATE]
    hist = read_json(HISTORY, {}) or {}
    if hist.get("parser_version") != PARSER_VERSION:
        hist = {"parser_version": PARSER_VERSION, "versions": {}, "skipped": {}}
    todo = [v for v in sorted(versions) if v not in hist["versions"] and v not in hist["skipped"]]
    if max_new is not None:
        todo = todo[:max_new]
    sess = session()
    base = cfg["celex_base"]
    for v in todo:
        celex = f"{base}-{v}"
        day = date(int(v[:4]), int(v[4:6]), int(v[6:]))
        try:
            body, _ = download_cellar(sess, celex, cfg)
            parsed = parse_eu.parse_html(body, today=day)
            if not sane(parsed):
                hist["skipped"][v] = f"failed sanity check ({len(parsed['listed'])} listed)"
            else:
                hist["versions"][v] = {"celex": celex, "codes": codes_for(parsed, day)}
            print(v, "ok" if v in hist["versions"] else hist["skipped"][v])
        except Exception as e:  # network problems: try again next run
            print(v, "error:", e)
        time.sleep(1.5)  # be polite to the Cellar service
        hist["updated"] = now_iso()
        write_json(HISTORY, hist, indent=None)
    return 0


def timeline(hist: dict) -> dict[str, list[dict]]:
    """Per E-number list of status changes between consecutive valid versions."""
    vs = sorted(hist.get("versions", {}))
    events: dict[str, list[dict]] = {}
    prev_codes = None
    prev_v = None
    for v in vs:
        codes = hist["versions"][v]["codes"]
        if prev_codes is not None:
            changed = {}
            for k in set(prev_codes) | set(codes):
                a, b = prev_codes.get(k, "X"), codes.get(k, "X")
                if a != b:
                    changed[k] = (a, b)
            # Renumbering (e.g. E 960 split into E 960a–d, E 160b into E 160b(i)/(ii)) is not a status change
            renumbered = set()
            for k, (a, b) in changed.items():
                kids = [c for c in changed if c != k and (c.startswith(k + "-") or
                        (c[:len(k)] == k and len(c) == len(k) + 1 and c[-1].isalpha()))]
                if kids and ((b == "X" and all(changed[c][0] == "X" for c in kids)) or
                             (a == "X" and all(changed[c][1] == "X" for c in kids))):
                    renumbered.add(k)
                    renumbered.update(kids)
            for k, (a, b) in changed.items():
                if k in renumbered:
                    continue
                events.setdefault(k, []).append({"version": v, "previous_version": prev_v,
                                                 "celex": hist["versions"][v]["celex"], "from": a, "to": b})
        prev_codes, prev_v = codes, v
    return events


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=None)
    sys.exit(run(ap.parse_args().max))
