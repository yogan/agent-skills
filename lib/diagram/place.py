"""Choosing where each callout sits, by measuring the alternatives.

d2 cannot do this: `tooltip.near` takes one of eight fixed anchors, d2 avoids no overlaps, and
it does not grow the canvas to fit a callout. So the renderer decides — render every anchor,
measure in a browser, keep the best.

What is optimised is READABILITY, as five terms compared in order:

  1. **Clipping**, at 1e6, so it is never traded. An earlier 1e3 swapped a 31px clip for a
     little less overlap.
  2. **Text made unreadable**, in px². Also never traded — see `_score`.
  3. **The smallest glyph in the finished drawing.** An anchor can widen the canvas, and a
     wider canvas is scaled further down, so a callout can shrink every letter in the diagram.
  4. **Landmarks covered** — the corners a route goes round, and its ends, being the arrowhead
     or where the line leaves its box. A COUNT, not a price.
  5. **Overlap and page height, priced against each other** at `HEIGHT_PRICE`. Overlap is
     weighted by what it damages (`browser.OVERLAP_WEIGHTS`): unweighted, the search optimises
     area and will hide a label to stay off a big rectangle.

Term 4 is a rank rather than a price because that is what is actually true of a landmark: those
are the parts of a route a reader cannot infer from what is still showing — a bridged run reads
as one line, a corner can be half-guessed from its two runs, but nothing left in the picture
says which way an arrow with no visible head was pointing. So **any position that keeps them
clear beats any position that does not**, whatever it costs in page height or line covered. A
price would have needed a number chosen to mean that, and would still have lost to a large
enough height saving.

It sits below glyph size on purpose: shrinking every letter in the figure to save one arrowhead
is the worse trade, and a word nobody can read is worse still.

A rank also has to degrade, and this one does. On a crowded drawing there may be no position
that clears every landmark — measured, on a three-note figure whose 512 combinations were all
enumerated, the best available still covers one corner — and then term 4 ties and term 5
decides, so the search is back to minimising line and page. What a rank cannot do is report
itself, so `figure` says out loud when the drawing that ships still covers one.

Term 5 is priced by how much of it there is, so nothing needs a threshold to prefer covering
less. Only what is REPORTED to an author needs a line drawn, and that line is the landmark:
nothing is said about a stretch bridged mid-run.

Often every term ties, because on a roomy diagram most anchors cover nothing. That is the model
saying there is no readability argument left, and the tie falls to the order of `spec.NEAR`.

The search settles one callout at a time and then sweeps again, until a whole pass moves
nothing. **Both halves of that are load-bearing, and it replaced an exhaustive 8^n grid.**
Sweeping is needed because a single pass settles the first callout against wherever the second
still happens to be, and once the second moves, the first one's answer was decided against a
drawing that no longer exists — measured over 42 two-callout diagrams, a single pass got four
of them wrong, once badly enough to put the smallest text in the drawing under the 10px floor.
The grid is not needed because on those same 42 the sweep reached its answer every time, for
roughly a third of the compiling.

What a sweep cannot reach and the grid could is a pair of anchors that works only together and
sits on a plateau, every single-callout move away from it scoring exactly the same. Nothing
real produces one, because these scores are continuous px measurements and do not tie exactly;
`test_place.TestThePlateauTheSweepCannotCross` is the shape of it, built by hand.

Where no anchor is clip-free the fix is editorial: shorten the note.
"""
import copy

from .. import parallel
from . import arrows
from . import browser as browser_mod
from . import callout as callout_mod
from . import render as render_mod
from .spec import NEAR, validate

# Clipping dominates overlap absolutely; see the module docstring.
CLIP_PENALTY = 1e6

# Most passes over the callouts the search will make before giving up on settling. A backstop,
# not a budget — `_sweep` argues it cannot be reached, and real diagrams settle within three —
# and it exists because this module has oscillated before (`lib/diagram/README.md`, "Dead
# ends"). Reaching it is not an error, since the placement returned is still the cheapest found,
# but it means that argument broke; the `sweeps` count in the placement report is where it shows.
MAX_SWEEPS = 8

