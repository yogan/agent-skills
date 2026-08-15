"""Moving an edge label to the part of its route where it costs least.

ELK parks a label at the middle of the route it computed, and d2 then punches a hole in the
line underneath it — every connection carries `mask="url(#…)"`, and the mask holds one black
rectangle per label. On a route that turns, the middle is usually the *turn*, so the label
lands on the short horizontal leg between two vertical ones and the mask deletes the whole
leg. Measured on the architecture figure this module was written for: a 127x17 label sitting
across a 32px horizontal jog, with 65px and 43px of vertical line either side of it that
would have cost 17px of masking instead of all 32.

Neither engine will do this for us. d2 exposes exactly five ELK options and the label ones are
not among them (see `d2.ELK_OPTS`), so the choice is a fork of d2 or a post-pass. This is the
post-pass, and the geometry it needs is all in the SVG: the route is a polyline, the label's
exact box is the mask rectangle d2 already measured, and everything a label must not cover is
a rect, a polygon or another path.

**A label stays centred on its line. The only thing that moves is WHERE along the route.**

That is the whole design, and it is narrower than the one built first, which also allowed a
label to be offset sideways so the line survived intact underneath it. Offsetting measures
better — it hides nothing at all — and reads worse, for two reasons that only show up in a
finished drawing. A label beside a line no longer says which line it belongs to, which is the
entire problem where two arrows run parallel a hundred px apart; and two offset labels on
neighbouring routes line up flush left and read as one paragraph that wrapped. A 17px break in
a vertical line costs nothing by comparison — the eye closes it, and the label is unambiguously
ON the arrow it names.

So a candidate is: centred across some straight leg, at some point ALONG it. Sliding along the
leg is what buys the clearance that offsetting used to: the reference `GraphQL` label ends 5px
inside the Kubernetes container's top border, and 10px up its own 180px leg it does not.

What it will not do is trade one defect for a worse one, so a candidate that leaves the canvas
or lands on a box scores below staying put. `reposition` is a no-op unless some candidate
strictly beats where ELK put the label, which means a figure with nothing to gain comes out
byte-identical — and most labels have nothing to gain, because ELK's own choice is already
centred on a leg with room around it.

Then `_align_rows` pulls labels that landed near the same height onto exactly that height,
spending drift and nothing else. It runs after every label is placed rather than as each one is
chosen, and that ordering is the whole of why it terminates — see there.

A `sequence` is excluded by its caller. Its labels are placed by d2's own sequence engine, not
by ELK, and its rows are re-stacked afterwards by `compact.py` — two reasons for this module
not to be the third thing with an opinion about that geometry.
"""
import re
from collections import namedtuple

from . import palette

# How far off-axis a leg may be and still count as straight. ELK emits near-vertical legs that
# drift a fraction of a pixel over their length (`M 344.49 439 L 344.02 546`), and treating
# those as diagonal would exclude exactly the legs this module exists to use.
AXIS_EPS = 1.5

# Line a leg must keep showing beyond the label, in px total. A label centred on a leg barely
# its own length leaves no arrow either side of it, and then the break IS the leg.
CENTRE_SLACK = 8

# Where along a leg a label may sit, as fractions of the room it has to slide in. Uniform and
# 17 of them: fine enough that "just clear of that border" is always within ~6px on a long leg,
# coarse enough that the whole search stays a few thousand rectangle intersections. The ends are
# included — a label pushed hard against one end of its leg is exactly the answer when the other
# end is where the obstacle is.
SLIDE = tuple(i / 16 for i in range(17))

# How far a label will travel to line up with one already placed, in px. Three arrows leaving a
# row of boxes get three labels at three heights, each individually optimal and collectively
# looking like nothing was decided; at one height they read as a row. Bounded because the
# alignment is worth a few px of drift and not a diagram's worth: past this the label is no
# longer near the middle of its own arrow, which is the thing alignment was decorating.
ALIGN_REACH = 40

# A stroke's width for the purpose of "how much line does this label cover" — d2 draws
# connections at 2 and the mask is what actually hides them.
STROKE = 2

# A container's border, as a strip this thick. The interior is not an obstacle: a label
# crossing the empty part of a group is ordinary and ELK routes through it all the time. The
# border is, because a label lying along it reads as a gap in the box.
BORDER = 3

# Rounding, in px² of area and px of distance, before two candidates are compared. Without it
# a quarter-pixel of shape overlap outranks a whole leg of hidden line, since the terms are
# compared in order rather than priced against each other.
ROUND = (1, 4, 8, 4)

