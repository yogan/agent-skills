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
import os
import re
import shutil
import subprocess
import sys
import tempfile

from . import compact
from . import d2 as d2mod
from . import palette

# d2 version this recipe was measured against. Several behaviours it relies on are
# undocumented (the table property coupling, the missing intrinsic size, the 1.3x header
# scale), so a version bump is a reason to re-run the gates, not a routine upgrade.
PINNED_VERSION = "0.8.1"

# d2's own callout paint, matched verbatim. Plain white with a grey hairline: invisible
# against a dark page and too timid against a light one. Tagging it `d2-callout` at the
# same time is what gives HOST_CSS something to select — d2 offers no class of its own.
CALLOUT_ATTRS = 'fill="white" stroke="#DEE1EB"'
CALLOUT_REPLACEMENT = ('class="d2-callout" fill="var(--d-callout-bg)" '
                       'stroke="var(--d-callout-br)"')

# CSS the host page MUST ship for these SVGs to render correctly. Not optional styling.
HOST_CSS = """\
/* d2 emits a callout as <foreignObject height="24"><div class="md"><p>…</p></div>, and
   ships no paragraph reset. The browser default `p{margin:1em 0}` then pushes the text
   out of its 24px box and only the top sliver of the glyphs shows — which reads as a
   colour or contrast problem and is actually a clip. The missing font-family is the
   second half of the same bug: the div otherwise inherits the page's body font, whose
   metrics differ from the font d2 measured the box with, so the text re-wraps. */
.diagram foreignObject{overflow:visible}
.diagram foreignObject .md{display:flex;align-items:center;height:100%;
  font-family:system-ui,-apple-system,'Segoe UI',sans-serif}
.diagram foreignObject .md p{margin:0;line-height:1.2;font-size:12.5px;white-space:nowrap}
/* A callout is an annotation ABOUT the drawing, so it should read as sitting above it.
   Depth does that; a dashed outline does not — d2 welds a solid pointer triangle onto the
   box and a dashed edge fights it at the junction. Keep fill-opacity >= 0.94: the contrast
   gate measures the solid colour, so heavier transparency drifts away from what it
   verified. A plain shadow vanishes on a dark page, hence the accent glow in dark mode. */
.diagram .d2-callout{fill-opacity:.95;filter:drop-shadow(0 2px 5px rgba(0,0,0,.28))}
/* The shadow paints outside the viewBox and the <svg> clips it by default, so a callout
   flush with the edge lost its glow. Diagram geometry is kept inside the viewBox by the
   placement pass; this only frees the shadow into the card's padding. */
.diagram svg{overflow:visible}
.diagram svg{max-width:100%;height:auto}
"""

# The one theme-dependent rule, kept apart from HOST_CSS so a page can emit it under whatever
# selector its dark mode actually uses. A plain drop shadow disappears against a dark
# background, so dark mode gets an accent glow as well.
CALLOUT_DARK_CSS = """\
{dark} .diagram .d2-callout{{fill-opacity:.94;
  filter:drop-shadow(0 0 5px rgba(224,137,90,.40)) drop-shadow(0 2px 4px rgba(0,0,0,.55))}}
"""


def callout_dark_css(dark_selector="[data-theme=dark]"):
    """`CALLOUT_DARK_CSS` bound to one dark-mode selector."""
    return CALLOUT_DARK_CSS.format(dark=dark_selector)


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


def compile_source(source, animate_interval=0, pad=8, binary="d2"):
    """d2 source -> raw SVG text.

    `--theme 0` is deliberate: the palette is applied by our own literals and by the
    substitution below, so d2's themes would only add colours nothing maps.

    Output goes to a temp file rather than stdout because an animated diagram is
    "multiboard" output, and d2 refuses to write that to stdout at all
    ("multiboard output cannot be written to stdout"). Routing every render through a file
    keeps one code path instead of branching on whether this diagram animates.
    """
    if d2_version(binary) is None:
        raise RenderError(f"d2 not found on PATH "
                          f"(install: brew install d2, pin {PINNED_VERSION})")
    with tempfile.TemporaryDirectory(prefix="lib-diagram-") as tmp:
        target = os.path.join(tmp, "out.svg")
        cmd = [binary, "--pad", str(pad), "--theme", "0"]
        if animate_interval:
            cmd += ["--animate-interval", str(animate_interval)]
        cmd += ["-", target]
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
    if theme_vars:
        svg = palette.to_vars(svg)
    return svg


