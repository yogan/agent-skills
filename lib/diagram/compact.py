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

import base64
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

# The second line of a box's label — the real module names behind an abstract lane, the
# columns behind a collapsed area. 11px, and the recession the eye reads comes mostly from
# the muted colour rather than from the two points of size. DETAIL_DY is the baseline gap,
# down from d2's 15px for two equal lines, because the second line is now shorter.
#
# It is deliberately AT the primary legibility floor rather than under it, and `DETAIL_CLASS`
# is what lets the size gate hold it to its own, lower floor (`gates/size.MIN_READABLE_DETAIL`).
# Without that tag the two were measured together, which made a subtitle the most expensive
# text in the diagram: at 11px it could not survive any downscale at all, so a figure carrying
# one had to fit the content column exactly while the same figure without one had 141px of
# slack. A subtitle is supplementary by construction — it may ride a little smaller.
DETAIL_FONT = 11
DETAIL_DY = 13
DETAIL_CLASS = "d2-detail"

# The group legend under a sequence's lifelines: how much canvas it needs, how heavy its rule
# is, and how loud. Lane colour says which side of the wire a lane is on and nothing says what
# the colours mean — there is no legend anywhere else in this renderer, and a reader of the
# first real figure guessed "probably FE/BE". The rule spans exactly the lanes in its group,
# which is what ties the name to them; it is a 2px rounded rule at a third opacity because at
# full strength it read as part of the drawing rather than as an annotation of it.
#
# The rule and the name take DIFFERENT colours, which looks like a fussy distinction and is
# not: the rule takes the lane's border colour, because matching the boxes it spans is the
# whole point of it, while the name is text and takes the text-grade colour. Painting a name
# in a border colour is what a two-group figure never caught — the third group is green
# (#3f9142) and lands at 3.76:1 on white, under the 4.5:1 the contrast gate demands. Muting
# the rule further therefore costs nothing: the name is what has to stay readable.
LEGEND_BAND = 22
LEGEND_RULE = 2
LEGEND_OPACITY = 0.22
LEGEND_FONT = 12
LEGEND_GAP = 5            # lifeline feet -> rule
LEGEND_BASELINE = 19      # lifeline feet -> the name's baseline

# The dot a state machine begins at — UML's start marker, which is the one thing a reader of
# the reference state diagram could not find: being the top node is implicit, and any state
# with no incoming transition looks the same.
#
# It is drawn into the finished SVG rather than declared as a node in the d2 source, because as
# a node it costs a whole rank: 796px -> 910px on the reference machine, 114px of canvas for a
# 14px dot. Drawn here it goes in the margin the drawing already has, and which side has the
# margin depends on the layout:
#
#   * laid out DOWNWARD the first state is one box in a rank of its own, with empty margin
#     either side of it, and the canvas usually does not grow at all.
#   * laid out to the RIGHT the first state is flush with the left edge and every px of width
#     is scaled away in the content column, so the marker goes ABOVE it instead — height is
#     what a landscape figure has to spare.
#
# The stem is long because a short one reads as a bullet stuck to the box rather than as an
# arrow arriving from outside the machine.
START_R = 7               # dot radius; 14px across reads at the size of a glyph
START_ARROW = 60          # dot edge -> the box's side, laid out downward
START_ARROW_ABOVE = 40    # the same, laid out to the right, where the stem is vertical
START_HEAD = 6            # arrowhead length
START_HALF = 4            # arrowhead half-width

# d2's own `--pad`, which render.compile_source passes: the margin between the drawing and
# every canvas edge, and therefore the offset between the viewBox and the drawing. Duplicated
# rather than imported to keep this module free of the render pipeline; the tests pin both, so
# a change to one fails loudly.
D2_PAD = 8


class CompactError(Exception):
    """The SVG did not have the structure this module needs to move rows safely."""


