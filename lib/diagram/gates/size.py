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
MAX_H = 800.0          # one viewport, with room left for browser chrome
H2_PX = 26.6           # 1.4rem at a 19px root
BODY_PX = 19.0         # 1rem at a 19px root
MIN_READABLE = 11.0    # below this, text in body copy is effectively unreadable

_FONT_ATTR = re.compile(r'font-size="([\d.]+)')
_FONT_CSS = re.compile(r"font-size:\s*([\d.]+)px")
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

    modal = collections.Counter(fonts).most_common(1)[0][0]
    rendered = [f * unit * scale for f in fonts]
    return {
        "nat_w": nat_w, "nat_h": nat_h, "scale": scale,
        "rend_w": nat_w * scale, "rend_h": nat_h * scale,
        "fmax": max(rendered), "fmin": min(rendered), "fmodal": modal * unit * scale,
    }


def check(svg, name="diagram", avail_w=AVAIL_W, max_h=MAX_H, standalone=False):
    """All four size-family gates in one Result.

    `standalone=True` is for an image opened as a file rather than embedded in a page, and it
    drops three of the four checks — not as a relaxation, but because they are all statements
    about a page that is not there:

    * **height** bounded a figure to one viewport so a reader could take it in without
      scrolling past it mid-article. A file in a viewer is scrolled and zoomed by definition.
    * **maximum glyph** compared against an `h2`, i.e. against surrounding prose.
    * **modal glyph** compared against body text, likewise.

    Getting this wrong has a specific cost, and it was observed: applying the page's 777px
    content width to a standalone image makes any wide diagram "fail", and the only fix the
    author has is to split it — so a nine-table schema that would have been perfectly legible
    at full size gets chopped into two for no reason at all.

    What survives is the one check that is about the drawing itself: nothing may render below
    ~11px. At natural scale that is a statement about the authored font sizes.
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
    if m["fmin"] < MIN_READABLE:
        problems.append(f"TINY {m['fmin']:.1f}px < {MIN_READABLE:.0f}px")
    detail = (f"{m['nat_w']:.0f}x{m['nat_h']:.0f} @{m['scale']:.2f} "
              f"modal {m['fmodal']:.1f} range {m['fmin']:.1f}-{m['fmax']:.1f}")
    return Result(name, "size", problems, detail)
