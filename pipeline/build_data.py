"""Parse all raw sources, join them, validate, and publish data/published/*.json.

If a source fails to parse or fails validation, the previous published
values for that jurisdiction are kept (marked stale) and the problem is
written to data/published/health.json; the workflow turns that into an issue.
"""
from __future__ import annotations

import re
import sys
import traceback
from collections import Counter, defaultdict
from datetime import date

import yaml

from pipeline import eu_history, parse_anz, parse_ca, parse_eu, parse_fr, parse_specs, parse_uk, parse_us
from pipeline.common import CURATED, INTERIM, PUBLISHED, RAW, load_sources, now_iso, read_json, write_json
from pipeline.model import JUR_ORDER
from pipeline.names import e_display, e_parts, e_sort, name_variants, norm_name

# Bump when parsing or matching rules change. Status differences caused by a method
# change are not reported as regulatory changes on the changes page.
METHOD_VERSION = "2026-10-01.4"

STATUS_RANK = {"authorised": 7, "phase_out": 6, "listed_noreg": 5, "prohibited": 4, "delisted": 3,
               "not_authorised": 2, "not_listed": 1, "unknown": 0}
EU_CLASS = {"Colours": "Colours", "Sweeteners": "Sweeteners"}
FLAVOUR_SECTIONS = {"172.510", "172.515"}
GENERIC_KEYS = {"fatty acid", "wax", "gum", "starch", "caramel", "color", "colour", "extract", "oil", "resin",
                "polymer", "salt", "acid", "ester", "glyceride", "sugar", "vinegar"}
MATCH_QUALITY = {"reviewed": 5, "Colour": 4, "CAS": 4, "name": 3, "alternative": 2, "FDA": 3, "Federal": 5}


def match_rank(m, ent_key: str):
    r, how = m
    q = MATCH_QUALITY.get((how or "").split(" ")[0], 1)
    exact = 1 if r["key"] and match_key(r["name"]) == ent_key else 0
    return (q, exact, STATUS_RANK.get(r["status"], 0))


def match_key(name: str) -> str:
    """norm_name plus simple plural folding, used for name matching only."""
    words = []
    for w in norm_name(name).split():
        if len(w) >= 5 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        words.append(w)
    return " ".join(words)


def match_variants(name: str) -> set[str]:
    return {" ".join(w[:-1] if len(w) >= 5 and w.endswith("s") and not w.endswith("ss") else w
                     for w in v.split()) for v in name_variants(name)}


FUNCTION_WORDS = [
    ("colo", "colour"), ("preserv", "preservative"), ("antimicrobial", "preservative"), ("antioxid", "antioxidant"),
    ("sweeten", "sweetener"), ("emulsif", "emulsifier"), ("stabili", "stabiliser"), ("thicken", "thickener"),
    ("gelling", "gelling agent"), ("ph control", "acidity regulator"), ("acidity", "acidity regulator"),
    ("acidulant", "acidity regulator"), ("acid-reacting", "acidity regulator"), ("flavor enhancer", "flavour enhancer"),
    ("flavour enhancer", "flavour enhancer"), ("flavoring", "flavouring"), ("flavouring", "flavouring"),
    ("leavening", "raising agent"), ("raising", "raising agent"), ("anticaking", "anti-caking agent"),
    ("anti-caking", "anti-caking agent"), ("free-flow", "anti-caking agent"), ("humectant", "humectant"),
    ("firming", "firming agent"), ("glazing", "glazing agent"), ("polishing", "glazing agent"),
    ("surface-finishing", "glazing agent"), ("dough strengthener", "flour treatment agent"),
    ("flour treat", "flour treatment agent"), ("bleaching", "flour treatment agent"), ("maturing", "flour treatment agent"),
    ("sequestr", "sequestrant"), ("chelat", "sequestrant"), ("nutrient supplement", "nutrient"), ("texturiz", "texturiser"),
    ("bulking", "bulking agent"), ("antifoam", "anti-foaming agent"), ("anti-foam", "anti-foaming agent"),
    ("foaming", "foaming agent"), ("propellant", "propellant"), ("packaging gas", "packaging gas"),
    ("curing", "curing agent"), ("drying agent", "drying agent"), ("solvent", "carrier or solvent"),
    ("carrier", "carrier or solvent"), ("enzyme", "enzyme"), ("processing aid", "processing aid"),
    ("modified starch", "modified starch"), ("starch-modif", "starch-modifying agent"), ("yeast food", "yeast food"),
]


def canonical_function(raw: str) -> str | None:
    t = (raw or "").lower()
    for key, name in FUNCTION_WORDS:
        if key in t:
            return name
    return None


def key_label(k: str) -> str:
    m = re.match(r"e(\d+)([a-z]?)(?:-([ivx]+))?$", k)
    return e_display(int(m.group(1)), m.group(2) or "", m.group(3) or "") if m else k


def base_key(k: str) -> str:
    m = re.match(r"(e\d+[a-z]?)", k)
    return m.group(1) if m else k


