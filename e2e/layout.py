"""Basic checks on the review-ui sidebar as drawn: what a mod test cannot see, because it reads
the element tree and never the screen. A line one cell too wide passes every test of the tree,
then wraps on screen, the sidebar comes out a row taller than its pane, and its last row (a
box's bottom border, the end of a scrollbar) is cut off.

    problems(panel) -> [str]     # the sidebar's text (drive.py's `region_of(screen, "panel")`)

Fast, and only what holds on every drawing:

- every rounded box is whole: each top corner `╭` has its bottom corner `╰` below it in the
  same column, and no bottom stands without its top. A box whose foot an area clips (a
  thread box scrolled part out of the file's area) is no cut: only one still open at the
  sidebar's last row is, since that is where a sidebar taller than its pane loses rows;
- a scrollbar's thumb is whole: in its column, one run of cells, a cell filled from the bottom
  only at its top and one filled from the top only at its end;
- an area's line (`── name ───`) is one row: the row under it is not its own text wrapped onto
  it, which shows as a row of nothing but spaces before a word.
"""
import re
import unicodedata

# A scrollbar's thumb as review-ui draws it (sidebar.ts's BAR_LOWER and BAR_UPPER): its body,
# the cell its top starts in (filled from the bottom), the cell it ends in (filled from the top).
FULL = "█"
LOWER = "▁▂▃▄▅▆▇"
UPPER = "▔\U0001fb82\U0001fb83▀\U0001fb84\U0001fb85\U0001fb86"
# The columns from the right a scrollbar stands in: an area's at the last, a thread box's in its
# padding left of its border and the file's bar. Code further left may hold block elements.
BAR_COLUMNS = 6


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
    last = max((n for n, r in enumerate(rows, 1) if r.strip()), default=0)
    found += [f"row {n}: a box with no bottom border" for col, n in sorted(open_at.items(), key=lambda x: x[1])
              if any(c == col and ch == "│" for c, ch in _columns(rows[last - 1], "│"))]
    # Scrollbars: each run of thumb cells in a column is whole.
    width = max(sum(_width(ch) for ch in r) for r in rows)
    cells = {}
    for n, row in enumerate(rows, 1):
        for col, ch in _columns(row, FULL + LOWER + UPPER):
            if col >= width - BAR_COLUMNS:
                cells.setdefault(col, {})[n] = ch
    for col, at in sorted(cells.items()):
        for n, ch in sorted(at.items()):
            above, below = at.get(n - 1), at.get(n + 1)
            if ch in LOWER and above is not None:
                found.append(f"row {n}: a scrollbar's thumb starts in the middle of its run")
            if ch in UPPER and below is not None:
                found.append(f"row {n}: a scrollbar's thumb ends in the middle of its run")
            if ch == FULL and above is not None and above in UPPER:
                found.append(f"row {n}: a scrollbar's thumb goes on past its end")
    # An area's line on one row.
    for n, row in enumerate(rows[:-1], 1):
        if row.lstrip("│ ").startswith("── ") and re.match(r"^[│ ]{6,}[A-Za-z]", rows[n]) \
                and "─" not in rows[n]:
            found.append(f"row {n + 1}: the line on row {n} wrapped onto it: {rows[n].strip()[:40]!r}")
    return found
