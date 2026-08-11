#!/usr/bin/env python3
"""THROWAWAY — one colour map, three consumers.

The production render.py already does literal-colour -> var(--x) substitution on graphviz
output so the embedded SVG follows the page's light/dark toggle. This module generalises
that to all three engines, and lets the contrast checker resolve the same map to CONCRETE
light or dark values so both themes can be measured.

Columns: literal emitted by the engine -> (css var, light value, dark value)
"""

# role literals we chose ourselves (shared warm palette)
ROLES = {
    # text / lines
    "#1a1a1a": ("--d-fg",        "#1a1a1a", "#e8e6e1"),
    "#6b6b6b": ("--d-muted",     "#6b6b6b", "#9a9a9a"),
    "#b5541f": ("--d-accent",    "#b5541f", "#e0895a"),
    # group container
    "#faf8f4": ("--d-grp-bg",    "#faf8f4", "#262b34"),   # NOT #1f2229: that is the card surface, so the group box vanished in dark
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

# literals the ENGINE forces on us and we cannot set at generation time
ENGINE_LOCKED = {
    # d2 sql_table / class inner columns (type + constraint + comment)
    "#0d32b2": ("--d-fg",     "#1a1a1a", "#e8e6e1"),
    "#4a6ff3": ("--d-link",   "#2b5fd0", "#8ab4f8"),
    "#676c7e": ("--d-muted",  "#6b6b6b", "#9a9a9a"),
    "#0a0f25": ("--d-fg",     "#1a1a1a", "#e8e6e1"),
    "#cfd2dd": ("--d-grp-br", "#d9d4c8", "#3a3d44"),
    "#dee1eb": ("--d-grp-bg", "#faf8f4", "#262b34"),
    "#e3e9fd": ("--d-svc-bg", "#ece4f7", "#2b2340"),
    "#9499ab": ("--d-muted",  "#6b6b6b", "#9a9a9a"),
    # mermaid defaults that survive themeVariables
    "#333333": ("--d-fg",     "#1a1a1a", "#e8e6e1"),
    "#333":    ("--d-fg",     "#1a1a1a", "#e8e6e1"),
    "#000000": ("--d-fg",     "#1a1a1a", "#e8e6e1"),
    "#000":    ("--d-fg",     "#1a1a1a", "#e8e6e1"),
    "#cccccc": ("--d-grp-br", "#d9d4c8", "#3a3d44"),
    "#ffffff": ("--d-surface", "#ffffff", "#1f2229"),
    # d2 ambient theme colours (row stripes, subtle fills) — 18 of these per diagram
    "#edf0fd": ("--d-surface", "#ffffff", "#1f2229"),
    "#f7f8fe": ("--d-surface", "#ffffff", "#1f2229"),
    "#eef1f8": ("--d-grp-bg",  "#faf8f4", "#262b34"),
    "#b6c3f0": ("--d-grp-br",  "#d9d4c8", "#3a3d44"),
    # mermaid leftovers (sequence diagram in particular)
    "#666666": ("--d-muted",   "#6b6b6b", "#9a9a9a"),
    "#666":    ("--d-muted",   "#6b6b6b", "#9a9a9a"),
    "#999999": ("--d-muted",   "#6b6b6b", "#9a9a9a"),
    "#999":    ("--d-muted",   "#6b6b6b", "#9a9a9a"),
    "#eaeaea": ("--d-grp-br",  "#d9d4c8", "#3a3d44"),
    "#e0e0e0": ("--d-grp-br",  "#d9d4c8", "#3a3d44"),
    "#1c0a1c": ("--d-fg",      "#1a1a1a", "#e8e6e1"),
    "#edf2ae": ("--d-cache-bg","#fdf0d5", "#3a2f14"),
    # text-grade role variants (d2 reuses shape `fill` as member-name text colour)
    "#2c6b30": ("--d-store-tx", "#2c6b30", "#7fd08b"),
    "#6a3fa8": ("--d-svc-tx",   "#6a3fa8", "#c4aef0"),
    "#a32b1f": ("--d-ext-tx",   "#a32b1f", "#f79b8f"),
    "#7a5410": ("--d-cache-tx", "#7a5410", "#e8c98a"),
    "#5a5a5a": ("--d-neutral-tx", "#5a5a5a", "#b0b0b0"),
    # sentinels so d2's three coupled table/class roles can theme INDEPENDENTLY:
    #   fill       -> border + header bg + member text   (role colour, flips light in dark mode)
    #   stroke     -> body background                    (#fffffe)
    #   font-color -> header title                       (#fffffd, must contrast the header bg)
    "#fffffe": ("--d-tbl-bg",    "#ffffff", "#1f2229"),
    "#fffffd": ("--d-tbl-title", "#ffffff", "#16181d"),
}

# vars with no literal key of their own — the d2 tooltip callout is retargeted by
# attribute pair in gallery.embed(), because its box is plain `fill="white"`.
EXTRA_VARS = {
    "--d-callout-bg": ("#fff4e8", "#2a2114"),
    "--d-callout-br": ("#b5541f", "#e0895a"),
}

ALL = {**ROLES, **ENGINE_LOCKED}


def css_block():
    """`:root` + `[data-theme=dark]` definitions for every var used above."""
    seen = {}
    for var, light, dark in ALL.values():
        seen[var] = (light, dark)
    seen.update(EXTRA_VARS)
    light = "".join(f"{v}:{l};" for v, (l, d) in seen.items())
    dark = "".join(f"{v}:{d};" for v, (l, d) in seen.items())
    return f":root{{{light}}}\n[data-theme=dark]{{{dark}}}\n"


def _sub(svg, pick):
    """Replace every known literal (case-insensitively) with pick(entry).

    CRITICAL: <mask> contents are skipped. A mask works on LUMINANCE (white = show,
    black = hide), so rewriting #ffffff to a dark theme colour inside a mask inverts it
    and blanks the whole drawing. d2 uses a mask for class/sql_table headers, so blanket
    colour substitution — the technique that themes graphviz output for free — is only
    safe outside mask regions.
    """
    import re
    pattern = re.compile("|".join(re.escape(k) for k in sorted(ALL, key=len, reverse=True)), re.I)
    out, pos = [], 0
    for m in re.finditer(r"<mask\b.*?</mask>", svg, re.S):
        out.append(pattern.sub(lambda x: pick(ALL[x.group(0).lower()]), svg[pos:m.start()]))
        out.append(m.group(0))          # verbatim
        pos = m.end()
    out.append(pattern.sub(lambda x: pick(ALL[x.group(0).lower()]), svg[pos:]))
    return "".join(out)


def to_vars(svg):
    return _sub(svg, lambda e: f"var({e[0]})")


def to_light(svg):
    return _sub(svg, lambda e: e[1])


def to_dark(svg):
    return _sub(svg, lambda e: e[2])


def unmapped(svg):
    """Literals still present that we have NO mapping for — these break theming."""
    import re, collections
    found = collections.Counter()
    for c in re.findall(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b", svg):
        if c.lower() not in ALL:
            found[c.lower()] += 1
    return found
