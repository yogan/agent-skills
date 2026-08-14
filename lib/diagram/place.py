"""Choosing where each callout sits, by measuring the alternatives.

d2 cannot do this. `tooltip.near` takes exactly one of eight fixed anchors relative to its
shape, d2 does no overlap avoidance, and it does not even grow the canvas to fit the
callout. There is no "let d2 decide" mode. So the renderer decides: render every anchor,
measure the result in a browser, keep the best one.

What is being optimised is READABILITY, and the three terms are one idea at three scales —
text cut off, text shrunk, text covered:

  1. **Clipping is disqualifying, not a trade-off.** A cut-off callout is strictly worse than
     one that overlaps something, so it is weighted at 1e6 — the rest only ever breaks ties
     among candidates that are equally (un)clipped. An earlier version weighted it at 1e3
     and happily traded a 31px clip for a bit less overlap.
  2. **Then the smallest glyph in the finished drawing.** An anchor can grow the canvas, and a
     wider canvas is scaled further down in the content column, so a callout can shrink every
     letter in the diagram. Blind to that, the search moved a callout on the reference ER to
     `center-left` and took 12.6px text down to 11.5px everywhere to buy a 22% cut in that one
     callout's overlap.
  3. **Then overlap and page height, traded against each other** at `HEIGHT_PRICE`. Overlap is
     weighted by what it damages (see `browser.OVERLAP_WEIGHTS`): burying a label costs 6, an
     edge 2, the body of a shape 0.3, and anything covering half the canvas is ignored as a
     container. Unweighted, the search optimises total area and cheerfully hides a label to
     stay off a big rectangle. Height belongs in the same term because an anchor that hangs a
     callout off the bottom makes the figure taller without shrinking any text, so neither
     glyph size nor overlap can see it.

Greedy per-callout is not always enough — two callouts can each look fine alone and only fit
in a particular combination — so a diagram with few enough of them gets an exhaustive search
instead. Eight anchors means 8^n candidates, which is affordable at n=2 (64) and not at n=3
(512), hence `JOINT_MAX`.

Where no anchor is clip-free, the fix is editorial rather than geometric: shorten the note.
A narrower callout fits where a wide one cannot, which is why the content limits cap a note
at a few words.
"""
import copy
import itertools

from . import browser as browser_mod
from . import render as render_mod
from .spec import NEAR, validate

# Clipping dominates overlap absolutely; see the module docstring.
CLIP_PENALTY = 1e6

# Above this many callouts, search greedily instead of exhaustively.
JOINT_MAX = 2

# What one pixel of extra page height is worth in weighted-overlap units. Overlap and height
# have to be traded, not ordered: putting height first made the search bury text to save 50px
# (an anchor covering 15228 units beat one covering 3082), and putting overlap first made it
# spend 50px to save an overlap difference of 315, which is noise on totals in the thousands.
#
# Bucketing both was tried and is subtly wrong — two values closer together than the bucket
# still land either side of a boundary, which is exactly how the reference ER came to prefer a
# callout hanging off the bottom for a 490-unit overlap saving.
#
# So: one budget, one exchange rate. 20 puts 47px of height (the ER case) at 940 units, which
# outranks that 490 saving, while the 12146-unit difference in the bury-the-text case would
# need 600px of height to justify and so never happens.
HEIGHT_PRICE = 20

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
    """Cost of one candidate, as a tuple compared left to right: clipped, then how small the
    text ended up, then how much the callout covers.

    What is being optimised is READABILITY, and the three terms are that one idea at three
    scales — text cut off, text shrunk, text covered.

    **Clipping** is measured against the CARD, not the svg box, and the difference is not
    academic. `measure.js` reports both, and this used to take the svg-box number on the
    reasoning that d2 reserves no canvas space for a callout so any overflow is the wrong
    anchor. But the page gives a callout the card's padding to bleed into — `HOST_CSS` sets
    `overflow: visible` precisely so a shadow can — and the clipping *gate*, which is what
    decides whether output is acceptable, holds callouts to the card for that reason. Scoring
    on the stricter boundary made the search reject anchors that clip nothing:

      the reference state machine, portrait, `center-left` — 16px past the svg box, 0px past
      the card, and HALF the overlap of the anchor that won (10488 vs 20104). It put the
      callout out in the empty left margin; the winner sat it on top of three arrows.

    **Glyph size** is here because an anchor can grow the drawing, and a wider drawing is
    scaled further down inside the content column — so a callout can make EVERY letter in the
    diagram smaller. Without this term the search took that trade blind: on the reference ER
    it moved a callout to `center-left`, spending 12.6px text for 11.5px across the whole
    figure, to buy a 22% reduction in one callout's overlap. Rounded to the nearest half
    pixel, so a rounding-level difference does not outrank a real overlap.

    **Overlap and height share one term**, priced against each other — see `HEIGHT_PRICE`.
    They cannot be ordered: whichever goes first, the other becomes free.
    """
    clip = sum(c["clipVsCard"] for c in measurement["callouts"])
    overlap = sum(c["overlap"] for c in measurement["callouts"])
    fmin = measurement.get("fmin") or 0
    height = measurement.get("rend_h") or 0
    return ((clip * CLIP_PENALTY, -round(fmin * 2) / 2, overlap + HEIGHT_PRICE * height),
            clip, overlap)


