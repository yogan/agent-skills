"""Size gates: does the diagram fit, and is its text the right size once it has?

The two questions are the same question. A diagram's natural size decides how much the
page has to scale it down to fit the content column, and that scale applies to every glyph
— so an over-wide diagram passes "no text too large" for the worst possible reason, by
shrinking its text until nobody can read it. Checking height, maximum glyph, modal glyph
and minimum glyph together is what closes that loophole.

Page geometry these numbers come from (the explainers' HTML):

    body max-width 880px, padding 1.5rem x2 at a 19px root = 57px,
    .diagram padding 1.2rem x2 = 45.6px          ->  ~777px of usable drawing width
    h2 = 1.4rem at a 19px root                   ->  26.6px
    body text = 1rem                             ->  19px

The subtle one is `scale`. It is tempting to write `min(1, available / natural)` and move
on — shrink to fit, never grow. That is only true if the SVG declares an intrinsic size,
and d2's does not: it emits a viewBox alone, so a browser will happily *enlarge* it.
`render.pin_intrinsic` writes the size on, and the check below refuses to measure an SVG
where that did not happen — because assuming shrink-only is exactly how the prototype's
first size gate reported a diagram rendering at 1.34x as passing.
"""
import collections
import re

from . import GateError, Result

AVAIL_W = 777.0        # usable px inside a .diagram card on the real page

# The hard ceiling, and the one number that governs: nothing ships taller than this. `MAX_H` is
# what the renderer aims at and `RESCUE_H` is the rest of the allowance, derived rather than
# guessed so the two can never drift apart or add up to something nobody agreed to.
MAX_TOTAL_H = 900.0
MAX_H = 820.0          # what a figure should come in under

# Height a figure may borrow beyond MAX_H, and only ever to buy a fix. The layout search will
# not spend it on a drawing that merely comes out taller — among candidates that read equally
# well it still takes the shortest — but it will take a taller candidate that resolves a defect
# the shorter one cannot, such as an arrow left running through a container's title because
# there was no room to break the line without stranding its arrowhead.
#
# Enforced by `render._pick_at_spacing`, which ranks a candidate's unfixable defects above its
# height. This gate cannot see that distinction and does not try to: it holds the ceiling.
#
# 80px, and 40 before that. The old number was fitted to the single case that existed when it
# was written and bought nothing afterwards: the reference architecture needed 158px past the
# ceiling to put its arrowheads on straight line, four times the allowance. It is derived from
# the hard ceiling now, so raising one cannot silently raise the total.
RESCUE_H = MAX_TOTAL_H - MAX_H
H2_PX = 26.6           # 1.4rem at a 19px root
BODY_PX = 19.0         # 1rem at a 19px root
# Measured rather than assumed: every figure in both corpora was shown inside the real 777px
# column, against the article's real body text, at 14px down to 8px, and these are the sizes
# that survived. See the ladder under "Setting the renderer's limits".
MIN_READABLE = 10.0    # below this, text in body copy is effectively unreadable

# The same floor for a box's subtitle line, which is supplementary by construction: the muted
# second line under a name, carrying the real module or column names behind an abstract label.
# Held apart because measuring it with the primary text made it the most expensive text in the
# diagram — authored AT the floor (compact.DETAIL_FONT), it could not survive any downscale at
# all, so a figure carrying a subtitle had to fit the column exactly while the same figure
# without one had 141px of slack. It is chosen so it never binds first, and that is what fixes
# its value: a figure authored at 14px primary hits ITS floor at 1088px of natural width, where
# an 11px subtitle has come down to 7.9. Anything above ~7.86 would stop such a figure by its
# supplementary line rather than by the text a reader needs.
MIN_READABLE_DETAIL = 7.5

# And for an edge label, which is annotation of the same sort: it qualifies a relationship the
# arrow has already drawn. It matters here because d2 sets edge labels at 13px against a table's
# 14px rows, so in an `er` or `class` diagram the label is the smallest text and decides the
# whole layout — on one reference diagram it was the 0.1px between a cardinality reading
# "1 doc :" / "n sessions" and being folded onto a third line to save nine pixels of width.
MIN_READABLE_EDGE = 9.0

