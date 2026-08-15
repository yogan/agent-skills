"""What an arrow has to look like once ELK has routed it and d2 has painted it.

Three rules, none of them about what is WRITTEN on the arrow — that is `edgelabel`:

  * every straight run of a route is vertical or horizontal;
  * the last run into an arrowhead is long enough for the head to sit on visible line;
  * nothing hides that run — not an edge label's gap, not a gap this repo cut itself.

This module MEASURES and never redraws. Where an arrow goes is decided upstream — the route is
ELK's and the rounding of its corners is d2's — and both attempts to edit that here were worse
drawings. Squaring off the corner an arrowhead is painted over does free the head, and leaves a
hard 90° turn 12px from a box where the rest of the figure is rounded.

So the fixes live where the space is decided. `d2.ELK_EDGE_LADDER` buys an arrowhead its
straight line with page, per figure, as far as the size gate allows; `render._pick_at_spacing`
ranks on the count; and `edgelabel` charges a label for a gap that would strand a head, using
`shortfall` from here so the rule and its remedy cannot drift apart.

Reading a route is a matter of knowing what d2 emits. A connection is one `<path>` of straight
`L` runs joined by short `S` curves, one curve per corner, and an arrowhead is a `marker-start`
or `marker-end` on that same element — painted back over the last few px of the line rather
than beyond its tip, which is why a gap there strands it. `points` keeps only the endpoints of
a curve, so every corner becomes one short off-axis step; telling that step apart from a run
that really is diagonal is the whole job of `CORNER`.
"""
import re
from collections import namedtuple

# How far off-axis a run may be and still count as straight. ELK's long runs drift a fraction
# of a pixel, and calling those diagonal would condemn most of the corpus.
AXIS_EPS = 1.5

# How far a rounded corner reaches on EACH axis, plus slack — measured off the corpus, where
# d2's corners span 7.5 to 17.2px. Anything longer than this off-axis is a run and not a
# corner. It is a floor on the diagonal a check can see: at 20 per axis a diagonal has to be
# 28px before it is reported, which is the price of never reporting a corner as one.
CORNER = 20.0

# A stroke's width, for the purpose of "how much line does this box cover" — d2 draws
# connections at 2 and the mask is what actually hides them.
STROKE = 2

# How far d2's arrowhead reaches back from the tip, and how much plain line has to survive
# beyond it before a reader joins the head to the arrow. The marker is 10px long with its
# reference point 7px in, so 10 is the conservative reading of "this is head, not line".
HEAD_REACH = 10
MIN_RUN = 6

# The shortest straight run an arrowhead may sit on, which is simply its own length. Shorter
# than this and the triangle is painted across the corner the line just came round: it is drawn
# along the curve, so it leans out to one side and reads as a shape stuck on the drawing rather
# than as the end of a line.
#
# It is deliberately not `HEAD_REACH + MIN_RUN`. There is no line to leave showing BEHIND a
# head — behind it is the rest of the arrow — and the extra 6px would ask a whole rung more of
# `d2.ELK_EDGE_LADDER` for a head that already sits flat.
APPROACH = HEAD_REACH

CONNECTION = re.compile(r'<path d="([^"]*)"[^>]*class="connection"[^>]*/>')
# The mask block, with its own box. That box — NOT the root's viewBox — is the canvas every
# coordinate in a d2 SVG is expressed in; they are not the same rectangle and the offset
# between them is not constant.
MASK = re.compile(r'(<mask\b[^>]*\bx="([-\d.]+)" y="([-\d.]+)" width="([\d.]+)" '
                  r'height="([\d.]+)"[^>]*>)(.*?)(</mask>)', re.S)
# Every hole in that mask, whoever cut it: d2 cuts one under each edge label, `edgelabel` cuts
# one over each container title. A hole is a hole to the arrow underneath it.
HOLE = re.compile(r'<rect x="([-\d.]+)" y="([-\d.]+)" width="([\d.]+)" '
                  r'height="([\d.]+)"[^>]*fill="black"[^>]*>')