# Every caller parses SVG path data (`d="M 10 20 L 30 40"`) and nothing else, which is what
# makes the exponent part safe to accept: no path command letter is `e` or `E`, so an `e`
# between digits can only be exponent notation. Without it `1e-5` parsed as TWO numbers, 1 and
# -5 — and the damage would have been silent, because `_path_extent` guards on the coordinate
# count being even and splitting one number into two keeps it even.
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")


def _floats(text):
    return [float(n) for n in _NUMBER.findall(text)]


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
    """Shrink and mute the second line of any two-line *box* label.

    d2 emits `"Title\\nDetail"` as one `<text>` of two `<tspan>`s and offers no way to style
    them differently, so the distinction is made here.

    An **edge** label is excluded, and that is load-bearing: a two-line edge label is one
    phrase that wrapped, not a subtitle. Shrinking its continuation would misread it and cost
    the diagram its headroom — `DETAIL_FONT` is the legibility floor, so a diagram containing
    one cannot be scaled down at all, and wrapping a long edge label is the cheapest width
    there is (1159px to 842px on a four-column architecture).
    """
    def restyle(match):
        head, spans = match.group(1), match.group(2)
        if "text-italic" in head:      # d2's class for an edge label
            return match.group(0)
        tspans = re.findall(r"<tspan\b[^>]*>.*?</tspan>", spans, re.S)
        if len(tspans) < 2:
            return match.group(0)
        out = [tspans[0]]
        for tspan in tspans[1:]:
            fixed = re.sub(r'\sdy="[\d.-]+"', f' dy="{DETAIL_DY}.000000"', tspan)
            fixed = fixed.replace("<tspan", f'<tspan class="{DETAIL_CLASS}" '
                                            f'fill="{palette.MUTED}" '
                                            f'style="font-size:{DETAIL_FONT}px"', 1)
            out.append(fixed)
        return f"{head}{''.join(out)}</text>"

    return re.sub(r"(<text\b[^>]*>)((?:\s*<tspan\b[^>]*>.*?</tspan>\s*)+)</text>",
                  restyle, svg, flags=re.S)


def _shape_rects(svg, body_start):
    """(x, y, width, height) per drawn box, in document order.

    Every top-level group holding a `class="shape"` — sequence lanes for the legend, states
    for the start marker, and for either one the boxes an annotation must not land on.
    """
    out = []
    for start, end in _top_level_groups(svg, body_start):
        rect = _rect_in(svg[start:end])
        if rect:
            out.append(rect)
    return out


def _rect_in(body):
    """One group's box as (x, y, width, height), or None if it draws no shape."""
    if 'class="shape"' not in body:
        return None
    rect = re.search(r"<rect\b[^>]*>", body)
    if not rect:
        return None
    got = [re.search(rf'\s{attr}="([\d.-]+)"', rect.group(0))
           for attr in ("x", "y", "width", "height")]
    if not all(got):
        raise CompactError("a box has no x/y/width/height to measure")
    return tuple(float(m.group(1)) for m in got)


def _runs(lanes):
    """Consecutive lanes sharing a group -> [(group, rule colour, name colour, first, last)].

    Consecutive rather than "all lanes with this group": a split group is drawn as two spans,
    which is honest — the rule marks where those lanes actually are. spec.py already advises
    against splitting one.
    """
    out = []
    for i, (group, rule, name) in enumerate(lanes):
        if not group:
            continue
        if out and out[-1][0] == group and out[-1][4] == i - 1:
            out[-1] = (group, rule, name, out[-1][3], i)
        else:
            out.append((group, rule, name, i, i))
    return out


