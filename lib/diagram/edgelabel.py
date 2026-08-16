"""Moving an edge label to the part of its route where it costs least.

ELK parks a label at the middle of the route it computed, and d2 punches a hole in the line
underneath it — every connection carries `mask="url(#…)"`, and the mask holds one black
rectangle per label. On a route that turns, the middle is usually the turn itself, so the
label lands on a short horizontal leg between two vertical ones and the mask deletes the whole
leg rather than the label's own height of it.

Neither engine will fix that for us: d2 exposes a handful of ELK options and the label ones
are not among them (see `d2.ELK_OPTS`), so it is a fork of d2 or a post-pass. This is the
post-pass, and the geometry it needs is all in the SVG — the route is a polyline, the label's
exact box is the mask rectangle d2 measured, and what a label must not cover is a rect, a
polygon or another path.

**A label stays centred on its line. The only thing that moves is WHERE along the route.**

Offsetting a label sideways instead was built first and reads worse, though it hides no line
at all: beside a line a label stops saying WHICH line it belongs to, and two offset labels on
neighbouring routes line up flush and read as one wrapped paragraph. A short break in a
vertical line costs less than either.

So a candidate is centred across some straight leg, at some point along it. Sliding along the
leg is what buys clearance from whatever the label would otherwise sit on.

It will not trade one defect for a worse one: a candidate off the canvas or on a box scores
below staying put, so a figure with nothing to gain comes out byte-identical — which most
labels are, ELK's own choice usually being fine.

Two passes follow. `_align_rows` pulls labels that landed near the same height onto exactly
that height. `_cut_gaps` gives a connection crossing a container's TITLE the same break d2
already cuts for its own labels.

What an arrow has to look like underneath all this — where its head may not be broken, what
counts as a straight leg — is `arrows`, and both modules read it from there. This one only
decides where the words go.

A `sequence` is excluded by its caller: its labels come from d2's own sequence engine and its
rows are re-stacked by `compact.py`, so this must not be a third opinion about that geometry.
"""
import re
from collections import namedtuple

from . import palette
from .arrows import (CONNECTION, MASK, STROKE, Box, ends, leaves_a_stub, leg_boxes, legs,
                     pairs, points, shortfall)

# Line a leg must keep showing beyond the label, in px total. A label centred on a leg barely
# its own length leaves no arrow either side of it, and then the break IS the leg.
CENTRE_SLACK = 8

# Where along a leg a label may sit, as fractions of the room it has to slide in. Fine enough
# to clear an obstacle by a few px, coarse enough to keep the search cheap. The ends are
# included: pushed hard against one end is the answer when the obstacle is at the other.
SLIDE = tuple(i / 16 for i in range(17))

# How far along its own leg a label may end up from the middle of it, as a fraction, when it
# moves to join a row. Bounded, because alignment is worth some drift and not a diagram's worth
# — past this the label is no longer near the middle of the arrow it names, which is the thing
# the alignment was tidying.
#
# Relative, and that replaced a flat 40px. The px version asked the wrong question: it refused
# two labels 44px apart that had 480px of leg to move in, and admitted two 39px apart whose legs
# gave them 20px of travel between them. What a drift COSTS depends on the leg it happens on.
#
# Two numbers, because the axes are not alike — the legs are not. A figure's horizontal runs are
# far longer than its vertical ones, so one fraction buys a much larger ABSOLUTE travel sideways:
# 40% of a 563px run is 225px, where 40% of a 150px one is 60. A label sliding a fifth of the
# page along the reading direction is a bigger event than the same fraction going up.
ALIGN_FRACTION = {"y": 0.4, "x": 0.2}

# A container's border, as a strip this thick. The interior is not an obstacle: a label
# crossing the empty part of a group is ordinary and ELK routes through it all the time. The
# border is, because a label lying along it reads as a gap in the box.
BORDER = 3

# A container's TITLE, which d2 draws inside the box and gives no measurable width. It is
# centred, so its half-width is the distance from the container's left edge to the text anchor
# less the padding d2 puts between them. That padding is the one number here that was measured
# rather than read out of the SVG; `test_edgelabel` re-measures it against a browser so a d2
# upgrade that moves it fails loudly rather than drifting.
TITLE_PAD = 5

