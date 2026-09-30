"""Parse all raw sources, join them, validate, and publish data/published/*.json.

If a source fails to parse or fails validation, the previous published
values for that jurisdiction are kept and the problem is written to
data/published/health.json (the workflow turns that into a GitHub issue).
"""
from __future__ import annotations

import re
import sys
import traceback
from collections import Counter, defaultdict

import yaml

from pipeline import parse_ca, parse_eu, parse_uk, parse_us, parse_wikidata
from pipeline.common import CURATED, INTERIM, PUBLISHED, RAW, load_sources, now_iso, read_json, write_json
from pipeline.model import JUR_ORDER
from pipeline.names import e_display, e_id, e_parts, e_sort, name_variants, norm_name

STATUS_RANK = {"authorised": 6, "phase_out": 5, "prohibited": 4, "delisted": 3,
               "not_authorised": 2, "not_listed": 1, "unknown": 0}

EU_CLASS = {"Colours": "Colours", "Sweeteners": "Sweeteners"}


def load_curated() -> dict:
    p = CURATED / "crosswalk.yml"
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def base_key(k: str) -> str:
    m = re.match(r"(e\d+[a-z]?)", k)
    return m.group(1) if m else k


class Build:
    def __init__(self):
        self.sources = load_sources()
        self.problems: list[str] = []
        self.notes: list[str] = []
        self.parsed: dict = {}
        self.curated = load_curated()
        self.prev = read_json(PUBLISHED / "additives.json", {}) or {}
        self.prev_by_id = {a["id"]: a for a in self.prev.get("additives", [])}

    # ------------------------------------------------------------ parsing
    def parse(self, sid: str, fn):
        try:
            self.parsed[sid] = fn()
            return self.parsed[sid]
        except FileNotFoundError as e:
            self.problems.append(f"{sid}: raw file missing ({e})")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.problems.append(f"{sid}: parse failed: {type(e).__name__}: {e}")
        self.parsed[sid] = None
        return None

    # ------------------------------------------------------------ validation
    def validate(self):
        """Structural checks; a failing source is dropped (previous values kept)."""
        eu = self.parsed.get("eu_annex2")
        if eu is not None:
            n = len(eu["listed"])
            with_use = sum(1 for k in eu["listed"] if parse_eu.eu_uses_for(eu, k))
            if n < 250 or n > 450:
                self.problems.append(f"eu_annex2: {n} additives in Part B (expected 250–450); EU data not updated")
                self.parsed["eu_annex2"] = None
            elif with_use < 0.8 * n:
                self.problems.append(f"eu_annex2: only {with_use}/{n} additives have a Part E use; EU data not updated")
                self.parsed["eu_annex2"] = None
            else:
                # reference checks: well-known entries must be present / absent
                for k in ("e100", "e330", "e951", "e129"):
                    if k not in eu["listed"] or not parse_eu.eu_uses_for(eu, k):
                        self.problems.append(f"eu_annex2: reference additive {k} missing or without uses; EU data not updated")
                        self.parsed["eu_annex2"] = None
                        break
        us = self.parsed.get("us_fda_substances")
        if us is not None:
            n = len(us["records"])
            if n < 2500:
                self.problems.append(f"us_fda_substances: only {n} records (expected ~4000); US data not updated")
                self.parsed["us_fda_substances"] = None
        ca = self.parsed.get("ca_lists")
        if ca is not None:
            empty = [k for k, v in ca["rows_per_list"].items() if v == 0 and k != "05"]
            if len(ca["records"]) < 300 or empty:
                self.problems.append(f"ca_lists: {len(ca['records'])} additives, empty lists {empty}; Canada data not updated")
                self.parsed["ca_lists"] = None
        uk = self.parsed.get("uk_fsa")
        if uk is not None and len(uk["records"]) < 20:
            self.problems.append(f"uk_fsa: only {len(uk['records'])} additives; UK register data not used")
            self.parsed["uk_fsa"] = None
        # a previous comparable count must not drop by more than 10 %
        prev_q = self.prev.get("counts", {})
        for sid, count in self.counts().items():
            old = prev_q.get(sid)
            if old and count < 0.9 * old:
                self.problems.append(f"{sid}: count dropped from {old} to {count}; data not updated")
                self.parsed[sid] = None

    def counts(self) -> dict:
        c = {}
        if self.parsed.get("eu_annex2"):
            c["eu_annex2"] = len(self.parsed["eu_annex2"]["listed"])
        if self.parsed.get("us_fda_substances"):
            c["us_fda_substances"] = len(self.parsed["us_fda_substances"]["records"])
        if self.parsed.get("ca_lists"):
            c["ca_lists"] = len(self.parsed["ca_lists"]["records"])
        if self.parsed.get("uk_fsa"):
            c["uk_fsa"] = len(self.parsed["uk_fsa"]["records"])
        return c

    # ------------------------------------------------------------ joining
    def entities(self) -> dict:
        """Create E-numbered entities from EU list, UK register and Wikidata."""
        ents: dict[str, dict] = {}
        eu = self.parsed.get("eu_annex2")
        wd = (self.parsed.get("wikidata") or {}).get("by_e", {})
        uk = (self.parsed.get("uk_fsa") or {}).get("records", {})

        def ensure(key, e, name, sort):
            if key not in ents:
                ents[key] = {"id": key, "e": e, "sort": sort, "name": name, "names": [], "cas": [],
                             "classes": [], "wikidata": None, "jur": {}}
            return ents[key]

        if eu:
            for k, r in eu["listed"].items():
                ent = ensure(k, r["e"], r["name"], r["sort"])
                ent["names"].append(r["name"])
                if r["section"] in EU_CLASS:
                    ent["classes"].append(EU_CLASS[r["section"]])
        for k, r in uk.items():
            p = e_parts(r["e"])
            ent = ensure(k, r["e"], r["name"], e_sort(*p))
            if r["name"]:
                ent["names"].append(r["name"])
        for k, r in wd.items():
            if k in ents:
                ent = ents[k]
            elif base_key(k) in ents and k.startswith(base_key(k) + "-"):
                continue
            else:
                continue  # only enrich here; Wikidata-only entities are added after US/CA matching
            ent["names"].extend(r["labels"] + r["aliases"])
            ent["cas"].extend(c for c in r["cas"] if c not in ent["cas"])
            ent["wikidata"] = ent["wikidata"] or (r["qids"][0] if r["qids"] else None)
        return ents

    def name_index(self, ents: dict) -> tuple[dict, dict]:
        by_name: dict[str, set[str]] = defaultdict(set)
        by_cas: dict[str, set[str]] = defaultdict(set)
        wd = (self.parsed.get("wikidata") or {}).get("by_e", {})
        for k, ent in ents.items():
            for n in ent["names"]:
                for v in name_variants(n):
                    by_name[v].add(k)
            for c in ent["cas"]:
                by_cas[c].add(k)
        # Wikidata E-numbers that are not on the EU/UK lists (historic or other) are
        # indexed too, so US/Canadian records can be linked to them.
        for k, r in wd.items():
            if k in ents:
                continue
            for n in r["labels"] + r["aliases"]:
                for v in name_variants(n):
                    by_name[v].add(k)
            for c in r["cas"]:
                by_cas[c].add(k)
        for alias, k in (self.curated.get("names") or {}).items():
            by_name[norm_name(alias)].add(k)
        return by_name, by_cas

    def pick(self, candidates: set[str], ents: dict) -> str | None:
        if not candidates:
            return None
        if len(candidates) == 1:
            return next(iter(candidates))
        # prefer the base E-number if all candidates share it; else prefer listed entities
        bases = {base_key(c) for c in candidates}
        if len(bases) == 1:
            b = bases.pop()
            return b if b in candidates or b in ents else sorted(candidates)[0]
        listed = [c for c in candidates if c in ents]
        if len(listed) == 1:
            return listed[0]
        return None  # ambiguous

    def match_us(self, ents, by_name, by_cas):
        us = self.parsed.get("us_fda_substances")
        if not us:
            return {}, {}
        forced = {norm_name(k): v for k, v in (self.curated.get("us") or {}).items()}
        matched = defaultdict(list)
        stats = Counter()
        unmatched = []
        for r in us["records"]:
            how = None
            key = forced.get(r["key"])
            if key == "none":
                stats["excluded"] += 1
                continue
            if key:
                how = "reviewed match table"
            if not key and r["cas"]:
                key = self.pick(set().union(*(by_cas.get(c, set()) for c in r["cas"])), ents)
                if key:
                    how = f"CAS {', '.join(r['cas'])}"
            if not key:
                cands = set()
                for n in [r["name"]] + r["other_names"]:
                    for v in name_variants(n):
                        cands |= by_name.get(v, set())
                key = self.pick(cands, ents)
                if key:
                    how = "name"
            if key:
                matched[key].append((r, how))
                stats["matched"] += 1
            else:
                stats["unmatched"] += 1
                unmatched.append(r)
        self.us_unmatched = unmatched
        return matched, stats

    def match_ca(self, ents, by_name):
        ca = self.parsed.get("ca_lists")
        if not ca:
            return {}, {}
        forced = {norm_name(k): v for k, v in (self.curated.get("ca") or {}).items()}
        matched = defaultdict(list)
        stats = Counter()
        unmatched = []
        for nk, r in ca["records"].items():
            key = forced.get(nk)
            how = "reviewed match table" if key else None
            if key == "none":
                stats["excluded"] += 1
                continue
            if not key:
                cands = set()
                for n in r["names"]:
                    for v in name_variants(n):
                        cands |= by_name.get(v, set())
                key = self.pick(cands, ents)
                how = "name" if key else None
            if key:
                matched[key].append((r, how))
                stats["matched"] += 1
            else:
                stats["unmatched"] += 1
                unmatched.append(r)
        self.ca_unmatched = unmatched
        return matched, stats

    # ------------------------------------------------------------ statuses
    def eu_status(self, key, ent):
        eu = self.parsed.get("eu_annex2")
        if not eu:
            return None
        src = self.sources["eu_annex2"]
        celex = eu.get("celex") or ""
        url = src["html_url"].format(celex=celex) if celex else src["discovery_urls"][0]
        refs = [{"label": "Annex II, Regulation (EC) No 1333/2008 (EUR-Lex)", "url": url},
                {"label": "EU food additives database", "url": "https://ec.europa.eu/food/food-feed-portal/screen/food-additives/search"}]
        listed = eu["listed"].get(key) or (eu["listed"].get(base_key(key)) if key != base_key(key) else None)
        if not listed:
            return {"status": "not_authorised", "headline": "Not on the EU list of authorised food additives",
                    "refs": refs, "source": "eu_annex2", "match": "e_number",
                    "match_detail": "Checked by E-number against Annex II, Part B."}
        cats = parse_eu.eu_uses_for(eu, key)
        facts = [["EU list section", listed["section"] or "—"]]
        notes = []
        if listed.get("marks"):
            last = sorted(listed["marks"], key=lambda m: int(m[1:]) if m[1:].isdigit() else 0)[-1]
            act = eu["acts"].get(last)
            if act:
                facts.append(["Entry last amended by", act])
        if listed.get("note"):
            notes.append(listed["note"])
        if not cats:
            return {"status": "not_authorised",
                    "headline": "On the Union list, but no authorised use in any food category",
                    "facts": facts, "notes": notes, "refs": refs, "source": "eu_annex2", "match": "e_number",
                    "match_detail": "Matched by E-number (Annex II Parts B and E)."}
        facts.insert(0, ["Food categories with a use", str(len(cats))])
        return {"status": "authorised",
                "headline": f"Authorised in {len(cats)} food categor{'y' if len(cats) == 1 else 'ies'}",
                "facts": facts, "notes": notes, "cats": cats, "refs": refs, "source": "eu_annex2",
                "match": "e_number", "match_detail": "Matched by E-number (Annex II Parts B and E)."}

    def gb_status(self, key, ent):
        uk = self.parsed.get("uk_fsa")
        if not uk:
            return None
        r = uk["records"].get(key) or uk["records"].get(base_key(key))
        refs = [{"label": "FSA Regulated Products Register", "url": "https://data.food.gov.uk/regulated-products"}]
        if not r:
            return {"status": "unknown", "headline": "Not found in the FSA register",
                    "refs": refs, "source": "uk_fsa", "match": "e_number",
                    "match_detail": "The FSA register was searched by E-number. Absence here does not prove the additive is not authorised in Great Britain."}
        st = parse_uk.status_of(r)
        facts = [["Register status", ", ".join(r["phases"])]]
        if r["nations"]:
            facts.append(["Applies in", ", ".join(r["nations"])])
        if r["groups"]:
            facts.append(["Group", "; ".join(r["groups"][:3])])
        if r["last_modified"]:
            facts.append(["Register entry updated", r["last_modified"][:10]])
        if r.get("url"):
            refs.insert(0, {"label": "FSA register entry", "url": r["url"].replace("http://", "https://")})
        return {"status": st, "headline": "Authorised in Great Britain" if st == "authorised" else ", ".join(r["phases"]),
                "facts": facts, "notes": r["notes"][:5] + [f"Terms: {t}" for t in r["terms"][:6]],
                "refs": refs, "source": "uk_fsa", "match": "e_number",
                "match_detail": "Matched by E-number in the FSA register."}

    def us_status(self, key, matches):
        if self.parsed.get("us_fda_substances") is None:
            return None
        src = self.sources["us_fda_substances"]
        refs = [{"label": "FDA Substances Added to Food", "url": src["page_url"]}]
        if not matches:
            return {"status": "not_listed", "headline": "Not found in FDA's Substances Added to Food inventory",
                    "refs": refs, "source": "us_fda_substances", "match": None,
                    "match_detail": "Searched by CAS number and known names. The inventory is not a complete list of substances that may be used."}
        best = max(matches, key=lambda m: STATUS_RANK[m[0]["status"]])
        r = best[0]
        facts = [["FDA name", r["display"]]]
        cfr = sorted({c for m in matches for c in m[0]["cfr"]}, key=lambda s: tuple(int(x) for x in s.split(".")))
        if cfr:
            facts.append(["21 CFR", ", ".join(cfr[:8])])
            for c in cfr[:4]:
                refs.append({"label": f"21 CFR {c} (eCFR)", "url": f"https://www.ecfr.gov/current/title-21/section-{c}"})
        effects = sorted({e for m in matches for e in m[0]["effects"]})
        if effects:
            facts.append(["Used for", ", ".join(effects[:6])])
        notes = []
        if len(matches) > 1:
            notes.append("Matching FDA inventory entries: " + "; ".join(
                f"{m[0]['display']} ({m[0]['headline']})" for m in matches[:12]))
        return {"status": r["status"], "headline": r["headline"], "facts": facts, "notes": notes,
                "refs": refs, "source": "us_fda_substances", "match": best[1],
                "match_detail": f"Matched to the FDA inventory by {best[1]}."}

    def ca_status(self, key, matches):
        if self.parsed.get("ca_lists") is None:
            return None
        base = "https://www.canada.ca/en/health-canada/services/food-nutrition/food-safety/food-additives/lists-permitted.html"
        if not matches:
            return {"status": "not_authorised", "headline": "Not found on Health Canada's Lists of Permitted Food Additives",
                    "refs": [{"label": "Lists of Permitted Food Additives", "url": base}],
                    "source": "ca_lists", "match": None,
                    "match_detail": "Searched the 15 lists by name and known synonyms. If a list uses an unusual name, it may be missed — check the lists."}
        lists = {}
        for r, how in matches:
            for L in r["lists"]:
                lists.setdefault(L["no"], L)
        names = sorted({n for r, _ in matches for n in r["names"]})
        facts = [["Listed as", "; ".join(names[:4])],
                 ["Lists", ", ".join(f"List {n}" for n in sorted(lists))]]
        purposes = sorted({p for L in lists.values() for p in L["purposes"]})[:6]
        if purposes:
            facts.append(["Purpose of use", "; ".join(purposes)])
        refs = [{"label": f"List {L['no']}: {L['title'].replace('List of Permitted ', '')}", "url": L["url"]}
                for L in sorted(lists.values(), key=lambda x: x["no"])][:4]
        notes = [f"{L['title']}: item{'s' if len(L['items']) > 1 else ''} {', '.join(L['items'][:8])}" for L in
                 sorted(lists.values(), key=lambda x: x["no"]) if L["items"]]
        how = matches[0][1]
        return {"status": "authorised",
                "headline": f"On {len(lists)} of Health Canada's permitted lists" if len(lists) > 1 else f"On Health Canada's {lists[min(lists)]['title']}",
                "facts": facts, "notes": notes, "refs": refs, "source": "ca_lists", "match": how,
                "match_detail": f"Matched to Health Canada's lists by {how}."}

    # ------------------------------------------------------------ main
    def run(self) -> int:
        self.parse("eu_annex2", parse_eu.parse_all)
        self.parse("uk_fsa", parse_uk.parse_all)
        self.parse("us_fda_substances", parse_us.parse_all)
        self.parse("ca_lists", parse_ca.parse_all)
        self.parse("wikidata", parse_wikidata.parse_all)
        self.validate()

        ents = self.entities()
        by_name, by_cas = self.name_index(ents)
        us_m, us_stats = self.match_us(ents, by_name, by_cas)
        ca_m, ca_stats = self.match_ca(ents, by_name)

        # Add Wikidata-only E-numbers (not on EU/UK lists) when a US or Canadian record links to them.
        wd = (self.parsed.get("wikidata") or {}).get("by_e", {})
        for k in set(us_m) | set(ca_m):
            if k in ents or k not in wd:
                continue
            r = wd[k]
            p = e_parts("E" + k[1:].replace("-", "(") + (")" if "-" in k else ""))
            name = (r["labels"] or [k])[0]
            ents[k] = {"id": k, "e": e_display(*p) if p else k.upper(), "sort": e_sort(*p) if p else k,
                       "name": name[:1].upper() + name[1:], "names": r["labels"] + r["aliases"],
                       "cas": r["cas"], "classes": [], "wikidata": r["qids"][0] if r["qids"] else None, "jur": {}}

        # US-only entities: explicitly prohibited or delisted substances without an E-number.
        for r in getattr(self, "us_unmatched", []):
            if r["status"] in ("prohibited", "delisted") and "NLFG" not in r["flags"]:
                sid = "us-" + re.sub(r"[^a-z0-9]+", "-", r["name"].lower()).strip("-")[:60]
                if sid in ents:
                    continue
                ents[sid] = {"id": sid, "e": None, "sort": "~" + sid, "name": r["display"], "names": [r["display"]] + r["other_names"],
                             "cas": r["cas"], "classes": [], "wikidata": None, "jur": {}, "us_only": True}
                us_m[sid] = [(r, "FDA inventory entry")]

        additives = []
        for k, ent in ents.items():
            jur = {}
            prev = self.prev_by_id.get(k, {}).get("jur", {})
            for j, fn in (("eu", lambda: self.eu_status(k, ent)), ("gb", lambda: self.gb_status(k, ent)),
                          ("us", lambda: self.us_status(k, us_m.get(k, []))),
                          ("ca", lambda: self.ca_status(k, ca_m.get(k, [])))):
                rec = fn()
                if rec is None:  # source unavailable: keep previous value, marked stale
                    rec = dict(prev.get(j, {"status": "unknown", "headline": "Source temporarily unavailable"}))
                    rec["stale"] = True
                jur[j] = rec
            # classes from US effects / Canada lists
            classes = list(dict.fromkeys(ent["classes"]))
            for r, _ in ca_m.get(k, []):
                for c in r.get("classes", []):
                    if c not in classes:
                        classes.append(c)
            aka = []
            seen = {norm_name(ent["name"])}
            for n in ent["names"] + [m[0]["display"] for m in us_m.get(k, [])] + [n for m in ca_m.get(k, []) for n in m[0]["names"]]:
                nn = norm_name(n)
                if nn and nn not in seen and len(n) <= 70:
                    seen.add(nn)
                    aka.append(n)
            cas = list(dict.fromkeys(ent["cas"] + [c for m in us_m.get(k, []) for c in m[0]["cas"]]))
            a = {"id": k, "e": ent["e"], "sort": ent["sort"], "name": ent["name"], "aka": aka[:20],
                 "cas": cas[:5], "classes": classes, "wikidata": ent["wikidata"], "jur": jur}
            additives.append(a)
        additives.sort(key=lambda a: a["sort"])

        # changelog: compare statuses with the previous publication
        changelog = read_json(PUBLISHED / "changelog.json", {"entries": [], "tracking_since": None})
        today = now_iso()[:10]
        if not changelog.get("tracking_since"):
            changelog["tracking_since"] = today
        if self.prev_by_id:
            for a in additives:
                old = self.prev_by_id.get(a["id"])
                for j in JUR_ORDER:
                    new_st = a["jur"][j].get("status")
                    if a["jur"][j].get("stale"):
                        continue
                    old_st = (old or {}).get("jur", {}).get(j, {}).get("status") if old else None
                    if old is None:
                        continue  # new entity: recorded in 'added' notes, not as a status change
                    if old_st and new_st and old_st != new_st and "unknown" not in (old_st, new_st):
                        changelog["entries"].append({"date": today, "id": a["id"], "name": a["name"], "jur": j,
                                                     "from": old_st, "to": new_st})
        # keep 'updated' dates
        for a in additives:
            old = self.prev_by_id.get(a["id"])
            if old and {j: old["jur"].get(j, {}).get("status") for j in JUR_ORDER} == {j: a["jur"][j].get("status") for j in JUR_ORDER}:
                a["updated"] = old.get("updated", today)
            else:
                a["updated"] = today

        # source metadata for the site
        src_out = {}
        for sid, cfg in self.sources.items():
            meta = read_json(RAW / sid / "meta.json", {}) or {}
            files = meta.get("files", {})
            f0 = next(iter(files.values()), {}) if files else {}
            used = self.parsed.get(sid) is not None
            src_out[sid] = {
                "title": cfg["title"], "publisher": cfg["publisher"],
                "url": cfg.get("page_url") or cfg.get("url") or (cfg.get("base", "") + "") or cfg.get("discovery_urls", [""])[0],
                "licence": cfg["licence"], "licence_url": cfg.get("licence_url", ""),
                "attribution": cfg["attribution"],
                "retrieved_at": meta.get("last_success"),
                "content_changed_at": max((f.get("content_changed_at") or "" for f in files.values()), default=None) or None,
                "version": f0.get("celex"),
                "state": "ok" if used else "stale",
            }
            if meta.get("last_error"):
                self.problems.append(f"{sid}: last fetch failed: {meta['last_error']}")
        if src_out.get("ca_lists"):
            src_out["ca_lists"]["url"] = "https://www.canada.ca/en/health-canada/services/food-nutrition/food-safety/food-additives/lists-permitted.html"
        if src_out.get("eu_annex2") and self.parsed.get("eu_annex2"):
            src_out["eu_annex2"]["url"] = self.sources["eu_annex2"]["html_url"].format(celex=self.parsed["eu_annex2"]["celex"])

        quality = []
        n_us = sum(us_stats.values()) if us_stats else 0
        if n_us:
            quality.append(f"FDA inventory: {us_stats.get('matched', 0)} of {n_us} entries linked to an additive on this site "
                           f"(most unlinked entries are flavourings and other substances without an E-number).")
        n_ca = sum(ca_stats.values()) if ca_stats else 0
        if n_ca:
            quality.append(f"Health Canada lists: {ca_stats.get('matched', 0)} of {n_ca} listed additives linked to an E-number.")
        write_json(INTERIM / "unmatched_us.json", [
            {k: r[k] for k in ("name", "cas", "cfr", "status", "effects")} for r in getattr(self, "us_unmatched", [])])
        write_json(INTERIM / "unmatched_ca.json", [
            {"name": r["name"], "lists": [L["no"] for L in r["lists"]]} for r in getattr(self, "ca_unmatched", [])])

        published = {
            "generated_at": now_iso(), "data_version": today, "sources": src_out,
            "counts": self.counts() or self.prev.get("counts", {}), "quality": quality,
            "additives": additives,
        }
        if not additives:
            self.problems.append("No additives produced; nothing published")
        else:
            write_json(PUBLISHED / "additives.json", published)
            write_json(PUBLISHED / "changelog.json", changelog)
        write_json(PUBLISHED / "health.json", {"at": now_iso(), "problems": self.problems,
                                               "counts": self.counts(), "us_match": dict(us_stats or {}),
                                               "ca_match": dict(ca_stats or {})})
        print(f"{len(additives)} additives; US {dict(us_stats or {})}; CA {dict(ca_stats or {})}")
        for p in self.problems:
            print("PROBLEM", p)
        return 0 if additives else 1


if __name__ == "__main__":
    sys.exit(Build().run())
