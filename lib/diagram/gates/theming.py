"""Nothing in the SVG may quietly take its appearance from the page around it.

Colours, mostly — and one thing that is not a colour, at the bottom.

The cheapest gate here and the one most likely to catch a d2 upgrade. Theming works by
rewriting d2's baked-in colour literals to CSS vars; a literal with no mapping simply stays
what d2 chose, which means it keeps its light-mode value on a dark page. That failure is
easy to miss by eye — one hairline or one row stripe staying pale — and trivial to detect
by counting.

It is also the gate that decides which d2 features are usable at all. `shape: code` is
excluded from the spec's shape list because it brings its own syntax-highlighting theme
(`#1e1e2e`, `#cdd6f4`) that no palette entry claims, and this is what surfaced that.

Mask contents are exempt: a `<mask>` is luminance data, not paint, and its colours must
stay exactly as d2 authored them — rewriting them inverts the mask and blanks the drawing.
"""
import re

from .. import palette
from . import GateError, Result

_VAR = re.compile(r"var\((--[\w-]+)\)")
# A <text> with no class of its own. `\b` keeps it off `<textPath>`.
_UNCLASSED = re.compile(r"<text\b(?![^>]*\bclass=)[^>]*>")


def check(svg, name="diagram"):
    """Report colour literals with no CSS-var mapping, var refs with no definition, and text
    with no class to take a font from."""
    if "<svg" not in svg:
        raise GateError("no <svg> element to check")
    problems = []

    missing = palette.unmapped(svg)
    if missing:
        listed = ", ".join(f"{colour} x{count}" for colour, count in
                           sorted(missing.items(), key=lambda kv: -kv[1]))
        problems.append(f"unmapped literal(s): {listed} — these keep their light-mode "
                        "value on a dark page; add them to palette.py or stop using the "
                        "d2 feature that emits them")

    # The mirror-image failure: a var the page never defines resolves to nothing, so the
    # attribute is simply dropped and the element renders unpainted.
    undefined = sorted({m.group(1) for m in _VAR.finditer(svg)} - set(palette.BY_VAR))
    if undefined:
        problems.append(f"undefined CSS var(s): {', '.join(undefined)} — "
                        "not defined by palette.css_block()")

    # The same failure in a property that is not a colour. d2 scopes its embedded font to
    # `.text` / `.text-italic` and gives those classes nothing else, so a <text> WE add
    # without one inherits whatever font the host page sets — Georgia on the explainer, which
    # shipped a sequence diagram's group names in a serif while every label d2 drew was sans.
    # It survived because it looks like a design choice rather than a bug, and because a
    # standalone render has no page to inherit from and comes out right.
    bare = len(_UNCLASSED.findall(svg))
    if bare:
        problems.append(f"{bare} <text> element(s) with no class — d2 scopes its font to "
                        "`.text`, so these inherit the host page's font instead (a serif, on "
                        "the explainer page). Whatever adds them must set `class=\"text\"`")

    used = len({m.group(1) for m in _VAR.finditer(svg)})
    return Result(name, "theming", problems,
                  f"{used} vars, {sum(missing.values())} unmapped, {bare} unclassed text")
