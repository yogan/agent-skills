"""Colour literal -> CSS custom property, so one SVG follows the host page's theme toggle.

The technique: d2 bakes concrete hex colours into its output, so the renderer rewrites
every known literal to `var(--d-…)` and the page defines those vars twice — once under
`:root`, once under `[data-theme=dark]`. One SVG, both themes, no JavaScript.

Three things make this less trivial than a search-and-replace:

  * **`<mask>` contents must be skipped.** A mask works on luminance — white shows, black
    hides — so rewriting `#ffffff` to a dark theme value inside one inverts it and blanks
    the drawing. d2 uses masks for table and class headers, so this is not hypothetical.
  * **Some literals are ours and some are d2's.** The ones we choose (`ROLES`) come from
    the emitter; the ones in `ENGINE_LOCKED` are painted by d2 itself and cannot be set
    at generation time, only mapped after the fact.
  * **Two sentinels carry no colour meaning at all.** See `ENGINE_LOCKED`: d2 couples
    three visual roles of a table to two properties, and the only way to theme them
    independently is to feed it near-white values that exist purely to be recognised
    here.

`unmapped()` is the safety net and `gates/theming.py` turns it into a test. Every literal
in this file was verified against real d2 output; a d2 upgrade that introduces a new one
fails that gate instead of silently shipping an unthemed colour.
"""
import collections
import re

# Role literals the emitter chooses itself — a shared warm palette. `-bg` is a shape
# fill, `-br` its border. The dark values are not algorithmic inversions; they were
# picked so the pair passes WCAG AA against the page background in its own theme.
ROLES = {
    # A sentinel, like the table pair below: the canvas behind a STANDALONE image, painted
    # only there (embedded, the root stays transparent — see render.standalone). Deliberately
    # NOT `PAGE` below, which is the host page's colour and has to stay #fafaf8 for the
    # contrast gate to measure an embedded diagram honestly. A file is not on a page: it is
    # shown inside a frame it cannot paint, so it wants pure white, which disappears into a
    # browser's own white rather than sitting on it as a faintly grey slab.
    # Dark maps to the page colour and not the card colour (#1f2229) on purpose — that is
    # what a table's body is filled with, so a canvas of the same value would swallow it.
    "#fffffc": ("--d-canvas",    "#ffffff", "#16181d"),
    # text / lines
    "#1a1a1a": ("--d-fg",        "#1a1a1a", "#e8e6e1"),
    "#6b6b6b": ("--d-muted",     "#6b6b6b", "#9a9a9a"),
    "#b5541f": ("--d-accent",    "#b5541f", "#e0895a"),
    # group container. The dark value must NOT be the card surface (#1f2229) or the
    # container box vanishes into the page in dark mode — it did, and it read as a
    # missing border rather than a colour bug.
    "#faf8f4": ("--d-grp-bg",    "#faf8f4", "#262b34"),
    "#d9d4c8": ("--d-grp-br",    "#d9d4c8", "#3a3d44"),
    # client / frontend
    "#e8effc": ("--d-client-bg", "#e8effc", "#1e2a44"),
    "#3b6fd4": ("--d-client-br", "#3b6fd4", "#7ba4ec"),
    # service / logic
    "#ece4f7": ("--d-svc-bg",    "#ece4f7", "#2b2340"),
    "#7c4dbd": ("--d-svc-br",    "#7c4dbd", "#b39ae6"),
    # persistent store
    "#e3f5e3": ("--d-store-bg",  "#e3f5e3", "#16301c"),
    "#3f9142": ("--d-store-br",  "#3f9142", "#6cc478"),
    # cache / queue
    "#fdf0d5": ("--d-cache-bg",  "#fdf0d5", "#3a2f14"),
    "#d99a2b": ("--d-cache-br",  "#d99a2b", "#e5bb6b"),
    # external boundary
    "#fdecec": ("--d-ext-bg",    "#fdecec", "#3a1414"),
    "#c0392b": ("--d-ext-br",    "#c0392b", "#f0796b"),
}