def add_group_legend(svg, lanes, pad=D2_PAD):
    """Name each lane group under the diagram, in its own colour.

    `lanes` is (group name or None, rule colour, name colour) per participant, in order. Two
    colours because the rule matches the boxes it spans and the name has to read as text.

    Returns the SVG unchanged when there is nothing to explain: fewer than two groups, or no
    group covering more than one lane. A group of one lane is not grouping — its rule is an
    underline under a single box.

    The band goes INSIDE the inner `<svg>`, whose viewBox is the coordinate system the drawing
    is laid out in; the outer one is offset from it, so a legend placed there lands a padding
    out of position. Both grow, or the drawing is cropped.
    """
    runs = _runs(lanes)
    if len(runs) < 2 or not any(last > first for _g, _r, _n, first, last in runs):
        return svg

    outer_tag, inner_tag = _svg_tags(svg)
    _vx, vy, _vw, vh = _viewbox(inner_tag)
    rects = _shape_rects(svg, _body_start(svg))
    if len(rects) < len(lanes):
        raise CompactError(f"{len(lanes)} lanes but {len(rects)} actor boxes")

    foot = vy + vh - pad
    marks = []
    for group, rule, name, first, last in runs:
        x0 = rects[first][0]
        x1 = rects[last][0] + rects[last][2]
        marks.append(
            f'<rect x="{x0:.1f}" y="{foot + LEGEND_GAP:.1f}" width="{x1 - x0:.1f}" '
            f'height="{LEGEND_RULE}" rx="{LEGEND_RULE / 2:g}" fill="{rule}" '
            f'fill-opacity="{LEGEND_OPACITY}"/>'
            f'<text x="{(x0 + x1) / 2:.1f}" y="{foot + LEGEND_BASELINE:.1f}" fill="{name}" '
            f'style="text-anchor:middle;font-size:{LEGEND_FONT}px">{_escape(group)}</text>')

    return _append(_grown(svg, bottom=LEGEND_BAND), marks)


def add_start_marker(svg, state_id, colour, vertical=False, pad=D2_PAD):
    """Draw UML's start dot next to `state_id`, with an arrow into it.

    `vertical` puts the dot ABOVE the box instead of beside it, falling back to below — for a
    landscape layout, where width is the scarce axis. Beside comes first otherwise, falling
    back to the box's right. See the START_* constants for why either beats declaring the dot
    as a node in the d2 source.

    The only thing that can be in the way is another box: the start state is a source, so the
    rank it shares holds other sources and nothing else. Both sides blocked is a diagram this
    cannot annotate, and raises.

    The canvas grows only by however much of the marker falls outside it, plus d2's own pad so
    the dot keeps the same margin as the drawing. Laid out downward that is usually nothing.
    """
    _outer_tag, inner_tag = _svg_tags(svg)
    vx, vy, vw, vh = _viewbox(inner_tag)
    body_start = _body_start(svg)
    rects = _shape_rects(svg, body_start)
    box = _rect_for(svg, body_start, state_id)

    x, y, w, h = box
    stem = START_ARROW_ABOVE if vertical else START_ARROW
    span = stem + 2 * START_R
    for side in (-1, 1):
        # `edge` is the box side the arrow lands on, `far` the dot's outer edge; the two
        # bracket the strip the marker needs. `across` is the strip's other axis, which is
        # just the dot's width centred on the box.
        edge = (y if side < 0 else y + h) if vertical else (x if side < 0 else x + w)
        far = edge + side * span
        near, off = min(edge, far), max(edge, far)
        mid = (x + w / 2) if vertical else (y + h / 2)
        lo, hi = mid - START_R, mid + START_R
        blocked = any(
            r is not box
            and (r[1] < off and r[1] + r[3] > near and r[0] < hi and r[0] + r[2] > lo
                 if vertical else
                 r[0] < off and r[0] + r[2] > near and r[1] < hi and r[1] + r[3] > lo)
            for r in rects)
        if blocked:
            continue
        centre = edge + side * (stem + START_R)   # the dot's centre, on the marker's axis
        tip = edge - side * 1                     # a hair inside the box's stroke
        head = tip + side * START_HEAD
        stem_end = centre - side * START_R        # the dot's edge, where the stem starts
        if vertical:
            marks = [
                f'<circle cx="{mid:.1f}" cy="{centre:.1f}" r="{START_R}" fill="{colour}"/>',
                f'<path d="M{mid:.1f} {stem_end:.1f} V{head:.1f}" stroke="{colour}" '
                'stroke-width="2" fill="none"/>',
                f'<path d="M{mid:.1f} {tip:.1f} L{mid - START_HALF:.1f} {head:.1f} '
                f'L{mid + START_HALF:.1f} {head:.1f} Z" fill="{colour}"/>',
            ]
            grow = {"top": max(0, vy - (near - pad)),
                    "bottom": max(0, (off + pad) - (vy + vh))}
        else:
            marks = [
                f'<circle cx="{centre:.1f}" cy="{mid:.1f}" r="{START_R}" fill="{colour}"/>',
                f'<path d="M{stem_end:.1f} {mid:.1f} H{head:.1f}" stroke="{colour}" '
                'stroke-width="2" fill="none"/>',
                f'<path d="M{tip:.1f} {mid:.1f} L{head:.1f} {mid - START_HALF:.1f} '
                f'L{head:.1f} {mid + START_HALF:.1f} Z" fill="{colour}"/>',
            ]
            grow = {"left": max(0, vx - (near - pad)),
                    "right": max(0, (off + pad) - (vx + vw))}
        return _append(_grown(svg, **grow), marks)
    raise CompactError(f"no room {'above or below' if vertical else 'beside'} {state_id!r} "
                       "for a start marker — a box sits on both sides of it")


