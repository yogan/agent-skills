"""Specs in, finished figures out. The one entry point a caller should need.

Everything below this line — which gates apply to which target, escalating the layer spacing
until no text is unreadable, measuring eight anchors per callout in a browser, batching those
measurements so Chrome starts once per document — is this module's business and nobody else's.
A caller states what it wants a picture FOR and gets back a picture plus a list of what is
wrong with it.

That boundary was not there at first, and the shape of its absence is worth recording. Both
consumers spelled the pipeline out by hand:

    validate -> content_warnings -> place.place -> render.render -> size, contrast, theming

so both named the gate modules, and the explainer — which has no business knowing that
contrast is measured at all — imported four of them plus `GateError`. The two copies then
drifted, in the way two copies do: `explain-diff` ran three gates and not the fourth, and the
fourth is the only one that can see a callout sitting on top of a word. Nothing was visibly
broken for as long as that lasted, because the reference corpus passes the gate that was not
being run.

`target` is the only thing a caller says, because it is the only thing a caller knows:

  * `"embed"` — a themeable SVG for a host page that ships `render.page_css()`. Colours are
    `var(--d-…)` references and follow the page's own light/dark toggle; the drawing is sized
    to be scaled into a content column.
  * `"file"`  — a standalone image, colours baked to one theme, canvas painted, CSS carried
    inside the file. Shown at natural size, so it is gated differently: the page-width and
    viewport-height rules are statements about a host page and do not apply to a file.
"""
from collections import namedtuple

from . import browser as browser_mod
from . import callout as callout_mod
from . import place as place_mod
from . import render as render_mod
from .gates import GateError
from .gates import clipping as _clipping
from .gates import contrast as _contrast
from .gates import size as _size
from .gates import theming as _theming
from .spec import SpecError, content_warnings, validate

TARGETS = ("embed", "file")


class Figure(namedtuple("Figure", "name svg results placement problems advice blocked")):
    """One drawn diagram and everything known about it.

    Three views of what is wrong, because two callers want it two ways:

      * `results`   — the gates' own verdicts, for a caller whose product is a report.
      * `placement` — a callout no anchor could fit. Not a gate verdict: the remedy is
                      editorial, so a CLI says it rather than failing on it.
      * `problems`  — both, flattened and prefixed with the figure's name, which is why the
                      explainer needs neither the gate modules nor `Result`.

    `advice` is kept apart because it is about the spec as AUTHORED, not the drawing; one list
    would make "wide" read as loudly as "clipped". `blocked` is a gate that could not run,
    which is never silence and never a pass — see gates/__init__.py.
    """

    @property
    def ok(self):
        return not self.problems and not self.blocked


def draw(specs, target="embed", theme="dark", place_callouts=True, gates=True, binary="d2"):
    """Render every spec in `specs` (a `{name: spec}` mapping) and return a list of `Figure`.

    Takes a mapping rather than one spec because the browser work batches across a document:
    the clipping gate measures every figure in a single Chrome launch, so a six-figure article
    pays for one instead of six. A caller with one diagram passes a mapping of one and loses
    nothing.

    Raises `SpecError` for a spec that is not valid, because that is the caller's mistake and
    there is no picture to hand back. Everything else — an unplaceable callout, a failed gate,
    a browser that will not start — comes back inside the `Figure`, since a flawed diagram
    alongside a precise list of what is wrong with it beats no diagram at all.
    """
    if target not in TARGETS:
        raise ValueError(f"target must be one of {TARGETS}, not {target!r}")
    standalone = target == "file"

    # One launch for every note in the document, before any of them is drawn. The width a note
    # renders at depends on the string and nothing else, so measuring it here means the anchor
    # search — 64 renders of the same spec — never pays for it again. See `callout.prime`.
    callout_mod.prime([site["note"] for spec in specs.values()
                       for site in place_mod.note_sites(spec)])

    drawn = []
    for name, spec in specs.items():
        # Before placement rewrites the spec's `near` values: the advice is about what the
        # author wrote, not about what the search made of it.
        validate(spec)
        _reject_internal_keys(name, spec)
        advice = [f"{name}: {line}" for line in content_warnings(spec)]
        # Which drawing this is — direction, edge wrap, layer spacing — decided once and then
        # used for BOTH the anchor search and the render that ships. Deciding it again for the
        # render would re-run the search on a spec that by then has its `near` values, and a
        # callout changes the geometry the search ranks on. See `render.choose_drawing`.
        pinned = render_mod.choose_drawing(spec, name, theme, standalone, binary)

        placement = []
        if place_callouts:
            spec, placement = _place(spec, name, theme, standalone, pinned)
        svg = _render(spec, name, theme, standalone, pinned, binary)

        results, blocked = ([], [])
        if gates:
            results, blocked = _static_gates(svg, name, standalone, theme)
        drawn.append(Figure(name, svg, results, placement, [], advice, blocked))

    if gates:
        drawn = _clipping_gate(drawn, theme, standalone)
    # `problems` last, so it is the union of everything above however the gates went.
    return [f._replace(problems=f.placement
                       + [f"{f.name}: {p}" for r in f.results for p in r.problems])
            for f in drawn]


# Keys the RENDERER sets on a copy of a spec, which an author may therefore not set on the
# original. They cannot be rejected in `spec.validate`, because `d2.emit` validates on every
# call and by then the search has already set them — so the check belongs at the door authors
# come in through, which is this module.
INTERNAL_KEYS = {
    "direction": "the renderer draws an embedded figure both ways, measures each, and keeps "
                 "the one that stays legible. Pinning it turns that off, and takes the "
                 "spacing escalation that keeps text readable with it: the reference ER "
                 "pinned to its own measured direction comes out 862x257 with a cardinality "
                 "sitting on a table, where the same spec unpinned comes out 892x257 and "
                 "clean. A standalone image is laid out wide by default; a sequence has its "
                 "own engine and ignores this entirely",
}


