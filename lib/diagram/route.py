"""Moving the tail of a route, where ELK turned too late for the arrowhead to sit on line.

This is the one place in the renderer that changes where a line goes, and it is deliberately
not in [`arrows.py`](arrows.py) — that module measures and does not redraw, and keeping the two
apart is what stops "nudge it" becoming the answer to every route complaint.

**What it does, and only this.** ELK brings an edge across the drawing on one long run, steps
sideways at the very last moment, and arrives with about 3.5px of straight line for a 10px
arrowhead — so the head is painted along the curve and reads as a shape stuck on the line. Both
corpora produce that shape and nothing else: measured across all twelve, the tail is always

    tip <- 3.5px <- corner <- a 5..56px step <- corner <- a 75..494px run, parallel to the tip's

The whole run is travelling the right way already; only the STEP is in the wrong place. So the
step slides back along the run it comes off, into space the route already occupies the far side
of, and the head gets the line the step was standing on. Nothing else about the drawing changes:
same page, same layout, same corner radii, every coordinate that is not part of the tail
untouched.

**What buys it.** Nothing — that is the point. The alternative is `d2.ELK_EDGE_LADDER`, which
buys the same straight line with 40 to 100px of page, per figure, and which one figure cannot
afford at any rung: the reference architecture needs rung 24 and its text falls under the
readability floor at 21.

**What stops it going wrong.** `arrows.crosses` — the invariant that no arrow is drawn across a
box it does not end at. A slid tail is offered to it before it is kept, so a move that would put
a line through a shape is simply not made and the defect stays reported as it was. That check
exists for this and reads zero on everything the repo ships.

Three things bound how far back the step may go, and each is a defect that would otherwise be
traded for the one being fixed:

  * the run has to survive as a run, hence `KEEP_RUN`;
  * it has to stay long enough to hold the words that sit on it, or the label it was
    carrying has nowhere left to go;
  * it may not cross a shape.

It runs BEFORE `edgelabel`, so the words are placed on the route that ships rather than on the
one ELK proposed. Only `marker-end` tails are moved: the twelve are all at that end, which is
the end that arrives at a box, and a `marker-start` needing this would still be reported by
`arrows.defects` for someone to come back to.
"""
import re

from . import arrows
from . import edgelabel

# What must be left of the long run after the step has slid back along it. A run shorter than
# this cannot hold the corner at the end of it — d2's reach ~10px — and still read as a run
# rather than as a second jog. Stated from the two constants that already mean those things.
KEEP_RUN = arrows.CORNER + arrows.MIN_RUN

# The straight line a head is aimed at when there is room for it: its own length plus the run
# `arrows.ends` wants showing behind it. `APPROACH` alone is the point at which the defect stops
# being reported, which is a floor and a poor thing to aim at.
COMFORTABLE = arrows.APPROACH + arrows.MIN_RUN

# Where in the room available the step is put, once `COMFORTABLE` is met. Halfway, because both
# ends of that room are somewhere a turn should not be: hard against the label it just passed,
# or hard against the box it is about to reach. Measured on the reference architecture, half of
# the room puts `read · write`'s turn within 9px of where it was placed by hand.
SHARE = 0.5

_CMD = re.compile(r"([A-Za-z])([^A-Za-z]*)")
_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def straighten(svg, obstacles):
    """`svg` with every tail moved that can be, given the shapes a route may not cross.

    Rewrites the `d` of the connections it moves and nothing else, so a drawing with no short
    approach comes back byte-identical.
    """
    out, end = [], 0
    for match in arrows.CONNECTION.finditer(svg):
        moved = _slide(match.group(1), arrows.holes(svg), obstacles)
        if moved:
            out.append(svg[end:match.start()])
            out.append(match.group(0).replace(match.group(1), moved))
            end = match.end()
    return "".join(out) + svg[end:] if out else svg


def _commands(d):
    """`[(letter, [(x, y), ...])]`, absolute — the only kind d2 emits for a connection."""
    return [(letter, list(zip(nums[::2], nums[1::2])))
            for letter, body in _CMD.findall(d)
            for nums in [[float(n) for n in _NUM.findall(body)]]]


def _emit(commands):
    return " ".join(
        " ".join([letter] + [f"{v:f}" for point in points for v in point]).strip()
        for letter, points in commands)


