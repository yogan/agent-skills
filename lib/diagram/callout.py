"""A callout: where it sits on the drawing, and what type its note is set in.

Three jobs, all about words a figure carries beyond the drawing itself.

**Reading a callout off the SVG.** `notes` and `boxes` say where each `tooltip.near` box
landed, in the drawing's own coordinates — which is what the placement search, the route
repair and the edge-label pass measure against.

**Setting the note in the drawing's own type.** d2 sets it several points above the labels of
the drawing it annotates, so the annotation reads louder than the picture; `fit` re-sets it and
re-cuts the box to match. No browser is needed for that: d2 measured the note in the font it
embeds with the figure, and an advance width scales with the type size, so the width at our
size is its own measurement times the ratio.

**Measuring a legend's words in a browser.** Those this repo writes itself, as HTML, and
`compact.add_legend` lays a row out from their widths and draws nothing without them — so that
number can only come from a browser. It depends on nothing but the string, so it is measured
once per document and cached: `figure.draw` primes the cache and every later render is a
dictionary lookup. Guessing a width from a character count is the wrong risk: the words are
`nowrap` inside an `overflow:visible` box, so an underestimate spills them over the drawing.
"""
import re

from . import arrows
from . import browser as browser_mod

# d2's own padding between the box and the words it holds, in px a side. Read off its output
# rather than chosen: every callout it emits puts the words' box 10px inside the rect, and
# re-cutting to `words + 2 * PAD` is what keeps the two sides equal.
PAD = 10

# How near an edge the pointer has to be to count as anchored to it rather than centred. d2
# puts it 20px in from the corner for a `*-left` / `*-right` anchor and at the midpoint for a
# `*-center` one, so anything inside this band is one of the two corner cases.
CORNER = 24

# The type size d2 sets a note at, and where it puts the baseline inside the box it gives the
# words. Both read off its output rather than chosen. The baseline is held as a multiple of
# the type size measured DOWN from the middle of that box, so a note re-set at another size
# stays optically where d2 put it without the box having to change height — and the box
# keeping its height is what holds the callout's own height where every pinned figure has it.
D2_NOTE_PX = 16
NOTE_BASELINE_BELOW_MIDDLE = 0.375

_OPEN = re.compile(r'<g class="positioned-tooltip">')
_RECT = re.compile(r'<rect x="([-\d.]+)" y="([-\d.]+)" width="([\d.]+)" height="([\d.]+)"')
_POINTER = re.compile(r'<path d="M ([-\d.]+) ([-\d.]+) L ([-\d.]+) ([-\d.]+) '
                      r'L ([-\d.]+) ([-\d.]+) Z"')
# The box d2 puts a note's words in: a nested <svg> whose viewBox is exactly the width it
# measured them at, so the two numbers move together when the box is re-cut.
_WRAP = re.compile(r'<svg x="([-\d.]+)" y="([-\d.]+)" width="([\d.]+)" height="([\d.]+)" '
                   r'viewBox="0 0 ([\d.]+) ([\d.]+)"')
_TEXT = re.compile(r"<text\b[^>]*>(.*?)</text>", re.S)
_FONT = re.compile(r'(<text\b[^>]*?)\sfont-size="[\d.]+"')
_BASELINE = re.compile(r'(<text\b[^>]*?\sy=")([\d.]+)(")')
_MARKUP = re.compile(r"<[^>]+>")

_WIDTHS = {}


def _groups(svg):
    """(start, end) of each callout group's contents, the `<g>` tags themselves excluded.

    Found by counting depth rather than by matching the next `</g>`, because the group holds a
    `<g>` of its own — the block d2 renders a note's words in. Stopping at the first close tag
    ends the body just before the words.
    """
    for opened in _OPEN.finditer(svg):
        depth = 1
        for tag in re.finditer(r"<g\b[^>]*>|</g\s*>", svg[opened.end():]):
            depth += -1 if tag.group(0).startswith("</") else 1
            if depth == 0:
                yield opened.end(), opened.end() + tag.start()
                break