# A title's band, relative to its baseline: d2's own edge-label rectangles sit 13px above the
# baseline and are 17px tall, so a gap cut for a title matches the gaps already in the drawing.
TITLE_RISE, TITLE_HEIGHT = 13, 17

# What a px of line hidden right before an arrowhead is worth, in px hidden anywhere else. The
# one place in this module where two things are priced against each other rather than compared
# in order, and it is priced because neither order is right: rank it above hidden line and a
# label will delete a whole leg to win 4px of clearance; rank it below and it never wins
# anything.
#
# Both bounds are measured, on `repo/arch`, where one label's only two spots are 4px into an
# arrowhead or across the route's horizontal jog. Hiding the jog costs 103px² more than the leg
# it would otherwise cover, so anything from 13 up buys that trade — and looking at it settles
# it: the arrow then leaves its box as a 6px stub and reappears below the words. 5 keeps the
# label on the leg and leaves the stranded head standing as a defect, which is the honest
# outcome: no placement on THAT layout is right, and `render._pick_at_spacing` is what can
# choose a different one.
HEAD_WEIGHT = 5

# Rounding, in px² of area and px of distance, before two candidates are compared. Without it
# a quarter-pixel of shape overlap outranks a whole leg of hidden line, since the terms are
# compared in order rather than priced against each other.
ROUND = (1, 4, 1, 8, 4, 4)

_ROOT = re.compile(r"<svg\b[^>]*>")
_MASK_RECT = re.compile(r'<rect x="([-\d.]+)" y="([-\d.]+)" width="([\d.]+)" '
                        r'height="([\d.]+)" fill="black">\s*</rect>')
# The holes this module cuts itself, as opposed to the ones d2 cut for its edge labels. Tagged
# so `_MASK_RECT` cannot read one back as a label's box, and so a re-run can clear them first.
_GAP_RECT = re.compile(r'<rect [^>]*data-gap="title">\s*</rect>\s*')
# A labelled connection: d2 emits the path and its label as adjacent siblings, with nothing
# between them. Requiring that adjacency is what keeps an UNlabelled connection from being
# paired with the next connection's label.
_LABELLED = re.compile(
    r'(?P<path><path d="(?P<d>[^"]*)"[^>]*class="connection"[^>]*/>)'
    r'(?P<text><text x="(?P<tx>[-\d.]+)" y="(?P<ty>[-\d.]+)"[^>]*?class="text-italic".*?</text>)',
    re.S)
_RECT = re.compile(r'<rect x="([-\d.]+)" y="([-\d.]+)" width="([\d.]+)" height="([\d.]+)"'
                   r'[^>]*?/>')
# A container and the title d2 draws inside it, which it emits as adjacent siblings with only a
# closing tag between them.
_TITLE = re.compile(r'<rect x="([-\d.]+)" y="([-\d.]+)" width="([\d.]+)" height="[\d.]+"'
                    r'[^>]*?/>(?:</g>)*<text x="([-\d.]+)" y="([-\d.]+)"[^>]*?class="text"')
_POLY = re.compile(r'<polygon points="([^"]*)"[^>]*/>')
_PATH = re.compile(r'<path d="([^"]*)"[^>]*/>')
# A group container's border, in EITHER form. This pass runs before `palette.to_vars`, so what
# it sees is the literal; matching only the `var()` spelling recognised no container at all,
# which counted every one as a solid box and drove labels out of their containers entirely.
# Both spellings, sourced from the palette so they cannot drift.
_GRP = re.compile("|".join(re.escape(s) for s in
                           ["var(--d-grp-br)"]
                           + [lit for lit, (var, *_) in palette.ROLES.items()
                              if var == "--d-grp-br"]))


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
            out.append((Box((x - w / 2, y - h / 2, x + w / 2, y + h / 2)),
                        "y" if kind == "v" else "x", lo, hi))
    return out


