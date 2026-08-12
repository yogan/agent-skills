"""Choosing where each callout sits, by measuring the alternatives.

d2 cannot do this. `tooltip.near` takes exactly one of eight fixed anchors relative to its
shape, d2 does no overlap avoidance, and it does not even grow the canvas to fit the
callout. There is no "let d2 decide" mode. So the renderer decides: render every anchor,
measure the result in a browser, keep the best one.

Scoring, in the order that matters:

  1. **Clipping is disqualifying, not a trade-off.** A cut-off callout is strictly worse than
     one that overlaps something, so it is weighted at 1e6 — overlap only ever breaks ties
     among candidates that are equally (un)clipped. An earlier version weighted it at 1e3
     and happily traded a 31px clip for a bit less overlap.
  2. **Overlap is weighted by what it damages** (see `browser.OVERLAP_WEIGHTS`): burying a
     label costs 6, an edge 2, the body of a shape 0.3, and anything covering half the
     canvas is ignored as a container. Unweighted, the search optimises total area and
     cheerfully hides a label to stay off a big rectangle.

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

    kind = spec.get("kind")
    walk_nodes(spec.get("nodes"))
    walk_nodes(spec.get("participants"))
    walk_nodes(spec.get("states"))
    for key in ("tables", "classes"):
        for item in spec.get(key) or []:
            if "note" in item:
                found.append(item)
    if kind == "steps":
        for step in spec.get("steps") or []:
            walk_nodes(step.get("add_nodes"))
    return found


def _apply(spec, anchors):
    """A deep copy of `spec` with the i-th note's `near` set to `anchors[i]`."""
    trial = copy.deepcopy(spec)
    for site, anchor in zip(note_sites(trial), anchors):
        site["near"] = anchor
    return trial


def _score(measurement):
    """Total cost of one candidate: clipping first, then weighted overlap."""
    clip = sum(c["clip"] for c in measurement["callouts"])
    overlap = sum(c["overlap"] for c in measurement["callouts"])
    return clip * CLIP_PENALTY + overlap, clip, overlap


def _measure_candidates(spec, name, combos, theme, standalone=False):
    """Render and measure one anchor combination per entry in `combos`.

    A candidate d2 refuses to compile is dropped rather than fatal — some anchor
    combinations are simply invalid for a given shape, and the remaining ones still give a
    usable answer.
    """
    jobs, kept = [], []
    for index, anchors in enumerate(combos):
        trial = _apply(spec, anchors)
        try:
            # Render the form this diagram will actually ship in. It matters: a standalone
            # image is padded far more generously (nothing else gives a callout's shadow
            # room), and that padding changes the canvas the clip is measured against — so
            # measuring the embedded form would pick anchors for a different geometry.
            if standalone:
                svg = render_mod.standalone(trial, name=f"{name}-p{index}", theme=theme)
            else:
                svg = render_mod.render(trial, name=f"{name}-p{index}")
        except render_mod.RenderError:
            continue
        jobs.append({"key": str(index),
                     "html": render_mod.harness_html(svg, theme=theme,
                                                     standalone=standalone)})
        kept.append(anchors)
    if not jobs:
        raise PlacementError(f"{name}: d2 compiled none of the {len(combos)} candidate "
                             "placements")
    try:
        results = browser_mod.measure(jobs)
    except browser_mod.BrowserError as exc:
        raise PlacementError(f"{name}: {exc}") from exc
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


def _greedy(spec, name, sites, theme, anchors, standalone=False):
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
        measured = _measure_candidates(spec, f"{name}-{i}", combos, theme, standalone)
        _, chosen, clip, overlap = _best(measured)
        current = list(chosen)
        candidates += len(combos)
    return tuple(current), clip, overlap, candidates


def _joint(spec, name, sites, theme, anchors, standalone=False):
    """Exhaustive: every anchor for every callout. 8^n, so only for small n."""
    combos = list(itertools.product(anchors, repeat=len(sites)))
    measured = _measure_candidates(spec, name, combos, theme, standalone)
    _, chosen, clip, overlap = _best(measured)
    return chosen, clip, overlap, len(combos)


def place(spec, name="diagram", theme="light", joint_max=JOINT_MAX, anchors=NEAR,
          standalone=False):
    """Return `(spec_with_anchors, report)`.

    Greedy first, escalating to an exhaustive search only if greedy leaves a callout
    clipped and the count is small enough to afford it. Measured on the reference corpus:
    greedy reaches the same anchors as the exhaustive search on every diagram in about a
    third of the time, so paying for 8^n up front buys nothing most of the time — but two
    callouts genuinely can each look fine alone and only fit in one combination, so the
    fallback has to exist.

    `report` describes what was chosen and what it cost, so a caller can surface "this
    callout could not be placed without clipping" instead of silently shipping the
    least-bad option. A spec with no notes is returned untouched and costs nothing — no d2
    run, no browser.
    """
    validate(spec)
    sites = note_sites(spec)
    if not sites:
        return spec, []

    chosen, clip, overlap, candidates = _greedy(spec, name, sites, theme, anchors,
                                               standalone)
    strategy = "greedy"
    if clip > 0 and len(sites) <= joint_max:
        joint = _joint(spec, name, sites, theme, anchors, standalone)
        if joint[1] < clip:
            chosen, clip, overlap, extra = joint
            strategy = "joint"
            candidates += extra

    return _apply(spec, chosen), _report(chosen, clip, overlap, strategy, candidates)


def unplaceable(report):
    """The entries in a placement report that could not avoid clipping.

    Surfacing this is the point: the remedy is to shorten the note text, and only the author
    can do that.
    """
    return [entry for entry in report if entry["clip"] > 0]
