"""Shared vocabulary: jurisdictions and normalised status categories.

The normalised categories deliberately keep legal meanings apart:
"not on a positive list" is not the same as "prohibited", and
"not in the FDA inventory" says nothing about legality at all.
"""
from __future__ import annotations

JURISDICTIONS = {
    "eu": {
        "short": "EU",
        "name": "European Union",
        "name_in": "the European Union",
        "system": "positive list",
        "system_note": "Only additives on the Union list (Annex II of Regulation (EC) No 1333/2008) may be used, and only in the foods and at the levels listed.",
    },
    "gb": {
        "short": "UK (GB)",
        "name": "Great Britain (England, Scotland, Wales)",
        "name_in": "Great Britain",
        "system": "positive list",
        "system_note": "Great Britain keeps its own version of the EU additives list. Northern Ireland follows EU rules, so it is covered by the EU column.",
    },
    "us": {
        "short": "US",
        "name": "United States (federal)",
        "name_in": "the United States",
        "system": "listed regulations + GRAS",
        "system_note": "Food additives and colour additives need FDA approval, but substances 'generally recognized as safe' (GRAS) can be used without being listed. So 'not in the FDA inventory' does not mean 'not allowed'. State laws are not covered.",
    },
    "ca": {
        "short": "Canada",
        "name": "Canada",
        "name_in": "Canada",
        "system": "positive list",
        "system_note": "Food additives must appear on one of Health Canada's 15 Lists of Permitted Food Additives, for the listed foods and purposes.",
    },
}

JUR_ORDER = ["eu", "gb", "us", "ca"]

# status key -> (label, short label, tone, description)
STATUSES = {
    "authorised": (
        "Authorised", "Allowed", "ok",
        "On the official permitted list. Conditions apply (foods, maximum levels, purity).",
    ),
    "phase_out": (
        "Being phased out", "Phase-out", "warn",
        "Authorisation has been withdrawn or revoked, but a transition period is still running.",
    ),
    "not_authorised": (
        "Not authorised", "Not allowed", "no",
        "Not on the official permitted list, so it may not be used as a food additive.",
    ),
    "prohibited": (
        "Prohibited", "Prohibited", "no",
        "Explicitly prohibited from use in food by regulation.",
    ),
    "delisted": (
        "Delisted / revoked", "Revoked", "no",
        "A previous authorisation was removed from the regulations.",
    ),
    "listed_noreg": (
        "In FDA inventory, no regulation cited", "Listed", "unknown",
        "In FDA's Substances Added to Food inventory, but no regulation, GRAS listing or FEMA flavouring status is cited. FDA says inclusion does not mean approval.",
    ),
    "not_listed": (
        "Not on the list", "Not listed", "unknown",
        "Not found on this jurisdiction's list, but that list does not cover every permitted substance (US: substances generally recognized as safe; Canada: substances treated as food ingredients). Not counted as 'not allowed'.",
    ),
    "unknown": (
        "No data", "No data", "unknown",
        "We could not match this substance to the source for this jurisdiction.",
    ),
}

ALLOWED_LIKE = {"authorised", "phase_out"}
NOT_ALLOWED_LIKE = {"not_authorised", "prohibited", "delisted"}


def status_label(key: str) -> str:
    return STATUSES.get(key, STATUSES["unknown"])[0]
