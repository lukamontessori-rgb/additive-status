"""Turn HTML tables into plain grids, expanding rowspan/colspan."""
from __future__ import annotations

import re

from lxml import html as lhtml

WS = re.compile(r"\s+")


def cell_text(el) -> str:
    # Keep line breaks between block children so lists inside cells stay readable.
    for br in el.xpath(".//br"):
        br.tail = "\n" + (br.tail or "")
    for blk in el.xpath(".//p|.//li|.//div"):
        blk.tail = "\n" + (blk.tail or "")
    text = el.text_content()
    lines = [WS.sub(" ", ln).strip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def table_to_grid(table) -> list[list[str]]:
    """Return rows of cell texts; spanned cells are repeated."""
    grid: list[list[str | None]] = []
    pending: dict[tuple[int, int], str] = {}
    for r_idx, tr in enumerate(table.xpath("./thead/tr|./tbody/tr|./tr|./tfoot/tr")):
        row: list[str] = []
        c_idx = 0
        cells = tr.xpath("./th|./td")
        ci = 0
        while ci < len(cells) or (r_idx, c_idx) in pending:
            if (r_idx, c_idx) in pending:
                row.append(pending.pop((r_idx, c_idx)))
                c_idx += 1
                continue
            cell = cells[ci]
            ci += 1
            txt = cell_text(cell)
            try:
                rs = max(1, int(cell.get("rowspan", "1") or 1))
                cs = max(1, int(cell.get("colspan", "1") or 1))
            except ValueError:
                rs, cs = 1, 1
            for k in range(cs):
                row.append(txt)
                for dr in range(1, rs):
                    pending[(r_idx + dr, c_idx)] = txt
                c_idx += 1
        grid.append(row)
    return grid


def parse(html_bytes: bytes):
    return lhtml.fromstring(html_bytes)