def _maybe_compact(raw, spec, name):
    """Close a sequence diagram's dead vertical space; see `compact.py`.

    A failure here is reported and swallowed. The compaction is a legibility improvement on
    output d2 already laid out correctly, so falling back to d2's spacing costs height and
    nothing else — where raising would deny the caller a diagram over cosmetics.
    """
    if spec.get("kind") != "sequence":
        return raw
    try:
        return compact.compact_sequence(raw)
    except compact.CompactError as exc:
        print(f"{name}: could not compact the sequence rows ({exc}) — "
              "falling back to d2's spacing", file=sys.stderr)
        return raw


def render(spec, name="diagram", animate_interval=1800, binary="d2", theme_vars=True):
    """Spec -> embeddable SVG, for a host page that ships `page_css()`.

    `name` namespaces the SVG's ids, so it must be unique within a page. For a file to open
    on its own, use `standalone()` instead — this output depends on the page.
    """
    source = d2mod.emit(spec)
    interval = animate_interval if d2mod.is_animated(spec) else 0
    raw = compile_source(source, animate_interval=interval, binary=binary)
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


def standalone(spec, name="diagram", theme="light", animate_interval=1800, binary="d2"):
    """Spec -> a self-contained SVG that can be opened on its own.

    Three things separate this from `render()`, and all three are what make an SVG file
    actually work by itself rather than only inside a page that cooperates:

    1. **The colours are baked**, not `var()` references. An undefined custom property makes
       the browser drop the whole attribute, so a themed SVG opened directly renders as
       unpainted shapes.
    2. **The canvas is painted.** See d2.py's `_prelude` — the exact inverse of the embedded
       case, and not optional: left transparent, the drawing is composited onto whatever the
       viewer uses, and a dark one on white gives its muted edge labels no contrast at all.
       Pure white in light mode, so the image disappears into a browser's own white instead of
       sitting on it as a faintly grey slab.
    3. **The CSS travels inside the file**, in a `<style>` element, because a callout's text
       is HTML in a `<foreignObject>` and d2 ships no paragraph reset for it.

    Defaults to light. A file has no toggle, so it has to pick one, and it is viewed inside a
    frame it cannot paint — a browser's white page, an image viewer's chrome. A dark drawing
    sits in that frame as a slab that fights its surroundings; a light one blends.
    """
    if theme not in ("light", "dark"):
        raise RenderError(f"theme must be 'light' or 'dark', not {theme!r}")
    source = d2mod.emit(spec, background=palette.CANVAS, standalone=True)
    interval = animate_interval if d2mod.is_animated(spec) else 0
    raw = compile_source(source, animate_interval=interval, pad=STANDALONE_PAD,
                         binary=binary)
    # Substitute to vars first, then resolve them down to one theme. The detour exists so
    # mappability can be checked while it still means something: once the colours are
    # concrete, every one of them is a non-key and `unmapped()` can no longer tell an
    # un-themeable literal from a correctly baked one. So the theming gate has no standalone
    # equivalent — this check replaces it, at the only moment it can run.
    svg = postprocess(_maybe_compact(raw, spec, name), name, theme_vars=True)
    missing = palette.unmapped(svg)
    if missing:
        listed = ", ".join(f"{colour} x{count}" for colour, count in missing.items())
        raise RenderError(
            f"{name}: d2 emitted colour(s) with no palette mapping: {listed}. A standalone "
            "image bakes one theme, so an unmapped literal would be frozen at its light-mode "
            "value. Add it to palette.py, or stop using the d2 feature that emits it.")
    svg = palette.resolve(svg, theme)
    return _inline_style(svg, STANDALONE_CSS + STANDALONE_CALLOUT[theme])


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