_CMD = re.compile(r"([MmLlSsCcQqZz])([^A-Za-z]*)")
_NUM = re.compile(r"-?\d+(?:\.\d+)?")

Defect = namedtuple("Defect", "rule at detail")


class Box(tuple):
    """(x0, y0, x1, y1), with the two operations a placement search needs."""

    @property
    def w(self):
        return self[2] - self[0]

    @property
    def h(self):
        return self[3] - self[1]

    def holds(self, point):
        return self[0] <= point[0] <= self[2] and self[1] <= point[1] <= self[3]

    def overlap(self, other):
        dx = min(self[2], other[2]) - max(self[0], other[0])
        dy = min(self[3], other[3]) - max(self[1], other[1])
        return dx * dy if dx > 0 and dy > 0 else 0.0

    def outside(self, canvas):
        return self.w * self.h - self.overlap(canvas)


def pairs(text):
    nums = [float(n) for n in _NUM.findall(text)]
    return list(zip(nums[::2], nums[1::2]))


def points(d):
    """The polyline a d2 connection path visits, absolute, in order.

    The control points of a curve are dropped and only its endpoint is kept, which turns each
    of ELK's rounded corners into one short off-axis step between the two straight runs it
    joins. That is the right reading for everything here: a corner is not a run, must never
    host a label, and is not the diagonal `defects` is looking for.
    """
    out, current = [], (0.0, 0.0)
    for cmd, body in _CMD.findall(d):
        pts = pairs(body)
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


def legs(pts):
    """(kind, x, y, length) per straight leg, `kind` being 'v' or 'h'."""
    out = []
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if abs(x1 - x0) <= AXIS_EPS and abs(y1 - y0) > AXIS_EPS:
            out.append(("v", (x0 + x1) / 2, (y0 + y1) / 2, abs(y1 - y0)))
        elif abs(y1 - y0) <= AXIS_EPS and abs(x1 - x0) > AXIS_EPS:
            out.append(("h", (x0 + x1) / 2, (y0 + y1) / 2, abs(x1 - x0)))
    return out


def leg_boxes(d):
    """A connection's straight legs as thin boxes, so covering one can be priced."""
    out = []
    for kind, cx, cy, length in legs(points(d)):
        if kind == "v":
            out.append(Box((cx - STROKE / 2, cy - length / 2,
                            cx + STROKE / 2, cy + length / 2)))
        else:
            out.append(Box((cx - length / 2, cy - STROKE / 2,
                            cx + length / 2, cy + STROKE / 2)))
    return out


def walk(pts, step=1.0):
    """The polyline sampled at ~`step` px, so an index into it is a distance along the line."""
    out = []
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        span = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
        for i in range(max(1, int(span / step))):
            t = i / max(1, int(span / step))
            out.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
    if pts:
        out.append(pts[-1])
    return out


def connections(svg):
    """(tag, polyline) per connection — the tag because the arrowheads are attributes on it."""
    return [(m.group(0), points(m.group(1))) for m in CONNECTION.finditer(svg)]


def holes(svg):
    """Every hole punched in the connection mask, whoever cut it."""
    mask = MASK.search(svg)
    if not mask:
        return []
    return [Box((float(m.group(1)), float(m.group(2)),
                 float(m.group(1)) + float(m.group(3)),
                 float(m.group(2)) + float(m.group(4))))
            for m in HOLE.finditer(mask.group(6))]