def _words(body):
    """One callout's note, read back out of the drawing.

    Joined across every piece of text in the group and stripped of the markup d2 set them in,
    so a note broken over two lines — or split across spans — reads back as the one string it
    was authored as. That is the form the spec wrote and the form `known` is keyed by, so
    anything else would silently fail to match.
    """
    words = " ".join(_MARKUP.sub(" ", m.group(1)) for m in _TEXT.finditer(body))
    return " ".join(words.split())


def prime(texts):
    """Measure any of `texts` not already known, in one browser launch. Returns nothing.

    Silent when there is no browser, which is what keeps every fast test browser-free: a
    legend whose words went unmeasured is not drawn at all, and `compact.add_legend` says so
    rather than laying out a row from guesses.
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
    the browser's default size — a number with no relationship to the row it is for.
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


def notes(svg):
    """Every callout as `(note text, box)`, in the drawing's own coordinates.

    Read from the SVG rather than from a browser because the callers that need it are
    measuring the DRAWING against them — which turn a callout covers, which leg it
    lies along — and the route geometry is only in these coordinates. The browser is
    still the authority on what a callout looks like on a page; this is where it sits
    on the picture.

    The text comes from the callout itself and not from the spec, so a caller naming a
    note in a message cannot name the wrong one: the order d2 emits them in is its
    business, and pairing two lists by index assumed it matches the spec's.
    """
    out = []
    for start, end in _groups(svg):
        body = svg[start:end]
        rect = _RECT.search(body)
        if not rect:
            continue
        x, y, w, h = (float(v) for v in rect.groups())
        out.append((_words(body), arrows.Box((x, y, x + w, y + h))))
    return out


def boxes(svg):
    """Just the boxes — what the placement search needs, which has no use for the text."""
    return [box for _text, box in notes(svg)]


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
    """Set every note in the type the drawing's own labels use, and re-cut its box to match.

    d2 draws a note several points above the labels of the drawing it annotates, so the
    annotation reads louder than the picture it is about. Re-setting it is the whole of the
    look; the rest of this is the box catching up with its words.

    **The new width comes from d2's own measurement, not from a browser.** d2 measured the
    note in the font it embeds with the figure, and an advance width scales with the type
    size, so the width at our size is its width times the ratio — the same property
    `compact.MEASURED_AT` relies on for a legend. Checked against a real browser on every note
    in both corpora: the largest disagreement was under a pixel.

    A callout whose group is not the shape described above is left exactly as it was, because
    a partial re-cut is worse than d2's own box: the words would keep a size the box was not
    cut for.
    """
    from . import render as render_mod          # local: render imports this module
    size = render_mod.ANNOTATION_PX
    out = []
    for start, end in _groups(svg):
        body = svg[start:end]
        rect, pointer, wrap = _RECT.search(body), _POINTER.search(body), _WRAP.search(body)
        if not (rect and pointer and wrap):
            continue
        rect_x, rect_w = float(rect.group(1)), float(rect.group(3))
        wrap_x, wrap_w = float(wrap.group(1)), float(wrap.group(3))
        view_w, view_h = float(wrap.group(5)), float(wrap.group(6))
        trim = wrap_w * (1 - size / D2_NOTE_PX)
        new_x = _new_x(rect_x, rect_w, trim, [float(g) for g in pointer.groups()])
        body = _sub(_RECT, body, {1: new_x, 3: rect_w - trim})
        body = _sub(_WRAP, body, {1: wrap_x + (new_x - rect_x), 3: wrap_w - trim,
                                  5: view_w - trim})
        body = _FONT.sub(rf'\1 font-size="{size:g}"', body)
        baseline = view_h / 2 + size * NOTE_BASELINE_BELOW_MIDDLE
        body = _BASELINE.sub(lambda m: f"{m.group(1)}{baseline:g}{m.group(3)}", body)
        out.append((start, end, body))
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