def title_boxes(svg, body_start=0):
    """The box of every container title, derived from the geometry d2 already emits.

    d2 writes the container's rect and then its title as a centred `<text>`, with nothing
    between them but a closing tag. That pair is enough: the anchor's distance from the left
    edge is the title's half-width plus `TITLE_PAD`.

    Used twice, and the second use is the point. As an OBSTACLE it keeps an edge label off a
    container title. As a hole in the mask it gives every connection crossing that title the
    same break it already gets for an edge label — which is what makes `Kubernetes cluster`
    readable with an arrow through it.
    """
    boxes = []
    for match in _TITLE.finditer(svg, body_start):
        # Containers only. A leaf node's label is centred in its BOX, so the same arithmetic
        # would return half the box width and mask a hole the width of the node.
        if not _GRP.search(match.group(0)):
            continue
        left, anchor, baseline = (float(match.group(i)) for i in (1, 4, 5))
        half = max(0.0, anchor - left - TITLE_PAD)
        boxes.append(Box((anchor - half, baseline - TITLE_RISE,
                           anchor + half, baseline - TITLE_RISE + TITLE_HEIGHT)))
    return boxes


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


def _shape_boxes(svg, body_start):
    """`(box, kind)` per drawn shape, `kind` being 'group', 'callout' or 'node'.

    One parse with the kinds kept, because the two questions asked of it want different
    subsets: a LABEL has to keep off all three, while a ROUTE crosses a container by design.
    Reading the geometry twice under two names is how the two answers would drift apart.

    `body_start` is where the drawing begins, so the `<mask>` block's own rectangles — which
    are label boxes, handled separately — and the root's transparent backdrop are not read as
    geometry.
    """
    out = []
    for match in _RECT.finditer(svg, body_start):
        if 'fill="transparent"' in match.group(0):
            continue
        x, y, w, h = (float(g) for g in match.group(1, 2, 3, 4))
        out.append((Box((x, y, x + w, y + h)), _kind(match.group(0))))
    for match in _POLY.finditer(svg, body_start):
        pts = pairs(match.group(1))
        if pts and "connection" not in match.group(0):
            out.append((_bounds(pts), _kind(match.group(0))))
    for match in _PATH.finditer(svg, body_start):
        if "connection" in match.group(0) or "Z" not in match.group(1):
            continue
        pts = points(match.group(1))
        if pts:
            out.append((_bounds(pts), _kind(match.group(0))))
    return out


def _kind(tag):
    """A shape's kind, from how it is painted. Containers first: only they carry `_GRP`.

    A callout is recognised in EITHER spelling, and that is not belt and braces. This pass runs
    before `render.postprocess`, which is where the `d2-callout` class is added — so matching
    only the class recognises no callout at all, reads every one as a solid node, and leaves
    `route._clear` with nothing to keep a line off. Exactly the trap `_GRP` documents above,
    found again the same way: a check that silently measured nothing.
    """
    if _GRP.search(tag):
        return "group"
    return "callout" if ("d2-callout" in tag or palette.CALLOUT_PAINT in tag) else "node"


def _bounds(pts):
    return Box((min(p[0] for p in pts), min(p[1] for p in pts),
                max(p[0] for p in pts), max(p[1] for p in pts)))


def _shapes(svg, body_start):
    """Everything a label must not sit on: node boxes, table rows, callouts, group borders.

    A group container contributes its four border strips rather than its area: see `BORDER`.
    """
    boxes = []
    for box, kind in _shape_boxes(svg, body_start):
        if kind == "group":
            boxes += [Box((box[0], box[1], box[2], box[1] + BORDER)),
                      Box((box[0], box[3] - BORDER, box[2], box[3])),
                      Box((box[0], box[1], box[0] + BORDER, box[3])),
                      Box((box[2] - BORDER, box[1], box[2], box[3]))]
        else:
            boxes.append(box)
    return boxes


def callout_boxes(svg, body_start=0):
    """Just the callouts, for something that needs to keep clear of one rather than off it.

    The other half of `route_obstacles`, from the same parse. A route may be drawn UNDER a
    callout — `place` weighs that against everything else it has to — but resting against one
    is not a decision anybody took, it is what a search that measures occlusion cannot see.
    """
    return [box for box, kind in _shape_boxes(svg, body_start) if kind == "callout"]


def route_obstacles(svg, body_start=0):
    """The shapes a ROUTE may not be drawn across — node bodies, and nothing else.

    Feeds `arrows.through`. Two exclusions, and both are deliberate:

    **Containers.** An edge leaving a nested node crosses its parent's border on the way out,
    and every figure here that has containers does it several times. A container's TITLE is a
    different matter and is already handled, as a gap in the mask — see `title_boxes`.

    **Callouts.** They are placed on top of a finished drawing by `place`, which prices
    covering a line against clipping, hidden text and glyph size. Forbidding it here would
    overrule that search from underneath, and with a rule that cannot see any of what it
    weighed.
    """
    return [box for box, kind in _shape_boxes(svg, body_start) if kind == "node"]


