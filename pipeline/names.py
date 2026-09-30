"""Normalisation of identifiers and names used for matching."""
from __future__ import annotations

import re
import unicodedata

CAS_RE = re.compile(r"\b(\d{2,7})-(\d{2})-(\d)\b")
# E 160a(ii), E 1422, E 150c, E 960b(i)
E_RE = re.compile(r"\bE[\s -]*(\d{3,4})([a-z]?)(?![a-z0-9])\s*(\((?:i|ii|iii|iv|v|vi|vii|viii|ix|x)\))?", re.I)


def cas_valid(cas: str) -> bool:
    m = CAS_RE.fullmatch(cas.strip())
    if not m:
        return False
    digits = (m.group(1) + m.group(2))[::-1]
    return sum((i + 1) * int(d) for i, d in enumerate(digits)) % 10 == int(m.group(3))


def find_cas(text: str) -> list[str]:
    out = []
    for m in CAS_RE.finditer(text or ""):
        c = m.group(0)
        if cas_valid(c) and c not in out:
            out.append(c)
    return out


def e_parts(text: str):
    """Parse 'E 160a(ii)' -> (160, 'a', 'ii'). Returns None if not an E-number."""
    m = E_RE.search(text or "")
    if not m:
        return None
    sub = (m.group(3) or "").strip("()").lower()
    return int(m.group(1)), (m.group(2) or "").lower(), sub


def e_display(num: int, letter: str = "", sub: str = "") -> str:
    return f"E {num}{letter}" + (f"({sub})" if sub else "")


def e_id(num: int, letter: str = "", sub: str = "") -> str:
    """URL-safe id: e160a, e160a-ii."""
    return f"e{num}{letter}" + (f"-{sub}" if sub else "")


def e_sort(num: int, letter: str = "", sub: str = "") -> str:
    roman = ["", "i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"]
    return f"{num:04d}{letter or ' '}{roman.index(sub) if sub in roman else 0:02d}"


def e_key(text: str) -> str | None:
    p = e_parts(text)
    return e_id(*p) if p else None


def e_base_key(text: str) -> str | None:
    """Key without the sub-number: E 160a(ii) -> e160a."""
    p = e_parts(text)
    return e_id(p[0], p[1]) if p else None


_ROMAN_END = re.compile(r"\b(no|number)\s*\.?\s*(\d+)\b")


def norm_name(name: str) -> str:
    """Aggressive normalisation for name matching."""
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    s = s.lower()
    s = s.replace("&", " and ")
    s = re.sub(r"\bf\s*d\s*(?:and|&)?\s*c\b", "fdc", s)
    s = re.sub(r"\bfd and c\b", "fdc", s)
    s = re.sub(r"[\*†‡]+", " ", s)           # footnote markers
    s = re.sub(r"\([^)]*\)", " ", s)                  # parentheticals
    s = _ROMAN_END.sub(r"\2", s)                       # "no. 40" -> "40"
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\b(the|of)\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # simple British/American spelling folds
    for a, b in (("colour", "color"), ("sulph", "sulf"), ("aluminium", "aluminum"),
                 ("caesium", "cesium"), ("oestr", "estr")):
        s = s.replace(a, b)
    return s


def name_variants(name: str) -> set[str]:
    """Normalised variants: full name, and each part split on ',', ';' or '/'."""
    out = set()
    if not name:
        return out
    n = norm_name(name)
    if n:
        out.add(n)
    for part in re.split(r"[;/]| or |, ", name):
        v = norm_name(part)
        if len(v) >= 4:
            out.add(v)
    return out


def title_case_chem(name: str) -> str:
    """FDA names are ALL CAPS; make them readable without mangling formulas."""
    if not name or not name.isupper():
        return name
    small = {"and", "or", "of", "with", "from", "for", "in", "on", "the", "to"}
    words = re.split(r"(\s+|-|,|\(|\))", name.lower())
    out = []
    first = True
    for w in words:
        if not w or re.fullmatch(r"\s+|-|,|\(|\)", w):
            out.append(w)
            continue
        if re.fullmatch(r"[ivx]+", w) and len(w) <= 4 and not first:
            out.append(w.upper())
        elif w in {"fd", "fdc", "fd&c", "d&c", "ext", "bht", "bha", "tbhq", "edta", "dl", "pvp", "pvpp", "mct", "ph"}:
            out.append(w.upper())
        elif w in small and not first:
            out.append(w)
        elif re.fullmatch(r"[a-z]\d*", w) and not first:
            out.append(w)
        else:
            out.append(w[:1].upper() + w[1:])
        first = False
    s = "".join(out)
    s = re.sub(r"\bFD&c\b|\bFd&c\b", "FD&C", s)
    s = re.sub(r"\bNo\.\s*", "No. ", s)
    return s