# What a pixel of page height is worth in the same units. The two must be priced, not ordered:
# height first buried text to save 50px, overlap first spent 50px to save 315 units of noise.
# Bucketing both is subtly wrong — two values closer than the bucket still land either side of
# a boundary.
#
# It was 20 while overlap was measured as box against box (`js/measure.js`), which counted the
# empty middle of an L-shaped route and the empty interior of every container — inflating a real
# overlap more than tenfold. Against honest numbers 20 buys any arrow for a few px of page:
# measured on the reference state machine, the free anchor cost 38px of height (765 units at
# that price) and saved 71 units of coverage, so the search hid a turn to save half a line of
# scrolling.
#
# Set from the two figures that actually make this trade, by measuring where each of them flips
# rather than by picking a ratio and checking it: the sequence is indifferent at 6.1 per px and
# the state machine at 4.3, and every price below that gives both a placement covering nothing.
# So 2 — a factor of two clear of the nearer flip, where 4 sat 8% under it and would have been
# decided by a slightly shorter note. Nothing in either sample set or the held-out set moves
# anywhere between 0.5 and 4.
#
# What bounds the other direction is not this number: a candidate that shrinks the drawing's
# text ranks above anything here (see `_score`), so a cheap page cannot buy a smaller glyph, and
# `gates/size` still refuses a figure taller than one viewport.
HEIGHT_PRICE = 2

DEFAULT_NEAR = "top-center"


class PlacementError(RuntimeError):
    """Placement could not be completed — no browser, or d2 refused every candidate."""


def note_sites(spec):
    """Every object in `spec` carrying a `note`, in a stable document order.

    Returns the live dicts, so a caller can set `near` on them directly. Order is purely
    structural, which means `note_sites` on a deep copy yields the corresponding objects at
    the same indices — that is what lets the search apply an anchor tuple to a fresh copy.
    """
    found = []

    def walk_nodes(nodes):
        for node in nodes or []:
            if "note" in node:
                found.append(node)
            walk_nodes(node.get("children"))

    walk_nodes(spec.get("nodes"))
    walk_nodes(spec.get("participants"))
    walk_nodes(spec.get("states"))
    for key in ("tables", "classes"):
        for item in spec.get(key) or []:
            if "note" in item:
                found.append(item)
    return found


def _apply(spec, anchors):
    """A deep copy of `spec` with the i-th note's `near` set to `anchors[i]`."""
    trial = copy.deepcopy(spec)
    for site, anchor in zip(note_sites(trial), anchors):
        site["near"] = anchor
    return trial


def _score(measurement):
    """Cost of one candidate, compared left to right: clipped, hidden, shrunk, covered.

    Each term is measured against a boundary that is not the obvious one, and each choice was
    made after the obvious one was wrong:

    **Clipping** is measured against the CARD, not the svg box. The page lets a callout bleed
    into the card's padding (`HOST_CSS` sets `overflow: visible` so a shadow can) and the
    clipping gate holds it to the card for that reason. On the stricter boundary the search
    rejected anchors that clip nothing — the state machine's `center-left` is 16px past the svg
    box, 0px past the card, and covers HALF what the winner did.

    **Hidden text** is the anchor's OWN share, `hiddenText - hiddenByLayout`. Both come out of
    one loop in `measure.js` under one threshold, so the subtraction is exact. The layout's
    share is near-constant across a search and would mostly cancel, but not reliably: an anchor
    can change the canvas, and then it would earn credit for relieving damage it never caused.
    It ranks above everything below because being unreadable is not a price — as weighted
    overlap alone, `HEIGHT_PRICE` could outbid it, and did.

    **Glyph size** catches the trade the other terms cannot see: a wider canvas is scaled
    further down, so one callout can shrink every letter in the figure. Rounded to the half
    pixel so rounding noise does not outrank a real overlap.

    **Overlap** means OCCLUSION, not proximity, and not containment either: it is what the
    callout as painted actually hides, so a route is charged the length of line under the box
    and a container only the border it crosses (`js/measure.js` has the geometry, and why
    clipping gets a different box). Both proxies it replaced were charging emptiness — the
    middle of an L-shaped route, the interior of a container — and both were deciding
    placements: the architecture figure's chosen anchor covered 113px of line and a turn where
    three anchors that cover nothing scored worse.

    **Landmarks covered** is its own rank above this, not part of it: see the module
    docstring. It is only ever a count, so nothing here decides how much a corner is worth.

    It ties often, and the tie falls to the order of `spec.NEAR`. That is deliberate: when
    several anchors are equally free, landing them on the same side as each other is a better
    answer than any number here can produce.
    """
    clip = sum(c["clipVsCard"] for c in measurement["callouts"])
    overlap = sum(c["overlap"] for c in measurement["callouts"])
    landmarks = measurement.get("landmarks") or 0
    # What this ANCHOR hides, which is the total less the part the layout hides on its own.
    # `measure.js` banks both under one threshold, so the subtraction is exact rather than an
    # estimate — see there.
    hidden = (measurement.get("hiddenText") or 0) - (measurement.get("hiddenByLayout") or 0)
    fmin = measurement.get("fmin") or 0
    height = measurement.get("rend_h") or 0
    return ((clip * CLIP_PENALTY, hidden, -round(fmin * 2) / 2, landmarks,
             overlap + HEIGHT_PRICE * height),
            clip, overlap)


