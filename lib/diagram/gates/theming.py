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
# A <text> that names no font it will actually get: no class to take one from, and no
# `font-family` of its own either. `\b` keeps it off `<textPath>`.
_FONTLESS = re.compile(r"<text\b(?![^>]*\bclass=)(?![^>]*font-family)[^>]*>")


def check(svg, name="diagram"):
    """Report colour literals with no CSS-var mapping, var refs with no definition, and text
    that names no font it will actually get."""
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

    # The same failure in a property that is not a colour: text that will be set in a font
    # nobody chose. A <text> WE add with neither a class nor a font-family inherits whatever
    # the host page sets — Georgia on the explainer, which shipped a sequence diagram's group
    # names in a serif while every label d2 drew was sans. It survived because it looks like a
    # design choice rather than a bug, and because a standalone render has no page to inherit
    # from and comes out right.
    #
    # EITHER answer passes, because they fix different halves of the same problem. d2's own
    # `.text` gets d2's face, which is the right one for text that belongs to the drawing —
    # but d2 embeds it as a SUBSET of the glyphs the drawing uses, so text added afterwards
    # loses whichever letters the drawing did not need, one character at a time. An annotation
    # therefore names a complete stack itself (see `compact.ANNOTATION_FONT`). What is not
    # allowed is neither.
    bare = len(_FONTLESS.findall(svg))
    if bare:
        problems.append(f"{bare} <text> element(s) name no font — with neither `class=\"text\"` "
                        "nor a `font-family`, these are set in whatever the host page uses (a "
                        "serif, on the explainer page). Text that belongs to the drawing takes "
                        "d2's class; an annotation names a complete stack, since d2's face is "
                        "only subsetted to the drawing's own glyphs")

    used = len({m.group(1) for m in _VAR.finditer(svg)})
    return Result(name, "theming", problems,
                  f"{used} vars, {sum(missing.values())} unmapped, {bare} fontless text")