_FONT_ATTR = re.compile(r'font-size="([\d.]+)')
_FONT_CSS = re.compile(r"font-size:\s*([\d.]+)px")
# A subtitle span, tagged by compact.style_detail_lines so the two floors can be told apart.
_DETAIL_SPAN = re.compile(r'<tspan class="d2-detail"[^>]*font-size:\s*([\d.]+)px')
# An edge label: d2's own class for one, which the post-processing leaves in place.
_EDGE_TEXT = re.compile(r'<text[^>]*class="text-italic[^"]*"[^>]*font-size:\s*([\d.]+)px')
_VIEWBOX = re.compile(r'viewBox="[-\d.]+ [-\d.]+ ([\d.]+) ([\d.]+)"')
_WIDTH = re.compile(r'\swidth="([\d.]+)(pt|px)?"')
_HEIGHT = re.compile(r'\sheight="([\d.]+)(pt|px)?"')


def analyse(svg, avail_w=AVAIL_W, standalone=False):
    """Measure one SVG. Raises GateError if it cannot be measured honestly.

    `standalone=True` fixes the scale at 1.0: a file opened in a viewer is shown at its
    natural size and zoomed by the reader, so there is no container to be scaled down into.
    """
    if "<svg" not in svg:
        raise GateError("no <svg> element to measure")
    tag = svg[svg.index("<svg"):]
    tag = tag[:tag.find(">") + 1]

    box = _VIEWBOX.search(tag)
    wm, hm = _WIDTH.search(tag), _HEIGHT.search(tag)
    if not (wm and hm) or 'width="100%"' in tag:
        # Refusing to guess. Without an intrinsic size the page's `max-width:100%` is not
        # a cap at all and the browser may scale the drawing UP, so any number this
        # function returned would be a floor, not a measurement.
        raise GateError(
            "the <svg> has no intrinsic width/height, so the browser may enlarge it and "
            "its glyphs with it — run render.pin_intrinsic() before measuring"
            + ("" if box else " (and it has no viewBox to pin from)"))

    unit = 4 / 3 if wm.group(2) == "pt" else 1.0
    nat_w, nat_h = float(wm.group(1)) * unit, float(hm.group(1)) * unit
    if nat_w <= 0 or nat_h <= 0:
        raise GateError(f"degenerate size {nat_w}x{nat_h}")

    scale = 1.0 if standalone else min(1.0, avail_w / nat_w)

    fonts = [float(x) for x in _FONT_ATTR.findall(svg)]
    fonts += [float(x) for x in _FONT_CSS.findall(svg)]
    fonts = [f for f in fonts if f > 0]
    if not fonts:
        # A diagram with no declared font size is either empty or the parse missed
        # everything; either way the glyph gates below would vacuously pass.
        raise GateError("no font-size declarations found — nothing to check glyph sizes against")

    # Subtitles are measured separately, and subtracted from the primary pool by value: the
    # spans are tagged, but a size only reaches this far as a number, so an 11px subtitle and
    # an 11px label are indistinguishable here. Removing one occurrence per tagged span is
    # exact when they agree and conservative when they do not.
    details = [float(x) for x in _DETAIL_SPAN.findall(svg)]
    edges = [float(x) for x in _EDGE_TEXT.findall(svg)]
    primary = list(fonts)
    for value in details + edges:
        if value in primary:
            primary.remove(value)
    if not primary:                      # a diagram of nothing but subtitles is not a thing
        primary = list(fonts)

    modal = collections.Counter(primary).most_common(1)[0][0]
    rendered = [f * unit * scale for f in primary]
    rendered_details = [f * unit * scale for f in details]
    rendered_edges = [f * unit * scale for f in edges]
    return {
        "nat_w": nat_w, "nat_h": nat_h, "scale": scale,
        "rend_w": nat_w * scale, "rend_h": nat_h * scale,
        # fmax counts edge labels, fmin does not: the ceiling is "nothing may shout louder
        # than an h2", which any text can breach, while the floor is per-kind — an edge label
        # has its own, lower one below.
        "fmax": max(rendered + rendered_edges), "fmin": min(rendered),
        "fmodal": modal * unit * scale,
        "fmin_detail": min(rendered_details) if rendered_details else None,
        "fmin_edge": min(rendered_edges) if rendered_edges else None,
    }