def _rect_for(svg, body_start, node_id):
    """The (x, y, width, height) of one node's box, found by the id d2 tags its group with.

    d2 emits `<g class="<base64 of the node id> <role>">` per node and offers no other handle
    on which box is which — no id attribute, no data-*. That is undocumented and load-bearing
    here, so it fails loudly rather than annotating the wrong box.
    """
    tag = base64.b64encode(str(node_id).encode()).decode()
    at = svg.find(f'<g class="{tag} ', body_start)
    if at < 0:
        at = svg.find(f'<g class="{tag}"', body_start)
    if at < 0:
        raise CompactError(f"no box tagged {node_id!r} in the rendered diagram")
    # `_top_level_groups` reports each group's CONTENTS, so the group opened at `at` is the
    # one whose contents begin just past that tag's `>`.
    opens = svg.index(">", at) + 1
    for start, end in _top_level_groups(svg, body_start):
        if start == opens:
            rect = _rect_in(svg[start:end])
            if rect:
                return rect
            break
    raise CompactError(f"the box tagged {node_id!r} has no shape to measure")


def _escape(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _body_start(svg):
    """Where the inner `<svg>`'s contents begin — the offset every group scan starts from."""
    inner = svg.find("<svg", svg.find("<svg") + 1)
    if inner < 0:
        raise CompactError("no inner <svg> element")
    return svg.index(">", inner) + 1


def _svg_tags(svg):
    """The outer and inner `<svg …>` tags, as text.

    Both are located by search rather than by position: at this point in the pipeline the
    document still opens with d2's XML declaration, and taking the first tag blindly grew that
    instead.
    """
    outer_at = svg.find("<svg")
    inner_at = svg.find("<svg", outer_at + 1) if outer_at >= 0 else -1
    if outer_at < 0 or inner_at < 0:
        raise CompactError("expected an outer and an inner <svg> to annotate")
    return (svg[outer_at:svg.index(">", outer_at) + 1],
            svg[inner_at:svg.index(">", inner_at) + 1])


def _viewbox(tag):
    box = re.search(r'viewBox="([-\d.]+) ([-\d.]+) ([\d.]+) ([\d.]+)"', tag)
    if not box:
        raise CompactError("an <svg> tag has no viewBox to measure from")
    return tuple(float(v) for v in box.groups())


def _append(svg, marks):
    """Put `marks` last inside the inner <svg>, so they paint over the drawing."""
    close = svg.rindex("</svg></svg>")
    return svg[:close] + "".join(marks) + svg[close:]


def _grown(svg, left=0, right=0, top=0, bottom=0):
    """The SVG with more canvas on the given sides — both `<svg>` tags and the backdrop.

    The backdrop is the transparent rect d2 lays under the drawing, which `render.standalone`
    has painted with the page colour: left un-grown, a standalone image gets an unpainted strip
    exactly where the annotation is.

    The outer tag has no height at this point in the pipeline — d2 emits a viewBox alone and
    `render.pin_intrinsic` writes the size on afterwards, from the viewBox this just grew. The
    inner tag does have one, and stale values there would crop the annotation.

    Room added on the LEFT or at the TOP moves the outer viewBox's origin, and the nested
    `<svg>` has to be moved with it or the whole drawing keeps its old position on a canvas
    that has shifted underneath it — which crops the far side by exactly the amount added.
    Growing at the bottom or the right does not move the origin, so this went unseen until a
    start marker first needed room above a landscape state machine: the canvas gained 36px at
    the top and the bottom row of boxes fell 36px outside it.
    """
    if not (left or right or top or bottom):
        return svg
    for tag in _svg_tags(svg):
        out = tag
        for attr, delta in (("width", left + right), ("height", top + bottom)):
            found = re.search(rf'\s{attr}="([\d.]+)"', out)
            if found and delta:
                out = (out[:found.start()] + f' {attr}="{float(found.group(1)) + delta:g}"'
                       + out[found.end():])
        box = re.search(r'viewBox="([-\d.]+) ([-\d.]+) ([\d.]+) ([\d.]+)"', out)
        if not box:
            raise CompactError("an <svg> tag has no viewBox to grow")
        bx, by, bw, bh = (float(v) for v in box.groups())
        out = (out[:box.start()]
               + f'viewBox="{bx - left:g} {by - top:g} {bw + left + right:g} '
                 f'{bh + top + bottom:g}"' + out[box.end():])
        svg = svg.replace(tag, out, 1)

    # Put the nested <svg> back on the canvas's new origin. It carries no x/y of its own, so it
    # defaults to (0, 0) — which stops being the top-left corner the moment the outer viewBox
    # moves. Written from the outer box rather than accumulated, so repeated growth cannot drift.
    outer, inner = _svg_tags(svg)
    bx, by, _bw, _bh = _viewbox(outer)
    placed = inner
    for attr, value in (("x", bx), ("y", by)):
        found = re.search(rf'\s{attr}="[-\d.]+"', placed)
        placed = (placed[:found.start()] + f' {attr}="{value:g}"' + placed[found.end():]
                  if found else placed[:-1] + f' {attr}="{value:g}">')
    svg = svg.replace(inner, placed, 1)

    # The backdrop is d2's first <rect>, immediately after the inner <svg> tag.
    inner_end = svg.index(">", svg.find("<svg", svg.find("<svg") + 1)) + 1
    back = re.compile(r"<rect\b[^>]*>").search(svg, inner_end)
    if back:
        rect = back.group(0)
        got = {a: re.search(rf'\s{a}="([\d.-]+)"', rect)
               for a in ("x", "y", "width", "height")}
        if all(got.values()):
            grown = rect
            for attr, value in (("x", float(got["x"].group(1)) - left),
                                ("y", float(got["y"].group(1)) - top),
                                ("width", float(got["width"].group(1)) + left + right),
                                ("height", float(got["height"].group(1)) + top + bottom)):
                grown = re.sub(rf'\s{attr}="[\d.-]+"', f' {attr}="{value:.6f}"', grown,
                               count=1)
            svg = svg[:back.start()] + grown + svg[back.end():]
    return svg


def compact_sequence(svg):
    """Re-stack the message rows of a d2 sequence SVG and shrink the canvas to match.

    Returns the SVG unchanged when there is nothing to gain. Raises `CompactError` if the
    markup is not the shape described in the module docstring.
    """
    body_start = _body_start(svg)

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
