"""Moving part of a route the engine drew, where what it drew cannot be painted well.

This is the one place in the renderer that changes where a line goes, and it is deliberately
not in [`arrows.py`](arrows.py) — that module measures and does not redraw, and keeping the two
apart is what stops "nudge it" becoming the answer to every route complaint.

Three repairs, each described at the function that does it, and none of them costs any page:

  * `_slide` moves the step at the END of a route, so its arrowhead sits on straight line;
  * `_square` deepens a sideways transition in the MIDDLE of one, so d2 can draw it as two
    turns instead of a wobble;
  * `_clear` steps a run out from under a callout resting against it.

The last is the odd one, because nothing about that route is malformed — it is `place` that
cannot see the problem, having been asked what a callout COVERS rather than what it touches.
The line moves because moving it is free and moving the callout is not.

**What the first one does.** ELK brings an edge across the drawing on one long run, steps
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

**All three refuse what they do not recognise.** Each checks every part of the shape it is
about to rewrite rather than assuming it, and returns the route untouched otherwise — a defect
left reported is a great deal better than a line moved somewhere nobody measured.
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

# How deep a sideways transition has to be before d2 draws it as two turns instead of one
# wobble. Not a threshold above which corners fit — d2 sizes a corner to the room it has — but
# the depth at which the two corners and the line between them read as a route changing channel
# rather than as a kink in one. Measured on the single route in either corpus that needs it.
STEP_DEPTH = 25

# Daylight a line needs from a callout lying over it. `place` charges a callout for OCCLUSION,
# so one whose box stops a fraction of a px short of a line pays nothing and still reads as
# resting on it — and it is worse than the boxes say, because a callout is drawn with a shadow
# that reaches a few px past its own edge (`render.HOST_CSS`). Measured on the reference state
# machine, where the gap was 0.4px over 214px of the line's length.
CALLOUT_CLEARANCE = 10

# How close two parallel runs may get before they read as one thick line rather than two
# arrows. Only relevant when something here MOVES a run, since ELK spaces its own channels far
# wider than this — 30px apart on the figure that needed the move. Below about this the two
# arrows arriving at one box stop being separately followable.
MIN_SEPARATION = 20

# The shortest straight run left between two rebuilt corners. Its job is structural rather than
# visual: every `S` must be preceded by an `L` or SVG takes its first control point from the
# reflection of the previous one and draws a tighter curve — see `_clear`.
STRAIGHT_MIN = 2

# How far a rebuilt corner reaches on each axis. d2's own span 7.5 to 17.2px across the corpus,
# sized to the room each has; 10 is what it uses on an unconstrained turn, so a corner rebuilt
# at this reach sits in the same visual family as the ones around it.
CORNER_REACH = 10

# Where in the room available the step is put, once `COMFORTABLE` is met. Halfway, because both
# ends of that room are somewhere a turn should not be: hard against the label it just passed,
# or hard against the box it is about to reach. Measured on the reference architecture, half of
# the room puts `read · write`'s turn within 9px of where it was placed by hand.
SHARE = 0.5

_CMD = re.compile(r"([A-Za-z])([^A-Za-z]*)")
_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def straighten(svg, obstacles):
    """`svg` with every route repaired that can be, given the shapes a route may not cross.

    The three run in this order because they touch different parts of a route and the later
    ones want the earlier ones' result: `_square` fixes a transition in the middle, `_clear`
    may then move a whole run, and `_slide` works on whatever tail that left. Rewrites the `d`
    of the connections it changes and nothing else, so a drawing needing none of it comes back
    byte-identical.
    """
    holes, canvas = arrows.holes(svg), _canvas(svg)
    callouts = edgelabel.callout_boxes(svg)
    # By POSITION, not keyed by the path data: two connections can be drawn identically — a
    # sequence diagram's lifelines routinely are — and keying on `d` would then hide each from
    # the other, so a run could be pushed onto a channel it was supposed to keep off.
    runs = [arrows.leg_boxes(m.group(1)) for m in arrows.CONNECTION.finditer(svg)]
    out, end = [], 0
    for where, match in enumerate(arrows.CONNECTION.finditer(svg)):
        original = match.group(1)
        others = [leg for i, legs in enumerate(runs) if i != where for leg in legs]
        fixed = _square(original, canvas, obstacles) or original
        fixed = _clear(fixed, callouts, others, canvas, obstacles) or fixed
        fixed = _slide(fixed, holes, obstacles) or fixed
        if fixed != original:
            out.append(svg[end:match.start()])
            out.append(match.group(0).replace(original, fixed))
            end = match.end()
    return "".join(out) + svg[end:] if out else svg


def _canvas(svg):
    """The box every coordinate in a d2 SVG is expressed in — the MASK's, not the viewBox's."""
    mask = arrows.MASK.search(svg)
    if not mask:
        return None
    x, y, w, h = (float(mask.group(i)) for i in (2, 3, 4, 5))
    return arrows.Box((x, y, x + w, y + h))


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


def _clear(d, callouts, others, canvas, obstacles):
    """A straight run stepped out from under a callout resting on it, or None.

    `place` decides where a callout goes by measuring what it OCCLUDES, and a box that stops a
    fraction of a px short of a line occludes nothing at all. So a callout can sit on a line
    for hundreds of px and pay for none of it — which is what happens to the reference state
    machine's `user leaves`, at a gap of 0.4px over 214px.

    Moving the callout instead is the placement pass's business and was measured: on that
    figure the only anchor free of every line is above the box rather than below it, and costs
    43px of page. This moves the LINE, which costs none.

    Only a route that is ONE straight run is recognised, which is the shape the case has and
    the only one where stepping out needs no corner rebuilt at the far end. The step goes in
    whichever direction is away from the callout, and only as far as it must: pushed further,
    the run starts crowding whatever channel is beyond it, and on that figure the next one is
    only 30px away.
    """
    commands = _commands(d)
    if [c for c, _p in commands] != ["M", "L"]:
        return None
    start, end = commands[0][1][-1], commands[1][1][-1]
    run_axis, run = _axis(start, end)
    if run_axis is None:
        return None
    index = 0 if run_axis == "x" else 1
    perp = 1 - index
    span = (min(start[index], end[index]), max(start[index], end[index]))

    line = start[perp]
    for box in callouts:
        if box[index + 2] <= span[0] or box[index] >= span[1]:
            continue                     # nowhere near it along the run
        for edge, way in ((box[perp + 2], 1.0), (box[perp], -1.0)):
            if 0 <= (line - edge) * way < CALLOUT_CLEARANCE:
                line = edge + way * CALLOUT_CLEARANCE
    if line == start[perp]:
        return None

    if canvas and not (canvas[perp] + CORNER_REACH <= line <= canvas[perp + 2] - CORNER_REACH):
        return None
    for leg in others:                   # not onto another arrow's channel
        flat = leg[perp + 2] - leg[perp] <= arrows.STROKE + 1
        if flat and leg[index] < span[1] and leg[index + 2] > span[0]:
            if abs(leg[perp] - line) < MIN_SEPARATION:
                return None

    way = 1.0 if run > 0 else -1.0
    down = 1.0 if line > start[perp] else -1.0
    # Sized to the room, as d2 sizes its own, and ALWAYS with a straight run between the two
    # corners. That run is not cosmetic: an `S` takes its first control point from the
    # reflection of the previous one, so an `S` following an `S` collapses both controls onto
    # the elbow and draws a visibly tighter turn than its twin. d2 never does it — every corner
    # it emits is preceded by an `L` — and the first version here, which dropped the run when
    # the step was shallow, produced exactly that mismatched pair.
    drop = abs(line - start[perp])
    reach = min(CORNER_REACH, (drop - STRAIGHT_MIN) / 2)
    if reach < STRAIGHT_MIN:
        return None                      # too shallow to draw as two turns at all
    elbow = start[index] + way * (reach + arrows.MIN_RUN)
    turn, out = elbow + way * reach, elbow + way * reach * 2
    moved = _emit([
        ("M", [start]),
        ("L", [_at(start, index, elbow)]),
        ("S", [_at(start, index, turn), _pair(index, turn, start[perp] + down * reach)]),
        ("L", [_pair(index, turn, line - down * reach)]),
        ("S", [_pair(index, turn, line), _pair(index, out, line)]),
        ("L", [_at(end, perp, line)])])
    if arrows.terminals(arrows.points(moved), obstacles) != \
            arrows.terminals([start, end], obstacles):
        return None                      # it would stop pointing at what it points at
    return None if arrows.crosses(moved, obstacles) else moved


def _square(d, canvas, obstacles):
    """A diagonal channel change turned into two proper turns, or None.

    d2 draws an orthogonal corner as `S` and a DIAGONAL one as `C`, so a cubic in a connection
    is the tell: the route moved on both axes at once with no straight run between. On the
    reference architecture's `fan-out` that is 6px across and 6px down as it leaves Redis, and
    the cubic's control points run out past the turn and back again — which is the small bump
    you can see on the line. It breaks the rule that every straight stretch is vertical or
    horizontal, and escapes `arrows.defects` only because `CORNER` has to tolerate 20px of
    off-axis before it will call anything diagonal.

    Six px is too little to turn twice in, so the fix is room: the run AFTER the transition
    moves out to `STEP_DEPTH`, in the direction the route was already heading, and the corner
    at its far end follows it. Which way needs no deciding and no knowledge of what else is on
    the page — the route already chose, this only makes it commit.

    The run BEFORE cannot move: its far end is where ELK put the port on the box, and that is
    not up for revision here. So a transition whose second run ends the route is refused rather
    than moved, since moving it would drag the arrowhead off the thing it points at.
    """
    commands = _commands(d)
    at = next((i for i, (letter, _p) in enumerate(commands) if letter in "Cc"), None)
    if at is None or at < 2 or at + 2 >= len(commands):
        return None
    if commands[at - 1][0] != "L" or commands[at + 1][0] != "L" or commands[at + 2][0] != "S":
        return None

    start, before, corner, after = (commands[at - 2][1][-1], commands[at - 1][1][-1],
                                    commands[at][1][-1], commands[at + 1][1][-1])
    run_axis, run = _axis(start, before)
    out_axis, out = _axis(corner, after)
    if run_axis is None or run_axis != out_axis or run * out <= 0:
        return None
    index = 0 if run_axis == "x" else 1
    perp = 1 - index
    depth = corner[perp] - before[perp]
    if abs(depth) <= arrows.AXIS_EPS or abs(depth) >= STEP_DEPTH:
        return None                      # already a real step, or no step at all

    way = 1.0 if run > 0 else -1.0                  # along the axis, in the travel direction
    down = 1.0 if depth > 0 else -1.0               # across it, the way the route already went
    line = before[perp] + down * STEP_DEPTH         # where the second run ends up
    extra = line - corner[perp]
    if canvas and not (canvas[perp] + CORNER_REACH <= line <= canvas[perp + 2] - CORNER_REACH):
        return None                      # the deeper channel would leave the drawing

    # Two corners with a straight step between them, in d2's own shape: an `L` to the end of the
    # run, an `S` whose control is the elbow, the step, and a second `S` turning back onto the
    # axis. Both corners sit on one line across, so the step really is straight.
    elbow = before[index] + way * CORNER_REACH
    rebuilt = (commands[:at]
               + [("S", [_at(before, index, elbow),
                         _pair(index, elbow, before[perp] + down * CORNER_REACH)]),
                  ("L", [_pair(index, elbow, line - down * CORNER_REACH)]),
                  ("S", [_pair(index, elbow, line),
                         _pair(index, elbow + way * CORNER_REACH, line)]),
                  ("L", [_at(after, perp, line)]),
                  (commands[at + 2][0], [_at(commands[at + 2][1][0], perp, line),
                                         _shift(commands[at + 2][1][1], perp, -extra)])]
               + commands[at + 3:])
    if at + 3 < len(commands):
        _kind, tail = _axis(rebuilt[at + 4][1][-1], commands[at + 3][1][-1])
        if abs(tail) < KEEP_RUN:
            return None                  # the run it turns into would be eaten by the move
    moved = _emit(rebuilt)
    return None if arrows.crosses(moved, obstacles) else moved


def _at(point, index, value):
    return (value, point[1]) if index == 0 else (point[0], value)


def _pair(index, along, across):
    return (along, across) if index == 0 else (across, along)


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