def _measure_candidates(spec, name, combos, theme, standalone=False, layout=None,
                        layers=None, edges=None):
    """Render and measure one anchor combination per entry in `combos`.

    `layout` is the `(direction, wrap)` to hold every candidate at — see `place`. Without it
    each candidate went through the whole layout search, so one could be measured portrait and
    the next landscape, and their overlap scores were then compared across different drawings.

    A candidate d2 refuses to compile is dropped rather than fatal — some anchor
    combinations are simply invalid for a given shape, and the remaining ones still give a
    usable answer.

    The candidates are compiled CONCURRENTLY, and this is the fan-out that made it worth having
    a helper for: placement is the biggest single cost in the renderer, and a two-callout
    diagram spends more d2 runs here than on the rest of a draw put together. They are
    independent by construction, each working on its own deep copy of the spec, so the only
    thing the order buys is being able to zip the results back against the anchors that
    produced them, which `parallel.each` preserves. See `lib/parallel.py` for why threads.

    Only one settling round is in here, so the fan-out is a round wide and the rounds are
    sequential. That is why fewer candidates do not always mean less waiting: on a small
    diagram with cores to spare, several short rounds can take longer than one wide one, and
    the saving is real only where the fan-out saturates the machine.
    """
    from .gates import GateError, size as size_gate   # local: gates import render

    direction, wrap = layout if layout else (None, None)
    jobs, kept, smallest = [], [], []

    def compile_one(job):
        index, anchors = job
        trial = _apply(spec, anchors)
        if direction:
            trial["direction"] = direction
        try:
            # Render the form this diagram will actually ship in. It matters: a standalone
            # image is padded far more generously (nothing else gives a callout's shadow
            # room), and that padding changes the canvas the clip is measured against — so
            # measuring the embedded form would pick anchors for a different geometry.
            if standalone:
                svg = render_mod.standalone(trial, name=f"{name}-p{index}", theme=theme,
                                            layers=layers, edges=edges)
            else:
                svg = render_mod.render(trial, name=f"{name}-p{index}",
                                        wrap_edges=wrap, layers=layers, edges=edges)
        except render_mod.RenderError:
            return None
        # The smallest glyph this candidate ends up with, so `_score` can see when an anchor
        # has grown the drawing and shrunk every letter in it. Measured from the SVG rather
        # than in the browser because the size gate already knows how — and it is the same
        # number the layout search ranks on, so the two agree about what "smaller" means.
        try:
            metrics = size_gate.analyse(svg)
            small = (metrics["fmin"], metrics["rend_h"])
        except GateError:
            small = (None, None)
        # Landmarks this candidate's callouts cover — corners and arrow ends. Read off the
        # SVG for the same reason as the glyph size: the module that knows how to read a
        # route already exists, and both are facts about the drawing's own coordinates rather
        # than about the page. `arrows.hides` is the same call `figure` makes to tell an
        # author what a note ended up covering, so the price and the report cannot disagree
        # about what a landmark is.
        landmarks = sum(hidden.turns + hidden.ends
                        for hidden in (arrows.hides(svg, box)
                                       for box in callout_mod.boxes(svg)))
        return index, anchors, svg, small, landmarks

    for outcome in parallel.each(compile_one, enumerate(combos)):
        if outcome is None:
            continue
        index, anchors, svg, small, landmarks = outcome
        jobs.append({"key": str(index),
                     "html": render_mod.harness_html(svg, theme=theme,
                                                     standalone=standalone)})
        smallest.append((*small, landmarks))
        kept.append(anchors)
    if not jobs:
        raise PlacementError(f"{name}: d2 compiled none of the {len(combos)} candidate "
                             "placements")
    try:
        results = browser_mod.measure(jobs)
    except browser_mod.BrowserError as exc:
        raise PlacementError(f"{name}: {exc}") from exc
    for measurement, (fmin, rend_h, landmarks) in zip(results, smallest):
        measurement["fmin"], measurement["rend_h"] = fmin, rend_h
        measurement["landmarks"] = landmarks
    return list(zip(kept, results))


def _report(chosen, clip, overlap, candidates, sweeps):
    """One entry per callout.

    `clip` and `overlap` are diagram-level sums, so every entry carries the same pair — and
    critically, the pair describing the FINAL placement. Recording each round's own cost
    instead was actively misleading: the first round measures a combination that later rounds
    go on to improve, so a finished, clip-free placement reported a clip and `unplaceable()`
    raised a false alarm.
    """
    return [{"index": i, "near": chosen[i], "clip": clip, "overlap": overlap,
             "candidates": candidates, "sweeps": sweeps}
            for i in range(len(chosen))]


