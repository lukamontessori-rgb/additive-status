"""Parse the Australia New Zealand Food Standards Code compilation (PDF).

Food additives in Australia and New Zealand are governed by Standard 1.3.1:
a substance may be used as a food additive only if Schedule 15 permits it for
the food, or if it is on one of the Schedule 16 lists (additives permitted at
GMP, colourings permitted at GMP, colourings permitted to a maximum level).
Schedule 8 gives the official names and INS code numbers.

We read:
  * Schedule 8 numerical listing -> code -> names (the universe of valid codes)
  * Schedule 16 lists           -> codes permitted at GMP / colourings
  * Schedule 15 table rows      -> codes permitted for specific foods
Amendment-history pages are skipped (they contain unrelated numbers).
"""
from __future__ import annotations

import io
import re
from collections import defaultdict

from pipeline.common import RAW, read_json, read_raw

CODE = r"\d{3,4}[a-z]?(?:\s?\((?:i|ii|iii|iv|v|vi|vii|viii|ix|x)\))?"
LINE_CODE_RE = re.compile(rf"^\s*({CODE})\s+(\S.*)$")
TOKEN_RE = re.compile(rf"(?<![\d.,/])({CODE})(?![\d.,/])")
LEAD_CODES_RE = re.compile(rf"^\s*((?:{CODE}\s+)+)(?=[A-Za-z]|\d(?:,\d)*-[A-Za-z]|$)")  # also "586 4-hexylresorcinol"


def norm_code(c: str) -> str:
    c = re.sub(r"\s+", "", c.lower())
    m = re.fullmatch(r"(\d{3,4})([a-z]?)(?:\((\w+)\))?", c)
    if not m:
        return c
    return f"e{m.group(1)}{m.group(2)}" + (f"-{m.group(3)}" if m.group(3) else "")


def page_texts(pdf: bytes) -> list[str]:
    from pypdf import PdfReader
    r = PdfReader(io.BytesIO(pdf))
    return [(p.extract_text() or "") for p in r.pages]


def section_pages(texts: list[str], start_marker: str, end_marker: str) -> list[int]:
    start = next((i for i, t in enumerate(texts) if start_marker in t), None)
    if start is None:
        return []
    end = next((i for i in range(start + 1, len(texts)) if end_marker in texts[i]), len(texts))
    # The amendment history (with unrelated numbers) runs to the end of each schedule.
    hist = next((i for i in range(start, end) if "Amendment History" in texts[i] or "Section affected" in texts[i]), end)
    return list(range(start, hist))


def parse_texts(texts: list[str]) -> dict:
    # Schedule 8: code -> names
    names: dict[str, list[str]] = defaultdict(list)
    in_num = False
    last = None
    for i in section_pages(texts, "S8—1 Name", "S9—1 Name"):
        for line in texts[i].split("\n"):
            if "numerical listing" in line:
                in_num = True
                continue
            if not in_num:
                continue
            if re.search(r"Authorised Version|Schedule 8 \d+|^\s*$", line):
                continue
            m = LINE_CODE_RE.match(line)
            if m:
                code = norm_code(m.group(1))
                nm = re.sub(r"\s+", " ", m.group(2)).strip()
                if nm and not re.match(r"^F20\d\d", nm):
                    names[code].append(nm)
                    last = (code, len(names[code]) - 1)
            elif last and re.match(r"^\s*[a-z(]", line):  # continuation of the previous name
                code, idx = last
                names[code][idx] = (names[code][idx] + " " + line.strip()).strip()
    valid = set(names)

    def plausible(c: str) -> bool:
        m = re.match(r"e(\d+)", c)
        return bool(m) and 100 <= int(m.group(1)) <= 1599

    # Schedule 16 lists (every code on these pages is an additive code)
    gmp, colours_gmp, colours_max = set(), set(), set()
    current = gmp
    for i in section_pages(texts, "S16—1 Name", "S17—1 Name"):
        for line in texts[i].split("\n"):
            if "S16—3" in line:
                current = colours_gmp
            elif "S16—4" in line:
                current = colours_max
            elif "S16—2" in line:
                current = gmp
            for tok in TOKEN_RE.findall(line):
                c = norm_code(tok)
                if c in valid or plausible(c):
                    current.add(c)

    # Schedule 15: codes that start a table row (or a continuation line of codes)
    s15 = set()
    s15_names = set()
    stop = {"in", "only", "maximum", "the", "calculated", "see", "for", "if", "of", "total", "when", "note", "and"}
    for i in section_pages(texts, "S15—1 Name", "S16—1 Name"):
        for line in texts[i].split("\n"):
            m = LEAD_CODES_RE.match(line)
            if m:
                rest = line[m.end():].strip()
                first = (rest.split() or [""])[0].lower()
                # a line of codes only must consist of known codes (otherwise it is a wrapped level)
                name_like = rest[:1].isupper() and first not in stop and not rest.startswith("F20")
                toks = [norm_code(t) for t in re.findall(CODE, m.group(1))]
                if rest == "" and not all(c in valid for c in toks):
                    continue
                for c in toks:
                    if c in valid or (name_like and plausible(c)):
                        s15.add(c)
                continue
            # rows without an INS number: " Benzyl alcohol 500 In the final food"
            nm = re.match(r"^\s*([A-Z][A-Za-z ,\-'()]+?)\s+(GMP|\d[\d ]*)\b", line)
            if nm and len(nm.group(1)) > 4 and nm.group(1).split()[0].lower() not in stop:
                s15_names.add(re.sub(r"\s+", " ", nm.group(1)).strip())
    # Schedule 18: processing aids (a different permission from food additives)
    s18 = []
    for i in section_pages(texts, "S18—1 Name", "S19—1 Name"):
        for line in texts[i].split("\n"):
            line = re.sub(r"\s+", " ", line).strip()
            if line and not re.search(r"Authorised Version|^\d+ \w+ 20\d\d Schedule 18", line):
                s18.append(line)
    return {"names": {k: v for k, v in names.items()}, "gmp": sorted(gmp), "colours_gmp": sorted(colours_gmp),
            "colours_max": sorted(colours_max), "schedule15": sorted(s15), "schedule15_names": sorted(s15_names),
            "schedule18_lines": s18}


