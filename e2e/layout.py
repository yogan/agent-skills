"""Basic checks on the review-ui sidebar as drawn: what a mod test cannot see, because it reads
the element tree and never the screen. A line one cell too wide passes every test of the tree,
then wraps on screen, the sidebar comes out a row taller than its pane, and its last row (a
box's bottom border, a scroll arrow) is cut off.

    problems(panel) -> [str]     # the sidebar's text (drive.py's `region_of(screen, "panel")`)

Fast, and only what holds on every drawing:

- every rounded box is whole: each top corner `╭` has its bottom corner `╰` below it in the
  same column, and no bottom stands without its top;
- the arrows that scroll an area come in pairs, as many down as up;
- an area's line (`── name ───`) is one row: the row under it is not its own text wrapped onto
  it, which shows as a row of nothing but spaces before a word.
"""
import re
import unicodedata

# The sidebar's scroll arrows (review-ui's GLYPH.up and GLYPH.down: md-pan_up, md-pan_down).
UP, DOWN = "\U000f0bbd", "\U000f0bb7"


def _width(ch):
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def _columns(line, wanted):
    """The columns (in cells) at which a character of `wanted` stands in `line`."""
    out, col = [], 0
    for ch in line:
        if ch in wanted:
            out.append((col, ch))
        col += _width(ch)
    return out


def problems(panel):
    rows = (panel or "").split("\n")
    if not any(r.strip() for r in rows):
        return []
    found = []
    # Boxes: a top corner waits for its bottom in the same column.
    open_at = {}
    for n, row in enumerate(rows, 1):
        for col, ch in _columns(row, "╭╰"):
            if ch == "╭":
                if col in open_at:
                    found.append(f"row {open_at[col]}: a box with no bottom border (another starts in its column on row {n})")
                open_at[col] = n
            elif col in open_at:
                del open_at[col]
            else:
                found.append(f"row {n}: a box's bottom border with no top")
    found += [f"row {n}: a box with no bottom border" for n in sorted(open_at.values())]
    # Arrows: an area that scrolls has both.
    ups = sum(r.count(UP) for r in rows)
    downs = sum(r.count(DOWN) for r in rows)
    if ups != downs:
        found.append(f"{ups} up arrow(s) and {downs} down arrow(s): an area's arrow is cut off")
    # An area's line on one row.
    for n, row in enumerate(rows[:-1], 1):
        if row.lstrip("│ ").startswith("── ") and re.match(r"^[│ ]{6,}[A-Za-z]", rows[n]) \
                and "─" not in rows[n]:
            found.append(f"row {n + 1}: the line on row {n} wrapped onto it: {rows[n].strip()[:40]!r}")
    return found
