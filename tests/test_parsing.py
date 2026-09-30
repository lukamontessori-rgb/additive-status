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
    assert parse_us.classify([(189, 145)], set(), False)[0] == "prohibited"
    assert parse_us.classify([(74, 340)], set(), False)[0] == "authorised"
    # Red No. 3 style: food listing plus a terminated provisional listing for lakes
    assert parse_us.classify([(74, 1303), (74, 303), (81, 10)], set(), False)[0] == "authorised"
    assert parse_us.classify([(81, 10), (81, 30)], {"DELISTED"}, False)[0] == "delisted"
    assert parse_us.classify([(74, 1306)], set(), False)[0] == "not_authorised"   # drugs only
    assert parse_us.classify([(177, 1520)], set(), False)[0] == "not_listed"
    assert parse_us.classify([], {"NLFG"}, True)[0] == "delisted"
    assert parse_us.classify([], set(), True)[0] == "authorised"
    assert parse_us.classify([], set(), False)[0] == "listed_noreg"


def test_us_html_grid():
    body = b"""<table><tr><td>CAS Reg No (or other ID)</td><td>Substance</td><td>Other Names</td>
      <td>Used for (Technical Effect)</td><td>Reg col01</td><td>Reg col02</td><td>regs Labeling &amp; Standards </td></tr>
      <tr><td> 25956-17-6</td><td> FD&amp;C RED NO. 40</td><td> &amp;diams; FD&amp;C RED NO. 40<br />&amp;diams; C.I. 16035<br />&amp;diams; ALLURA RED AC</td>
      <td> COLOR OR COLORING ADJUNCT,<br /> FLAVOR ENHANCER</td><td>=T("74.1340")</td><td>=T("74.340")</td><td> 136.110</td></tr>
      <tr><td> 5897-16-5</td><td> CALCIUM CYCLAMATE--PROHIBITED</td><td></td><td></td><td></td><td></td><td></td></tr></table>"""
    records, header = parse_us.parse_grid(parse_us.read_grid(body))
    red = records[0]
    assert red["name"] == "FD&C RED NO. 40"
    assert red["cfr"] == ["74.1340", "74.340"]          # standards of identity ignored
    assert red["colour_index"] == ["16035"]
    assert "ALLURA RED AC" in red["other_names"]
    assert red["effects"] == ["Color or coloring adjunct", "Flavor enhancer"]
    assert red["status"] == "authorised"
    cyc = records[1]
    assert cyc["name"] == "CALCIUM CYCLAMATE" and cyc["status"] == "prohibited"


def test_eu_period_and_ranges():
    from datetime import date
    from pipeline import parse_eu
    assert not parse_eu.period_applies("Period of application: until 31 July 2014", date(2026, 1, 1))
    assert parse_eu.period_applies("Period of application: from 1 August 2014", date(2026, 1, 1))
    assert not parse_eu.period_applies("Period of application: from 1 August 2030", date(2026, 1, 1))
    assert parse_eu.range_key("E 334–337 and E 354") == "e334-337,e354"
    assert parse_eu.expand_range("E 150a,b,d", []) == {"e150a", "e150b", "e150d"}
    groups = {"R:e338-341,e343,e450-452": ["e338", "e339", "e452"]}
    assert parse_eu.group_by_span("E 338-452", groups) == "R:e338-341,e343,e450-452"
    listed = {"e310": {"name": "Propyl gallate"}, "e315": {"name": "Erythorbic acid"},
              "e320": {"name": "Butylated hydroxyanisole (BHA)"}}
    assert parse_eu.filter_by_name({"e310", "e315", "e320"}, "Propyl gallate, TBHQ, BHA and BHT", listed) == {"e310", "e320"}


def test_fr_effective_date():
    from datetime import date
    from pipeline import parse_fr
    doc = {"dates": "This order is effective January 15, 2027, and January 18, 2028. Submit objections by February 18, 2025."}
    assert parse_fr.effective_date(doc) == date(2027, 1, 15)
    rev = [{"norm_title": " revocation color additive listing use orange b casing ", "publication_date": "2026-07-23"}]
    assert parse_fr.match_revocation({"key": "orange b", "other_names": []}, rev)
    assert parse_fr.match_revocation({"key": "orange", "other_names": []}, rev) is None


def test_eu_history_timeline_and_renumbering():
    from pipeline import eu_history
    hist = {"versions": {
        "20130601": {"celex": "c1", "codes": {"e171": "A", "e960": "A", "e100": "A"}},
        "20220222": {"celex": "c2", "codes": {"e171": "N", "e960": "A", "e100": "A"}},
        "20230601": {"celex": "c3", "codes": {"e171": "N", "e960a": "A", "e960b": "A", "e100": "A", "e999": "A"}},
    }}
    ev = eu_history.timeline(hist)
    assert ev["e171"] == [{"version": "20220222", "previous_version": "20130601", "celex": "c2", "from": "A", "to": "N"}]
    assert "e960" not in ev and "e960a" not in ev          # split into e960a/e960b is not a change
    assert ev["e999"][0]["from"] == "X" and ev["e999"][0]["to"] == "A"
    assert "e100" not in ev


def test_anz_codes_and_processing_aids():
    from pipeline import parse_anz
    assert parse_anz.norm_code("160a(i)") == "e160a-i"
    assert parse_anz.norm_code("160b (ii)") == "e160b-ii"
    pages = [
        "S8—1 Name\nnumerical listing\n129 Allura red AC\n171 Titanium dioxide\n586 4-hexylresorcinol\n960 Steviol glycosides\n",
        "S9—1 Name\n",
        "S15—1 Name\n5 Confectionery\n586 4-hexylresorcinol GMP\n960 Steviol glycosides 300\n1 500 In the final food\n",
        "S16—1 Name\nS16—2 Additives permitted at GMP\nS16—3 Colourings permitted at GMP\n171 Titanium dioxide\n"
        "S16—4 Colourings permitted to a maximum level\nAllura red AC 129\n",
        "S17—1 Name\n",
        "S18—1 Name\nGenerally permitted processing aids\nargon\nHydrogen peroxide 5\nPotassium bromate Germination control in malting\n",
        "S19—1 Name\n",
    ]
    p = parse_anz.parse_texts(pages)
    assert p["colours_max"] == ["e129"] and p["colours_gmp"] == ["e171"]
    assert "e586" in p["schedule15"] and "e960" in p["schedule15"]
    assert parse_anz.status_of(p, "e129")[0] == "authorised"
    assert parse_anz.status_of(p, "e102")[0] == "not_authorised"
    assert parse_anz.processing_aid(p, ["Argon"]) == "argon"
    assert parse_anz.processing_aid(p, ["Hydrogen"]) is None          # not "Hydrogen peroxide"
    assert parse_anz.processing_aid(p, ["POTASSIUM BROMATE"]).startswith("Potassium bromate")