# Text-grade role colours, used as a table's `fill`. d2 reuses that one property as the
# border, the header background AND the member text, so it has to be dark enough to read
# as text on the body background — a `-bg` pastel would be invisible. Each pair clears
# 4.5:1 against white in light mode and against both #1f2229 and #16181d in dark.
TABLE_ROLES = {
    "#27548f": ("--d-client-tx",  "#27548f", "#8fb8f0"),
    "#6a3fa8": ("--d-svc-tx",     "#6a3fa8", "#c4aef0"),
    "#2c6b30": ("--d-store-tx",   "#2c6b30", "#7fd08b"),
    "#7a5410": ("--d-cache-tx",   "#7a5410", "#e8c98a"),
    "#a32b1f": ("--d-ext-tx",     "#a32b1f", "#f79b8f"),
    "#5a5a5a": ("--d-neutral-tx", "#5a5a5a", "#b0b0b0"),
}

# Literals d2 forces on us. Everything here was found by rendering real diagrams and
# listing the hex values our map did not claim — not by reading d2's source.
ENGINE_LOCKED = {
    # table / class inner columns: type, constraint badge, comment
    "#0d32b2": ("--d-fg",      "#1a1a1a", "#e8e6e1"),
    "#0a0f25": ("--d-fg",      "#1a1a1a", "#e8e6e1"),
    "#4a6ff3": ("--d-link",    "#2b5fd0", "#8ab4f8"),
    "#676c7e": ("--d-muted",   "#6b6b6b", "#9a9a9a"),
    "#9499ab": ("--d-muted",   "#6b6b6b", "#9a9a9a"),
    # ambient theme colours: row stripes, subtle fills, hairlines
    "#cfd2dd": ("--d-grp-br",  "#d9d4c8", "#3a3d44"),
    "#b6c3f0": ("--d-grp-br",  "#d9d4c8", "#3a3d44"),
    "#dee1eb": ("--d-grp-bg",  "#faf8f4", "#262b34"),
    "#eef1f8": ("--d-grp-bg",  "#faf8f4", "#262b34"),
    "#e3e9fd": ("--d-svc-bg",  "#ece4f7", "#2b2340"),
    "#edf0fd": ("--d-surface", "#ffffff", "#1f2229"),
    "#f7f8fe": ("--d-surface", "#ffffff", "#1f2229"),
    "#ffffff": ("--d-surface", "#ffffff", "#1f2229"),
    # Sentinels, not colours. d2 couples three roles of a sql_table/class to two
    # properties -- `fill` is border + header background + member text, `stroke` is the
    # BODY background, `font-color` is only the header title. No single value keeps all
    # three readable in both themes, so the emitter feeds two near-white values that mean
    # nothing to d2 and everything here: they are how the body background and the header
    # title get to theme independently of the role colour.
    "#fffffe": ("--d-tbl-bg",    "#ffffff", "#1f2229"),
    "#fffffd": ("--d-tbl-title", "#ffffff", "#16181d"),
}

# Vars with no literal of their own. d2 paints a `tooltip.near` callout plain
# `fill="white" stroke="#DEE1EB"` and exposes no styling hook for it at all, so
# render.py retargets that exact attribute pair — see `CALLOUT_ATTRS` there.
EXTRA_VARS = {
    "--d-callout-bg": ("#fff4e8", "#2a2114"),
    "--d-callout-br": ("#b5541f", "#e0895a"),
}

ALL = {**ROLES, **TABLE_ROLES, **ENGINE_LOCKED}

# Host page backgrounds, used by the contrast gate as the backdrop when no shape sits behind a
# glyph. This is the explainer page's real background; it is NOT what a standalone image paints
# its canvas with (that is the `--d-canvas` sentinel above).
PAGE = {"light": "#fafaf8", "dark": "#16181d"}

# The literal render.standalone() feeds d2 as the root fill, resolved per theme via --d-canvas.
CANVAS = "#fffffc"

# Named literals the emitter reaches for directly, so no hex appears in d2.py.
FG = "#1a1a1a"          # ordinary label text
MUTED = "#6b6b6b"       # edge lines and edge labels
ACCENT = "#b5541f"      # callouts, captions, container titles, `new` emphasis

_LITERALS = re.compile("|".join(re.escape(k) for k in sorted(ALL, key=len, reverse=True)),
                       re.I)
