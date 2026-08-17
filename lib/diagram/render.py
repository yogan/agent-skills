"""Run d2, then fix up its SVG so a host page can theme and size it.

Four of the post-processing steps below are not polish — the SVG is wrong without them,
in ways that look like a styling opinion rather than a bug:

  1. **ids are global.** d2 self-scopes its CSS as `.d2-<hash>`, but marker and pattern
     ids are not namespaced, so two diagrams on one page collide and the second one's
     arrowheads come from the first.
  2. **the callout is unstyleable.** d2 paints a `tooltip.near` box plain
     `fill="white" stroke="#DEE1EB"` and exposes no hook for it, so its colours are
     retargeted by matching that exact attribute pair.
  3. **there is no intrinsic size**, only a viewBox — so `width:auto` lets the browser
     *enlarge* the drawing and every glyph with it. See `pin_intrinsic`.
  4. **colour literals are baked in**, so they cannot follow a theme toggle until they
     are rewritten to CSS vars (`palette.to_vars`).

And one thing the SVG cannot fix about itself: `HOST_CSS` is required for callout text to
render inside its box at all. An SVG produced here, opened on its own in a browser, still
shows clipped callouts. That coupling is real and documented rather than designed away —
d2 gives the callout no styling surface, so somebody has to own it, and the page is the
only party that can.
"""
import functools
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile

from .. import parallel
from . import arrows
from . import browser
from . import callout
from . import compact
from . import d2 as d2mod
from . import edgelabel
from . import palette
from . import route

# d2 version this recipe was measured against. Several behaviours it relies on are
# undocumented (the table property coupling, the missing intrinsic size, the 1.3x header
# scale), so a version bump is a reason to re-run the gates, not a routine upgrade.
PINNED_VERSION = "0.8.1"

# d2's own callout paint, matched verbatim. Plain white with a grey hairline: invisible
# against a dark page and too timid against a light one. Tagging it `d2-callout` at the
# same time is what gives HOST_CSS something to select — d2 offers no class of its own.
CALLOUT_ATTRS = palette.CALLOUT_PAINT
CALLOUT_REPLACEMENT = ('class="d2-callout" fill="var(--d-callout-bg)" '
                       'stroke="var(--d-callout-br)"')

# What the DRAWING needs, in whatever container it is put. Not optional styling.
#
# Scoped rather than global, and emitted once per container: a page with two places a diagram
# can appear — an inline card and a click-to-enlarge overlay, say — has to ship this for both.
# A container that never got it draws a callout as an empty box, which reads as a rendering
# fault in the diagram and is a missing rule on the page.
CONTENT_CSS = """\
/* d2 emits a callout as <foreignObject height="24"><div class="md"><p>…</p></div>, and
   ships no paragraph reset. The browser default `p{{margin:1em 0}}` then pushes the text
   out of its 24px box and only the top sliver of the glyphs shows — which reads as a
   colour or contrast problem and is actually a clip. The missing font-family is the
   second half of the same bug: the div otherwise inherits the page's body font, whose
   metrics differ from the font d2 measured the box with, so the text re-wraps. */
{scope} foreignObject{{overflow:visible}}
{scope} foreignObject .md{{display:flex;align-items:center;height:100%;
  font-family:system-ui,-apple-system,'Segoe UI',sans-serif}}
{scope} foreignObject .md p{{margin:0;line-height:1.2;font-size:12.5px;white-space:nowrap}}
/* A callout is an annotation ABOUT the drawing, so it should read as sitting above it.
   Depth does that; a dashed outline does not — d2 welds a solid pointer triangle onto the
   box and a dashed edge fights it at the junction. Keep fill-opacity >= 0.94: the contrast
   gate measures the solid colour, so heavier transparency drifts away from what it
   verified. A plain shadow vanishes on a dark page, hence the accent glow in dark mode. */
{scope} .d2-callout{{fill-opacity:.95;filter:drop-shadow(0 2px 5px rgba(0,0,0,.28))}}
/* The shadow paints outside the viewBox and the <svg> clips it by default, so a callout
   flush with the edge lost its glow. Diagram geometry is kept inside the viewBox by the
   placement pass; this only frees the shadow into the container's padding. */
{scope} svg{{overflow:visible}}
"""

# And what fitting a drawing into a content column needs, which is the half that must NOT be
# emitted for a container that sizes the drawing itself. `max-width:100%` there caps the
# drawing at the container's width and `height:auto` then overrides the height it was given,
# so an overlay meant to show the figure large gets it at its inline size instead.
FIT_CSS = """\
{scope} svg{{max-width:100%;height:auto}}
"""

# The one theme-dependent rule, kept apart so a page can emit it under whatever selector its
# dark mode actually uses. A plain drop shadow disappears against a dark background, so dark
# mode gets an accent glow as well.
CALLOUT_DARK_CSS = """\
{dark} {scope} .d2-callout{{fill-opacity:.94;
  filter:drop-shadow(0 0 5px rgba(224,137,90,.40)) drop-shadow(0 2px 4px rgba(0,0,0,.55))}}
"""


def content_css(scope=".diagram"):
    """`CONTENT_CSS` bound to one container selector."""
    return CONTENT_CSS.format(scope=scope)


def fit_css(scope=".diagram"):
    """`FIT_CSS` bound to one container selector."""
    return FIT_CSS.format(scope=scope)


def callout_dark_css(dark_selector="[data-theme=dark]", scope=".diagram"):
    """`CALLOUT_DARK_CSS` bound to one dark-mode selector and one container."""
    return CALLOUT_DARK_CSS.format(dark=dark_selector, scope=scope)


# Everything a `.diagram` card needs, which is both halves. Kept as the name every existing
# host uses, so a page with one container carries on saying so in one line.
HOST_CSS = content_css() + fit_css()


class RenderError(RuntimeError):
    """d2 is missing, the wrong version, or refused to compile the source."""


