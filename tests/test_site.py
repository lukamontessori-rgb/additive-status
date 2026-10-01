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
    assert check.main(str(out)) == 0


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