def ends(svg):
    """Per arrow end, the stretch of line no gap may touch — furthest point first, tip last.

    An END rather than a whole route, and precomputed, because that is the only part a gap can
    ruin and because the search in `edgelabel` asks about it thousands of times per figure. A
    box that touches this stretch at index `i` leaves `MIN_RUN - (i + 1)` px of line showing
    past the arrowhead, which is the arithmetic `shortfall` undoes.

    An end with no marker on it still gets `MIN_RUN`: a line that starts inside a gap reads as
    starting late, wherever it was going. It just gets no allowance for a head it does not have
    — a sequence diagram's lifelines are `class="connection"` paths with no marker at all, and
    charging them for arrowheads would forbid every gap near the top of the drawing.
    """
    out = []
    for tag, pts in connections(svg):
        if len(pts) < 2:
            continue
        line = walk(pts)
        head = int(HEAD_REACH + MIN_RUN)
        if "marker-start" in tag:
            out.append(list(reversed(line[:head])))
        else:
            out.append(list(reversed(line[:MIN_RUN])))
        if "marker-end" in tag:
            out.append(line[-head:])
        else:
            out.append(line[-MIN_RUN:])
    return out


def shortfall(box, zones):
    """How many px of visible line `box` takes off the ends of the arrows it crosses.

    Zero for a gap anywhere sane, which is most of them. Summed rather than reported per arrow
    because one rectangle in the mask hides every connection that crosses it — the mask is not
    per-edge — so a gap is either clear of every arrow's end or it is not.
    """
    total = 0
    for zone in zones:
        for i in range(len(zone) - 1, -1, -1):
            if box.holds(zone[i]):
                total += i + 1
                break
    return total


def leaves_a_stub(box, zones):
    """Whether cutting `box` out of the mask leaves every arrow through it visible at its ends."""
    return not shortfall(box, zones)


def defects(svg):
    """Every place the drawing breaks one of the three rules above.

    Counted by `render._pick_at_spacing`, which is the only thing that can act on the first
    two: they are properties of the route ELK computed, so the remedy is a different route.
    """
    found = []
    zones = ends(svg)
    for tag, pts in connections(svg):
        found += _diagonals(pts)
        found += _turns(tag, pts)
    for hole in holes(svg):
        missing = shortfall(hole, zones)
        if missing:
            found.append(Defect(
                "gap", ((hole[0] + hole[2]) / 2, (hole[1] + hole[3]) / 2),
                f"a gap in the line leaves {MIN_RUN - missing:.0f}px showing past an "
                f"arrowhead, {MIN_RUN} needed"))
    return found


def _diagonals(pts):
    out = []
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        dx, dy = abs(x1 - x0), abs(y1 - y0)
        if dx > AXIS_EPS and dy > AXIS_EPS and max(dx, dy) > CORNER:
            out.append(Defect("diagonal", ((x0 + x1) / 2, (y0 + y1) / 2),
                              f"a {dx:.0f}x{dy:.0f}px run is neither vertical nor horizontal"))
    return out


def _turns(tag, pts):
    out = []
    for tip, run in approaches(tag, pts):
        if run < APPROACH:
            out.append(Defect("turn", tip,
                              f"the arrow turns {run:.0f}px before its head, {APPROACH:.0f} "
                              "needed for the head to sit on straight line"))
    return out


def approaches(tag, pts):
    """(tip, length) per arrowhead: where it points, and the straight run leading into it."""
    if len(pts) < 2:
        return []
    out = []
    if "marker-start" in tag:
        out.append((pts[0], _run(pts)))
    if "marker-end" in tag:
        out.append((pts[-1], _run(list(reversed(pts)))))
    return out


def _run(pts):
    """The straight run beginning at `pts[0]`, in px, following the polyline outward.

    Consecutive segments on the same axis are one run: a route may be split at a point that is
    not a turn, and reading that as two runs would report a turn where the line is dead
    straight.
    """
    total, axis = 0.0, None
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        dx, dy = abs(x1 - x0), abs(y1 - y0)
        here = ("v" if dy > AXIS_EPS >= dx else "h" if dx > AXIS_EPS >= dy else None)
        if here is None or (axis is not None and here != axis):
            break
        axis, total = here, total + max(dx, dy)
    return total