def _axis(a, b):
    """`('x'|'y', signed length)` for a straight step, or `(None, 0)` for anything else."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    if abs(dx) <= arrows.AXIS_EPS and abs(dy) > arrows.AXIS_EPS:
        return "y", dy
    if abs(dy) <= arrows.AXIS_EPS and abs(dx) > arrows.AXIS_EPS:
        return "x", dx
    return None, 0.0


def _slide(d, holes, obstacles):
    """The path with its last step moved back, or None if it may not be or need not be.

    The tail this recognises is the only shape ELK produces here, and every part of it is
    checked rather than assumed: five commands, a long run and a short approach on the same
    axis and travelling the same way, joined by a step across. Anything else is left alone.
    """
    commands = _commands(d)
    if len(commands) < 6 or [c for c, _ in commands[-5:]] != ["L", "S", "L", "S", "L"]:
        return None
    # P -> A is the long run, A -> B and C -> D the two corners d2 rounds, B -> C the step
    # across, D -> T the approach the arrowhead is painted on.
    before, run_end, turn_in, step_end, turn_out, tip = (
        commands[-6][1][-1], commands[-5][1][-1], commands[-4][1][-1],
        commands[-3][1][-1], commands[-2][1][-1], commands[-1][1][-1])
    approach_axis, approach = _axis(turn_out, tip)
    run_axis, run = _axis(before, run_end)
    across_axis, _across = _axis(turn_in, step_end)
    if approach_axis is None or approach_axis != run_axis or across_axis == run_axis:
        return None
    if abs(approach) >= arrows.APPROACH or run * approach <= 0:
        return None

    # One dimension, always increasing in the direction of travel, so the arithmetic below does
    # not have to care which way the arrow points.
    way = 1.0 if run > 0 else -1.0
    index = 0 if run_axis == "x" else 1
    room = min(abs(run) - KEEP_RUN, *_label_room(holes, before, run_end, index))
    least = arrows.APPROACH - abs(approach)
    most = min(room, max(COMFORTABLE - abs(approach), room * SHARE))
    if most < least:
        return None

    # Shortest move last. The preferred distance is the one that reads best, but it is also the
    # one most likely to reach something: the reference ER's cardinality would slide 144px and
    # take the line through a table on the way. Backing off to a smaller move keeps the fix
    # where a single attempt would have abandoned it — and every rung is still offered to
    # `crosses`, so nothing is kept that a whole move would not have been kept for.
    for delta in _backing_off(least, most):
        shifted = [(letter, [_shift(p, index, way * delta) for p in points])
                   if -5 <= i - len(commands) <= -2 else (letter, points)
                   for i, (letter, points) in enumerate(commands)]
        moved = _emit(shifted)
        if not arrows.crosses(moved, obstacles):
            return moved
    return None


def _backing_off(least, most, steps=4):
    """`most` down to `least`, inclusive — the distances to try, best first."""
    if most <= least:
        return [most]
    return [most - (most - least) * i / steps for i in range(steps + 1)]


def _shift(point, index, by):
    return (point[0] - by, point[1]) if index == 0 else (point[0], point[1] - by)


def _label_room(holes, before, run_end, index):
    """How far back the step may go and still leave the run able to hold the words on it.

    The constraint is the label's SIZE, not the place d2 put it. `edgelabel` runs after this
    and re-centres the words on whatever leg is left, so pinning the step to ELK's own label
    position asks the wrong question — measured, it refused six of the twelve tails over room
    the finished drawing turns out to have, because the label had moved by then.

    What has to survive is therefore what `edgelabel._candidates` needs before it will put a
    label on a leg at all: the label's own extent plus `CENTRE_SLACK`. Every gap crossing the
    run is measured, not only this edge's own, since any of them may be the one that ends up
    there. A run with nothing on it is unconstrained, hence the lone infinity for `min`.
    """
    leg = arrows.Box((min(before[0], run_end[0]) - arrows.STROKE / 2,
                      min(before[1], run_end[1]) - arrows.STROKE / 2,
                      max(before[0], run_end[0]) + arrows.STROKE / 2,
                      max(before[1], run_end[1]) + arrows.STROKE / 2))
    length = abs(run_end[index] - before[index])
    room = [float("inf")]
    for hole in holes:
        if leg.overlap(hole):
            room.append(length - abs(hole[index + 2] - hole[index]) - edgelabel.CENTRE_SLACK)
    return room