@functools.lru_cache(maxsize=8)
def d2_version(binary="d2"):
    """The installed d2's version string, or None if it is not on PATH.

    Cached: a render pass asks for this once per diagram, and shelling out to
    `d2 --version` a dozen times to learn the same answer is pure overhead.
    """
    if not shutil.which(binary):
        return None
    proc = subprocess.run([binary, "--version"], capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip().lstrip("v")


def check_toolchain(binary="d2"):
    """Return a list of problems with the local toolchain; empty means usable.

    Reported rather than raised so a caller can show every problem at once, and so a
    version *mismatch* stays a warning: an upgraded d2 usually still works, it just means
    the gates are now the only thing standing between you and the undocumented behaviour
    this recipe depends on.
    """
    version = d2_version(binary)
    if version is None:
        return [f"d2 not found on PATH (install: brew install d2, pin {PINNED_VERSION})"]
    if not version.startswith(PINNED_VERSION):
        return [f"d2 is {version}, this recipe was measured against {PINNED_VERSION} — "
                "re-run the gates before trusting the output"]
    return []


def compile_source(source, pad=8, binary="d2", layers=None, edges=None):
    """d2 source -> raw SVG text, laid out by ELK.

    `--theme 0` is deliberate: the palette is applied by our own literals and by the
    substitution below, so d2's themes would only add colours nothing maps.

    `--layout elk` is not an option a caller chooses — see `d2.ELK_OPTS` for why this repo uses
    one engine and which one. Two of its spacings are, because both are escalated per figure by
    `_pick_layout`: `layers` when the tight default leaves text unreadable or a label with
    nowhere to sit on its own leg (`d2.ELK_SPACING_LADDER`), `edges` when an arrowhead has no
    straight line to sit on (`d2.ELK_EDGE_LADDER`).

    Output goes to a temp file rather than stdout: it costs a temp dir and it is the one
    form d2 will write in every case, including outputs it refuses to send to stdout.
    """
    if d2_version(binary) is None:
        raise RenderError(f"d2 not found on PATH "
                          f"(install: brew install d2, pin {PINNED_VERSION})")
    with tempfile.TemporaryDirectory(prefix="lib-diagram-") as tmp:
        target = os.path.join(tmp, "out.svg")
        layers = d2mod.ELK_OPTS["nodeNodeBetweenLayers"] if layers is None else layers
        edges = d2mod.ELK_OPTS["edgeNodeBetweenLayers"] if edges is None else edges
        box = d2mod.ELK_OPTS["padding"]
        top = d2mod.ELK_OPTS["paddingTop"]
        cmd = [binary, "--pad", str(pad), "--theme", "0", "--layout", "elk",
               "--elk-nodeNodeBetweenLayers", str(layers),
               "--elk-edgeNodeBetweenLayers", str(edges),
               "--elk-padding", f"[top={top},left={box},bottom={box},right={box}]",
               "-", target]
        proc = subprocess.run(cmd, input=source, capture_output=True, text=True)
        if proc.returncode != 0 or not os.path.exists(target):
            raise RenderError(f"d2 failed to compile the diagram:\n{proc.stderr.strip()}\n"
                              f"--- source ---\n{source}")
        with open(target, encoding="utf-8") as handle:
            svg = handle.read()
    if "<svg" not in svg:
        raise RenderError(f"d2 produced no <svg> element\n--- source ---\n{source}")
    return svg


def _namespace_ids(svg, name):
    """Prefix every id and every internal reference to one, so two diagrams can coexist.

    d2 scopes its *CSS* per diagram but not its ids, and markers are referenced by id, so
    without this the second diagram on a page silently borrows the first's arrowheads.
    """
    svg = re.sub(r'\sid="([^"]*)"', lambda m: f' id="{name}-{m.group(1)}"', svg)
    svg = re.sub(r"url\(#([^)]*)\)", lambda m: f"url(#{name}-{m.group(1)})", svg)
    svg = re.sub(r'(href="#)([^"]*)"', lambda m: f"{m.group(1)}{name}-{m.group(2)}\"", svg)
    return svg


def pin_intrinsic(svg):
    """Write `width`/`height` onto the root `<svg>` from its viewBox when it has none.

    d2 emits only a viewBox. A page styling diagrams with `max-width:100%; height:auto`
    therefore has no intrinsic size to cap, so the browser STRETCHES the drawing to the
    container — measured at 1.34x for the reference class diagram, which turned its 16px
    headers into 21.4px, larger than the 19px body text around them. Pinning the size
    makes `max-width` a real cap: shrink to fit, never grow.

    This was invisible to a static size check for a long time, because the obvious
    assumption — `scale = min(1, available / natural)` — quietly encodes "shrink only",
    and that is simply false for an SVG with no intrinsic size.
    """
    end = svg.find(">") + 1
    tag = svg[:end]
    if re.search(r'\swidth="[\d.]+(pt|px)?"', tag) and 'width="100%"' not in tag:
        return svg
    box = re.search(r'viewBox="[-\d.]+ [-\d.]+ ([\d.]+) ([\d.]+)"', tag)
    if not box:
        return svg
    width, height = float(box.group(1)), float(box.group(2))
    new = re.sub(r'\swidth="[^"]*"', "", tag)
    new = re.sub(r'\sheight="[^"]*"', "", new)
    new = new[:-1].rstrip() + f' width="{width:.0f}" height="{height:.0f}">'
    return new + svg[end:]


def centre_in_box(svg):
    """Make the root `<svg>` centre its drawing when the box it is given is not its own shape.

    d2 emits `preserveAspectRatio="xMinYMin meet"`, which pins the drawing to the TOP LEFT of
    whatever box it lands in. Inside a card that is invisible, because there the box is the
    drawing's own shape — `pin_intrinsic` wrote it on. It becomes visible the moment a container
    sizes the drawing itself: a click-to-enlarge overlay filling most of the viewport shows a
    portrait figure shoved against the left edge with every pixel of slack on the right.

    Only the root tag, and only where d2 wrote the attribute. The nested `<svg>` d2 puts inside
    has none, and the SVG default already is `xMidYMid meet`.
    """
    end = svg.find(">") + 1
    return svg[:end].replace('preserveAspectRatio="xMinYMin meet"',
                             'preserveAspectRatio="xMidYMid meet"') + svg[end:]


def postprocess(raw, name, theme_vars=True):
    """Raw d2 SVG -> embeddable SVG. Order matters; see the module docstring.

    The callout retarget must run before the colour substitution, because it inserts
    `var(--d-callout-…)` directly and matches on d2's literal `#DEE1EB` — which the
    substitution would otherwise have already rewritten.
    """
    if "<svg" not in raw:
        raise RenderError(f"{name}: d2 produced no <svg> element")
    svg = raw[raw.index("<svg"):]
    svg = _namespace_ids(svg, name)
    svg = svg.replace(CALLOUT_ATTRS, CALLOUT_REPLACEMENT)
    svg = pin_intrinsic(svg)
    svg = centre_in_box(svg)
    if theme_vars:
        svg = palette.to_vars(svg)
    return svg


def _maybe_compact(raw, spec, name, standalone=False):
    """The two things a sequence diagram's SVG needs after d2 has laid it out — closing its
    dead vertical space, and shrinking the second line of a two-line participant label. Both
    live in `compact.py`; neither is expressible in d2 source.

    A compaction failure is reported and swallowed. It is a legibility improvement on output
    d2 already laid out correctly, so falling back to d2's spacing costs height and nothing
    else — where raising would deny the caller a diagram over cosmetics.
    """
    # The detail restyle is kind-agnostic: it only touches a <text> that has more than one
    # <tspan>, which only happens where a label carries a newline. Row compaction is the part
    # that is sequence-only.
    # Both annotations are placed against the canvas edge, so they need the pad d2 was
    # actually run with — which is not the same on the two targets.
    pad = STANDALONE_PAD if standalone else compact.D2_PAD
    # Before anything measures or moves: a re-cut callout is a smaller obstacle, and both the
    # edge-label pass below and the anchor search above must see the box that ships.
    svg = compact.style_detail_lines(callout.fit(raw))
    # Before the edge labels, so the words are placed on the route that ships rather than on
    # the one ELK proposed — the only ordering that keeps a label off a leg that is about to
    # get shorter. See `route.py` for what it moves and what stops it.
    svg = route.straighten(svg, edgelabel.route_obstacles(svg))
    if spec.get("kind") == "state":
        # After the marker, not before: it moves the canvas top edge, and an edge label may
        # not be nudged past a boundary that is about to change.
        return edgelabel.reposition(_maybe_mark_start(svg, spec, name, standalone, pad))
    if spec.get("kind") != "sequence":
        return edgelabel.reposition(svg)
    try:
        svg = compact.compact_sequence(svg)
    except compact.CompactError as exc:
        print(f"{name}: could not compact the sequence rows ({exc}) — "
              "falling back to d2's spacing", file=sys.stderr)
    try:
        return compact.add_group_legend(svg, _lane_colours(spec["participants"]), pad=pad)
    except compact.CompactError as exc:
        print(f"{name}: could not add the group legend ({exc}) — the lane colours are "
              "unexplained, so say what they mean in the prose", file=sys.stderr)
        return svg


def _lane_colours(participants):
    """(group name or None, rule colour, name colour) per lane, for the legend.

    Two colours from the one palette entry: the rule takes the lane's border so it reads as
    belonging to those boxes, the name takes the text-grade colour so it is legible as text.
    """
    classes = d2mod.group_classes(participants)
    return [(p.get("group"),
             palette.vars_for(classes[p["group"]])[1] if p.get("group") else None,
             palette.vars_for(classes[p["group"]], table=True) if p.get("group") else None)
            for p in participants]


def _maybe_mark_start(svg, spec, name, standalone, pad):
    """Draw the start dot beside the state carrying `start: true`, if one does.

    Swallowed like the other annotations: a state diagram with no room for the marker is still
    the diagram, and refusing to render it over an annotation would be the wrong trade. The
    warning says what the reader loses, so the prose can cover it.
    """
    start = next((s["id"] for s in spec["states"] if s.get("start")), None)
    if not start:
        return svg
    try:
        # Laid out to the right, the dot goes above the first state instead of beside it:
        # that layout is already scaled down to fit the content column, so width is the one
        # axis that costs glyph size — see compact's START_* constants. The direction comes
        # from d2, not from the spec, because the standalone target's is a default there.
        return compact.add_start_marker(
            svg, start, palette.MUTED, pad=pad,
            vertical=d2mod.effective_direction(spec, standalone) == "right")
    except compact.CompactError as exc:
        print(f"{name}: could not mark the start state ({exc}) — say where the machine "
              "begins in the prose", file=sys.stderr)
        return svg


# The layouts `render()` will try when the spec does not pin one: both orientations, with the
# edge labels as written and then wrapped progressively harder. Landscape is not a preference
# — it wins when it wins, on measurement — but it is tried because a small diagram stacked
# downward leaves most of the content column empty and costs a screen of scrolling for nothing.
#
# The engine is NOT an axis here; there is one. See `d2.ELK_OPTS`.
CANDIDATES = tuple((direction, wrap)
                   for wrap in (None,) + d2mod.EDGE_WRAPS
                   for direction in ("down", "right"))

# Page height, in px, below which two candidates count as equally tall. Height and glyph size
# pull against each other — scaling a drawing down makes it physically shorter — so comparing
# heights exactly rewards shrinking, and the first version of this picked an 848px figure
# scaled to 0.92 (11.9px text) over the same one wrapped to 722px at full size (13.0px). A
# hundred px is about a fifth of a viewport: worth trading text size for, but not worth it for
# the 16px between those two.
HEIGHT_BUCKET = 100

# The shape a drawing may have before it reads as a strip rather than a figure. Inside this
# band nothing is charged; outside it, a candidate pays for how far outside it is.
#
# It exists because width is otherwise almost free. A very wide layout is short, so it wins on
# height, and the only term that notices the width is the glyph size — which sits last and is
# never reached. The repo state machine shipped at 947x211, a ratio of 4.5, with its text
# scaled down to 10.7px, while a wrapped candidate of the same height bucket was 796x215 at
# 12.7px. The wide one is not wrong so much as unpleasant: a 4.5:1 drawing reads as a banner.
#
# That is still the rule, but it no longer decides THAT case. It used to buy the better shape
# by folding every edge label in the figure, and a fold is a cost to a reader that this term
# cannot see — so `_folds` now sits above it and the shape is chosen only among candidates
# that break their labels equally. The repo state machine ships at 917x211 with two of its
# five labels folded, rather than 796x215 with all five.
#
# 2.0 rather than 16:9, because the band has to hold BOTH orientations and a portrait diagram
# at 1:2 is perfectly ordinary. Distances are measured in logs so that 4:1 is as far outside as
# 1:4, and bucketed, so two candidates of much the same shape tie and the terms below decide.
#
# The bucket is a tenth of a log unit, about 10% of ratio. A quarter was tried and is too coarse
# for the case this exists for: the repo state machine's three landscape candidates are 4.77,
# 4.31 and 3.98: all "equally bad" at that width, so the decision fell through to the wrap
# preference, which takes the widest of them. It ships 3.98 and a whole point of glyph size
# better. Note this term sits BELOW height on purpose — it chooses between drawings of the same
# height, it does not turn a landscape figure portrait.
ASPECT_BAND = (0.5, 2.0)
ASPECT_BUCKET = 0.1


def _folds(wrap):
    """How hard this candidate folds its edge labels — 0 for not at all.

    Ranked ABOVE the aspect band, which is what makes an unfolded label the default rather
    than a tie-break nothing ever reaches. A fold is a real cost to a reader: it is decided for
    the whole FIGURE, so a label that is not setting the width anywhere gets broken along with
    the one that is, and `edgelabel` may then slide it somewhere roomy — where the break has
    no visible reason at all. Two figures were doing exactly that, with an unfolded candidate
    sitting right there passing every gate.

    It sits below HEIGHT, so this does not turn a figure portrait to save a line break, and
    below the two correctness terms, so it can never buy a fold out of a clipped or crossed
    drawing. It only decides among candidates that read equally well and fit equally well.
    """
    return 0 if wrap is None else 1 + d2mod.EDGE_WRAPS.index(wrap)


def _out_of_shape(width, height):
    """How far outside `ASPECT_BAND` this drawing's shape is, in log units. 0 when inside."""
    if height <= 0 or width <= 0:
        return 0.0
    ratio = width / height
    if ASPECT_BAND[0] <= ratio <= ASPECT_BAND[1]:
        return 0.0
    return math.log(ratio / ASPECT_BAND[1] if ratio > ASPECT_BAND[1]
                    else ASPECT_BAND[0] / ratio)


def _pick_layout(spec, name, binary, theme_vars, edge_rungs=None):
    """Render each candidate layout and keep the one that measures best.

    Choosing between portrait and landscape by kind was a guess that had to be wrong half the
    time: the reference diagrams are too big to be laid out wide, and a four-box one is too
    small to be laid out tall. The renderer can simply try both and look, which costs a d2 run
    each (~40ms) against a placement pass that costs seconds.

    Ranked by: how many size problems it has, then rendered height to the nearest
    `HEIGHT_BUCKET` — a shorter figure is less to scroll past — then how hard it had to wrap,
    then the size of its smallest glyph.

    Wrapping ranks above glyph size, which is a judgement and was made twice. Wrapping does buy
    text size — a figure 848px wide is scaled to 0.92 and its labels render at 11.9px, where the
    same figure wrapped to 722px is not scaled at all and they render at 13.0px — but a label
    broken across three lines when the row had horizontal room to spare reads worse than
    slightly smaller text on one. So a layout wraps only to fit, never to gain a point of size.

    All four are rendered rather than stopping at the first that fits. An early exit was
    tried and got the case this exists for wrong: a four-box chain fits the column stacked
    downward at 164x643, so the search stopped and never learned that wrapping its labels
    fits the same content landscape at 916x93 — seven times shorter.

    Around all of that sits the edge-spacing ladder, which is the one lever on ELK's routing
    this repo has: room between an edge and the box it points at. It is climbed only for a
    figure that needs it, and only as far as its size gate still passes — see `_afford`.
    """
    best = None
    for edges in (edge_rungs or d2mod.ELK_EDGE_LADDER):
        layout, svg, layers, spill = _at_layer_spacing(spec, name, binary, theme_vars, edges)
        rank = _afford(svg, name, spill)
        if best is None or rank < best[0]:
            best = (rank, layout, svg, layers, edges)
        # Nothing to buy: the ladder exists to put straight line in front of an arrowhead,
        # so a drawing with none of that wrong never pays for a second compile of itself —
        # whatever else the size gate thinks of it.
        #
        # And nothing to buy it WITH: the rung is paid for in page height, and a figure already
        # past `size.MAX_H` is spending the rescue budget just to exist. It cannot also spend
        # 40 to 100px on its arrowheads, so the wider rung would be rendered, measured and
        # refused every single time. The reference architecture is that figure, and this is a
        # whole candidate search per run.
        if not rank[1] or _out_of_pocket(svg):
            break
    return best[1], best[2], best[3], best[4]


def _out_of_pocket(svg):
    """Whether this drawing is already taller than the page rule allows on its own.

    Not a failure — `size.RESCUE_H` exists precisely so a figure may exceed `MAX_H` to buy a
    fix. It is a statement about what is left: nothing.
    """
    from .gates import GateError, size as size_gate
    try:
        return size_gate.analyse(svg)["rend_h"] > size_gate.MAX_H
    except GateError:
        return False


def _afford(svg, name, spill, standalone=False):
    """`(is the drawing spoilt, how many arrow defects)` — what a wider rung has to beat.

    Spoilt first, and absolutely. The wider rung buys straight line in front of every arrowhead
    and spends 40 to 100px of the page for it, which most figures can pay — and what a figure
    that cannot pay runs out of is WIDTH. The reference architecture needs rung 24, where it is
    1079px wide, the content column scales it to 0.72 and its text lands at 9.4px against a 10px
    floor; its height is 478px at every rung on the ladder. A drawing nobody can read is not an
    improvement on an arrowhead sitting on a curve.

    Most of those arrowheads no longer reach here at all — `route.py` moves the step instead,
    for nothing — so this now decides one figure's rung rather than five.
    """
    from .gates import GateError, size as size_gate   # local: gates.clipping imports this
    try:
        problems = bool(size_gate.check(svg, name, standalone=standalone).problems)
    except GateError:
        problems = False
    return (problems or spill > 1, len(arrows.defects(svg)))


def _at_layer_spacing(spec, name, binary, theme_vars, edges):
    def rung(layers):
        layout, svg = _pick_at_spacing(spec, name, binary, theme_vars, layers, edges)
        hidden, spill = _faults(svg, enabled=theme_vars)
        return svg, hidden, spill, layout

    layers, svg, spill, layout = _climb_layers(rung, name)
    return layout, svg, layers, spill


def _climb_layers(rung, name, standalone=False):
    """`(layers, svg, spill, payload)` at the tightest layer spacing that draws soundly.

    `rung(layers)` renders at one spacing and hands back `(svg, hidden, spill, payload)`; the
    payload is whatever else its caller wants to keep from the rung it settles on.

    Two defects climb this ladder, and one gap between two layers pays for both.

    **Unreadable text** is absolute, because a label lands in that gap and at 15px it overlaps
    whatever borders it. Without a browser it cannot be measured, so the tight spacing stands
    and the clipping gate reports the consequence later.

    **A label with nowhere to sit** is the second, and it is the same shortage seen from the
    other side: ELK sizes the gap to hold the label and nothing more, so on a leg that short
    there is no spot that both leaves the line a beginning and keeps clear of the arrowhead at
    the end. `edgelabel` takes the least bad one and `arrows` reports what it cost. Unlike
    hidden text this one is only worth buying while it is affordable, so the rung is kept on
    `_afford` — the same test the edge ladder is held to — and a figure already out of pocket
    on height does not pay for a candidate search it will refuse.
    """
    best = last = None
    for layers in d2mod.ELK_SPACING_LADDER:
        svg, hidden, spill, payload = rung(layers)
        last = (layers, svg, spill, payload)
        if hidden:
            continue
        rank = _afford(svg, name, spill, standalone=standalone)
        if best is None or rank < best[0]:
            best = (rank, layers, svg, spill, payload)
        # A standalone image has no height rule to be out of pocket against — see
        # `gates/size.check` — so there the question does not arise.
        if not _cramped(svg) or (not standalone and _out_of_pocket(svg)):
            break
    return best[1:] if best else last


def _cramped(svg):
    """Whether a label's own gap is eating line an arrow needs at one of its ends.

    Only `gap`, and that is the split between the two ladders. A gap is what a LABEL costs the
    line it sits on, and how much room the label has is decided by the space between two layers
    — this ladder. A `turn` is where the route ends up pointing, which is the edge ladder's
    lever; climbing this one for a turn would cost a candidate search per rung on a figure it
    cannot help.
    """
    return any(defect.rule == "gap" for defect in arrows.defects(svg))


def _faults(svg, enabled=True, theme="light", standalone=False):
    """`(the LAYOUT hid some text, px of drawing outside its own canvas)`, in one measurement.

    Two questions, one browser launch, because launching Chrome costs more than measuring
    anything on the page — and both ladders ask their question per rung, so measuring twice
    doubled the cost of the whole corpus.

    Hidden text is what the LAYER spacing escalates for. Text buried by a callout is not
    counted: that is the placement pass's problem and it has its own term for it, and widening
    the diagram would not move an anchor.

    Spilling is what the EDGE spacing must not cause. A wider rung can push a landscape state
    machine past the height d2 wrote into its viewBox and cut the bottom row of boxes off; the
    clipping gate says so afterwards, which is too late to choose a rung. Callouts are excluded
    here too — d2 reserves them no canvas space at all, so one reaching into the card's padding
    is ordinary.

    Both come back false/zero when there is no browser to ask. The measurement needs real
    laid-out glyph boxes, and guessing would be worse than the gate that reports it later.

    `standalone` picks the boundary, and it has to match the target the SVG is FOR: a
    standalone image is measured against its own canvas at natural size, an embedded one
    inside the content column that scales it. See `harness_html`.
    """
    if not enabled or not browser.available():
        return False, 0.0
    try:
        measured = browser.measure([{"key": "t",
                                     "html": harness_html(svg, theme=theme,
                                                          standalone=standalone)}])
    except browser.BrowserError:
        return False, 0.0
    if not measured:
        return False, 0.0
    spill = max([sum(o["over"].values()) for o in measured[0].get("offenders") or []
                 if not o["callout"]] or [0.0])
    return bool(measured[0].get("hiddenByLayout")), spill


def spills(svg, theme="light", standalone=False):
    """How far the drawing reaches outside its own canvas, in px. See `_faults`.

    Public because `figure` needs it after PLACEMENT, which is the one thing the ladder in
    here cannot see: a callout takes up canvas only once it has an anchor.
    """
    return _faults(svg, theme=theme, standalone=standalone)[1]


def _pick_at_spacing(spec, name, binary, theme_vars, layers, edges=None):
    from .gates import GateError, size as size_gate   # local: gates.clipping imports this

    seen, wanted = set(), []
    for direction, wrap in CANDIDATES:
        variant = dict(spec, direction=direction)
        source = d2mod.emit(variant, wrap_edges=wrap)
        # A wrap level only changes anything if some edge label is longer than it. When every
        # label already fits, the wrapped source is character-for-character the unwrapped one,
        # and compiling it again cannot produce a different measurement — so the rung is
        # skipped. On the reference architecture, whose longest label is `verify JWT`, that is
        # a third of the search for free. It is a dedup, not a heuristic: identical input,
        # identical output, and the FIRST occurrence is kept so the wrap tie-break still
        # prefers the gentlest one.
        if source in seen:
            continue
        seen.add(source)
        wanted.append((direction, wrap, variant, source))

    # Emitting is pure string work and stays in order, because the dedup above depends on it.
    # COMPILING is a subprocess per candidate and they are independent, so they go at once —
    # see `lib/parallel.py`. The ranking below is unchanged and still runs in candidate order,
    # which is what keeps `CANDIDATES.index` meaningful as a tie-break.
    def draw(job):
        _direction, _wrap, variant, source = job
        raw = compile_source(source, binary=binary, layers=layers, edges=edges)
        return postprocess(_maybe_compact(raw, variant, name), name, theme_vars=theme_vars)

    best = None
    for (direction, wrap, _variant, _source), svg in zip(wanted, parallel.each(draw, wanted)):
        try:
            result = size_gate.check(svg, name)
            metrics = size_gate.analyse(svg)
        except GateError:
            # Unmeasurable is not a reason to render nothing; the gates report it later.
            return (direction, wrap), svg
        # Ranked on crossings before height, which is what makes `size.RESCUE_H` a budget
        # rather than a raised ceiling: among candidates that read equally well the shortest
        # still wins, so the extra height is never spent for nothing — but a taller candidate
        # that resolves a crossing no gap can rescue beats a shorter one that leaves it.
        #
        # Gentler wrapping ranks better only among layouts that read equally well, so a
        # diagram spends lines when that buys glyph size and not otherwise.
        #
        # `arrows.defects` sits BELOW both, and that position was measured twice. Ranked
        # above height it does buy real fixes — and it buys them at a price no reader would
        # agree to: the repo architecture loses one stranded arrowhead by breaking every edge
        # label in the figure across four lines, the reference ER goes from 902x257 to 820x592,
        # and the reference class diagram from 498x490 to 753x814. What pays for a route's
        # flaws is the edge-spacing ladder in `_pick_layout`, which buys the same fix with 40
        # to 100px of page instead of with the shape of the whole drawing. Here they only break
        # ties between candidates that are otherwise equal.
        rank = (len(result.problems), edgelabel.unfixable_crossings(svg),
                round(metrics["rend_h"] / HEIGHT_BUCKET),
                _folds(wrap),
                round(_out_of_shape(metrics["nat_w"], metrics["nat_h"]) / ASPECT_BUCKET),
                CANDIDATES.index((direction, wrap)),
                len(arrows.defects(svg)), -metrics["fmin"])
        if best is None or rank < best[0]:
            best = (rank, (direction, wrap), svg)
    return best[1], best[2]


def choose_layout(spec, name="diagram", binary="d2", edge_rungs=None):
    """`((direction, wrap), layers, edges)` this spec measures best at, or None if nothing to pick.

    Exposed for the callout placement pass, which needs to hold the layout STILL while it
    varies anchors. Without it, `place` rendered every anchor candidate through the full search
    and each one could land on a different layout — so their overlap scores were compared across
    different drawings, and the anchor that won might have been measured against a shape the
    final diagram does not have. Pinning one layout for the whole anchor search fixes that and
    happens to remove most of the cost: a two-callout diagram was 64 anchor candidates times a
    ~4-compile search, and ELK compiles are ~330ms.
    """
    if spec.get("direction") or spec.get("kind") == "sequence":
        return None
    layout, _svg, layers, edges = _pick_layout(spec, name, binary, theme_vars=True,
                                              edge_rungs=edge_rungs)
    return _relax(spec, name, binary, layout, layers, edges), layers, edges


def _relax(spec, name, binary, layout, layers, edges):
    """`layout` with every edge label put back on one line that the drawing can afford.

    The fold width is chosen for a whole FIGURE, and a reader judges each label where it sits.
    So the two disagree constantly: the reference state machine folded six labels to keep its
    text above the floor, when only two of them were paying for it — `401 invalid` sat broken
    across two lines with 300px of empty canvas beside it, for no reason a reader could see.

    Longest first, because a long label buys the most width back and is the one whose fold is
    most obvious. Each is offered on its own and kept only while the whole drawing still passes
    the size gate, so this can loosen a figure but never spoil one.

    Sequential on purpose, and not a fan-out: each answer depends on what the previous one was
    allowed to keep. It costs one d2 compile per folded label, on the figures that fold at all
    — three of ten today — and buys back four labels on one of them.
    """
    direction, wrap = layout
    if wrap is None:
        return layout
    folded = [label for label in _edge_labels(spec)
              if "\n" in d2mod.wrap_edge_label(label, wrap)]
    spared = frozenset()
    for label in sorted(folded, key=len, reverse=True):
        trial = spared | {label}
        if _sound(_one(spec, name, binary, direction, (wrap, trial), layers, edges), name):
            spared = trial
    return (direction, (wrap, spared) if spared else wrap)


def _one(spec, name, binary, direction, wrap, layers, edges):
    """One candidate drawn, at a pinned direction and fold. No search and no ladder."""
    source = d2mod.emit(dict(spec, direction=direction), wrap_edges=wrap)
    raw = compile_source(source, binary=binary, layers=layers, edges=edges)
    return postprocess(_maybe_compact(raw, dict(spec, direction=direction), name), name)


def _edge_labels(spec):
    """Every edge label in a spec, in document order, whatever the kind calls its edges."""
    seen = []
    for key in ("edges", "transitions", "messages", "relations"):
        for item in spec.get(key) or []:
            if item.get("label") and item["label"] not in seen:
                seen.append(item["label"])
    return seen


def _sound(svg, name):
    """Whether this drawing passes the size gate. Mirrors `figure._fits`, and answers the
    OPPOSITE way when it cannot measure: unmeasurable means a label is not spared here, and
    means no extra fold is spent there. Both are the cautious answer, because the two are
    deciding opposite actions — a change to one is a reason to look at the other."""
    from .gates import GateError, size as size_gate
    try:
        return not size_gate.check(svg, name).problems
    except GateError:
        return False


def choose_drawing(spec, name="diagram", theme="dark", standalone=False, binary="d2",
                   edge_rungs=None):
    """`(layout, layers, edges)` this spec is drawn at — decided ONCE, for the whole pipeline.

    `layout` is the `(direction, wrap)` pair, or None where there is nothing to choose: a
    standalone image takes its direction from `d2.DIRECTION` and does not wrap, and a sequence
    is laid out by an engine that ignores both.

    This exists because the decision was being made twice, and the second time on different
    input. `place` pinned a layout so that every anchor was measured on the same drawing —
    which is the whole reason `choose_layout` was written — and then the caller rendered the
    finished spec through the full search again. By then the spec HAS its `near` values, and a
    callout changes the geometry the search ranks on: the reference architecture is 579px wide
    with its callouts pinned and 462 without. So the drawing that shipped was not necessarily
    the drawing the anchors had been chosen against, which is precisely the bug the pinning was
    introduced to prevent, reappearing one step later.

    It is also most of the cost. Deciding once saves a ~4-compile search on the embedded path
    and a ladder of compiles plus browser launches on the standalone one, per figure.
    """
    if standalone:
        return (None, *choose_standalone_spacing(spec, name, theme, binary, edge_rungs))
    chosen = choose_layout(spec, name, binary, edge_rungs)
    return chosen if chosen else (None, None, None)


def render(spec, name="diagram", binary="d2", theme_vars=True, wrap_edges=None,
           layers=None, edges=None):
    """Spec -> embeddable SVG, for a host page that ships `page_css()`.

    `name` namespaces the SVG's ids, so it must be unique within a page. For a file to open
    on its own, use `standalone()` instead — this output depends on the page.

    The layout is chosen by measurement unless the spec pins a `direction` — see
    `_pick_layout`. A `sequence` is exempt: d2's sequence engine ignores `direction`, so
    there is nothing to choose between.

    `wrap_edges` pins the edge-label wrap that the search would otherwise pick, and is only
    meaningful alongside a pinned `direction`. `place` uses the pair to hold one layout still.
    """
    if not spec.get("direction") and spec.get("kind") != "sequence":
        return _pick_layout(spec, name, binary, theme_vars)[1]  # noqa: E501  (svg)
    raw = compile_source(d2mod.emit(spec, wrap_edges=wrap_edges), binary=binary,
                         layers=layers, edges=edges)
    return postprocess(_maybe_compact(raw, spec, name), name, theme_vars=theme_vars)


# CSS for a standalone image. Not the same as HOST_CSS: the page rules (`max-width:100%`,
# `height:auto`) exist to fit a drawing into a content column and would fight a viewer that
# is meant to show it at full size and let you zoom. What carries over is the part that is
# not styling at all — the paragraph reset and font-family a callout's `<foreignObject>`
# needs — plus the callout depth, baked to one theme since there is nothing to toggle.
STANDALONE_CSS = """\
foreignObject{overflow:visible}
foreignObject .md{display:flex;align-items:center;height:100%;
  font-family:system-ui,-apple-system,'Segoe UI',sans-serif}
foreignObject .md p{margin:0;line-height:1.2;font-size:12.5px;white-space:nowrap}
"""

STANDALONE_CALLOUT = {
    "dark": ".d2-callout{fill-opacity:.94;"
            "filter:drop-shadow(0 0 5px rgba(224,137,90,.40)) "
            "drop-shadow(0 2px 4px rgba(0,0,0,.55))}\n",
    "light": ".d2-callout{fill-opacity:.95;"
             "filter:drop-shadow(0 2px 5px rgba(0,0,0,.28))}\n",
}

# Padding for a standalone image, in px. Bigger than the embedded default of 8 because
# nothing else provides room here: embedded, a callout's drop-shadow bleeds into the card's
# ~23px of padding, and a standalone file has no card — so the canvas itself has to carry it.
STANDALONE_PAD = 20


def _standalone_at(spec, name, theme, binary, layers, edges=None):
    """One standalone SVG, compiled at exactly these two ELK spacings."""
    source = d2mod.emit(spec, background=palette.CANVAS, standalone=True)
    raw = compile_source(source, pad=STANDALONE_PAD, binary=binary, layers=layers, edges=edges)
    # Substitute to vars first, then resolve them down to one theme. The detour exists so
    # mappability can be checked while it still means something: once the colours are
    # concrete, every one of them is a non-key and `unmapped()` can no longer tell an
    # un-themeable literal from a correctly baked one. So the theming gate has no standalone
    # equivalent — this check replaces it, at the only moment it can run.
    svg = postprocess(_maybe_compact(raw, spec, name, standalone=True), name, theme_vars=True)
    missing = palette.unmapped(svg)
    if missing:
        listed = ", ".join(f"{colour} x{count}" for colour, count in missing.items())
        raise RenderError(
            f"{name}: d2 emitted colour(s) with no palette mapping: {listed}. A standalone "
            "image bakes one theme, so an unmapped literal would be frozen at its light-mode "
            "value. Add it to palette.py, or stop using the d2 feature that emits it.")
    svg = palette.resolve(svg, theme)
    return _inline_style(svg, STANDALONE_CSS + STANDALONE_CALLOUT[theme])


def _standalone_ladder(spec, name, theme, binary, edge_rungs=None):
    """`(svg, layers, edges)` at the tightest spacings that leave the drawing sound.

    The same two escalations `_pick_layout` does for the embedded target — layer spacing until
    the text is readable and every label fits its leg (`_climb_layers`), edge spacing until no
    arrowhead sits on a curve — done separately because the two targets are different drawings:
    standalone has its own direction default (`d2.DIRECTION`), its own far more generous
    padding, and no content column, so neither the layout nor the measurement carries across.

    What does NOT carry across is the cost. Embedded, every px of width is scaled back out of
    the glyphs, which is why the ladders are escalated rather than raised for everyone; a
    standalone image is shown at natural size, so the reference ER goes from 886x281 to
    916x281 with its text still at 12.5px. The gap is close to free here.
    """
    best = None
    for edges in (edge_rungs or d2mod.ELK_EDGE_LADDER):
        def rung(layers, edges=edges):
            svg = _standalone_at(spec, name, theme, binary, layers, edges)
            hidden, spill = _faults(svg, theme=theme, standalone=True)
            return svg, hidden, spill, None

        layers, svg, spill, _payload = _climb_layers(rung, name, standalone=True)
        rank = _afford(svg, name, spill, standalone=True)
        if best is None or rank < best[0]:
            best = (rank, svg, layers, edges)
        if not rank[1]:
            break
    return best[1], best[2], best[3]


def choose_standalone_spacing(spec, name="diagram", theme="dark", binary="d2",
                              edge_rungs=None):
    """The `(layers, edges)` spacing this spec needs as a standalone image.

    Exposed for the placement pass, for the same reason `choose_layout` is: the spacing has to
    be decided ONCE and then held still while anchors vary. Escalating inside `standalone()`
    during the search would put a browser launch inside a 64-candidate loop, where the whole
    point of `_measure_candidates` is that all 64 are measured in one.
    """
    return _standalone_ladder(spec, name, theme, binary, edge_rungs)[1:]


def standalone(spec, name="diagram", theme="dark", binary="d2", layers=None, edges=None):
    """Spec -> a self-contained SVG that can be opened on its own.

    Three things separate this from `render()`, and all three are what make a file work by
    itself rather than only inside a page that cooperates:

    1. **The colours are baked**, not `var()` references — an undefined custom property makes
       the browser drop the whole attribute, so a themed SVG opened directly is unpainted.
    2. **The canvas is painted** (see d2.py's `_prelude`). Left transparent, the drawing is
       composited onto whatever the viewer uses, and a dark one on white gives its muted edge
       labels no contrast at all.
    3. **The CSS travels inside the file**, because a callout's text is HTML in a
       `<foreignObject>` and d2 ships no paragraph reset for it.

    Dark by default, which reverses an earlier decision: the output is rasterised and handed to
    the system image viewer, whose chrome follows the OS appearance — so on a dark desktop a
    light drawing is the slab that fights its surroundings.

    The layer spacing climbs `d2.ELK_SPACING_LADDER` until no text is unreadable, as
    `_pick_layout` does for the embedded target — see `_standalone_ladder`. This path went
    without it, and every image the `visualize` skill wrote of the reference ER had a
    cardinality sitting on a table while the explainers' copy came out clean.

    `layers` pins that spacing instead of measuring it, for `place`.
    """
    if theme not in ("light", "dark"):
        raise RenderError(f"theme must be 'light' or 'dark', not {theme!r}")
    if layers is not None:
        return _standalone_at(spec, name, theme, binary, layers, edges)
    return _standalone_ladder(spec, name, theme, binary)[0]


# A title strip above the drawing, in the raster only. d2 has no page-title and adding one to
# the SVG means synthesising a text node and growing the canvas by hand; the rasteriser already
# builds a page, so the caption costs a line of HTML there. It matters most when one subject was
# split across several images: without it, part three is anonymous and a cross-reference in a
# callout points at a name the reader cannot see anywhere.
TITLE_H = 34
TITLE_CSS = ("font:600 15px system-ui,-apple-system,'Segoe UI',sans-serif;"
             "padding:8px 12px 0;letter-spacing:.2px")


def rasterise_standalone(svg, svg_path, title=None, theme="dark"):
    """Write `svg` beside `svg_path` as a PNG and return that path.

    The SVG is inlined into the page rather than referenced: an `<img src>` would be a second
    fetch of a file that may not be written yet, and inlining is also what the placement pass
    measures, so the raster and the measurements agree by construction.
    """
    width, height = natural_size(svg)
    if not width or not height:
        raise RenderError("the SVG has no natural size to rasterise")
    out = os.path.splitext(str(svg_path))[0] + ".png"
    fg = palette.for_theme(palette.FG, theme)
    bg = palette.for_theme(palette.CANVAS, theme)
    caption = ""
    if title:
        caption = (f'<div style="{TITLE_CSS};color:{fg}">'
                   f"{title.replace('&', '&amp;').replace('<', '&lt;')}</div>")
        height += TITLE_H
    page = (f'<!DOCTYPE html><meta charset="utf-8"><style>html,body{{margin:0;padding:0}}'
            f"body{{background:{bg}}}svg{{display:block}}</style>{caption}{svg}")
    try:
        return browser.rasterise(page, out, width, height)
    except browser.BrowserError as exc:
        raise RenderError(str(exc))


def _inline_style(svg, css):
    """Put `css` inside the SVG as the root element's first child.

    Wrapped in CDATA because a `<style>` element in SVG is XML, not HTML: any `<` or `&` in
    the CSS is otherwise parsed as markup. That is not hypothetical — a comment mentioning
    `<svg>` was enough to make the whole file a parse error, which every browser reports as
    "opening and ending tag mismatch" rather than anything about CSS.
    """
    end = svg.find(">") + 1
    return f"{svg[:end]}<style><![CDATA[\n{css}]]></style>{svg[end:]}"


def natural_size(svg):
    """(width, height) in CSS px from the root tag — pinned size first, else viewBox."""
    tag = svg[:svg.find(">") + 1]
    wm = re.search(r'\swidth="([\d.]+)(pt|px)?"', tag)
    hm = re.search(r'\sheight="([\d.]+)(pt|px)?"', tag)
    if wm and hm:
        width, height = float(wm.group(1)), float(hm.group(1))
        if wm.group(2) == "pt":
            width, height = width * 4 / 3, height * 4 / 3
        return width, height
    box = re.search(r'viewBox="[-\d.]+ [-\d.]+ ([\d.]+) ([\d.]+)"', tag)
    if box:
        return float(box.group(1)), float(box.group(2))
    return None, None


def page_css(dark_selector="[data-theme=dark]"):
    """Everything a host page needs: the theme vars plus the required host CSS.

    `dark_selector` covers the simple case of a page whose dark mode is one selector. A page
    that ALSO honours `prefers-color-scheme` has to compose the pieces itself — see
    `palette.declarations` and `callout_dark_css` — because those declarations then need to
    appear in a media query as well, and a page that emitted them once would show a
    light-coloured diagram on a dark background to any reader who never used its toggle.
    """
    return (palette.css_block(dark_selector) + HOST_CSS
            + callout_dark_css(dark_selector))


# The real page's geometry, which the browser gates have to reproduce exactly or they
# measure the wrong boundary. `.diagram` is the card that clips (overflow:hidden) and its
# padding is what a callout's shadow is allowed to bleed into; CONTENT_W is the usable
# drawing width the size gate uses, and the root font-size is what makes 1.2rem a real
# number of pixels.
ROOT_FONT_PX = 19
CARD_PADDING_REM = 1.2
CONTENT_W = 777

HARNESS = """<!DOCTYPE html><html data-theme="{theme}"><meta charset="utf-8"><style>
html{{font-size:{root}px}}
body{{margin:0;padding:0;background:var(--page,#fafaf8);
  font-family:Georgia,'Times New Roman',serif;font-size:1rem}}
{css}
.diagram{{{box}overflow:hidden;box-sizing:content-box}}
</style><div class="diagram">{svg}</div></html>"""


def harness_html(svg, theme="light", standalone=False):
    """A minimal page that reproduces the geometry the browser gates need to measure.

    Embedded, that means a card with the real page's width and padding, because what clips a
    callout on the real page is the CARD's `overflow:hidden`, not the SVG's own box.
    Measuring the proxy instead of the surface that actually clips is how the prototype's
    clipping gate came to report a callout as fitting while its glow was being cut.

    `standalone=True` collapses the card onto the drawing — natural size, no padding — so the
    boundary becomes the SVG's own canvas. That is the correct surface for a file: there is no
    card, and the room a callout's shadow needs comes from the canvas padding instead
    (`STANDALONE_PAD`). It also stops the page's content width from scaling the drawing down,
    which would make every measurement a scaled one.
    """
    box = ("display:inline-block;padding:0;" if standalone
           else f"width:{CONTENT_W}px;padding:{CARD_PADDING_REM}rem;")
    return HARNESS.format(theme=theme, root=ROOT_FONT_PX, css=page_css(), box=box, svg=svg)


def write_svg(svg, path):
    """Write an SVG, creating parent directories. Returns the path."""
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(svg)
    return path
