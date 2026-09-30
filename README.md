# Additive Status

A free website that shows, for each food additive, its official status in the
**European Union**, **Great Britain**, the **United States** (federal) and **Canada**
side by side, with links to the official sources.

No ads, no accounts, no tracking cookies.

## How it works

```
official sources ──► pipeline/fetch.py ──► data/raw/        (weekly snapshots, with checksums)
                     pipeline/build_data.py ──► data/published/ (validated, joined data + changelog)
                     web/build.py ──► dist/ ──► GitHub Pages
```

* `.github/workflows/update.yml` runs every Monday, and on every push.
* A failed or suspicious download never replaces the last good data.
* Problems are reported automatically as a GitHub issue labelled `pipeline`.

## Sources

| Source | Publisher | Licence |
|---|---|---|
| Regulation (EC) No 1333/2008, Annex II (EUR-Lex consolidated text) | EU | Reuse with acknowledgement (Decision 2011/833/EU) |
| Regulated Products Register — food additives | UK Food Standards Agency | Open Government Licence v3.0 |
| Substances Added to Food inventory; Color Additive Status List | U.S. FDA | U.S. Government work |
| Lists of Permitted Food Additives | Health Canada | Open Government Licence – Canada |
| E-number items (P628) | Wikidata | CC0 |

See the site's *About* page for status definitions, matching rules and limits.

## Local use

```
pip install -r requirements.txt
python -m pipeline.fetch        # needs normal internet access
python -m pipeline.build_data
python -m web.build && python -m web.check dist
python -m pytest
```

Not legal advice. Independent project, not affiliated with any government agency.