def _key(box, canvas, shapes, lines, zones, others, adrift, home, target):
    """How bad a candidate is, compared term by term rather than priced against each other.

    Six terms, in this order, and the order is the argument.

    **Off the canvas** is not a placement at all. **On a box** is the defect this module was
    written for — a cardinality with its left half inside the table it points at — and no
    amount of tidiness elsewhere buys it back.

    **`adrift`** — whether the label hangs off the end of its own leg instead of sitting within
    it. This is the module's founding rule ("a label stays centred on its line") defended
    against the term below, which quietly contradicts it: a label half past the end of its leg
    COVERS LESS OF THAT LEG, so hiding-line pays it to be in the wrong place. `measure every
    anchor` ended 3px from the end of a 95px leg that way, and a label off its leg is a label
    with no leg to slide along, which silently removes it from the row alignment. Ranking a
    legal position above the quantity being minimised is what fixed four separate complaints at
    once — a minimisation term can always be satisfied by leaving the domain it measures.

    **Hidden line** is the thing being minimised once the label is somewhere legal, and the px
    of it that sit right before an arrowhead count `HEAD_WEIGHT` times over, because losing
    those costs the arrow its meaning rather than a little of its length.

    Then two tie-breaks, and their order is a correction. **`home`** is the middle of the room
    the label has on its own leg — equal arrow showing above and below it. **`target`** is the
    middle of the whole route, which is where a reader looks. They agree on a plain straight
    arrow and disagree whenever the route continues past the leg the label sits on: the route's
    middle is then beyond the leg's end, so preferring it slid the label hard against whatever
    stopped it first and left every px the leg later gained piling up on the other side. Two
    labels in the repo architecture sat with 74px of line above them and 4px below.
    """
    shape_area = sum(box.overlap(s) for s in shapes) + sum(box.overlap(o) for o in others)
    line_area = (sum(box.overlap(line) for line in lines)
                 + HEAD_WEIGHT * STROKE * shortfall(box, zones))
    centre = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
    off = _distance(centre, home)
    drift = _distance(centre, target)
    terms = (box.outside(canvas), shape_area, bool(adrift), line_area, off, drift)
    return tuple(round(v / step) for v, step in zip(terms, ROUND))


def _distance(a, b):
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def _home(box, axis, lo, hi, target):
    """The middle of the room this candidate has on its leg — where the arrow shows equally.

    `target` where there is no leg to be in the middle of: a label ELK placed somewhere this
    module would not offer has no room of its own to be centred in.
    """
    if axis is None:
        return target
    middle = (lo + hi) / 2
    return ((box[0] + box[2]) / 2, middle) if axis == "y" else (middle, (box[1] + box[3]) / 2)


def reposition(svg):
    """Return `svg` with each edge label moved to its least damaging spot on its own route.

    The label is moved by wrapping its `<text>` in a `<g transform="translate(…)">` rather
    than by rewriting coordinates: a wrapped label is a `<text>` full of `<tspan x=…>`, and a
    translate carries all of them without this module having to know how many there are.

    The mask rectangle moves by the same delta — that rectangle IS the hole in the connection,
    so leaving it behind would delete a piece of line the label has vacated.
    """
    root = _ROOT.search(svg)
    mask = MASK.search(svg)
    if not root or not mask:
        return svg
    cx, cy, cw, ch = (float(g) for g in mask.group(2, 3, 4, 5))
    canvas = Box((cx, cy, cx + cw, cy + ch))

    rects = list(_MASK_RECT.finditer(mask.group(6)))
    labels = [m for m in _LABELLED.finditer(svg) if m.start() < mask.start()]
    if not rects or not labels:
        return svg

    titles = title_boxes(svg, root.end())
    shapes = _shapes(svg, root.end()) + titles
    all_paths = [m.group("d") for m in labels] + _unlabelled_paths(svg, labels)
    boxes = [Box((float(r.group(1)), float(r.group(2)),
                   float(r.group(1)) + float(r.group(3)),
                   float(r.group(2)) + float(r.group(4)))) for r in rects]

    lines = [b for d in all_paths for b in leg_boxes(d)]
    # The arrow ends, read once. Every candidate of every label is scored against them, and
    # they do not move: this pass edits words and mask rectangles, never a route.
    zones = ends(svg)
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

        def score(candidate, adrift, home, _o=others, _t=target):
            return _key(candidate, canvas, shapes, lines, zones, _o, adrift, home, _t)

        best, best_key, axis, lo, hi = box, None, None, 0.0, 0.0
        for candidate, candidate_axis, low, high in _candidates(box, legs(pts)):
            key = score(candidate, candidate_axis is None,
                        _home(candidate, candidate_axis, low, high, target))
            if best_key is None or key < best_key:
                best, best_key, axis, lo, hi = candidate, key, candidate_axis, low, high
        placed.append(_Placed(label, index, box, best, best_key, axis, lo, hi, score,
                              _home(best, axis, lo, hi, target)))
        drawn.append(best)

    # The mask always travels with the label. It is the hole the label punches in its own
    # connection, and a centred label is always standing on that connection — so there is no
    # case where the hole should be dropped or should stay behind.
    svg = _apply(svg, mask, rects,
                 [(p.label, p.index, p.box[0] - p.was[0], p.box[1] - p.was[1])
                  for p in _align_rows(placed)])
    return _cut_gaps(svg, titles)