_ROOT = re.compile(r"<svg\b[^>]*>")
# The mask's own box, NOT the root's viewBox, is the canvas every coordinate in this module is
# expressed in. They are not the same rectangle and the difference is not a constant: d2 emits
# `viewBox="0 0 432 591"` for a drawing whose geometry runs from x=-101 to x=331, so a label
# scored against the viewBox has 101px of room on the right that does not exist. The reference
# state machine's `401 invalid` was moved into it and came out 27px past the edge.
_MASK = re.compile(r'(<mask\b[^>]*\bx="([-\d.]+)" y="([-\d.]+)" width="([\d.]+)" '
                   r'height="([\d.]+)"[^>]*>)(.*?)(</mask>)', re.S)
_MASK_RECT = re.compile(r'<rect x="([-\d.]+)" y="([-\d.]+)" width="([\d.]+)" '
                        r'height="([\d.]+)" fill="black">\s*</rect>')
# A labelled connection: d2 emits the path and its label as adjacent siblings, with nothing
# between them. Requiring that adjacency is what keeps an UNlabelled connection from being
# paired with the next connection's label.
_LABELLED = re.compile(
    r'(?P<path><path d="(?P<d>[^"]*)"[^>]*class="connection"[^>]*/>)'
    r'(?P<text><text x="(?P<tx>[-\d.]+)" y="(?P<ty>[-\d.]+)"[^>]*?class="text-italic".*?</text>)',
    re.S)
_RECT = re.compile(r'<rect x="([-\d.]+)" y="([-\d.]+)" width="([\d.]+)" height="([\d.]+)"'
                   r'[^>]*?/>')
_POLY = re.compile(r'<polygon points="([^"]*)"[^>]*/>')
_PATH = re.compile(r'<path d="([^"]*)"[^>]*/>')
_NUM = re.compile(r"-?\d+(?:\.\d+)?")
# A group container's border, in EITHER form. This pass runs before `palette.to_vars`, so what
# it sees is the literal — and matching only the `var()` spelling meant no container was ever
# recognised, so every one of them counted as a solid box. The visible effect was not a label
# on a border, it was labels driven out of the container entirely: on the reference
# architecture `verify JWT`, `read · write` and `upsert` all ended up 90px below the middle of
# their own arrows, hard against the bottom of the drawing, because the only clear space was
# outside the Kubernetes cluster. Both spellings, sourced from the palette so they cannot drift.
_GRP = re.compile("|".join(re.escape(s) for s in
                           ["var(--d-grp-br)"]
                           + [lit for lit, (var, *_) in palette.ROLES.items()
                              if var == "--d-grp-br"]))
_CMD = re.compile(r"([MmLlSsCcQqZz])([^A-Za-z]*)")


class _Box(tuple):
    """(x0, y0, x1, y1), with the two operations the search needs."""

    @property
    def w(self):
        return self[2] - self[0]

    @property
    def h(self):
        return self[3] - self[1]

    def overlap(self, other):
        dx = min(self[2], other[2]) - max(self[0], other[0])
        dy = min(self[3], other[3]) - max(self[1], other[1])
        return dx * dy if dx > 0 and dy > 0 else 0.0

    def outside(self, canvas):
        return self.w * self.h - self.overlap(canvas)


def _pairs(text):
    nums = [float(n) for n in _NUM.findall(text)]
    return list(zip(nums[::2], nums[1::2]))


def points(d):
    """The polyline a d2 connection path visits, absolute, in order.

    The control points of a curve are dropped and only its endpoint is kept, which turns each
    of ELK's rounded corners into one short diagonal between the two straight legs it joins.
    That is the right reading here: a corner is not a leg and must never host a label, and
    being off-axis is exactly what excludes it below.
    """
    out, current = [], (0.0, 0.0)
    for cmd, body in _CMD.findall(d):
        pts = _pairs(body)
        upper = cmd.upper()
        if upper == "Z" or not pts:
            continue
        if cmd.islower():
            absolute, cursor = [], current
            for px, py in pts:
                cursor = (cursor[0] + px, cursor[1] + py)
                absolute.append(cursor)
            pts = absolute
        if upper in ("M", "L"):
            out += pts
        else:
            out.append(pts[-1])       # curve: endpoint only
        current = out[-1]
    return out


def _legs(pts):
    """(kind, x, y, length) per straight leg, `kind` being 'v' or 'h'."""
    legs = []
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if abs(x1 - x0) <= AXIS_EPS and abs(y1 - y0) > AXIS_EPS:
            legs.append(("v", (x0 + x1) / 2, (y0 + y1) / 2, abs(y1 - y0)))
        elif abs(y1 - y0) <= AXIS_EPS and abs(x1 - x0) > AXIS_EPS:
            legs.append(("h", (x0 + x1) / 2, (y0 + y1) / 2, abs(x1 - x0)))
    return legs