def _reject_internal_keys(name, spec):
    """Refuse a spec that sets something the renderer decides.

    Loud rather than ignored: an author who wrote one of these meant something by it, and
    silently dropping it would leave them believing it worked. Every document already said to
    leave `direction` out — "nearly always a mistake" — and nothing in the corpus or in any
    real spec ever set one, so what the field actually bought was a way to switch off the
    check that keeps text readable.
    """
    for key, why in INTERNAL_KEYS.items():
        if key in spec:
            raise SpecError(f"{name}: `{key}` is not yours to set — {why}. Remove it.")


def _render(spec, name, theme, standalone, pinned, binary):
    """The finished SVG, held to the drawing `choose_drawing` picked.

    Embedded, pinning means putting the chosen direction on the spec and passing the wrap —
    exactly what `place._measure_candidates` does to each candidate, so the figure that ships
    is the figure the anchors were measured on. `layout` is None where there was nothing to
    choose (a sequence, or a spec that pins its own `direction`), and then the renderer's own
    default path is already the right one.
    """
    layout, layers = pinned
    if standalone:
        return render_mod.standalone(spec, name=name, theme=theme, binary=binary,
                                     layers=layers)
    if not layout:
        return render_mod.render(spec, name=name, binary=binary)
    direction, wrap = layout
    return render_mod.render(dict(spec, direction=direction), name=name, wrap_edges=wrap,
                             layers=layers, binary=binary)


def _place(spec, name, theme, standalone, pinned):
    """Measure the anchors, and report a callout no anchor can fit.

    A placement failure is not fatal: the spec's own `near` values still render. It has to be
    said out loud, though, because the callouts are then positioned by guess — d2 reserves no
    canvas space for one, so an unmeasured anchor may well be clipped.
    """
    if not browser_mod.available():
        # Said only when there is something to place. A diagram with no callouts needs no
        # browser and should not be told it is missing one.
        if not place_mod.note_sites(spec):
            return spec, []
        return spec, [f"{name}: no browser, so its callouts were not placed by measurement "
                      f"({'; '.join(browser_mod.requirements())}) — d2 reserves no canvas "
                      "space for a callout, so one may well be clipped"]
    try:
        placed, report = place_mod.place(spec, name=name, theme=theme,
                                         standalone=standalone, pinned=pinned)
    except place_mod.PlacementError as exc:
        return spec, [f"{name}: callouts not placed by measurement ({exc}) — the anchors in "
                      "the spec are being trusted instead, and one may be clipped"]
    return placed, [
        f"{name}: callout {entry['index']} still clips by {entry['clip']:.0f}px at "
        f"{entry['near']} — no anchor fits, so shorten its note text"
        for entry in place_mod.unplaceable(report)]


def _static_gates(svg, name, standalone, theme):
    """The gates that read the SVG and need no browser.

    The two targets get genuinely different sets, because most of these are statements about a
    host page. A file drops the page-width, viewport-height and glyph-vs-prose rules — applying
    them to an image is what makes a wide diagram "fail" and pushes an author into splitting one
    that was perfectly legible — checks contrast for the one theme that got baked in, and skips
    theming entirely, there being no vars left to follow a toggle. `render.standalone` verifies
    mappability itself, at the only moment it still means anything.
    """
    results, blocked = [], []

    def run(label, fn):
        try:
            results.append(fn())
        except GateError as exc:
            blocked.append(f"{name}: {label} could not run — {exc}")

    run("size", lambda: _size.check(svg, name, standalone=standalone))
    # Contrast needs the scale, because WCAG's 3:1 allowance is for text >=18.66px AS
    # RENDERED: measured at authored size, 20px text in a figure scaled to 0.85 renders at
    # 17px and still claims it. Falling back to 1.0 when the drawing cannot be measured keeps
    # the two gates independent — the size gate is already reporting why, and one unmeasurable
    # SVG should not take a second gate down with it.
    try:
        scale = 1.0 if standalone else _size.analyse(svg)["scale"]
    except GateError:
        scale = 1.0
    run("contrast", lambda: _contrast.check(
        svg, name, scale=scale, themes=(theme,) if standalone else ("light", "dark")))
    if not standalone:
        run("theming", lambda: _theming.check(svg, name))
    return results, blocked


def _clipping_gate(drawn, theme, standalone):
    """The one gate that needs a browser, run over every figure at once.

    Separated from the rest for cost, not for category: launching Chrome dominates measuring
    one more page. It is also the only gate that can see a callout cut off or a label buried
    under one, because a callout's text is HTML in a `<foreignObject>` and a CSS drop-shadow's
    spread is invisible to every static check — so when it cannot run, that is reported as
    loudly as a failure.
    """
    if not drawn:
        return drawn
    try:
        results = _clipping.check_many({f.name: f.svg for f in drawn}, theme=theme,
                                       standalone=standalone)
    except GateError as exc:
        note = (f"clipping gate could not run — {exc}. It is the only gate that sees a callout "
                "cut off or a label buried under one, so its absence is not a clean bill of "
                "health")
        return [f._replace(blocked=f.blocked + [note]) for f in drawn]
    by_name = {r.name: r for r in results}
    return [f._replace(results=f.results + ([by_name[f.name]] if f.name in by_name else []))
            for f in drawn]