_MASK = re.compile(r"<mask\b.*?</mask>", re.S)
_ANY_HEX = re.compile(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b")
_VAR = re.compile(r"var\((--[\w-]+)\)")

# var name -> (light, dark), for resolving an already-substituted SVG back to concrete
# colours. Needed because the gates run on the *shipped* artifact, not on raw d2 output:
# the callout colours exist only as var() references (d2 paints the box plain white and
# render.py retargets it), so a gate that only understood literals could not see them.
BY_VAR = {}
for _var, _light, _dark in ALL.values():
    BY_VAR.setdefault(_var, (_light, _dark))
BY_VAR.update(EXTRA_VARS)


def vars_for(role, table=False):
    """The (fill, stroke) literals the emitter should use for `role`.

    `table=True` returns the text-grade colour, because a table's fill doubles as its
    member text; anything pastel enough to be a background is unreadable there.
    """
    if table:
        for literal, (var, _l, _d) in TABLE_ROLES.items():
            if var == f"--d-{role}-tx":
                return literal
        raise KeyError(f"no table colour for role {role!r}")
    fills = {"client": "#e8effc", "svc": "#ece4f7", "store": "#e3f5e3",
             "cache": "#fdf0d5", "ext": "#fdecec", "neutral": "#faf8f4"}
    strokes = {"client": "#3b6fd4", "svc": "#7c4dbd", "store": "#3f9142",
               "cache": "#d99a2b", "ext": "#c0392b", "neutral": "#d9d4c8"}
    return fills[role], strokes[role]


def declarations(theme):
    """Every var this module can emit, as a `--d-x:value;` run for one theme.

    Exposed separately from `css_block` because a host page may key dark mode off something
    other than `[data-theme=dark]` — a page that also honours `prefers-color-scheme` needs
    these same declarations in a media query too, and a page that emitted them under one
    selector only would show a light-coloured diagram on a dark background for any reader who
    never touched its toggle.
    """
    index = 0 if theme == "light" else 1
    seen = {}
    for var, light, dark in ALL.values():
        seen[var] = (light, dark)
    seen.update(EXTRA_VARS)
    return "".join(f"{var}:{pair[index]};" for var, pair in seen.items())


def css_block(dark_selector="[data-theme=dark]"):
    """`:root` + dark-mode definitions for every var this module can emit.

    The host page must ship this, or every colour in the SVG resolves to nothing — an
    undefined var makes the browser drop the whole attribute, leaving shapes unpainted.
    """
    return (f":root{{{declarations('light')}}}\n"
            f"{dark_selector}{{{declarations('dark')}}}\n")


def _sub(svg, pick):
    """Rewrite every known literal outside a `<mask>`, leaving mask contents verbatim."""
    out, pos = [], 0

    def swap(m):
        return pick(ALL[m.group(0).lower()])

    for m in _MASK.finditer(svg):
        out.append(_LITERALS.sub(swap, svg[pos:m.start()]))
        out.append(m.group(0))
        pos = m.end()
    out.append(_LITERALS.sub(swap, svg[pos:]))
    return "".join(out)


def to_vars(svg):
    """For the page: every literal becomes `var(--d-…)` and follows the theme toggle."""
    return _sub(svg, lambda entry: f"var({entry[0]})")


def resolve(svg, theme):
    """Concrete colours for one theme, exactly as the browser would compute them.

    Handles both forms, so a gate can be pointed at raw d2 output or at the finished
    embeddable SVG and get the same answer: bare literals are mapped through `ALL`, and
    `var(--d-…)` references — the form every colour takes after `to_vars` — are looked up
    in `BY_VAR`. An unknown var is left alone rather than guessed at; `unmapped()` and the
    theming gate are what report it.
    """
    index = 1 if theme == "light" else 2
    out = _sub(svg, lambda entry: entry[index])
    return _VAR.sub(
        lambda m: BY_VAR[m.group(1)][index - 1] if m.group(1) in BY_VAR else m.group(0),
        out)


def to_light(svg):
    """For the gates: resolve to concrete light-theme values, as the browser would."""
    return resolve(svg, "light")


def to_dark(svg):
    """For the gates: resolve to concrete dark-theme values."""
    return resolve(svg, "dark")


def unmapped(svg):
    """Counter of literals we have no mapping for — each one breaks theming.

    Mask contents are excluded: they are luminance data that must stay as authored, so a
    black or white value in there needs no entry and reporting it would be a false alarm.
    """
    spans = [(m.start(), m.end()) for m in _MASK.finditer(svg)]
    found = collections.Counter()
    for m in _ANY_HEX.finditer(svg):
        if any(a <= m.start() < b for a, b in spans):
            continue
        colour = m.group(0).lower()
        if colour not in ALL:
            found[colour] += 1
    return found