def _midpoint(pts):
    """The point half way along the path by arc length — where a reader looks for the label."""
    spans = [((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5 for a, b in zip(pts, pts[1:])]
    half = sum(spans) / 2
    for (x0, y0), (x1, y1), span in zip(pts, pts[1:], spans):
        if span >= half:
            t = half / span if span else 0
            return x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
        half -= span
    return pts[-1] if pts else (0.0, 0.0)


def _candidates(box, legs):
    """Every `(box, axis, lo, hi)` this label could take, the one ELK chose included first.

    All of them centred across a leg; they differ only in where along it they sit. `axis` is
    the coordinate that sliding moves — `y` on a vertical leg, `x` on a horizontal one — and
    `lo`/`hi` are how far that coordinate may travel and still leave the label on the leg.
    Those bounds are what the row alignment needs afterwards: it has to know whether a label
    can reach a height before it asks it to.

    A leg with no room to hold the label plus `CENTRE_SLACK` offers nothing, which is what
    keeps a label off the 3px stub ELK leaves between two corners.
    """
    w, h = box.w, box.h
    # ELK's own choice, carrying the leg it already sits on where it sits on one. Reporting it
    # as "on no leg" is tempting and wrong: a label that is already ideally placed then never
    # moves, so it has no axis, so the row alignment cannot see it — and `compile`, the label
    # every other one in its row wanted to line up WITH, sat 8px above them all.
    out = [(box, *_on_leg(box, legs))]
    for kind, cx, cy, length in legs:
        along = h if kind == "v" else w
        span = length - along - CENTRE_SLACK
        if span < 0:
            continue
        here = cy if kind == "v" else cx
        lo, hi = here - span / 2, here + span / 2
        for step in SLIDE:
            coord = lo + span * step
            x, y = (cx, coord) if kind == "v" else (coord, cy)
            out.append((_Box((x - w / 2, y - h / 2, x + w / 2, y + h / 2)),
                        "y" if kind == "v" else "x", lo, hi))
    return out


def _on_leg(box, legs):
    """`(axis, lo, hi)` for the leg `box` is already centred on, or `(None, 0, 0)`."""
    for kind, cx, cy, length in legs:
        across, along = ((cx, cy) if kind == "v" else (cy, cx))
        centre = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
        mine, slide = (centre if kind == "v" else (centre[1], centre[0]))
        span = length - (box.h if kind == "v" else box.w) - CENTRE_SLACK
        if span >= 0 and abs(mine - across) <= 1 and abs(slide - along) <= span / 2:
            return ("y" if kind == "v" else "x", along - span / 2, along + span / 2)
    return (None, 0.0, 0.0)


def _path_boxes(d):
    """A connection's straight legs as thin boxes, so a label can be charged for crossing one.

    The label's OWN path is charged too, and that is the whole of the "hidden line" term. It
    was excluded at first, on the reasoning that covering the line a label names is what the
    mask exists for — which is true of the leg the label sits on and false of every other leg
    of the same route. A four-leg route offset beside its first vertical then lay across its
    own horizontal, and the drawing came out with a line struck through the words.
    """
    boxes = []
    for kind, cx, cy, length in _legs(points(d)):
        if kind == "v":
            boxes.append(_Box((cx - STROKE / 2, cy - length / 2,
                               cx + STROKE / 2, cy + length / 2)))
        else:
            boxes.append(_Box((cx - length / 2, cy - STROKE / 2,
                               cx + length / 2, cy + STROKE / 2)))
    return boxes


def _shapes(svg, body_start):
    """Everything a label must not sit on: node boxes, table rows, callouts, group borders.

    `body_start` is where the drawing begins, so the `<mask>` block's own rectangles — which
    are label boxes, handled separately — and the root's transparent backdrop are not read as
    geometry. A group container contributes its four border strips rather than its area: see
    `BORDER`.
    """
    boxes = []
    for match in _RECT.finditer(svg, body_start):
        x, y, w, h = (float(g) for g in match.group(1, 2, 3, 4))
        if 'fill="transparent"' in match.group(0):
            continue
        if _GRP.search(match.group(0)):
            boxes += [_Box((x, y, x + w, y + BORDER)),
                      _Box((x, y + h - BORDER, x + w, y + h)),
                      _Box((x, y, x + BORDER, y + h)),
                      _Box((x + w - BORDER, y, x + w, y + h))]
        else:
            boxes.append(_Box((x, y, x + w, y + h)))
    for match in _POLY.finditer(svg, body_start):
        pts = _pairs(match.group(1))
        if pts and "connection" not in match.group(0):
            boxes.append(_Box((min(p[0] for p in pts), min(p[1] for p in pts),
                               max(p[0] for p in pts), max(p[1] for p in pts))))
    for match in _PATH.finditer(svg, body_start):
        if "connection" in match.group(0) or "Z" not in match.group(1):
            continue
        pts = points(match.group(1))
        if pts:
            boxes.append(_Box((min(p[0] for p in pts), min(p[1] for p in pts),
                               max(p[0] for p in pts), max(p[1] for p in pts))))
    return boxes


def _key(box, canvas, shapes, lines, others, target):
    """How bad a candidate is, compared term by term rather than priced against each other.

    The order is the argument. Off the canvas is not a placement at all. On a box is the defect
    this module was written for — a cardinality with its left half inside the table it points
    at — and no amount of tidiness elsewhere buys it back. Hidden line is the thing being
    minimised once the label is somewhere legal. Drift is the tie-break, and it ties often:
    keep the label near the middle of its own route, because that is where a reader looks for
    it.
    """
    shape_area = sum(box.overlap(s) for s in shapes) + sum(box.overlap(o) for o in others)
    line_area = sum(box.overlap(line) for line in lines)
    drift = (((box[0] + box[2]) / 2 - target[0]) ** 2
             + ((box[1] + box[3]) / 2 - target[1]) ** 2) ** 0.5
    terms = (box.outside(canvas), shape_area, line_area, drift)
    return tuple(round(v / step) for v, step in zip(terms, ROUND))


def reposition(svg):
    """Return `svg` with each edge label moved to its least damaging spot on its own route.

    The label is moved by wrapping its `<text>` in a `<g transform="translate(…)">` rather
    than by rewriting coordinates: a wrapped label is a `<text>` full of `<tspan x=…>`, and a
    translate carries all of them without this module having to know how many there are.

    The mask rectangle moves by the same delta — that rectangle IS the hole in the connection,
    so leaving it behind would delete a piece of line the label has vacated.
    """
    root = _ROOT.search(svg)
    mask = _MASK.search(svg)
    if not root or not mask:
        return svg
    cx, cy, cw, ch = (float(g) for g in mask.group(2, 3, 4, 5))
    canvas = _Box((cx, cy, cx + cw, cy + ch))

    rects = list(_MASK_RECT.finditer(mask.group(6)))
    labels = [m for m in _LABELLED.finditer(svg) if m.start() < mask.start()]
    if not rects or not labels:
        return svg

    shapes = _shapes(svg, root.end())
    all_paths = [m.group("d") for m in labels] + _unlabelled_paths(svg, labels)
    boxes = [_Box((float(r.group(1)), float(r.group(2)),
                   float(r.group(1)) + float(r.group(3)),
                   float(r.group(2)) + float(r.group(4)))) for r in rects]

    lines = [b for d in all_paths for b in _path_boxes(d)]
    placed, used, drawn = [], set(), []
    for label in labels:
        index = _match(label, boxes, used)
        if index is None:
            continue
        used.add(index)
        box = boxes[index]
        pts = points(label.group("d"))
        others = drawn + [b for i, b in enumerate(boxes) if i not in used]
        target = _midpoint(pts)

        def score(candidate, _o=others, _t=target):
            return _key(candidate, canvas, shapes, lines, _o, _t)

        best, best_key, axis, lo, hi = box, None, None, 0.0, 0.0
        for candidate, candidate_axis, low, high in _candidates(box, _legs(pts)):
            key = score(candidate)
            if best_key is None or key < best_key:
                best, best_key, axis, lo, hi = candidate, key, candidate_axis, low, high
        placed.append(_Placed(label, index, box, best, best_key, axis, lo, hi, score))
        drawn.append(best)

    # The mask always travels with the label. It is the hole the label punches in its own
    # connection, and a centred label is always standing on that connection — so there is no
    # case where the hole should be dropped or should stay behind.
    return _apply(svg, mask, rects,
                  [(p.label, p.index, p.box[0] - p.was[0], p.box[1] - p.was[1])
                   for p in _align_rows(placed)])


class _Placed(namedtuple("_Placed", "label index was box key axis lo hi score")):
    """One label after the search, and everything the row alignment needs to move it again."""

    @property
    def coord(self):
        return ((self.box[1] + self.box[3]) / 2 if self.axis == "y"
                else (self.box[0] + self.box[2]) / 2)

    def at(self, coord):
        """The same box with its slide coordinate set to `coord`."""
        shift = coord - self.coord
        dx, dy = (0, shift) if self.axis == "y" else (shift, 0)
        return _Box((self.box[0] + dx, self.box[1] + dy,
                     self.box[2] + dx, self.box[3] + dy))


def _align_rows(placed):
    """Pull labels that ended up near the same height onto exactly that height.

    Three arrows leaving a row of boxes get three labels at three individually optimal heights,
    which collectively looks like nothing was decided; at one height they read as a row.

    Done AFTER every label has been placed, and in one shot, because the obvious version does
    not work. Letting each label snap to one already settled aligns in document order only, so
    the first label can never join a row that forms behind it; feeding the result back for a
    second sweep fixes that and starts an oscillation, because with its own height excluded a
    pair simply swaps heights forever. Clustering finished positions converges by construction:
    nothing here reads a position it has also written.

    Every move is refused unless it scores IDENTICALLY on everything above drift — on the
    canvas, off every box, hiding no more line — so alignment can only ever spend drift. Two
    labels that would touch at a shared height are the case this has to survive, and it does it
    by admitting them one at a time, nearest first: the second one is refused and simply keeps
    the position it already had. A partial row beats a collision, and beats abandoning the row.
    """
    out = list(placed)
    for axis in ("x", "y"):
        members = sorted((p for p in out if p.axis == axis), key=lambda p: p.coord)
        for row in _rows(members):
            target = _target(row)
            if target is None:
                continue
            taken = []
            for member in sorted(row, key=lambda p: abs(p.coord - target)):
                moved = member.at(target)
                if not (member.lo <= target <= member.hi):
                    continue
                if member.score(moved)[:3] != member.key[:3]:
                    continue
                if any(moved.overlap(other) > 0 for other in taken):
                    continue
                taken.append(moved)
                out[out.index(member)] = member._replace(box=moved)
    return out


def _rows(members):
    """Runs of labels, sorted by coordinate, each within `ALIGN_REACH` of the run's first."""
    rows, run = [], []
    for member in members:
        if run and member.coord - run[0].coord > ALIGN_REACH:
            rows.append(run)
            run = []
        run.append(member)
    rows.append(run)
    return [row for row in rows if len(row) > 1]


def _target(row):
    """The height a row should share: inside every member's reach, else the middle of them.

    Clamped into the overlap of what the members can reach when there is one, so a row is not
    proposed a height half of it has to decline. Where there is no overlap the midpoint is
    offered anyway and whoever can reach it lines up — better a row of two out of three than a
    row of none.
    """
    lo = max(p.lo for p in row)
    hi = min(p.hi for p in row)
    middle = (row[0].coord + row[-1].coord) / 2
    return min(max(middle, lo), hi) if lo <= hi else middle


def _unlabelled_paths(svg, labels):
    """Connection paths with no label of their own — obstacles, but never hosts."""
    taken = {m.group("d") for m in labels}
    return [m.group(1) for m in re.finditer(r'<path d="([^"]*)"[^>]*class="connection"', svg)
            if m.group(1) not in taken]


def _match(label, boxes, taken):
    """The mask rectangle that IS this label's box: same centre line, containing its baseline."""
    tx, ty = float(label.group("tx")), float(label.group("ty"))
    for index, box in enumerate(boxes):
        if index in taken:
            continue
        if abs((box[0] + box[2]) / 2 - tx) <= 1.5 and box[1] - 2 <= ty <= box[3] + 2:
            return index
    return None


def _apply(svg, mask, rects, decided):
    """Rewrite the label elements and their mask rectangles in one pass, back to front.

    Back to front because every edit changes the offsets of everything after it, and the mask
    block sits at the very end of the SVG — so the label edits, which come earlier, would all
    be off by the mask's own delta if this ran forwards.
    """
    edits = []
    for label, index, dx, dy in decided:
        if not dx and not dy:
            continue
        rect = rects[index]
        x, y, w, h = (float(g) for g in rect.group(1, 2, 3, 4))
        edits.append((mask.start(6) + rect.start(), mask.start(6) + rect.end(),
                      f'<rect x="{x + dx:f}" y="{y + dy:f}" '
                      f'width="{w:g}" height="{h:g}" fill="black"></rect>'))
        edits.append((label.start("text"), label.end("text"),
                      f'<g transform="translate({dx:f},{dy:f})">'
                      f'{label.group("text")}</g>'))
    for start, end, text in sorted(edits, key=lambda e: e[0], reverse=True):
        svg = svg[:start] + text + svg[end:]
    return svg