def check(svg, name="diagram", avail_w=AVAIL_W, max_h=MAX_H + RESCUE_H,
          standalone=False):
    """Every size gate, in one Result.

    `standalone=True` drops the height, maximum-glyph and modal-glyph checks — not as a
    relaxation, but because all three are statements about a page that is not there: height
    bounded a figure to one viewport mid-article, and the glyph ceilings compare against
    surrounding prose. Applying the page's width to a file makes any wide diagram "fail", and
    the only fix an author has is to split a schema that was perfectly legible at full size.

    What survives is the floors, which are about the drawing itself. Three of them, because
    what a glyph is FOR decides how small it may get, and each is a RENDERED size — after the
    embedded column has scaled the drawing down, not the size it was authored at:

      * `MIN_READABLE` 10px — primary text: node labels, table rows, container titles;
      * `MIN_READABLE_EDGE` 9px — an edge label, which qualifies an arrow already drawn;
      * `MIN_READABLE_DETAIL` 7.5px — a subtitle, the muted second line under a name.

    On a standalone render they are present but cannot fire: the scale is fixed at 1.0, so
    every glyph renders at the size it was authored and that is above all three.
    """
    m = analyse(svg, avail_w=avail_w, standalone=standalone)
    problems = []
    if not standalone:
        if m["rend_h"] > max_h:
            problems.append(f"TALL {m['rend_h']:.0f}px > {max_h:.0f}px — split it into two "
                            "diagrams rather than shrinking it")
        if m["fmax"] > H2_PX:
            problems.append(f"GLYPH {m['fmax']:.1f}px > h2 {H2_PX:.1f}px")
        if m["fmodal"] > BODY_PX:
            problems.append(f"BODY modal glyph {m['fmodal']:.1f}px > body {BODY_PX:.0f}px")
    def too_small(what, rendered, floor):
        problem = f"TINY {what}{rendered:.1f}px < {floor:.0f}px"
        if m["scale"] < 1.0:
            # Say how much narrower, because "TINY" alone leaves the author guessing whether
            # the fix is one fewer box, shorter labels or half the diagram — and the numbers
            # to answer that are right here. Text shrinks in proportion to the width overrun,
            # so the width that puts the smallest glyph back on the floor is nat_w * fmin/MIN.
            target = m["nat_w"] * rendered / floor
            problem += (f" — {m['nat_w']:.0f}px wide, scaled to {m['scale']:.2f} to fit the "
                        f"{avail_w:.0f}px column. It has to come down to ~{target:.0f}px, i.e. "
                        f"{100 * (1 - target / m['nat_w']):.0f}% less width: wrapping a long "
                        "edge label onto two lines is the cheapest width there is, then shorter "
                        "labels and fewer columns, then one box fewer")
        return problem

    if m["fmin"] < MIN_READABLE:
        problems.append(too_small("", m["fmin"], MIN_READABLE))
    if m["fmin_edge"] is not None and m["fmin_edge"] < MIN_READABLE_EDGE:
        problems.append(too_small("edge label ", m["fmin_edge"], MIN_READABLE_EDGE))
    if m["fmin_detail"] is not None and m["fmin_detail"] < MIN_READABLE_DETAIL:
        problems.append(too_small("subtitle ", m["fmin_detail"], MIN_READABLE_DETAIL))
    detail = (f"{m['nat_w']:.0f}x{m['nat_h']:.0f} @{m['scale']:.2f} "
              f"modal {m['fmodal']:.1f} range {m['fmin']:.1f}-{m['fmax']:.1f}"
              + (f" edge {m['fmin_edge']:.1f}" if m["fmin_edge"] is not None else "")
              + (f" subtitle {m['fmin_detail']:.1f}" if m["fmin_detail"] is not None else ""))
    return Result(name, "size", problems, detail)