def unfixable_crossings(svg):
    """How many container titles an arrow runs through that no gap can rescue.

    A defect the layout can fix and this pass cannot: where an arrow terminates immediately
    under a title there is no room to break the line without stranding its arrowhead, so the
    gap is declined and the title keeps a line through it. A DIFFERENT layout of the same spec
    may not have that problem at all, which is what makes this worth counting per candidate —
    see `render._pick_at_spacing` and the rescue budget in `gates/size`.
    """
    zones = ends(svg)
    lines = [leg for m in CONNECTION.finditer(svg) for leg in leg_boxes(m.group(1))]
    return sum(1 for box in title_boxes(svg)
               if any(box.overlap(leg) > 0 for leg in lines)
               and not leaves_a_stub(box, zones))


def _cut_gaps(svg, boxes):
    """Give every connection crossing a container title the same break an edge label gets.

    d2 already does this for edge labels and nothing else, so an arrow entering a container
    runs straight through its title — `Kubernetes cluster` and `presence deploy x2` each had a
    line through the middle of a word. The mechanism is d2's own: a black rectangle in the mask
    every connection is drawn through.

    Tagged `data-gap`, so `_MASK_RECT` does not read these back as label boxes on a later pass.
    Without that a title's rectangle can match a label by centre and baseline, and the label
    would be moved to fit a box that is not its own.
    """
    # Any it cut last time go first. Appending unconditionally would stack a second identical
    # set on every call, which is harmless to look at and breaks the one property this module
    # relies on to prove it cannot loop — that running it again changes nothing.
    svg = _GAP_RECT.sub("", svg)
    if not boxes:
        return svg
    mask = MASK.search(svg)
    if not mask:
        return svg
    # One rectangle hides every connection that crosses it — the mask is not per-edge — so one
    # stranded arrowhead vetoes the whole gap. That is the right granularity anyway: a title is
    # either cleared or it is not. The rule is `arrows.leaves_a_stub`, the same one that stops
    # a LABEL's gap landing on an arrowhead; there is only one such rule in the renderer.
    zones = ends(svg)
    keep = [b for b in boxes if b.w > 1 and leaves_a_stub(b, zones)]
    rects = "".join(f'<rect x="{b[0]:f}" y="{b[1]:f}" width="{b.w:f}" height="{b.h:f}" '
                    f'fill="black" data-gap="title"></rect>\n' for b in keep)
    return svg[:mask.end(6)] + rects + svg[mask.end(6):]


