"""Shrinking a callout box to the text it actually holds.

d2 sizes a `tooltip.near` box by measuring the note with its OWN font, and then the box is
filled by a browser laying the same note out in the host page's font — `system-ui` at 12.5px,
which `render.HOST_CSS` sets because the alternative is the text re-wrapping out of its
24px-high `<foreignObject>`. The two measurements do not agree, and they cannot: the second
font is not known to the first program.

What the difference looks like: the note "the only entry point" gets a 152px box holding a
132px `<foreignObject>` — 10px of padding a side, which is what d2 intends — and renders about
110px of glyphs inside it. `.md` is a flex row, so all 22px of slack collects on the right and
the callout reads as a box with a hole in one end of it.

So the box is re-cut here, from the width the text really takes. That number can only come
from a browser (`browser.text_widths`), and it depends on nothing but the string, so it is
measured once per document and cached: `figure.draw` primes the cache with every note in the
document in one launch, and every subsequent render — the placement search alone is 64 of
them — is a dictionary lookup.

**Unmeasured text is left alone.** A render with no primed cache (every fast unit test, and
anything calling `render.render` directly) comes out exactly as d2 drew it. Guessing the width
from a character count was the alternative and it is the wrong risk: the text is `nowrap`
inside an `overflow:visible` box, so an underestimate does not clip, it spills the note out
past its own border and over the drawing.
"""
import re

from . import browser as browser_mod

# d2's own padding between the box and the text it holds, in px a side. Read off its output
# rather than chosen: every callout it emits puts the `<foreignObject>` 10px inside the rect.
# Re-cutting the box to `text + 2 * PAD` is what makes the two sides equal.
PAD = 10

# How near an edge the pointer has to be to count as anchored to it rather than centred. d2
# puts it 20px in from the corner for a `*-left` / `*-right` anchor and at the midpoint for a
# `*-center` one, so anything inside this band is one of the two corner cases.
CORNER = 24

# Never grow a box, and never shrink one by less than this. Growing would mean the measured
# text is already wider than d2's box, where the honest answer is that the note is overflowing
# and the fix is fewer words. A sub-pixel trim is not worth a diff.
MIN_TRIM = 2

_GROUP = re.compile(r'<g class="positioned-tooltip">(.*?)</g>', re.S)
_RECT = re.compile(r'<rect x="([-\d.]+)" y="([-\d.]+)" width="([\d.]+)" height="([\d.]+)"')
_POINTER = re.compile(r'<path d="M ([-\d.]+) ([-\d.]+) L ([-\d.]+) ([-\d.]+) '
                      r'L ([-\d.]+) ([-\d.]+) Z"')
_FO = re.compile(r'<foreignObject x="([-\d.]+)" y="([-\d.]+)" width="([\d.]+)" '
                 r'height="([\d.]+)"')
_TEXT = re.compile(r"<p>(.*?)</p>", re.S)

_WIDTHS = {}


def prime(texts):
    """Measure any of `texts` not already known, in one browser launch. Returns nothing.

    Silent when there is no browser: an unmeasured note keeps d2's box, which is the same
    drawing this module produced before it existed.
    """
    unknown = [t for t in dict.fromkeys(texts) if t and t not in _WIDTHS]
    if not unknown:
        return
    try:
        widths = browser_mod.text_widths(_page(unknown))
    except browser_mod.BrowserError:
        return
    if len(widths) != len(unknown):
        return
    _WIDTHS.update(zip(unknown, widths))


def known(text):
    """The measured width of `text`, or None."""
    return _WIDTHS.get(text)


def _escape(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _page(texts):
    """A page that lays each note out under the real callout CSS and nothing else.

    The `<foreignObject>` wrapper is not decoration: `HOST_CSS` selects `.diagram foreignObject
    .md p`, so a `<p>` measured outside one would be measured in the page's serif body font at
    the browser's default size — a number with no relationship to the callout it is for.
    """
    from . import render as render_mod    # local: render imports this module
    rows = "".join(
        f'<foreignObject x="0" y="{index * 30}" width="4000" height="24">'
        f'<div xmlns="http://www.w3.org/1999/xhtml" class="md">'
        f'<p data-w="{index}">{_escape(text)}</p></div></foreignObject>'
        for index, text in enumerate(texts))
    return (f'<!DOCTYPE html><html data-theme="light"><meta charset="utf-8"><style>'
            f"html{{font-size:{render_mod.ROOT_FONT_PX}px}}{render_mod.page_css()}</style>"
            f'<div class="diagram"><svg xmlns="http://www.w3.org/2000/svg" width="4000" '
            f'height="{30 * len(texts) + 30}">{rows}</svg></div></html>')


def _new_x(rect_x, rect_w, trim, pointer):
    """Where the box's left edge goes when it loses `trim` px, holding the pointer still.

    The pointer points AT the thing the note is about, so it is the one part of the group that
    may not move. Which edge it is on says how the box has to give way:

      * on a side (a `center-left` / `center-right` anchor) — the box hangs off that side, so
        that side stays and the far one comes in.
      * on the top or bottom — d2 puts it 20px in from a corner for a `*-left` / `*-right`
        anchor and at the midpoint for a `*-center` one, so the box keeps whichever edge the
        pointer is near, and a centred one closes in from both.
    """
    x1, y1, x2, y2, _ax, _ay = pointer
    if abs(x1 - x2) < 0.5:                       # base is vertical: pointer on a side
        return rect_x if x1 - rect_x < rect_w / 2 else rect_x + trim
    base = (x1 + x2) / 2 - rect_x
    if base <= CORNER:
        return rect_x
    if base >= rect_w - CORNER:
        return rect_x + trim
    return rect_x + trim / 2


def fit(svg):
    """Return `svg` with every callout box re-cut to `PAD + measured text + PAD`.

    A callout whose note was never measured, or whose text is already wider than the box d2
    gave it, is left exactly as it was.
    """
    out, cursor = [], 0
    for group in _GROUP.finditer(svg):
        body = group.group(1)
        rect, pointer, fo, text = (_RECT.search(body), _POINTER.search(body),
                                   _FO.search(body), _TEXT.search(body))
        if not (rect and pointer and fo and text):
            continue
        width = known(text.group(1).strip())
        if width is None:
            continue
        rect_x, rect_w = float(rect.group(1)), float(rect.group(3))
        fo_x, fo_w = float(fo.group(1)), float(fo.group(3))
        trim = fo_w - width
        if trim < MIN_TRIM:
            continue
        new_x = _new_x(rect_x, rect_w, trim, [float(g) for g in pointer.groups()])
        body = _sub(_RECT, body, {1: new_x, 3: rect_w - trim})
        body = _sub(_FO, body, {1: fo_x + (new_x - rect_x), 3: fo_w - trim})
        out.append((group.start(1), group.end(1), body))
    for start, end, body in reversed(out):
        svg = svg[:start] + body + svg[end:]
    return svg


def _sub(pattern, text, values):
    """Rewrite numbered groups of the first match of `pattern` in `text`."""
    match = pattern.search(text)
    if not match:
        return text
    for index in sorted(values, reverse=True):
        text = text[:match.start(index)] + f"{values[index]:f}" + text[match.end(index):]
    return text
