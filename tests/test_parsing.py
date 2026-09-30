"""Unit tests for normalisation and parsing logic (synthetic inputs)."""
from lxml import html

from pipeline import parse_ca, parse_us
from pipeline.htmltable import table_to_grid
from pipeline.names import (cas_valid, e_display, e_id, e_parts, find_cas, name_variants,
                            norm_name, title_case_chem)


def test_e_parts():
    assert e_parts("E 160a(ii)") == (160, "a", "ii")
    assert e_parts("E100 Curcumin") == (100, "", "")
    assert e_parts("E 150c") == (150, "c", "")
    assert e_parts("E-1422") == (1422, "", "")
    assert e_parts("Curcumin") is None
    assert e_id(160, "a", "ii") == "e160a-ii"
    assert e_display(960, "b", "i") == "E 960b(i)"


def test_cas():
    assert cas_valid("25956-17-6")      # Allura Red AC
    assert cas_valid("7732-18-5")       # water
    assert not cas_valid("25956-17-5")
    assert find_cas("CAS 13463-67-7; other 1234-56-0") == ["13463-67-7"]


def test_norm_name():
    assert norm_name("FD&C Red No. 40") == norm_name("FD and C red no 40") == "fdc red 40"
    assert norm_name("Sulphur dioxide") == norm_name("sulfur dioxide")
    assert norm_name("Allura Red*") == "allura red"
    assert "sunset yellow fcf" in name_variants("Sunset Yellow FCF/Orange Yellow S")
    assert "orange yellow s" in name_variants("Sunset Yellow FCF/Orange Yellow S")


def test_title_case():
    assert title_case_chem("FD&C RED NO. 40") == "FD&C Red No. 40"
    assert title_case_chem("POTASSIUM BROMATE") == "Potassium Bromate"
    assert title_case_chem("Mixed Case") == "Mixed Case"


def test_rowspan_grid():
    t = html.fromstring("""<table>
      <tr><th>Item</th><th>Additive</th><th>Food</th></tr>
      <tr><td rowspan="2">A.1</td><td rowspan="2">Alpha</td><td>Bread</td></tr>
      <tr><td>Jam</td></tr>
      <tr><td>A.2</td><td>Beta</td><td colspan="1">Milk</td></tr></table>""")
    g = table_to_grid(t)
    assert g[1] == ["A.1", "Alpha", "Bread"]
    assert g[2] == ["A.1", "Alpha", "Jam"]
    assert g[3] == ["A.2", "Beta", "Milk"]


def test_canada_page():
    body = b"""<html><body><table>
      <thead><tr><th>Item No.</th><th>Column 1<br>Additive</th><th>Column 2<br>Permitted in or Upon</th>
      <th>Column 3<br>Purpose of Use</th><th>Column 4<br>Maximum Level of Use</th></tr></thead>
      <tbody><tr><td>S.1</td><td>Sodium benzoate*</td><td>Jam</td><td>Preservative</td><td>1,000 p.p.m.</td></tr>
      <tr><td rowspan="2">S.2</td><td rowspan="2">Sorbic acid</td><td>Bread</td><td>Preservative</td><td>GMP</td></tr>
      <tr><td>Cheese</td><td>Preservative</td><td>3,000 p.p.m.</td></tr></tbody></table></body></html>"""
    rows = parse_ca.parse_page("11", body, "https://example.org/11")
    names = [r["name"] for r in rows]
    assert names == ["Sodium benzoate", "Sorbic acid", "Sorbic acid"]
    assert rows[0]["item"] == "S.1" and rows[0]["purpose"] == "Preservative"


def test_us_classify():
    assert parse_us.classify([(189, 145)], set())[0] == "prohibited"
    assert parse_us.classify([(74, 340)], set()) == ("authorised", "Certified colour additive (21 CFR 74)")
    assert parse_us.classify([(81, 10)], set())[0] == "delisted"
    assert parse_us.classify([(177, 1520)], set())[0] == "not_listed"
    assert parse_us.classify([], {"NLFG"})[0] == "delisted"


def test_us_html_grid():
    body = b"""<table><tr><td>CAS Reg No (or other ID)</td><td>Substance</td><td>Used for (Technical Effect)</td><td>21 CFR</td></tr>
      <tr><td>25956-17-6</td><td>FD&amp;C RED NO. 40</td><td>COLOR OR COLORING ADJUNCT</td><td>74.340</td></tr></table>"""
    grid = parse_us.read_grid(body)
    h = parse_us.find_header(grid)
    assert grid[h + 1][1] == "FD&C RED NO. 40"
