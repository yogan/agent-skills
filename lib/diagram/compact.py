"""Close the dead vertical space in a `sequence` diagram's SVG.

d2's sequence layout spends a fixed 86px of height per message and offers no knob for it:
`--layout elk`, `--dagre-nodesep` and the font size all leave the row pitch untouched
(measured: 86px labelled, 70px bare, 84px at an 11px font), because sequence diagrams have
their own layout engine inside d2, and the binary exposes no config key for it. A
seven-message diagram is ~600px of pitch carrying ~120px of label, and the arrows end up far
enough apart that the reader loses the thread between them.

So the rows are re-stacked here, after d2 has laid the diagram out. That is only safe because
the geometry it needs is *explicit* in d2's output rather than inferred:

- each message is one top-level `<g>` holding its own path and label, so a row can be moved
  without touching anything else;
- each label's mask rect sits one font-size above its baseline, which ties it to its row
  exactly (d2 masks the connection line under a label so the two do not overlap);
- a self-message's path carries its whole loop, so its extent falls out of the same
  arithmetic as a straight arrow's — which is why rows are re-stacked by measured extent
  rather than re-pitched by index, and why the loop needs no special case.

Nothing here guesses. Every shape whose position changes is matched by construction, and a
structure that does not match raises `CompactError` rather than shifting the wrong element:
a silently mangled diagram is worse than an uncompacted one, and the caller can fall back to
d2's own spacing.
"""

import re
from collections import namedtuple

from . import palette

# One message row: the bounds of its group's contents, that content, and the (top, bottom)
# of its ink. `start`/`end` exclude the `<g>` tags, so `body` can be rewritten in place.
Row = namedtuple("Row", "start end extent body")

# Vertical budget, in px. GAP is the space left between one row's ink and the next's, so a
# 17px label sits in a 67px pitch. What sets it is not the legibility of any one row but
# whether consecutive arrows still read as consecutive steps; below ~40 they read as a block.
GAP = 50
HEAD = GAP                # actor box bottom -> first row's ink: the same beat as between rows
TAIL = 22                 # last row's ink -> the end of the lifelines
MASK_TOLERANCE = 3        # slack when matching a label's mask rect to its baseline

# A lifeline's dash length. d2 sizes its dashes for the lane it laid out — 12px, with the gap
# computed so a whole number of them spans the lane — and on a lane compacted to well under
# half that length the same dashes read as a row of strokes rather than as a dashed line. The
# gap is recomputed the same way d2 does it, so the pattern still ends flush at the foot.
DASH = 8

# The second line of a participant's label — the real module names behind an abstract lane.
# 11px is the floor the size gate enforces (`gates/size.MIN_READABLE`), so this is as small as
# a legible diagram gets; the recession the eye reads comes mostly from the muted colour, not
# from the two points of size. DETAIL_DY is the baseline gap, down from d2's 15px for two
# equal lines, because the second line is now shorter than the first.
DETAIL_FONT = 11
DETAIL_DY = 13


class CompactError(Exception):
    """The SVG did not have the structure this module needs to move rows safely."""