def _sweep(spec, name, sites, theme, anchors, standalone=False, layout=None,
           layers=None, edges=None):
    """Settle one callout at a time, sweeping until a whole pass moves nothing.

    Starts from whatever the spec already pinned, so a hand-chosen anchor is a starting point
    rather than something thrown away.

    Two things make a pass that changes nothing cost no compiling at all, which is what lets
    "until it settles" be the ending condition rather than a fixed number of passes: every
    combination measured is kept, so a later pass re-offers a callout only the seven anchors it
    did not take; and a callout is skipped entirely while none of the others has moved since it
    settled.

    It terminates. A move is only ever taken to a strictly cheaper combination, or sideways to
    an equally cheap one earlier in `anchors`, so the pair (cost, position in `anchors`) falls
    on every move and no combination is ever current twice. `MAX_SWEEPS` backstops the one
    thing that would break that — a measurement that does not reproduce for the same drawing.
    """
    current = tuple(site.get("near", DEFAULT_NEAR) for site in sites)
    seen = {}
    candidates = moves = 0
    # When each callout was last settled, as the move count at the time. A callout only needs
    # looking at again once one of the others has moved — otherwise it is being offered the
    # same anchors on the same drawing and would reach the same answer. That is what keeps a
    # confirming pass from costing a full round per callout: the last one settled is always
    # already right, and on a diagram that settles in one pass so is every other.
    settled = {}
    for sweeps in range(1, MAX_SWEEPS + 1):
        moved = False
        for i in range(len(sites)):
            if settled.get(i) == moves:
                continue
            combos = [current[:i] + (anchor,) + current[i + 1:] for anchor in anchors]
            fresh = [combo for combo in combos if combo not in seen]
            if fresh:
                measured = _measure_candidates(spec, f"{name}-{sweeps}-{i}", fresh, theme,
                                               standalone, layout, layers, edges)
                candidates += len(fresh)
                seen.update((combo, _score(measurement)) for combo, measurement in measured)
            # Anything d2 refused is simply not on offer. The tie-break is the order of
            # `anchors` — `min` keeps the first of an equal set — and it is both what the
            # module docstring's last paragraph promises and what makes a sideways move
            # settle: once a callout is on the earliest of its equally-cheap anchors, it
            # stays there and the search cannot cycle between them.
            offered = [combo for combo in combos if combo in seen]
            best = min(offered, key=lambda combo: seen[combo][0])
            if best != current:
                current, moved = best, True
                moves += 1
            settled[i] = moves
        if not moved:
            break
    _, clip, overlap = seen[current]
    return current, clip, overlap, candidates, sweeps


def place(spec, name="diagram", theme="light", anchors=NEAR, standalone=False, pinned=None):
    """Return `(spec_with_anchors, report)`.

    One search for every callout count — see `_sweep` and the module docstring. Picking a
    strategy by the count is what made two callouts the most expensive number a diagram could
    have, dearer than three or four.

    `report` says what was chosen and what it cost, so a caller can surface "this callout could
    not be placed without clipping" rather than silently shipping the least-bad option. A spec
    with no notes costs nothing — no d2 run, no browser.
    """
    validate(spec)
    sites = note_sites(spec)
    if not sites:
        return spec, []

    # One layout, held still for the whole anchor search. Two reasons, and the first is
    # correctness: rendering each candidate through the full layout search let one be measured
    # portrait and the next landscape, so their overlap scores were compared across different
    # drawings, and the winning anchor could have been chosen against a shape the final diagram
    # does not have. The anchor genuinely can flip the layout — `center-left` on the reference
    # ER gives 949x207 where the others give 862x257 — which is exactly why this must be fixed
    # once rather than re-decided per candidate.
    #
    # The second reason is cost. Every candidate is a compile of the whole graph, and each one
    # was running a ~4-compile layout search of its own at ~330ms per ELK compile.
    #
    # The standalone target has no layout to choose — its direction is a per-kind default in
    # `d2.DIRECTION` and it does not wrap — but it does have a spacing to settle, and for the
    # same two reasons: candidates measured at different spacings are candidates measured on
    # different drawings, and escalating inside each one would put a browser launch inside a
    # loop that exists to need only one.
    #
    # `pinned` lets a caller that has already decided hand it over, which `figure.draw` does —
    # it needs the same pair for the FINAL render, and working it out twice was both the cost
    # and a correctness hole. See `render.choose_drawing`.
    layout, layers, edges = (pinned if pinned is not None
                             else render_mod.choose_drawing(spec, name, theme, standalone))
    chosen, clip, overlap, candidates, sweeps = _sweep(spec, name, sites, theme, anchors,
                                                       standalone, layout, layers, edges)
    return _apply(spec, chosen), _report(chosen, clip, overlap, candidates, sweeps)


def unplaceable(report):
    """The entries in a placement report that could not avoid clipping.

    Surfacing this is the point: the remedy is to shorten the note text, and only the author
    can do that.
    """
    return [entry for entry in report if entry["clip"] > 0]
