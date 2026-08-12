"""WCAG AA contrast, measured in BOTH themes.

Works structurally on the SVG — the exact declared colours — rather than on rasterised
pixels, so antialiasing cannot skew a ratio and no browser is needed. For every `<text>`:
resolve its fill, find the shape painted underneath it (the last shape in document order
whose box contains the text's anchor), and compute the WCAG 2.1 ratio. Text with nothing
behind it is measured against the page background, which is how dark-mode breakage
surfaces — a colour that is fine on white is often invisible on #16181d.

Thresholds: 4.5:1 normal, 3:1 for large text (>=18.66px *as rendered*, so a diagram scaled
down to fit loses the large-text allowance along with the size).

Two things it deliberately does:

  * **`<mask>` contents are excluded from the shapes considered.** A mask defines
    visibility and is never painted. d2 puts a full-canvas white rect inside one, which
    would otherwise be picked as the backdrop for every glyph above it and report a
    confident, bogus pass.
  * **Both themes, always.** A single-theme check is not half a gate, it is a gate that
    misses the entire class of bug this was built for.

What it cannot see, stated plainly rather than papered over: **callout text**. d2 renders a
`tooltip.near` label as a `<foreignObject>` — HTML inside SVG — whose colour is inherited
from the host page's CSS, not declared in the SVG. Nothing in the file says what colour
that text is, so no structural check can rule on it. The callout *box* colours are checked
(they resolve through the palette); its text is covered by the clipping gate in a real
browser and by the fixed `--d-callout-bg` / page-foreground pairing.
"""
import re

from .. import palette
from . import GateError, Result

LARGE_PX = 18.66

_NAMED = {"white": (255, 255, 255), "black": (0, 0, 0), "grey": (128, 128, 128),
          "gray": (128, 128, 128), "red": (255, 0, 0), "green": (0, 128, 0),
          "blue": (0, 0, 255)}

_MASK = re.compile(r"<mask\b.*?</mask>", re.S)
_SHAPE = re.compile(r"<(rect|ellipse|circle|polygon|path)\b([^>]*)>")
_NODES = re.compile(r"<g\b[^>]*>|</g>|<text\b[^>]*>.*?</text>", re.S)


def parse_color(value, default=None):
    if not value:
        return default
    value = value.strip().lower()
    if value in ("none", "transparent"):
        return None
    m = re.fullmatch(r"#([0-9a-f]{3})", value)
    if m:
        return tuple(int(ch * 2, 16) for ch in m.group(1))
    m = re.fullmatch(r"#([0-9a-f]{6})", value)
    if m:
        h = m.group(1)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    m = re.fullmatch(r"rgba?\(([^)]*)\)", value)
    if m:
        parts = [p.strip() for p in m.group(1).split(",")]
        try:
            return tuple(int(float(parts[i])) for i in range(3))
        except (ValueError, IndexError):
            return default
    return _NAMED.get(value, default)