def parse_all() -> dict:
    meta = read_json(RAW / "anz_code" / "meta.json", {}) or {}
    out = parse_texts(page_texts(read_raw("anz_code", "compilation.pdf")))
    out["meta"] = meta
    return out


def status_of(parsed: dict, key: str) -> tuple[str, list[str]]:
    """('authorised'|'not_authorised', reasons) for an E/INS key such as e129 or e160a-ii."""
    base = re.match(r"(e\d+[a-z]?)", key).group(1) if re.match(r"e\d", key) else key
    reasons = []
    for k in {key, base}:
        if k in parsed["gmp"]:
            reasons.append("Schedule 16: additive permitted at GMP")
        if k in parsed["colours_gmp"]:
            reasons.append("Schedule 16: colouring permitted at GMP")
        if k in parsed["colours_max"]:
            reasons.append("Schedule 16: colouring permitted to a maximum level")
        if k in parsed["schedule15"]:
            reasons.append("Schedule 15: permitted in specific foods")
    reasons = list(dict.fromkeys(reasons))
    return ("authorised" if reasons else "not_authorised"), reasons


def spelling_pattern(name: str) -> str:
    """Regex for a name that accepts British and American spellings (sulphate/sulfate, aluminium/aluminum)."""
    alts = {"sulph": "sul(?:ph|f)", "sulf": "sul(?:ph|f)", "aluminium": "alumin(?:i)?um",
            "aluminum": "alumin(?:i)?um", "colour": "colou?r", "color": "colou?r"}
    parts = re.split(r"(sulph|sulf|aluminium|aluminum|colour|color)", name.lower())
    return "".join(alts.get(p, re.escape(p)) for p in parts)


def processing_aid(parsed: dict, names: list[str]) -> str | None:
    """The Schedule 18 line that lists one of these names as a processing aid, if any.

    A name counts only at the start of a line (a table row or list item) and only when it is
    followed by the end of the line, a level, GMP or the next column - so "hydrogen" does not
    match "Hydrogen peroxide"."""
    lines = parsed.get("schedule18_lines") or []
    for nm in names:
        nm = re.sub(r"\s+", " ", nm or "").strip()
        if len(nm) < 4:
            continue
        body = spelling_pattern(nm)
        pat = re.compile(r"^(?:\([a-z]\) )?(?i:" + body + r")(?=$| ?[;,.(]| (?:GMP|\d)| [A-Z])")
        perm = re.compile(r"Permission to use (?i:" + body + r") as ")
        for line in lines:
            if pat.search(line) or perm.search(line):
                return line
    return None


if __name__ == "__main__":
    out = parse_all()
    print("codes in Schedule 8:", len(out["names"]), "GMP:", len(out["gmp"]), "colours GMP:", len(out["colours_gmp"]),
          "colours max:", len(out["colours_max"]), "Schedule 15:", len(out["schedule15"]))