class _Placed(namedtuple("_Placed", "label index was box key axis lo hi score home")):
    """One label after the search, and everything the row alignment needs to move it again."""

    @property
    def coord(self):
        return ((self.box[1] + self.box[3]) / 2 if self.axis == "y"
                else (self.box[0] + self.box[2]) / 2)

    @property
    def reach(self):
        """How far this label may TRAVEL to join a row, in px.

        Travel, not distance from the leg's centre, and that distinction is the whole rule. On
        the reference architecture `GraphQL` has already been slid clear of the Kubernetes
        container's border by the base search, so it sits well off its leg's centre for a good
        reason; measured from the centre it then had budget to be dragged 138px back down INTO
        the container to join a row with a third label. Measured as travel it has none, the
        far row is refused, and it keeps `WebSocket` level with it outside the box.

        Relative to the leg, not a fixed number, because the same drift means different things
        on a 70px leg and a 500px one: a third of the way along a short leg is nearly at the
        arrowhead, where the same px on a long leg is still comfortably in the middle third.
        """
        along = self.box.h if self.axis == "y" else self.box.w
        return ALIGN_FRACTION[self.axis] * (self.hi - self.lo + along + CENTRE_SLACK)

    def at(self, coord):
        """The same box with its slide coordinate set to `coord`."""
        shift = coord - self.coord
        dx, dy = (0, shift) if self.axis == "y" else (shift, 0)
        return Box((self.box[0] + dx, self.box[1] + dy,
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

    No label travels further than `_Placed.reach`, and a row that only exists at a coordinate
    beyond that simply does not form. That is what keeps a distant third label from dragging a
    pair that was already level: on the reference architecture the only height all three could
    share was inside the Kubernetes cluster, and `GraphQL` and `WebSocket` would have been
    pulled in to reach it.
    """
    out = list(placed)
    for axis in ("x", "y"):
        members = sorted((p for p in out if p.axis == axis), key=lambda p: p.coord)
        for row in _rows(members):
            best = []
            for target in _targets(row):
                taken, moved_to = [], []
                for member in sorted(row, key=lambda p: abs(p.coord - target)):
                    moved = member.at(target)
                    if not (member.lo <= target <= member.hi):
                        continue
                    # And not so far along its own leg that the label has stopped being in the
                    # middle of the arrow it names — which is the thing the row was tidying.
                    if abs(target - member.coord) > member.reach:
                        continue
                    # Everything above drift, whatever that is today — a term added to `_key`
                    # is a thing alignment must not be allowed to spend.
                    if member.score(moved, member.axis is None,
                                    member.home)[:-2] != member.key[:-2]:
                        continue
                    if any(moved.overlap(other) > 0 for other in taken):
                        continue
                    taken.append(moved)
                    moved_to.append((member, moved))
                if len(moved_to) > len(best):
                    best = moved_to
                if len(best) == len(row):
                    break
            for member, moved in best if len(best) > 1 else []:
                out[out.index(member)] = member._replace(box=moved)
    return out


def _rows(members):
    """Runs of labels, sorted by coordinate, that can all reach one shared coordinate.

    Reachability rather than a fixed window, because a window is the wrong question asked in
    px: two labels 44px apart with 480px of leg between them are trivially one row and were
    refused, while two 39px apart on two 50px legs were grouped and then could not move. What
    decides it is whether their slide ranges overlap at all — and how far each may travel is
    `_Placed.reach`, which is a fraction of its own leg.
    """
    rows, run, lo, hi = [], [], None, None
    for member in members:
        low = member.lo if lo is None else max(lo, member.lo)
        high = member.hi if hi is None else min(hi, member.hi)
        if run and low > high:
            rows.append(run)
            run, low, high = [], member.lo, member.hi
        run.append(member)
        lo, hi = low, high
    rows.append(run)
    return [row for row in rows if len(row) > 1]


def _targets(row):
    """Heights the row could share, best first.

    More than one, because a single proposal fails whenever something sits between the members
    — a container's edge between two labels is enough — and the row then gives up on a height
    that was available a few px away. The midpoint comes first, then each member's own current
    height, then the ends of the range they can all reach.

    A member's own height is offered RAW, not clamped into the range the whole row can reach.
    Clamping made every proposal feasible for everyone, and in doing so deleted the only useful
    ones: on the reference architecture a third label 150px away dragged the common range down
    inside the Kubernetes container, so `GraphQL`'s own height — where its neighbour wanted to
    be, and where two of the three could meet — was never even proposed. `_align_rows` already
    counts how many members actually take a target, so an unreachable proposal costs nothing
    and a partial row is a result.
    """
    lo, hi = max(p.lo for p in row), min(p.hi for p in row)
    clamp = (lambda v: min(max(v, lo), hi)) if lo <= hi else (lambda v: v)
    wanted = [clamp((row[0].coord + row[-1].coord) / 2)]
    wanted += [p.coord for p in row]
    if lo <= hi:
        wanted += [lo, hi]
    return list(dict.fromkeys(wanted))


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