def _floats(text):
    return [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", text)]


def _top_level_groups(svg, start):
    """(open_tag_end, close_tag_start) for each `<g>` that is a direct child of `start`.

    The bounds are the group's *contents* — the open and close tags are excluded, so a
    caller can rewrite what is between them without having to reconstruct either.
    """
    out = []
    depth = 0
    opened = None
    for m in re.finditer(r"<g\b[^>]*>|</g\s*>", svg[start:]):
        if m.group(0).startswith("</"):
            depth -= 1
            if depth == 0:
                out.append((opened, start + m.start()))
        else:
            if depth == 0:
                opened = start + m.end()
            depth += 1
    if depth:
        raise CompactError("unbalanced <g> elements")
    return out


def _path_extent(body):
    """(min_y, max_y) over every coordinate in the group's path data.

    Path data is `M x y L x y S x y x y ...` — pairs throughout, so the odd-indexed numbers
    are the y values. Only the paths count: a straight arrow's label straddles its line by
    about 9px either side, which `GAP` absorbs several times over, and a self-message's label
    sits inside its loop. Adding it would make the stacking exact and no different to look at.
    """
    ys = []
    for d in re.findall(r'\sd="([^"]*)"', body):
        nums = _floats(d)
        if len(nums) % 2:
            raise CompactError(f"odd coordinate count in path data: {d!r}")
        ys += nums[1::2]
    if not ys:
        raise CompactError("a message group has no path coordinates")
    return min(ys), max(ys)


def style_detail_lines(svg):
    """Shrink and mute the second line of any two-line participant label.

    d2 emits `"Title\\nDetail"` as a single `<text>` holding one `<tspan>` per line and offers
    no way to style them differently, so the distinction has to be made here. Every tspan after
    the first gets `DETAIL_FONT` and the muted foreground — the same colour the edge labels use,
    so it is already covered by the contrast gate and by the palette's var substitution.

    Untouched when there is no two-line label, which is the normal case.
    """
    def restyle(match):
        head, spans = match.group(1), match.group(2)
        tspans = re.findall(r"<tspan\b[^>]*>.*?</tspan>", spans, re.S)
        if len(tspans) < 2:
            return match.group(0)
        out = [tspans[0]]
        for tspan in tspans[1:]:
            fixed = re.sub(r'\sdy="[\d.-]+"', f' dy="{DETAIL_DY}.000000"', tspan)
            fixed = fixed.replace("<tspan", f'<tspan fill="{palette.MUTED}" '
                                            f'style="font-size:{DETAIL_FONT}px"', 1)
            out.append(fixed)
        return f"{head}{''.join(out)}</text>"

    return re.sub(r"(<text\b[^>]*>)((?:\s*<tspan\b[^>]*>.*?</tspan>\s*)+)</text>",
                  restyle, svg, flags=re.S)


def compact_sequence(svg):
    """Re-stack the message rows of a d2 sequence SVG and shrink the canvas to match.

    Returns the SVG unchanged when there is nothing to gain. Raises `CompactError` if the
    markup is not the shape described in the module docstring.
    """
    inner = svg.find("<svg", svg.find("<svg") + 1)
    if inner < 0:
        raise CompactError("no inner <svg> element")
    body_start = svg.find(">", inner) + 1

    actors, lifelines, rows = [], [], []
    for start, end in _top_level_groups(svg, body_start):
        body = svg[start:end]
        if 'class="shape"' in body:
            actors.append(body)
        elif "<foreignObject" in body:
            # A callout. It is anchored to a participant's box rather than to the canvas
            # (see place.py), so re-stacking the rows below it leaves it correctly placed,
            # and moving it here would drag it away from the shape it points at.
            continue
        elif _is_lifeline(body):
            lifelines.append((start, end))
        else:
            rows.append(Row(start, end, _path_extent(body), body))
    if not rows:
        return svg
    if not actors or not lifelines:
        raise CompactError("sequence SVG with messages but no actors or lifelines")

    bottoms = [y + h for y, h in _rects(actors)]
    if not bottoms:
        raise CompactError("no actor box rects")
    actor_bottom = max(bottoms)

    rows.sort(key=lambda row: row.extent[0])
    shifts = []                                  # (row, dy)
    cursor = actor_bottom + HEAD
    for row in rows:
        top, bottom = row.extent
        shifts.append((row, cursor - top))
        cursor += (bottom - top) + GAP
    last_ink = cursor - GAP

    lifeline_end = _lifeline_end(svg, lifelines)
    new_end = last_ink + TAIL
    shrink = lifeline_end - new_end
    if shrink <= 0:
        return svg

    # Right to left, so an edit never moves a span that has not been applied yet.
    edits = []
    for row, dy in shifts:
        if abs(dy) >= 0.5:
            edits.append((row.start, row.end, _shift(row.body, dy)))
    edits += _mask_edits(svg, shifts)
    edits += _lifeline_edits(svg, lifelines, new_end)
    out = svg
    for start, end, text in sorted(edits, key=lambda e: -e[0]):
        out = out[:start] + text + out[end:]
    return _shrink_canvas(out, shrink)


ROW_TAGS = ("path", "text")

# d2 inlines the arrowhead marker into the first message group that uses one. Its contents
# are in the marker's own coordinate system, not the canvas's, so they must be left exactly
# as they are — shifting them would move the arrowhead off the end of every arrow.
MARKER = re.compile(r"<marker\b.*?</marker\s*>", re.S)


def _shift(body, dy):
    """Move one message row down the canvas by `dy`, by rewriting its coordinates.

    A `transform` on the group would be shorter and is wrong: d2 masks the line under each
    label with a `maskUnits="userSpaceOnUse"` mask, and that user space is the *transformed*
    one — so a translated row is measured against a mask window that moved with it, and
    every arrow far enough from its original y silently vanishes. Editing the coordinates
    keeps each row in the one coordinate system the shared mask is expressed in.
    """
    parts = MARKER.split(body)
    markers = MARKER.findall(body)
    shifted = [_shift_coords(part, dy) for part in parts]
    out = [shifted[0]]
    for marker, part in zip(markers, shifted[1:]):
        out += [marker, part]
    return "".join(out)


def _shift_coords(body, dy):
    tags = set(re.findall(r"<(\w+)\b", body)) - {"g"}
    if not tags <= set(ROW_TAGS):
        raise CompactError(f"unexpected element(s) in a message row: {sorted(tags)} — "
                           f"only {list(ROW_TAGS)} have coordinates this knows how to move")

    def path(m):
        seen = [-1]

        def number(n):
            seen[0] += 1
            value = float(n.group(0))
            return f"{value + dy:.6f}" if seen[0] % 2 else n.group(0)

        # Every command d2 emits here (M, L, S) takes coordinate *pairs*, so the y values
        # are simply the odd-numbered ones; the command letters are left untouched.
        return f' d="{re.sub(r"-?[\d.]+", number, m.group(1))}"'

    body = re.sub(r'\sd="([^"]*)"', path, body)
    return re.sub(r'(<text\b[^>]*\sy=")([\d.]+)(")',
                  lambda m: f"{m.group(1)}{float(m.group(2)) + dy:.6f}{m.group(3)}", body)


def _is_lifeline(body):
    """A lifeline is a single vertical path and nothing else."""
    paths = re.findall(r'\sd="([^"]*)"', body)
    if len(paths) != 1 or "<text" in body:
        return False
    nums = _floats(paths[0])
    return len(nums) == 4 and abs(nums[0] - nums[2]) < 0.5


def _rects(bodies):
    for body in bodies:
        for m in re.finditer(r'<rect\b[^>]*>', body):
            y = re.search(r'\sy="([\d.-]+)"', m.group(0))
            h = re.search(r'\sheight="([\d.-]+)"', m.group(0))
            if y and h:
                yield float(y.group(1)), float(h.group(1))


def _mask_edits(svg, shifts):
    """Move each label's mask rect with the row it belongs to.

    A rect is identified by sitting one font-size above its label's baseline — d2's own
    arithmetic, and the reason the offset is read off each `<text>` rather than fixed here:
    it is 13px under a 13px edge label and 16 under d2's default. Deriving it also keeps the
    match specific, where taking the *nearest* rect would move one row's mask twice and
    another's not at all as soon as two rows sat close together.
    """
    mask = svg.find("<mask")
    if mask < 0:
        return []
    wanted = {}
    for row, dy in shifts:
        for m in re.finditer(r'<text\b[^>]*\sy="([\d.]+)"[^>]*>', row.body):
            size = re.search(r"font-size:([\d.]+)px", m.group(0))
            if not size:
                raise CompactError(f"a label carries no font-size: {m.group(0)[:80]!r}")
            wanted[round(float(m.group(1)) - float(size.group(1)))] = dy
    if not wanted:
        return []
    edits, matched = [], set()
    for m in re.finditer(r'<rect\b[^>]*>', svg[mask:]):
        found = re.search(r'\sy="([\d.]+)"', m.group(0))
        if not found:
            continue
        y = float(found.group(1))
        for want, dy in wanted.items():
            if abs(y - want) <= MASK_TOLERANCE:
                start = mask + m.start() + found.start(1)
                edits.append((start, mask + m.start() + found.end(1), f"{y + dy:.0f}"))
                matched.add(want)
                break
    missing = set(wanted) - matched
    if missing:
        raise CompactError(f"no mask rect for label(s) at y={sorted(missing)} — moving the "
                           "rows would leave the label backgrounds behind")
    return edits


def _lifeline_end(svg, lifelines):
    ends = set()
    for start, end in lifelines:
        for d in re.findall(r'\sd="([^"]*)"', svg[start:end]):
            ends.add(round(_floats(d)[3]))
    if len(ends) != 1:
        raise CompactError(f"lifelines end at differing depths: {sorted(ends)}")
    return ends.pop()


def _lifeline_edits(svg, lifelines, new_end):
    """Shorten each lifeline to `new_end` and re-dash it for the length it now has."""
    edits = []
    for start, end in lifelines:
        top = None
        for m in re.finditer(r'\sd="([^"]*)"', svg[start:end]):
            nums = _floats(m.group(1))
            top = nums[1]
            d = f"M {nums[0]:.6f} {top:.6f} L {nums[2]:.6f} {new_end:.6f}"
            edits.append((start + m.start(1), start + m.end(1), d))
        for m in re.finditer(r"stroke-dasharray:[\d.,]+", svg[start:end]):
            edits.append((start + m.start(), start + m.end(), _dasharray(new_end - top)))
    return edits


def _dasharray(length):
    """`DASH`-long dashes, with the gap sized so a whole number of them spans `length`."""
    period = length / max(1, round(length / (DASH * 2)))
    return f"stroke-dasharray:{DASH:.6f},{max(period - DASH, 2):.6f}"


def _shrink_canvas(svg, shrink):
    """Take `shrink` px off every element that states the canvas height.

    The height is written in five places — both `<svg>` tags, the painted background rect,
    the mask's own box and the mask's full-canvas rect — and a drawing that shrinks only
    some of them either keeps the old whitespace or clips its own bottom row.

    The three below the root are found by *being canvas-sized*, not by position: an actor
    box is a `<rect>` too, and counting rects from the top of the file would resize the
    first participant instead of the background.
    """
    head_end = svg.find(">", svg.find("<svg", svg.find("<svg") + 1)) + 1
    head, rest = svg[:head_end], svg[head_end:]
    box = re.search(r'viewBox="[-\d.]+ [-\d.]+ [\d.]+ ([\d.]+)"', head)
    if not box:
        raise CompactError("no viewBox on the root <svg>")
    canvas = float(box.group(1))

    def shrunk(m):
        return f' height="{float(m.group(1)) - shrink:g}"'

    head = re.sub(r'viewBox="([-\d.]+) ([-\d.]+) ([\d.]+) ([\d.]+)"',
                  lambda m: (f'viewBox="{m.group(1)} {m.group(2)} {m.group(3)} '
                             f'{float(m.group(4)) - shrink:g}"'), head)
    head = re.sub(r'\sheight="([\d.]+)"', shrunk, head)

    found = 0

    def canvas_sized(m):
        nonlocal found
        height = re.search(r'\sheight="([\d.]+)"', m.group(0))
        if not height or abs(float(height.group(1)) - canvas) > 0.5:
            return m.group(0)
        found += 1
        return re.sub(r'\sheight="([\d.]+)"', shrunk, m.group(0))

    rest = re.sub(r'<(?:rect|mask)\b[^>]*>', canvas_sized, rest)
    if found < 3:
        raise CompactError(f"found {found} canvas-sized elements below the root, expected the "
                           "background rect, the mask and the mask's own rect")
    return head + rest