def _measure_candidates(spec, name, combos, theme, standalone=False, layout=None,
                        layers=None):
    """Render and measure one anchor combination per entry in `combos`.

    `layout` is the `(direction, wrap)` to hold every candidate at — see `place`. Without it
    each candidate went through the whole layout search, so one could be measured portrait and
    the next landscape, and their overlap scores were then compared across different drawings.

    A candidate d2 refuses to compile is dropped rather than fatal — some anchor
    combinations are simply invalid for a given shape, and the remaining ones still give a
    usable answer.
    """
    from .gates import GateError, size as size_gate   # local: gates import render

    direction, wrap = layout if layout else (None, None)
    jobs, kept, smallest = [], [], []
    for index, anchors in enumerate(combos):
        trial = _apply(spec, anchors)
        if direction:
            trial["direction"] = direction
        try:
            # Render the form this diagram will actually ship in. It matters: a standalone
            # image is padded far more generously (nothing else gives a callout's shadow
            # room), and that padding changes the canvas the clip is measured against — so
            # measuring the embedded form would pick anchors for a different geometry.
            if standalone:
                svg = render_mod.standalone(trial, name=f"{name}-p{index}", theme=theme)
            else:
                svg = render_mod.render(trial, name=f"{name}-p{index}",
                                        wrap_edges=wrap, layers=layers)
        except render_mod.RenderError:
            continue
        jobs.append({"key": str(index),
                     "html": render_mod.harness_html(svg, theme=theme,
                                                     standalone=standalone)})
        # The smallest glyph this candidate ends up with, so `_score` can see when an anchor
        # has grown the drawing and shrunk every letter in it. Measured from the SVG rather
        # than in the browser because the size gate already knows how — and it is the same
        # number the layout search ranks on, so the two agree about what "smaller" means.
        try:
            metrics = size_gate.analyse(svg)
            smallest.append((metrics["fmin"], metrics["rend_h"]))
        except GateError:
            smallest.append((None, None))
        kept.append(anchors)
    if not jobs:
        raise PlacementError(f"{name}: d2 compiled none of the {len(combos)} candidate "
                             "placements")
    try:
        results = browser_mod.measure(jobs)
    except browser_mod.BrowserError as exc:
        raise PlacementError(f"{name}: {exc}") from exc
    for measurement, (fmin, rend_h) in zip(results, smallest):
        measurement["fmin"], measurement["rend_h"] = fmin, rend_h
    return list(zip(kept, results))


def _best(measured):
    best = None
    for anchors, measurement in measured:
        total, clip, overlap = _score(measurement)
        if best is None or total < best[0]:
            best = (total, anchors, clip, overlap)
    return best


