# Additive Status

A free website that shows, for each food additive, its official status in the
**European Union**, **Great Britain**, the **United States** (federal), **Canada** and
**Australia/New Zealand** side by side, with links to the official sources.

Live site: https://lukamontessori-rgb.github.io/additive-status/

No ads, no accounts, no cookies, no tracking, no third-party requests.

## What is on the site

* A page for every additive (405 at the time of writing): status in each place, the official
  references, how the match was made, EU conditions of use, Canadian permitted foods, identity
  data from the EU specifications and the EU history since 2013.
* A searchable, filterable table; compare pages for every pair of places; a differences heatmap.
* A **label checker** (paste an ingredients list; runs entirely in the browser).
* A **quiz** ("Where is it allowed?").
* Weekly change tracking, a glossary, and CSV/JSON downloads.

## How it works

```
official sources ──► pipeline/fetch.py ──► data/raw/          (weekly snapshots, gzip, with checksums)
                     pipeline/eu_history.py ──► data/history/   (all EU consolidated versions since 2013)
                     pipeline/build_data.py ──► data/published/  (validated, joined data + changelog)
                     web/build.py ──► dist/ ──► GitHub Pages
```

* `.github/workflows/update.yml` runs every Monday and on every push.
* A failed or suspicious download never replaces the last good data; a source that fails its
  checks is dropped for that run and the previous status is kept (marked as stale).
* Problems are reported automatically as a GitHub issue.
* Access controls are respected: robots.txt is honoured, and sources behind a challenge page are
  not bypassed (the EU texts come from the Publications Office's Cellar service instead).

## Sources

| Source | Publisher | Licence |
|---|---|---|
| Regulation (EC) No 1333/2008, Annex II (consolidated versions) | European Union (Publications Office, Cellar) | Reuse with acknowledgement (Decision 2011/833/EU) |
| Regulation (EU) No 231/2012 (specifications) | European Union | Reuse with acknowledgement |
| Regulated Products Register — food additives | UK Food Standards Agency | Open Government Licence v3.0 |
| Substances Added to Food inventory | U.S. FDA | U.S. Government work |
| Final rules and orders (21 CFR parts 73, 74, 172, 173, 180, 184, 189) | Federal Register (API) | U.S. Government work |
| Lists of Permitted Food Additives (15 lists) | Health Canada | Open Government Licence – Canada |
| Food Standards Code compilation (Standard 1.3.1, Schedules 8, 15, 16, 18) | Food Standards Australia New Zealand | CC BY 4.0 |

The only hand-made data is `data/curated/crosswalk.yml`, a small reviewed table of name matches.
See the site's *About* and *Legal* pages for status definitions, matching rules, limits and licences.

The site's privacy policy, terms of use, cookies-and-storage page and accessibility statement are in
`web/templates/`. The fonts (Unbounded and Instrument Sans) are self-hosted from `web/static/fonts/`
under the SIL Open Font License 1.1; the licence texts are next to the font files.

All animation lives in `web/static/motion.js` (no libraries). It is decoration only: pages read the same without
it, it stops for visitors who ask their system for reduced motion, and the menu has a *Pause animations* button.

## Local use

```
pip install -r requirements.txt
python -m pipeline.fetch          # needs normal internet access
python -m pipeline.eu_history     # optional: EU history (downloads old versions once)
python -m pipeline.build_data
SITE_URL=https://example.org/additive-status python -m web.build
SITE_URL=https://example.org/additive-status python -m web.check dist
python -m pytest
```

Not legal advice. Independent project, not affiliated with any government agency.