def load_curated() -> dict:
    p = CURATED / "crosswalk.yml"
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class Build:
    def __init__(self, today: date | None = None):
        self.today = today or date.today()
        self.sources = load_sources()
        self.problems: list[str] = []
        self.parsed: dict = {}
        self.curated = load_curated()
        self.prev = read_json(PUBLISHED / "additives.json", {}) or {}
        self.prev_by_id = {a["id"]: a for a in self.prev.get("additives", [])}
        self.log: dict[str, list] = defaultdict(list)

    # ------------------------------------------------------------ parsing / validation
    def parse(self, sid: str, fn):
        try:
            self.parsed[sid] = fn()
        except FileNotFoundError as e:
            self.problems.append(f"{sid}: raw file missing ({e})")
            self.parsed[sid] = None
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.problems.append(f"{sid}: parse failed: {type(e).__name__}: {e}")
            self.parsed[sid] = None

    def drop(self, sid: str, why: str):
        self.problems.append(f"{sid}: {why}; previous data kept")
        self.parsed[sid] = None

    def validate(self):
        eu = self.parsed.get("eu_annex2")
        if eu is not None:
            n = len(eu["listed"])
            with_use = sum(1 for k in eu["listed"] if parse_eu.eu_uses_for(eu, k))
            if not 250 <= n <= 450:
                self.drop("eu_annex2", f"{n} additives in Part B (expected 250–450)")
            elif with_use < 0.85 * n:
                self.drop("eu_annex2", f"only {with_use}/{n} additives have a Part E use")
            elif set("ABCDE") - set(eu["parts_seen"]):
                self.drop("eu_annex2", f"Annex II parts missing: {eu['parts_seen']}")
            else:
                # reference checks from the legal text (stable facts)
                refs_ok = all(parse_eu.eu_uses_for(eu, k) for k in ("e100", "e330", "e951", "e129")) \
                    and not parse_eu.eu_uses_for(eu, "e171") and "e924" not in eu["listed"]
                if not refs_ok:
                    self.drop("eu_annex2", "reference additives failed (E 100/E 330/E 951/E 129 uses, E 171 none, E 924 absent)")
        us = self.parsed.get("us_fda_substances")
        if us is not None:
            n = len(us["records"])
            names = {r["name"] for r in us["records"]}
            if n < 3000 or not {"ASPARTAME", "FD&C RED NO. 40", "TITANIUM DIOXIDE"} <= names:
                self.drop("us_fda_substances", f"{n} records or reference substances missing")
        ca = self.parsed.get("ca_lists")
        if ca is not None:
            empty = [k for k, v in ca["rows_per_list"].items() if v == 0]
            if len(ca["records"]) < 300 or empty:
                self.drop("ca_lists", f"{len(ca['records'])} additives, empty lists {empty}")
        uk = self.parsed.get("uk_fsa")
        if uk is not None and len(uk["records"]) < 250:
            self.drop("uk_fsa", f"only {len(uk['records'])} additives in the register")
        anz = self.parsed.get("anz_code")
        if anz is not None:
            ok = (len(anz["names"]) >= 250 and len(anz["gmp"]) >= 100
                  and parse_anz.status_of(anz, "e129")[0] == "authorised"
                  and parse_anz.status_of(anz, "e171")[0] == "authorised")
            if not ok:
                self.drop("anz_code", f"{len(anz['names'])} codes, {len(anz['gmp'])} GMP additives or reference checks failed")
        sp = self.parsed.get("eu_specs")
        if sp is not None and len(sp["entries"]) < 250:
            self.drop("eu_specs", f"only {len(sp['entries'])} specification entries")
        prev_counts = self.prev.get("counts", {})
        for sid, count in self.counts().items():
            old = prev_counts.get(sid)
            if old and count < 0.9 * old:
                self.drop(sid, f"count dropped from {old} to {count}")

    def counts(self) -> dict:
        c = {}
        for sid, key in (("eu_annex2", "listed"), ("us_fda_substances", "records"), ("ca_lists", "records"),
                         ("uk_fsa", "records"), ("eu_specs", "entries"), ("anz_code", "names")):
            if self.parsed.get(sid):
                c[sid] = len(self.parsed[sid][key])
        return c

    # ------------------------------------------------------------ entities and indexes
    def build_entities(self) -> dict:
        ents: dict[str, dict] = {}
        eu = self.parsed.get("eu_annex2")
        uk = (self.parsed.get("uk_fsa") or {}).get("records", {})

        def ensure(key, e, name, sort):
            if key not in ents:
                ents[key] = {"id": key, "e": e, "sort": sort, "name": name, "names": [], "cas": [],
                             "ci": [], "classes": [], "jur": {}}
            return ents[key]

        if eu:
            for k, r in eu["listed"].items():
                ent = ensure(k, r["e"], r["name"], r["sort"])
                ent["names"].append(r["name"])
                ent.setdefault("official", []).append(r["name"])
                if r["section"] in EU_CLASS:
                    ent["classes"].append(EU_CLASS[r["section"]])
                p = e_parts(r["e"])
                if p and 1400 <= p[0] <= 1452 and "starch" in r["name"].lower():
                    ent["classes"].append("Modified starches")   # E 14xx: named as modified starches
        elif self.prev_by_id:  # EU source down: keep previous E-numbered entities
            for k, a in self.prev_by_id.items():
                if a.get("e"):
                    ensure(k, a["e"], a["name"], a.get("sort", k))["names"].append(a["name"])
        for k, r in uk.items():
            p = e_parts(r["e"])
            ent = ensure(k, r["e"], r["name"], e_sort(*p))
            if r["name"]:
                ent["names"].append(r["name"])
                ent.setdefault("official", []).append(r["name"])
        specs = (self.parsed.get("eu_specs") or {}).get("entries", {})
        for sk, sp in specs.items():
            k = sk if sk in ents else base_key(sk)
            if k not in ents:
                continue
            ent = ents[k]
            ent["names"].extend([sp["title"].title() if sp["title"].isupper() else sp["title"]] + sp["synonyms"])
            ent.setdefault("chem_names", []).extend(sp["chem_names"])
            for c in sp["cas"]:
                if c not in ent["cas"]:
                    ent["cas"].append(c)
            for n in sp["colour_index"]:
                if n not in ent["ci"]:
                    ent["ci"].append(n)
        return ents

    def build_index(self, ents: dict):
        by_name: dict[str, set[str]] = defaultdict(set)
        by_cas: dict[str, set[str]] = defaultdict(set)
        by_ci: dict[str, set[str]] = defaultdict(set)
        for k, ent in ents.items():
            for i, n in enumerate(ent["names"]):
                # official list names may hold several synonyms separated by commas;
                # specification synonyms are already one name each
                vs = match_variants(n) if n in ent.get("official", []) else {match_key(n)}
                for v in vs:
                    by_name[v].add(k)
            for n in ent.get("chem_names", []):
                for part in n.split(";"):
                    part = part.strip()
                    if part and len(part.split()) <= 5 and not re.search(r"co-|poly|:\d", part, re.I):
                        by_name[match_key(part)].add(k)
            for c in ent["cas"]:
                by_cas[c].add(k)
            for c in ent["ci"]:
                by_ci[c].add(k)
        for alias, k in (self.curated.get("names") or {}).items():
            by_name[match_key(alias)] = {k}
        return by_name, by_cas, by_ci

    def pick(self, cands: set[str], ents: dict) -> str | None:
        cands = {c for c in cands if c in ents}
        if not cands:
            return None
        if len(cands) == 1:
            return next(iter(cands))
        bases = {base_key(c) for c in cands}
        if len(bases) == 1:
            b = next(iter(bases))
            return b if b in ents else sorted(cands)[0]
        return None

    # ------------------------------------------------------------ US
    def apply_revocations(self):
        us, fr = self.parsed.get("us_fda_substances"), self.parsed.get("us_fr_revocations")
        if not us or not fr:
            return
        for r in us["records"]:
            if r["status"] not in ("authorised", "listed_noreg"):
                continue
            hit = parse_fr.match_revocation(r, fr["revocations"])
            if not hit:
                continue
            eff = hit["effective"]
            pub = hit["publication_date"]
            if eff and eff > self.today:
                r["status"] = "phase_out"
                r["headline"] = f"Authorisation revoked; ends {eff.strftime('%-d %B %Y')}"
            else:
                r["status"] = "delisted"
                r["headline"] = f"Authorisation revoked{(' — effective ' + eff.strftime('%-d %B %Y')) if eff else ''}"
            r["revocation"] = {"title": hit["title"], "url": hit["url"], "published": pub,
                               "effective": eff.isoformat() if eff else None, "doc": hit["document_number"]}
            self.log["revocations_applied"].append(f"{r['name']}: {hit['document_number']} ({eff})")

    def match_us(self, ents, by_name, by_cas, by_ci):
        us = self.parsed.get("us_fda_substances")
        if not us:
            return {}, Counter(), []
        forced = {match_key(k): v for k, v in (self.curated.get("us") or {}).items()}
        matched = defaultdict(list)
        stats, unmatched = Counter(), []
        for r in us["records"]:
            key, how = None, None
            f = forced.get(match_key(r["name"]))
            if f == "none":
                stats["excluded by review"] += 1
                unmatched.append(r)
                continue
            if f:
                keys = [k for k in (f if isinstance(f, list) else [f]) if k in ents]
                if not keys:
                    self.problems.append(f"crosswalk: US mapping for {r['name']} points to unknown ids {f}")
                for k in keys:
                    matched[k].append((r, "reviewed match table"))
                stats["reviewed"] += 1
                continue
            if not key and r["colour_index"]:
                key = self.pick(set().union(*(by_ci.get(c, set()) for c in r["colour_index"])), ents)
                how = f"Colour Index {', '.join(r['colour_index'])}" if key else None
            if not key and r["cas"]:
                key = self.pick(set().union(*(by_cas.get(c, set()) for c in r["cas"])), ents)
                how = f"CAS {', '.join(r['cas'])}" if key else None
            if not key:
                key = self.pick(by_name.get(match_key(r["name"]), set()), ents)
                how = "name" if key else None
            if not key:
                cands = set()
                for o in r["other_names"]:
                    mk = match_key(o)
                    if len(mk) >= 5 and mk not in GENERIC_KEYS:
                        cands |= by_name.get(mk, set())
                key = self.pick(cands, ents)
                how = "alternative name" if key else None
            if key:
                matched[key].append((r, how))
                stats[how.split(" ")[0] if how else "?"] += 1
            else:
                unmatched.append(r)
                stats["unmatched"] += 1
        return matched, stats, unmatched

    # ------------------------------------------------------------ Canada
    def match_ca(self, ents, by_name):
        ca = self.parsed.get("ca_lists")
        if not ca:
            return {}, Counter(), []
        forced = {match_key(k): v for k, v in (self.curated.get("ca") or {}).items()}
        matched = defaultdict(list)
        stats, unmatched = Counter(), []
        for nk, r in ca["records"].items():
            f = forced.get(match_key(r["name"]))
            if f == "none":
                stats["excluded by review"] += 1
                unmatched.append(r)
                continue
            if f:
                keys = [k for k in (f if isinstance(f, list) else [f]) if k in ents]
                if not keys:
                    self.problems.append(f"crosswalk: Canada mapping for {r['name']} points to unknown ids {f}")
                for k in keys:
                    matched[k].append((r, "reviewed match table"))
                stats["reviewed"] += 1
                continue
            key, how = None, None
            if not key:
                cands = set()
                for n in r["names"]:
                    cands |= by_name.get(match_key(n), set())
                key = self.pick(cands, ents)
                how = "name" if key else None
            if not key:
                cands = set()
                for n in r["names"]:
                    for v in match_variants(n):
                        cands |= by_name.get(v, set())
                key = self.pick(cands, ents)
                how = "name part" if key else None
            if key:
                matched[key].append((r, how))
                stats[how.split(" ")[0]] += 1
            else:
                unmatched.append(r)
                stats["unmatched"] += 1
        return matched, stats, unmatched

    # ------------------------------------------------------------ statuses
    def eu_status(self, key, ent):
        eu = self.parsed.get("eu_annex2")
        if not eu:
            return None
        src = self.sources["eu_annex2"]
        celex = eu.get("celex") or ""
        refs = [{"label": "Regulation (EC) No 1333/2008, Annex II (EUR-Lex)",
                 "url": src["html_url"].format(celex=celex)},
                {"label": "EU food additives database", "url": "https://ec.europa.eu/food/food-feed-portal/screen/food-additives/search"}]
        listed = eu["listed"].get(key)
        if not listed:
            detail = "Checked by E-number against Annex II, Part B." if ent.get("e") else \
                "This substance has no E-number, and no entry on the EU list matches its names or identifiers."
            return {"status": "not_authorised", "headline": "Not on the EU list of authorised food additives",
                    "refs": refs, "source": "eu_annex2", "match": "e_number" if ent.get("e") else "name",
                    "match_detail": detail}
        cats = parse_eu.eu_uses_for(eu, key)
        notes = list(eu["notes"].get(key, []))
        facts = [["EU list section", listed["section"] or "—"]]
        lc = parse_eu.last_change(eu, key)
        if lc:
            facts.append(["Entry last amended by", f"{lc['act']} ({lc.get('oj_date') or lc.get('adopted')})"])
            if lc.get("url"):
                refs.append({"label": lc["act"], "url": lc["url"]})
        base = {"facts": facts, "notes": notes, "refs": refs, "source": "eu_annex2", "match": "e_number",
                "match_detail": "Matched by E-number (Annex II, Parts B, C and E)."}
        if cats:
            cat_names = eu["categories"]
            top = sorted({c.split(".")[0] for c in cats})
            facts.insert(0, ["Food categories with a use", str(len(cats))])
            notes.append("Food categories: " + "; ".join(f"{c} {cat_names.get(c, '')}".strip() for c in cats[:60]) +
                         ("…" if len(cats) > 60 else ""))
            return {"status": "authorised",
                    "headline": f"Authorised in {len(cats)} food categor{'y' if len(cats) == 1 else 'ies'}",
                    "cats": cats, "top_categories": top, **base}
        joined = " ".join(notes).lower()
        if "not authorised in the food categories" in joined:
            return {"status": "not_authorised",
                    "headline": "Not authorised in food (kept on the list only for use in medicines)", **base}
        m = re.search(r"authorised until (\d{1,2} \w+ \d{4})", joined)
        if m:
            d = parse_eu.parse_date(m.group(1))
            if d and d < self.today:
                return {"status": "delisted", "headline": f"EU authorisation ended on {d.strftime('%-d %B %Y')}", **base}
        if key in eu.get("annex3", []):
            return {"status": "authorised",
                    "headline": "Authorised only in food additives, enzymes, flavourings or nutrients (Annex III)", **base}
        notes.append("This additive is on the Union list, but Annex II Part E sets no food category for it. "
                     "Its use may be governed by other EU rules (for example, wine legislation).")
        return {"status": "authorised", "headline": "On the EU list; no food category set in Annex II", **base}

    def gb_status(self, key, ent):
        uk = self.parsed.get("uk_fsa")
        if not uk:
            return None
        refs = [{"label": "FSA Regulated Products Register", "url": "https://data.food.gov.uk/regulated-products"}]
        r = uk["records"].get(key)
        if not r:
            return {"status": "not_authorised", "headline": "Not in the GB register of authorised food additives",
                    "refs": refs, "source": "uk_fsa", "match": "e_number" if ent.get("e") else "name",
                    "match_detail": "Checked by E-number against the Food Standards Agency register." if ent.get("e")
                    else "No E-number and no match in the Food Standards Agency register."}
        st = parse_uk.status_of(r)
        facts = [["Register status", ", ".join(r["phases"])]]
        if r["nations"]:
            facts.append(["Applies in", ", ".join(sorted(r["nations"]))])
        if r["groups"]:
            facts.append(["Group", "; ".join(r["groups"][:3])])
        if r["last_modified"]:
            facts.append(["Register entry updated", r["last_modified"][:10]])
        if r.get("url"):
            refs.insert(0, {"label": "FSA register entry", "url": r["url"].replace("http://", "https://")})
        notes = r["notes"][:5] + r.get("phase_notes", [])
        headline = "Authorised in Great Britain" if st == "authorised" else (
            "; ".join(r.get("phase_notes", [])) or ", ".join(r["phases"]))
        terms = " ".join(r.get("terms", []))
        if st == "authorised" and r.get("terms") and "1333/2008" not in terms and "conditions of use" not in terms.lower():
            # Listed only through its purity specification (e.g. canthaxanthin, kept for medicines):
            # the register gives no permitted food use.
            st = "not_authorised"
            headline = "Not authorised in food: the register lists only its specification"
            facts.append(["Legal basis in the register", "; ".join(r["terms"])])
            notes.append("The FSA register lists this additive only under the specifications regulation "
                         "(assimilated Regulation (EU) No 231/2012), not under the food uses in Annex II or III "
                         "of assimilated Regulation (EC) No 1333/2008.")
        return {"status": st, "headline": headline, "facts": facts, "notes": notes, "refs": refs,
                "source": "uk_fsa", "match": "e_number", "match_detail": "Matched by E-number in the FSA register."}

    def us_status(self, key, matches):
        us = self.parsed.get("us_fda_substances")
        if us is None:
            return None
        src = self.sources["us_fda_substances"]
        refs = [{"label": "FDA Substances Added to Food", "url": src["page_url"]}]
        if not matches and "Colours" in self._ent_classes.get(key, []):
            return {"status": "not_authorised", "headline": "Not listed as a colour additive for food in the US",
                    "refs": refs + [{"label": "21 CFR Part 73 and Part 74 (eCFR)", "url": "https://www.ecfr.gov/current/title-21/chapter-I/subchapter-A"}],
                    "source": "us_fda_substances", "match": None,
                    "match_detail": "Colour additives may only be used in US food if FDA lists them in 21 CFR 73 or 74. "
                                    "No food listing was found by Colour Index number, CAS number or name."}
        if not matches:
            return {"status": "not_listed", "headline": "Not found in FDA's Substances Added to Food inventory",
                    "refs": refs, "source": "us_fda_substances", "match": None,
                    "match_detail": "Searched by Colour Index number, CAS number and known names. The inventory is not a complete list of substances that may be used."}
        ent_key = match_key(self._ent_names.get(key, ""))
        best = max(matches, key=lambda m: match_rank(m, ent_key))
        r = best[0]
        facts = [["FDA name", r["display"]]]
        cfr = sorted({c for m in matches for c in m[0]["cfr"]}, key=lambda s: tuple(int(x) for x in s.split(".")))
        def indirect(c):  # food-contact sections (see parse_us.classify)
            p, x = (int(v) for v in c.split(".")[:2])
            return p in (175, 176, 177, 178, 186) or (p == 181 and x not in (33, 34)) or (p == 182 and x in parse_us.INDIRECT_182)
        # 73.1xxx/74.1xxx are drugs/cosmetics; part 81/82 are provisional listings and terminations
        food_cfr = [c for c in cfr if not re.match(r"^(73|74)\.\d{4}$", c) and not indirect(c)
                    and not c.startswith(("81.", "82."))]
        if food_cfr:
            feed_only = r["status"] == "not_authorised" and all(
                c.startswith("73.") and int(c.split(".")[1]) in parse_us.FEED_ONLY_73 for c in food_cfr)
            facts.append(["21 CFR (animal feed uses)" if feed_only else "21 CFR (food uses)", ", ".join(food_cfr[:8])])
            for c in food_cfr[:3]:
                refs.append({"label": f"21 CFR {c} (eCFR)", "url": f"https://www.ecfr.gov/current/title-21/section-{c}"})
        effects = sorted({e for m in matches for e in m[0]["effects"]})
        if effects:
            facts.append(["Technical effects (FDA inventory)", ", ".join(effects[:6])])
        notes = []
        if r.get("revocation"):
            rv = r["revocation"]
            refs.insert(0, {"label": f"Federal Register {rv['doc']}", "url": rv["url"]})
            notes.append(f"{rv['title']} (Federal Register, published {rv['published']}"
                         + (f"; effective {rv['effective']}" if rv.get("effective") else "") + ").")
            if us.get("inventory_updated"):
                notes.append(f"FDA's inventory (last updated {us['inventory_updated']}) may still list the old regulation.")
        if len(matches) > 1:
            notes.append("Matching FDA inventory entries: " + "; ".join(
                f"{m[0]['display']} ({m[0]['headline']})" for m in matches[:12]))
        return {"status": r["status"], "headline": r["headline"], "facts": facts, "notes": notes,
                "refs": refs, "source": "us_fda_substances", "match": best[1],
                "match_detail": ("Status taken from a Federal Register final rule; the substance is no longer in FDA's inventory."
                                 if (best[1] or "").startswith("Federal Register") else
                                 f"Matched to the FDA inventory by {best[1]}.")}

    def ca_status(self, key, matches):
        if self.parsed.get("ca_lists") is None:
            return None
        base = "https://www.canada.ca/en/health-canada/services/food-nutrition/food-safety/food-additives/lists-permitted.html"
        if not matches:
            refs = [{"label": "Lists of Permitted Food Additives", "url": base}]
            unsure = (self.curated.get("ca_uncertain") or {}).get(key)
            if unsure:   # reviewed cases where the Canadian list may cover the substance under another name
                return {"status": "not_listed", "headline": "Not on the list under this name", "notes": [unsure],
                        "refs": refs, "source": "ca_lists", "match": None,
                        "match_detail": "No entry with this name or its synonyms. See the note for a related Canadian entry."}
            closed = {"Sweeteners": ("List 9", "List of Permitted Sweeteners"),
                      "Flour treatment agents": ("List 2", "List of Permitted Bleaching, Maturing and Dough Conditioning Agents")}
            cls = [c for c in self._ent_classes.get(key, []) if c in closed]
            if cls and key not in (self.curated.get("ca_closed_exclude") or []):
                no, title = closed[cls[0]]
                note = (self.curated.get("ca_notes") or {}).get(key)
                return {"status": "not_authorised", "headline": f"Not on Health Canada's {title}",
                        "notes": [note] if note else [], "refs": refs, "source": "ca_lists", "match": None,
                        "match_detail": f"{cls[0]} may be used as food additives in Canada only if they are on {no}. "
                                        "The list was searched by name and known synonyms."}
            if "Colours" in self._ent_classes.get(key, []):
                return {"status": "not_authorised", "headline": "Not on Health Canada's List of Permitted Food Colours",
                        "refs": refs, "source": "ca_lists", "match": None,
                        "match_detail": "Food colours must be on List 3 to be used in Canada. The list was searched by name and known synonyms."}
            return {"status": "not_listed", "headline": "Not found on Health Canada's Lists of Permitted Food Additives",
                    "refs": refs, "source": "ca_lists", "match": None,
                    "match_detail": "Searched the 15 lists by name and known synonyms. Some substances (for example monosodium glutamate "
                                    "or modified starches) are treated as food ingredients in Canada and are not on these lists, so "
                                    "this does not show that the substance is banned."}
        lists = {}
        for r, how in matches:
            for L in r["lists"]:
                lists.setdefault(L["no"], L)
        names = sorted({n for r, _ in matches for n in r["names"]})
        facts = [["Listed as", "; ".join(names[:4])],
                 ["Lists", ", ".join(f"List {n}" for n in sorted(lists))]]
        purposes = list({p.lower(): p for L in lists.values() for p in sorted(L["purposes"], reverse=True)}.values())
        purposes = sorted(purposes, key=str.lower)[:6]
        if purposes:
            facts.append(["Purpose of use", "; ".join(purposes)])
        refs = [{"label": f"List {L['no']}: {L['title'].replace('List of Permitted ', '')}", "url": L["url"]}
                for L in sorted(lists.values(), key=lambda x: x["no"])][:4]
        notes = [f"{L['title']}: item{'s' if len(L['items']) > 1 else ''} {', '.join(L['items'][:8])}"
                 for L in sorted(lists.values(), key=lambda x: x["no"]) if L["items"]]
        how = matches[0][1]
        first = lists[min(lists)]
        return {"status": "authorised",
                "headline": f"On {len(lists)} of Health Canada's permitted lists" if len(lists) > 1
                else f"On the {first['title']}",
                "facts": facts, "notes": notes, "refs": refs, "source": "ca_lists", "match": how,
                "match_detail": f"Matched to Health Canada's lists by {how}."}

    def anz_status(self, key, ent):
        anz = self.parsed.get("anz_code")
        if anz is None:
            return None
        refs = [{"label": "Standard 1.3.1 Food additives", "url": "https://www.legislation.gov.au/F2015L00396/latest/text"},
                {"label": "Schedule 15 (Federal Register of Legislation)", "url": "https://www.legislation.gov.au/F2015L00439/latest/text"},
                {"label": "Schedule 16", "url": "https://www.legislation.gov.au/F2015L00442/latest/text"},
                {"label": "Schedule 8 (names and code numbers)", "url": "https://www.legislation.gov.au/F2015L00478/latest/text"}]
        doc = ((anz.get("meta") or {}).get("files", {}).get("compilation.pdf", {}) or {}).get("document_url")
        if doc:
            refs.append({"label": "FSANZ Food Standards Code compilation (PDF)", "url": doc})
        codes = []
        if ent.get("e"):
            codes.append((self.curated.get("anz_codes") or {}).get(key, key))
        if ent.get("ins"):
            code = "e" + ent["ins"].lower()
            codes += [code] + ([code[:-1]] if code[-1].isalpha() else [])   # INS 924a -> 924a, then 924
        for code in codes:
            st, reasons = parse_anz.status_of(anz, code)
            if st == "authorised":
                listed = anz["names"].get(code) or anz["names"].get(re.match(r"(e\d+[a-z]?)", code).group(1)) or []
                num = code[1:].replace("-", "(") + (")" if "-" in code else "")
                facts = [["INS number", num]]
                if listed:
                    facts.append(["Listed as", "; ".join(listed[:2])])
                facts.append(["Permitted through", "; ".join(reasons)])
                how = "INS number" + (" (reviewed table)" if code != key and ent.get("e") else "")
                return {"status": "authorised", "headline": "Permitted as a food additive" if "GMP" in " ".join(reasons)
                        or "maximum level" in " ".join(reasons) else "Permitted in specific foods (Schedule 15)",
                        "facts": facts, "refs": refs, "source": "anz_code", "match": how,
                        "match_detail": f"Matched by {how} in Schedules 8, 15 and 16."}
        # substances listed by name only in Schedule 15
        wanted = {match_key(n) for n in [ent["name"]] + ent.get("names", [])[:8]}
        alias = (self.curated.get("anz_names") or {}).get(key)
        if alias:
            wanted.add(match_key(alias))
        for nm in anz.get("schedule15_names", []):
            if match_key(re.sub(r"\s*\(.*$", "", nm)) in wanted or match_key(nm) in wanted:
                return {"status": "authorised", "headline": "Permitted in specific foods (Schedule 15, by name)",
                        "facts": [["Listed as", nm]], "refs": refs, "source": "anz_code", "match": "name",
                        "match_detail": "Schedule 15 lists this substance by name, without an INS number."}
        for code, nms in anz["names"].items():
            # Schedule 8 names can join alternatives: "Carbon blacks or Vegetable carbon"
            parts = [p for n in nms for p in re.split(r"\s+or\s+", n)]
            if any(match_key(n) in wanted for n in nms + parts):
                st, reasons = parse_anz.status_of(anz, code)
                if st == "authorised":
                    return {"status": "authorised", "headline": "Permitted as a food additive",
                            "facts": [["INS number", code[1:]], ["Listed as", "; ".join(nms[:2])],
                                      ["Permitted through", "; ".join(reasons)]],
                            "refs": refs, "source": "anz_code", "match": "name",
                            "match_detail": "Matched by name in Schedule 8."}
        aid = parse_anz.processing_aid(anz, [ent["name"]] + ent.get("names", [])[:8])
        if aid:
            return {"status": "not_authorised",
                    "headline": "Not a permitted food additive; permitted as a processing aid (Schedule 18)",
                    "facts": [["Schedule 18 entry", aid[:160]]],
                    "notes": ["A processing aid is used during manufacture for a technological purpose and does not "
                              "perform that purpose in the final food. Standard 1.3.3 sets the conditions."],
                    "refs": refs + [{"label": "Schedule 18 Processing aids", "url": "https://www.legislation.gov.au/F2015L00452/latest/text"}],
                    "source": "anz_code", "match": "name",
                    "match_detail": "Not in Schedule 15 or on the Schedule 16 lists; its name is listed in Schedule 18 (processing aids)."}
        unsure = (self.curated.get("anz_uncertain") or {}).get(key)
        if unsure:
            return {"status": "not_listed", "headline": "Not in the food additive schedules", "notes": [unsure],
                    "refs": refs, "source": "anz_code", "match": None,
                    "match_detail": "No Schedule 8, 15, 16 or 18 entry matches its names. See the note."}
        if ent.get("e") or ent.get("ins"):
            return {"status": "not_authorised", "headline": "Not permitted as a food additive in Australia and New Zealand",
                    "refs": refs, "source": "anz_code", "match": "INS number",
                    "match_detail": "Its INS number is not in Schedule 15 or on the Schedule 16 lists, and no entry matches its name."}
        # Substances without an INS number on this site are US or Canadian food additives. Under
        # Standard 1.3.1 an additive may be used only if Schedule 15 or 16 permits it.
        return {"status": "not_authorised", "headline": "Not permitted as a food additive in Australia and New Zealand",
                "refs": refs, "source": "anz_code", "match": "name",
                "match_detail": "No Schedule 8, 15, 16 or 18 entry matches its names. Food additives may be used only "
                                "if Schedule 15 or Schedule 16 permits them."}

    # ------------------------------------------------------------ details and overview
    def details(self, key, ent, us_matches, ca_matches) -> dict:
        """Official facts for the additive page: identity, EU conditions of use, Canadian foods and limits."""
        out: dict = {}
        specs = (self.parsed.get("eu_specs") or {}).get("entries", {})
        mine = [sp for sk, sp in sorted(specs.items()) if sk == key or base_key(sk) == key and sk.startswith(key + "-")]
        if mine:
            main = specs.get(key) or mine[0]
            ident = {"definition": main.get("definition", ""), "description": main.get("description", ""),
                     "formula": main.get("formula", [])[:4], "einecs": main.get("einecs", [])[:4],
                     "colour_index": main.get("colour_index", [])[:2], "synonyms": main.get("synonyms", [])[:6],
                     "spec_title": main["title"].capitalize() if main["title"].isupper() else main["title"]}
            if len(mine) > 1 or key not in specs:
                ident["parts"] = [{"key": sp["key"], "label": key_label(sp["key"]),
                                   "title": sp["title"].capitalize() if sp["title"].isupper() else sp["title"],
                                   "description": sp.get("description", "")[:200]} for sp in mine[:8]]
            out["identity"] = {k: v for k, v in ident.items() if v}
        eu = self.parsed.get("eu_annex2")
        if eu:
            rows = eu.get("use_rows", {}).get(key) or []
            m = re.match(r"(e\d+[a-z]?)-", key)
            if not rows and m:
                rows = eu.get("use_rows", {}).get(m.group(1)) or []
            seen, uses = set(), []
            for r in sorted(rows, key=lambda r: [int(x) for x in r["cat"].split(".")]):
                sig = (r["cat"], r["level"], r["restr"], r.get("group"))
                if sig in seen:
                    continue
                seen.add(sig)
                uses.append({"cat": r["cat"], "cat_name": eu["categories"].get(r["cat"], ""), "level": r["level"],
                             "restr": r["restr"], "notes": r.get("foot_text", [])[:3], "group": r.get("group"),
                             "entry": r["entry"] if r["entry"] != (ent.get("e") or "") and not r.get("group") else ""})
            if uses:
                out["eu_uses"] = uses[:400]
                if len(uses) > 400:
                    out["eu_uses_total"] = len(uses)
        ca_lists = []
        for r, _ in ca_matches:
            for L in r["lists"]:
                if L.get("rows"):
                    ca_lists.append({"no": L["no"], "title": L["title"], "url": L["url"], "listed_as": r["name"],
                                     "rows": L["rows"][:40], "row_count": L.get("row_count", len(L["rows"]))})
        if ca_lists:
            out["ca_uses"] = sorted(ca_lists, key=lambda x: x["no"])[:6]
        functions = []

        def add(raw):
            f = canonical_function(raw)
            if f and f not in functions:
                functions.append(f)
        # Most reliable first: EU list section, then Canada's function-based lists, then the
        # first technical effects of the best FDA match (FDA effect lists are long and noisy).
        if "Colours" in ent["classes"]:
            add("colour")
        if "Sweeteners" in ent["classes"]:
            add("sweetener")
        for r, _ in ca_matches:
            for L in r["lists"]:
                if L["no"] in (5, 13, 14, 15):
                    continue  # processing aids and reagents, not the additive's own function
                if L["no"] in (4, 8):
                    for p in L["purposes"][:2]:
                        add(p)
                else:
                    add(L["title"].replace("List of Permitted ", ""))
        good = [m for m in us_matches if m[1] and not m[1].startswith("alternative")]
        src = "lists"
        if good and not functions:
            # FDA's technical effects are long, noisy lists: used only when nothing better exists
            ent_key = match_key(ent["name"])
            best = max(good, key=lambda m: match_rank(m, ent_key))[0]
            for e in best["effects"][:2]:
                add(e)
            src = "fda"
        out["functions"] = functions[:3]
        out["functions_source"] = src
        return out

    def overview(self, a: dict) -> list[str]:
        """A few plain sentences built only from the data on the page."""
        from pipeline.model import JURISDICTIONS
        sents = []
        name = a["name"] + (f" ({a['e']})" if a.get("e") else "")
        fn = a.get("functions", [])
        if fn:
            fl = fn[:3]
            fs = fl[0] if len(fl) == 1 else ", ".join(fl[:-1]) + " and " + fl[-1]
            if a.get("functions_source") == "fda":
                sents.append(f"{name} is a food additive. FDA's inventory lists {'this technical effect' if len(fl) == 1 else 'these technical effects'}: {fs}.")
            else:
                sents.append(f"{name} is a food additive. The official lists name {'this function' if len(fl) == 1 else 'these functions'}: {fs}.")
        else:
            sents.append(f"{name} is a food additive.")
        ident = a.get("identity", {})
        if ident.get("description"):
            d = ident["description"].split(". ")[0].rstrip(".")
            if len(d) <= 220:
                sents.append(f"The EU specification describes it as: \u201c{d}.\u201d")
        eu = a["jur"]["eu"]
        cat_names = (self.parsed.get("eu_annex2") or {}).get("categories", {})
        if eu.get("status") == "authorised" and a.get("eu_uses"):
            tops = []
            for u in a["eu_uses"]:
                top = u["cat"].split(".")[0].zfill(2) if u["cat"] != "0" else "0"
                nm = "all foods" if top in ("0", "00") else re.sub(r"\s*\(.*$", "", cat_names.get(top, "")).strip()
                if nm and nm.lower() not in [t.lower() for t in tops]:
                    tops.append(nm)
            n = len({u["cat"] for u in a["eu_uses"]})
            ex = "; ".join(t[0].lower() + t[1:] for t in tops[:4])
            sents.append(f"In the EU it may be used in {n} food categor{'y' if n == 1 else 'ies'}"
                         + (f", in these food groups: {ex}" if ex else "") + ("…" if len(tops) > 4 else "."))
        elif eu.get("status") in ("not_authorised", "delisted") and a.get("e"):
            h = eu.get("headline", "").rstrip(".")
            sents.append(f"In the EU: {h[0].lower() + h[1:]}." if h else "")
        for h in (a.get("eu_history") or [])[-1:]:
            d = date.fromisoformat(h["date"])
            sents.append(f"EU history: {h['change'].lower()}, first shown in the consolidated text of {d.strftime('%-d %B %Y')}.")
        us = a["jur"]["us"]
        fda = next((f[1] for f in us.get("facts", []) if f[0] == "FDA name"), None)
        if us.get("status") in ("authorised", "phase_out", "delisted", "prohibited") and fda:
            sents.append(f"In the US it is listed as {fda}: {us['headline'][0].lower() + us['headline'][1:]}.")
        elif us.get("status") == "not_authorised":
            sents.append(f"In the US: {us['headline'][0].lower() + us['headline'][1:]}.")
        ca = a["jur"]["ca"]
        if ca.get("status") == "authorised":
            listed = next((f[1] for f in ca.get("facts", []) if f[0] == "Listed as"), None)
            sents.append(f"Canada permits it{(' as ' + listed) if listed and listed.lower() != a['name'].lower() else ''} "
                         f"({ca['headline'][0].lower() + ca['headline'][1:]}).")
        return [x for x in sents if x]

    # ------------------------------------------------------------ run
    def run(self) -> int:
        self.parse("eu_annex2", parse_eu.parse_all)
        self.parse("uk_fsa", parse_uk.parse_all)
        self.parse("us_fda_substances", parse_us.parse_all)
        self.parse("ca_lists", parse_ca.parse_all)
        self.parse("eu_specs", parse_specs.parse_all)
        self.parse("us_fr_revocations", parse_fr.parse_all)
        self.parse("anz_code", parse_anz.parse_all)
        self.validate()
        self.apply_revocations()

        ents = self.build_entities()
        self._ent_names = {k: e["name"] for k, e in ents.items()}
        self._ent_classes = {k: e["classes"] for k, e in ents.items()}
        by_name, by_cas, by_ci = self.build_index(ents)
        us_m, us_stats, us_unmatched = self.match_us(ents, by_name, by_cas, by_ci)

        # US-only entities: prohibited/revoked FDA substances, plus a reviewed list of notable ones
        def _entries(key):
            out = {}
            for it in self.curated.get(key) or []:
                it = {"name": it} if isinstance(it, str) else dict(it)
                out[it["name"]] = it
            return out
        us_only_info = _entries("us_only")
        ca_only_info = _entries("ca_only")
        us_only = set(us_only_info)
        missing = us_only - {r["name"] for r in us_unmatched}
        for n in sorted(missing):
            self.log["us_only_not_found_or_matched"].append(n)
        for r in us_unmatched:
            notable_status = (r["status"] in ("prohibited", "delisted", "phase_out") and "NLFG" not in r["flags"]
                              and "lake" not in r["key"])
            notable_use = r["name"] in us_only
            if not (notable_status or notable_use):
                continue
            sid = "us-" + re.sub(r"[^a-z0-9]+", "-", r["name"].lower()).strip("-")[:60]
            if sid in ents:
                us_m[sid].append((r, "FDA inventory entry"))
                continue
            info = us_only_info.get(r["name"], {})
            ents[sid] = {"id": sid, "e": None, "sort": "~" + sid, "name": r["display"],
                         "names": [r["display"]] + info.get("aka", []) + r["other_names"][:10], "cas": r["cas"],
                         "ci": r["colour_index"], "classes": [], "jur": {}, "us_only": True,
                         "ins": info.get("ins")}
            us_m[sid] = [(r, "FDA inventory entry")]
            for n in [r["name"]] + r["other_names"][:10]:
                by_name[match_key(n)].add(sid)

        # Revoked substances known only from the Federal Register (no longer in FDA's inventory)
        fr = self.parsed.get("us_fr_revocations")
        for item in (self.curated.get("us_fr_entities") or []):
            doc = next((r for r in (fr or {}).get("revocations", []) if r["document_number"] == item["doc"]), None)
            if not doc:
                self.log["us_fr_entities_missing"].append(item["doc"])
                continue
            sid = "us-" + re.sub(r"[^a-z0-9]+", "-", item["name"].lower()).strip("-")
            eff = doc["effective"]
            rec = {"name": item["name"].upper(), "display": item["name"], "other_names": item.get("aka", []),
                   "cas": [], "effects": [], "cfr": [], "flags": [], "colour_index": [],
                   "status": "phase_out" if eff and eff > self.today else "delisted",
                   "headline": (f"Authorisation revoked; ends {eff.strftime('%-d %B %Y')}" if eff and eff > self.today
                                else f"Authorisation revoked{(' — effective ' + eff.strftime('%-d %B %Y')) if eff else ''}"),
                   "key": match_key(item["name"]),
                   "revocation": {"title": doc["title"], "url": doc["url"], "published": doc["publication_date"],
                                  "effective": eff.isoformat() if eff else None, "doc": doc["document_number"]}}
            ents[sid] = {"id": sid, "e": None, "sort": "~" + sid, "name": item["name"],
                         "names": [item["name"]] + item.get("aka", []), "cas": [], "ci": [], "classes": [],
                         "jur": {}, "us_only": True}
            self._ent_names[sid] = item["name"]
            self._ent_classes[sid] = []
            us_m[sid] = [(rec, "Federal Register final rule")]
            for n in ents[sid]["names"]:
                by_name[match_key(n)].add(sid)

        # FDA names of confidently matched records help match Canadian names
        for k, ms in us_m.items():
            for r, how in ms:
                if how and not how.startswith("alternative") and r["status"] in ("authorised", "phase_out", "listed_noreg"):
                    for n in [r["name"]] + r["other_names"][:15]:
                        mk = match_key(n)
                        if mk and len(mk) >= 5 and not re.search(r"\d{3,}", mk):
                            by_name[mk].add(k)
        ca_m, ca_stats, ca_unmatched = self.match_ca(ents, by_name)

        # Canada-only entities: permitted in Canada, no counterpart on the EU/GB lists or among the
        # notable US entries. Enzymes (5), starch-modifying agents (13), yeast foods (14) and
        # carrier/extraction solvents (15) are left out: they are processing aids, not additives
        # in the EU sense.
        fda_by_key = defaultdict(list)
        for r in us_unmatched:
            for n in [r["name"]] + r["other_names"][:10]:
                fda_by_key[match_key(n)].append(r)
        ca_only = set(ca_only_info)
        for r in ca_unmatched:
            if r["name"] not in ca_only:
                continue
            cid = "ca-" + re.sub(r"[^a-z0-9]+", "-", norm_name(r["name"])).strip("-")[:60]
            if cid in ents:
                continue
            ents[cid] = {"id": cid, "e": None, "sort": "~~" + cid, "name": r["name"], "names": list(r["names"]),
                         "cas": [], "ci": [], "classes": [], "jur": {}, "ca_only": True,
                         "ins": ca_only_info.get(r["name"], {}).get("ins")}
            self._ent_names[cid] = r["name"]
            ca_m[cid].append((r, "Health Canada list entry"))
            ca_stats["own entry"] += 1
            ca_stats["unmatched"] -= 1
            hits = []
            for n in r["names"]:
                hits.extend((h, "name") for h in fda_by_key.get(match_key(n), []))
            for n in ca_only_info.get(r["name"], {}).get("us_names", []):   # reviewed FDA names
                hits.extend((h, "reviewed match table") for h in fda_by_key.get(match_key(n), []))
            seen = set()
            for h, how in hits:
                if id(h) not in seen:
                    seen.add(id(h))
                    us_m[cid].append((h, how))

        additives = []
        for k, ent in ents.items():
            prev = self.prev_by_id.get(k, {}).get("jur", {})
            classes = list(dict.fromkeys(ent["classes"]))
            for r, _ in ca_m.get(k, []):
                for c in r.get("classes", []):
                    if c not in classes:
                        classes.append(c)
            good_us = [m for m in us_m.get(k, []) if not (m[1] or "").startswith("alternative")]
            if not classes and good_us:
                # FDA effect lists are long and noisy (talc lists "non-nutritive sweetener"), so only
                # the first two effects of the best match are used.
                best_us = max(good_us, key=lambda m: match_rank(m, match_key(ent["name"])))[0]
                eff = {e.lower() for e in best_us["effects"][:2]}
                for e, c in (("color or coloring adjunct", "Colours"), ("preservative", "Preservatives"),
                             ("antioxidant", "Antioxidants"), ("non-nutritive sweetener", "Sweeteners"),
                             ("emulsifier or emulsifier salt", "Emulsifiers, stabilisers, thickeners and gelling agents"),
                             ("stabilizer or thickener", "Emulsifiers, stabilisers, thickeners and gelling agents"),
                             ("dough strengthener", "Flour treatment agents"), ("flour treating agent", "Flour treatment agents")):
                    colour_cfr = any(re.match(r"^7[34]\.\d{1,3}$", x) for x in best_us["cfr"])
                    if e in eff and c not in classes and (c != "Colours" or colour_cfr):
                        classes.append(c)
            self._ent_classes[k] = classes
            jur = {}
            for j, fn in (("eu", lambda: self.eu_status(k, ent)), ("gb", lambda: self.gb_status(k, ent)),
                          ("us", lambda: self.us_status(k, us_m.get(k, []))),
                          ("ca", lambda: self.ca_status(k, ca_m.get(k, []))),
                          ("anz", lambda: self.anz_status(k, ent))):
                rec = fn()
                if rec is None:
                    rec = dict(prev.get(j, {"status": "unknown", "headline": "Source temporarily unavailable"}))
                    rec["stale"] = True
                jur[j] = rec
            aka, seen = [], {match_key(ent["name"])}
            pool = ent["names"] + [m[0]["display"] for m in us_m.get(k, [])
                                   if m[0]["status"] != "delisted" and not (m[1] or "").startswith("alternative")
                                   and "lake" not in m[0]["display"].lower()] + \
                [n for m in ca_m.get(k, []) for n in m[0]["names"]]
            for n in pool:
                mk = match_key(n)
                if mk and mk not in seen and len(n) <= 60 and not n.upper().startswith("CI "):
                    seen.add(mk)
                    aka.append(n)
            cas = list(dict.fromkeys(ent["cas"] + [c for m in us_m.get(k, []) for c in m[0]["cas"]
                                                   if m[1] and not m[1].startswith("alternative")]))
            item = {"id": k, "e": ent["e"], "sort": ent["sort"], "name": ent["name"], "aka": aka[:16],
                    "cas": cas[:4], "classes": classes, "jur": jur}
            if ent.get("ins"):
                item["ins"] = ent["ins"]
            item.update(self.details(k, ent, us_m.get(k, []), ca_m.get(k, [])))
            additives.append(item)
        additives.sort(key=lambda a: a["sort"])

        # EU history from consolidated versions (since the Union list applied, 1 June 2013)
        hist = read_json(eu_history.HISTORY, {}) or {}
        events = eu_history.timeline(hist) if hist.get("versions") else {}
        change_text = {("X", "A"): "Added to the EU list", ("A", "X"): "Removed from the EU list",
                       ("A", "N"): "No longer authorised in food", ("N", "A"): "Authorised in food again",
                       ("N", "X"): "Removed from the EU list", ("X", "N"): "Added to the EU list, but not for use in food"}
        eu_hist_all = []
        for a in additives:
            evs = []
            seq = [e["from"] for e in events.get(a["id"], [])[:1]] + [e["to"] for e in events.get(a["id"], [])]
            if len(seq) != len(set(seq)):
                # A status that comes back (e.g. authorised -> not -> authorised) is more likely an
                # artefact of reading differently formatted old texts than a real legal history.
                # Show nothing rather than a history we cannot vouch for.
                a["eu_history_unclear"] = True
                continue
            for e in events.get(a["id"], []):
                d = e["version"]
                item = {"date": f"{d[:4]}-{d[4:6]}-{d[6:]}", "change": change_text.get((e["from"], e["to"]), "Status changed"),
                        "url": f"https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:{e['celex']}"}
                evs.append(item)
                eu_hist_all.append({**item, "id": a["id"]})
            if evs:
                a["eu_history"] = evs
        for a in additives:
            a["overview"] = self.overview(a)
        vs = sorted(hist.get("versions", {}))
        eu_history_meta = {"versions": len(vs), "first": vs[0] if vs else None, "last": vs[-1] if vs else None,
                           "skipped": len(hist.get("skipped", {}))}

        # changelog
        today = self.today.isoformat()
        changelog = read_json(PUBLISHED / "changelog.json", {"entries": [], "tracking_since": None})
        changelog.setdefault("tracking_since", today)
        if not changelog.get("tracking_since"):
            changelog["tracking_since"] = today
        seen_ids = set()
        same_method = self.prev.get("method_version") == METHOD_VERSION
        if not same_method:
            self.log["changelog"].append(f"method changed ({self.prev.get('method_version')} -> {METHOD_VERSION}); "
                                         "differences not recorded as changes")
            if not changelog.get("entries"):
                changelog["tracking_since"] = today
        if self.prev_by_id and same_method:
            for a in additives:
                seen_ids.add(a["id"])
                old = self.prev_by_id.get(a["id"])
                if not old:
                    continue
                for j in JUR_ORDER:
                    if a["jur"][j].get("stale"):
                        continue
                    o, n = old["jur"].get(j, {}).get("status"), a["jur"][j].get("status")
                    if o and n and o != n and "unknown" not in (o, n):
                        changelog["entries"].append({"date": today, "id": a["id"], "name": a["name"], "jur": j,
                                                     "from": o, "to": n})
        for a in additives:
            old = self.prev_by_id.get(a["id"])
            same = old and all(old["jur"].get(j, {}).get("status") == a["jur"][j].get("status") for j in JUR_ORDER)
            a["updated"] = old.get("updated", today) if same else today

        # sources
        src_out = {}
        for sid, cfg in self.sources.items():
            meta = read_json(RAW / sid / "meta.json", {}) or {}
            files = meta.get("files", {})
            f0 = next(iter(files.values()), {}) if files else {}
            url = cfg.get("page_url") or cfg.get("url") or ""
            if sid in ("eu_annex2", "eu_specs") and f0.get("celex"):
                url = cfg["html_url"].format(celex=f0["celex"])
            if sid == "ca_lists":
                url = "https://www.canada.ca/en/health-canada/services/food-nutrition/food-safety/food-additives/lists-permitted.html"
            if sid == "uk_fsa":
                url = "https://data.food.gov.uk/regulated-products"
            src_out[sid] = {
                "title": cfg["title"], "publisher": cfg["publisher"], "url": url,
                "licence": cfg["licence"], "licence_url": cfg.get("licence_url", ""),
                "attribution": cfg["attribution"], "retrieved_at": meta.get("last_success"),
                "content_changed_at": max((f.get("content_changed_at") or "" for f in files.values()), default="") or None,
                "version": f0.get("celex"),
                "state": "ok" if self.parsed.get(sid) is not None else "stale",
            }
            if meta.get("last_error"):
                self.problems.append(f"{sid}: last fetch failed: {meta['last_error']}")

        quality = []
        n_ca = sum(ca_stats.values())
        if n_ca:
            quality.append(f"Health Canada lists: {n_ca - ca_stats.get('unmatched', 0) - ca_stats.get('excluded', 0)} "
                           f"of {n_ca} listed substances linked to an additive on this site; the rest are mostly "
                           "enzymes, solvents and substances with no counterpart here.")
        linked_us = sum(1 for a in additives if a["jur"]["us"].get("status") not in ("not_listed", "unknown"))
        quality.append(f"FDA inventory: {linked_us} additives on this site are linked to at least one FDA inventory entry.")
        n_e = sum(1 for a in additives if a.get("e"))
        quality.append(f"{n_e} additives with an E-number (all entries on the EU and GB lists) and "
                       f"{len(additives) - n_e} US substances without an E-number.")

        write_json(INTERIM / "unmatched_us.json", [
            {k: r[k] for k in ("name", "cas", "cfr", "status", "effects")} for r in us_unmatched
            if r["status"] != "authorised" or not set(r["cfr"]) <= FLAVOUR_SECTIONS])
        write_json(INTERIM / "unmatched_ca.json", [
            {"name": r["name"], "lists": [L["no"] for L in r["lists"]]} for r in ca_unmatched])
        write_json(INTERIM / "match_log.json", {"us": dict(us_stats), "ca": dict(ca_stats), **self.log})

        published = {"generated_at": now_iso(), "data_version": today, "method_version": METHOD_VERSION,
                     "eu_history": eu_history_meta,
                     "eu_history_events": sorted(eu_hist_all, key=lambda x: x["date"], reverse=True),
                     "sources": src_out,
                     "counts": self.counts() or self.prev.get("counts", {}), "quality": quality,
                     "additives": additives}
        if len(additives) < 200:
            self.problems.append(f"Only {len(additives)} additives produced; not published")
        else:
            write_json(PUBLISHED / "additives.json", published)
            write_json(PUBLISHED / "changelog.json", changelog)
        write_json(PUBLISHED / "health.json", {"at": now_iso(), "problems": self.problems,
                                               "counts": self.counts(), "us_match": dict(us_stats),
                                               "ca_match": dict(ca_stats)})
        print(f"{len(additives)} additives; US {dict(us_stats)}; CA {dict(ca_stats)}")
        for p in self.problems:
            print("PROBLEM", p)
        return 0 if len(additives) >= 200 else 1


if __name__ == "__main__":
    sys.exit(Build().run())