def _report(chosen, clip, overlap, strategy, candidates):
    """One entry per callout.

    `clip` and `overlap` are diagram-level sums, so every entry carries the same pair — and
    critically, the pair describing the FINAL placement. Recording each greedy round's own
    cost instead was actively misleading: the first round measures a combination that later
    rounds go on to improve, so a finished, clip-free placement reported a clip and
    `unplaceable()` raised a false alarm.
    """
    return [{"index": i, "near": chosen[i], "clip": clip, "overlap": overlap,
             "strategy": strategy, "candidates": candidates}
            for i in range(len(chosen))]


def _greedy(spec, name, sites, theme, anchors, standalone=False, layout=None,
            layers=None):
    """Settle one callout at a time, holding the others where they are.

    Starts from whatever the spec already pinned, so a hand-chosen anchor is a starting
    point rather than something thrown away.
    """
    current = [site.get("near", DEFAULT_NEAR) for site in sites]
    clip = overlap = 0
    candidates = 0
    for i in range(len(sites)):
        combos = []
        for anchor in anchors:
            trial = list(current)
            trial[i] = anchor
            combos.append(tuple(trial))
        measured = _measure_candidates(spec, f"{name}-{i}", combos, theme,
                                       standalone, layout, layers)
        _, chosen, clip, overlap = _best(measured)
        current = list(chosen)
        candidates += len(combos)
    return tuple(current), clip, overlap, candidates


def _joint(spec, name, sites, theme, anchors, standalone=False, layout=None,
           layers=None):
    """Exhaustive: every anchor for every callout. 8^n, so only for small n."""
    combos = list(itertools.product(anchors, repeat=len(sites)))
    measured = _measure_candidates(spec, name, combos, theme, standalone, layout,
                                   layers)
    _, chosen, clip, overlap = _best(measured)
    return chosen, clip, overlap, len(combos)


def place(spec, name="diagram", theme="light", joint_max=JOINT_MAX, anchors=NEAR,
          standalone=False):
    """Return `(spec_with_anchors, report)`.

    Exhaustive whenever the callout count can afford it (`joint_max`), greedy above that.

    This used to run greedy first and escalate only when greedy left something CLIPPED, on
    the measured claim that greedy reached the same anchors anyway. That claim was true only
    because clipping used to be common: greedy clipped, so it escalated, so it got the right
    answer for the wrong reason. The moment `_score` started measuring against the card (see
    there) far fewer candidates clipped, nothing escalated, and greedy's real weakness showed
    — on the reference ER diagram it returned 5483 total overlap where the joint search finds
    3123, because settling one callout at a time cannot see a pair that only works together.

    So the trigger is affordability, not clipping. For one callout the two searches are the
    same 8 candidates, so nothing is lost; for two it is 64 measurements instead of 16, which
    is the price of not shipping a placement 75% worse than the one available.

    `report` describes what was chosen and what it cost, so a caller can surface "this
    callout could not be placed without clipping" instead of silently shipping the
    least-bad option. A spec with no notes is returned untouched and costs nothing — no d2
    run, no browser.
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
    # The second reason is cost. A two-callout diagram is 64 candidates, each of which was
    # running a ~4-compile search at ~330ms per ELK compile.
    chosen = None if standalone else render_mod.choose_layout(spec, name)
    layout, layers = chosen if chosen else (None, None)
    search = _joint if len(sites) <= joint_max else _greedy
    chosen, clip, overlap, candidates = search(spec, name, sites, theme, anchors,
                                               standalone, layout, layers)
    strategy = "joint" if search is _joint else "greedy"
    return _apply(spec, chosen), _report(chosen, clip, overlap, strategy, candidates)


def unplaceable(report):
    """The entries in a placement report that could not avoid clipping.

    Surfacing this is the point: the remedy is to shorten the note text, and only the author
    can do that.
    """
    return [entry for entry in report if entry["clip"] > 0]
