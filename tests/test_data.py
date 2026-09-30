"""Structural checks on the published dataset (run in CI after the data build).

These only test invariants that must hold whatever the law says, so a
legitimate change in an official list never blocks publication.
"""
import json
from pathlib import Path

import pytest

from pipeline.model import JUR_ORDER, STATUSES

DATA = Path(__file__).resolve().parent.parent / "data" / "published" / "additives.json"

pytestmark = pytest.mark.skipif(not DATA.exists(), reason="no published data yet")


@pytest.fixture(scope="module")
def data():
    return json.loads(DATA.read_text(encoding="utf-8"))


def test_ids_unique_and_complete(data):
    ids = [a["id"] for a in data["additives"]]
    assert len(ids) == len(set(ids))
    assert len(ids) >= 300
    for a in data["additives"]:
        assert a["name"].strip() == a["name"] and a["name"]
        assert set(a["jur"]) == set(JUR_ORDER), a["id"]
        for j in JUR_ORDER:
            rec = a["jur"][j]
            assert rec["status"] in STATUSES, (a["id"], j, rec["status"])
            for ref in rec.get("refs", []):
                assert ref["url"].startswith("https://"), (a["id"], ref)


def test_every_e_number_is_on_eu_or_gb_list(data):
    for a in data["additives"]:
        if a["e"]:
            eu, gb = a["jur"]["eu"]["status"], a["jur"]["gb"]["status"]
            assert eu != "unknown" and gb != "unknown", a["id"]


def test_sources_documented(data):
    for sid, s in data["sources"].items():
        assert s["licence"] and s["attribution"] and s["url"].startswith("https://"), sid


def test_positive_list_jurisdictions_never_use_not_listed(data):
    # EU and GB lists are complete positive lists: "not on the list" means not authorised
    for a in data["additives"]:
        assert a["jur"]["eu"]["status"] != "not_listed"
        assert a["jur"]["gb"]["status"] != "not_listed"