def luminance(rgb):
    def channel(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(x) for x in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(a, b):
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def css_rules(svg):
    """class name -> {property: value} from every `<style>` block, last rule winning."""
    out = {}
    for block in re.findall(r"<style[^>]*>(.*?)</style>", svg, re.S):
        block = re.sub(r"@[\w-]+[^{]*\{(?:[^{}]|\{[^}]*\})*\}", "", block)  # drop at-rules
        for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", block):
            decl = {}
            for part in body.split(";"):
                if ":" in part:
                    key, value = part.split(":", 1)
                    decl[key.strip()] = value.strip()
            if not decl:
                continue
            for one in selector.split(","):
                classes = re.findall(r"\.([A-Za-z_][\w-]*)", one)
                if classes:
                    out.setdefault(classes[-1], {}).update(decl)
    return out


def _masked_spans(svg):
    return [(m.start(), m.end()) for m in _MASK.finditer(svg)]


def shapes(svg):
    """(pos, bbox, fill, classes) for every painted shape, in document order."""
    spans = _masked_spans(svg)

    def in_mask(i):
        return any(a <= i < b for a, b in spans)

    out = []
    for m in _SHAPE.finditer(svg):
        if in_mask(m.start()):
            continue
        kind, attrs = m.group(1), m.group(2)

        def attr(name):
            g = re.search(rf'\s{name}="([^"]*)"', attrs)
            return g.group(1) if g else None

        fill = attr("fill")
        if fill is None:
            style = attr("style") or ""
            g = re.search(r"fill:\s*([^;]+)", style)
            fill = g.group(1) if g else None
        classes = (attr("class") or "").split()
        try:
            if kind == "rect":
                x, y, w, h = (float(attr(k) or 0) for k in ("x", "y", "width", "height"))
                box = (x, y, x + w, y + h)
            elif kind in ("ellipse", "circle"):
                cx, cy = float(attr("cx") or 0), float(attr("cy") or 0)
                rx = float(attr("rx") or attr("r") or 0)
                ry = float(attr("ry") or attr("r") or 0)
                box = (cx - rx, cy - ry, cx + rx, cy + ry)
            elif kind == "polygon":
                pts = [float(v) for v in re.findall(r"-?[\d.]+", attr("points") or "")]
                if not pts:
                    continue
                box = (min(pts[0::2]), min(pts[1::2]), max(pts[0::2]), max(pts[1::2]))
            else:  # path — bbox approximated from its coordinate pairs
                pts = [float(v) for v in re.findall(r"-?\d+\.?\d*", attr("d") or "")]
                if len(pts) < 4:
                    continue
                box = (min(pts[0::2]), min(pts[1::2]), max(pts[0::2]), max(pts[1::2]))
        except (TypeError, ValueError, IndexError):
            continue
        out.append((m.start(), box, fill, classes))
    return out


def texts(svg):
    """Yield each `<text>` with the classes of every open ancestor `<g>`.

    Ancestor classes matter because SVG text is routinely coloured by a rule scoped to a
    parent group rather than by an attribute on the element itself.
    """
    stack = []
    for m in _NODES.finditer(svg):
        token = m.group(0)
        if token.startswith("</g"):
            if stack:
                stack.pop()
            continue
        if token.startswith("<g"):
            c = re.search(r'\sclass="([^"]*)"', token)
            stack.append(c.group(1).split() if c else [])
            continue
        inner = re.match(r"<text\b([^>]*)>(.*?)</text>", token, re.S)
        if not inner:
            continue
        attrs, body = inner.group(1), inner.group(2)
        content = re.sub(r"<[^>]+>", "", body).strip()
        if not content:
            continue

        def attr(name):
            g = re.search(rf'\s{name}="([^"]*)"', attrs)
            return g.group(1) if g else None

        try:
            x, y = float(attr("x") or 0), float(attr("y") or 0)
        except ValueError:
            continue
        style = attr("style") or ""
        size = attr("font-size") or ""
        if not size:
            g = re.search(r"font-size:\s*([\d.]+)", style)
            size = g.group(1) if g else "14"
        fill = attr("fill")
        if not fill:
            g = re.search(r"fill:\s*([^;]+)", style)
            fill = g.group(1) if g else None
        own = (attr("class") or "").split()
        ancestors = [c for level in stack for c in level]
        yield (m.start(), x, y, float(re.sub(r"[^\d.]", "", size) or 14), fill,
               own + ancestors, content)


def worst_in_theme(svg, theme, scale=1.0):
    """The lowest-contrast glyph in one theme: (ratio, required, text, rendered_px)."""
    resolved = palette.resolve(svg, theme)
    rules = css_rules(resolved)
    painted = shapes(resolved)
    page = parse_color(palette.PAGE[theme])

    def class_color(classes, prop):
        for c in reversed(classes):
            if c in rules and prop in rules[c]:
                return rules[c][prop]
        return None

    lowest = None
    for pos, x, y, size, fill, classes, content in texts(resolved):
        fg = (parse_color(fill) or parse_color(class_color(classes, "fill"))
              or parse_color("#000"))
        bg = None
        for spos, (x0, y0, x1, y1), sfill, sclasses in painted:
            if spos > pos:
                break
            colour = parse_color(sfill) or parse_color(class_color(sclasses, "fill"))
            if colour is None:
                continue
            if x0 - 1 <= x <= x1 + 1 and y0 - 1 <= y <= y1 + 1:
                bg = colour
        if bg is None:
            bg = page
        rendered = size * scale
        needed = 3.0 if rendered >= LARGE_PX else 4.5
        found = ratio(fg, bg)
        if lowest is None or found < lowest[0]:
            lowest = (found, needed, content[:30], rendered)
    return lowest


def check(svg, name="diagram", scale=1.0, themes=("light", "dark")):
    """WCAG AA in both themes. Raises GateError if there is no text to measure.

    `themes` narrows it to one for a standalone image, whose colours are baked to a single
    theme. Checking both there would measure that theme's colours against the *other* theme's
    page background and report a confident failure about a combination that cannot occur.
    An embedded SVG keeps both, since it really does render both ways.
    """
    if "<svg" not in svg:
        raise GateError("no <svg> element to check")
    worst, problems, cells = {}, [], []
    for theme in themes:
        found = worst_in_theme(svg, theme, scale=scale)
        if found is None:
            # No text at all means the parse found nothing, and a contrast gate with
            # nothing to measure must not report success.
            raise GateError(f"{theme}: no <text> elements found — nothing to measure")
        worst[theme] = found
        cells.append(f"{theme[0]} {found[0]:.2f}:1")
        if found[0] < found[1]:
            problems.append(f"{theme}: {found[0]:.2f}:1 (needs {found[1]}:1) "
                            f"on “{found[2]}”")
    return Result(name, "contrast", problems, "  ".join(cells))
