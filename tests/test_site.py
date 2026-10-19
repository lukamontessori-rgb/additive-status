"""Build the site from a test fixture and run the structural checker."""
from pathlib import Path

from web import check
from web.build import Builder, summary_sentence

FIX = Path(__file__).parent / "fixtures" / "published_fixture.json"


def test_build_and_check(tmp_path):
    out = tmp_path / "dist"
    Builder(FIX, out).build()
    assert (out / "index.html").exists()
    assert (out / "additive" / "e901x" / "index.html").exists()
    assert (out / "compare" / "allowed-in-gb-not-eu" / "index.html").exists()
    for page in ("places", "place/eu", "place/anz", "map", "eu-food", "eu-food/01-1", "eu-food/14", "glossary"):
        assert (out / page / "index.html").exists(), page
    html = (out / "additive" / "e902x" / "index.html").read_text(encoding="utf-8")
    assert "Warning label in the EU" in html and "/eu-food/01-1/" in html
    assert "504 nm" in (out / "additive" / "e901x" / "index.html").read_text(encoding="utf-8")
    assert check.main(str(out)) == 0


def test_food_category_inverts_uses(tmp_path):
    b = Builder(FIX, tmp_path / "dist")
    b.build()
    cats = {c["code"]: c for c in b.cats}
    assert [a["id"] for a, _ in cats["01.1"]["direct"]] == ["e902x"]
    assert cats["14"]["groups"][0]["g"] == "II" and cats["01"]["n_tree"] == 1


def test_summary_sentence_is_factual():
    a = {"name": "X", "e": "E 1", "jur": {"eu": {"status": "authorised"}, "gb": {"status": "authorised"},
                                          "us": {"status": "not_listed"}, "ca": {"status": "not_authorised"}}}
    s = summary_sentence(a)
    assert s == ("X (E 1): authorised in the EU and the UK (GB); not authorised in Canada; "
                 "not on the list in the US (that list does not cover every permitted substance).")


def test_not_listed_is_never_counted_as_not_allowed(tmp_path):
    out = tmp_path / "dist"
    b = Builder(FIX, out)
    sets = b.compare_sets()
    # e902x is EU-authorised and US not_listed: must NOT appear in "allowed in EU, not in US"
    for s in sets:
        if (s["a"], s["b"]) == ("eu", "us"):
            assert all(a["id"] != "e902x" for a in s["items"])
