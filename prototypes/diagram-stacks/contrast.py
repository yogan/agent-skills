#!/usr/bin/env python3
"""THROWAWAY — WCAG contrast gate for generated diagram SVGs.

Works structurally on the SVG (exact declared colours) rather than on rasterised pixels,
so antialiasing can't skew the result.

For every <text>: resolve its fill, find the shape painted underneath it (last shape in
document order whose bounding box contains the text anchor), and compute the WCAG 2.1
contrast ratio. Text with no shape behind it is measured against the PAGE background —
checked for BOTH themes, which is how the dark-mode breakage shows up.

Thresholds (WCAG AA): 4.5:1 for normal text, 3:1 for large text (>=18.66px).
"""
import re, sys, pathlib
import palette

PAGE_LIGHT = "#fafaf8"
PAGE_DARK = "#16181d"
LARGE_PX = 18.66


# ---------- colour ----------
def parse_color(c, default=None):
    if not c:
        return default
    c = c.strip().lower()
    if c in ("none", "transparent"):
        return None
    m = re.fullmatch(r"#([0-9a-f]{3})", c)
    if m:
        return tuple(int(ch * 2, 16) for ch in m.group(1))
    m = re.fullmatch(r"#([0-9a-f]{6})", c)
    if m:
        h = m.group(1)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    m = re.fullmatch(r"rgba?\(([^)]*)\)", c)
    if m:
        p = [x.strip() for x in m.group(1).split(",")]
        try:
            return tuple(int(float(p[i])) for i in range(3))
        except (ValueError, IndexError):
            return default
    named = {"white": (255, 255, 255), "black": (0, 0, 0), "grey": (128, 128, 128),
             "gray": (128, 128, 128), "red": (255, 0, 0), "green": (0, 128, 0), "blue": (0, 0, 255)}
    return named.get(c, default)


def lum(rgb):
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(x) for x in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(a, b):
    la, lb = lum(a), lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


# ---------- css ----------
def css_rules(svg):
    """class name -> {prop: value} from every <style> block (last wins)."""
    out = {}
    for block in re.findall(r"<style[^>]*>(.*?)</style>", svg, re.S):
        block = re.sub(r"@[\w-]+[^{]*\{(?:[^{}]|\{[^}]*\})*\}", "", block)  # drop at-rules
        for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", block):
            decl = {}
            for d in body.split(";"):
                if ":" in d:
                    k, v = d.split(":", 1)
                    decl[k.strip()] = v.strip()
            if not decl:
                continue
            for one in sel.split(","):
                cls = re.findall(r"\.([A-Za-z_][\w-]*)", one)
                if cls:
                    out.setdefault(cls[-1], {}).update(decl)
    return out


# ---------- geometry ----------
def shapes(svg):
    """(bbox, fill) for every PAINTED shape, in document order.

    <mask> contents are excluded: they define visibility, they are never drawn. d2 puts a
    full-canvas white rect in a mask, which otherwise gets picked as the backdrop for any
    text above it and reports a bogus failure.
    """
    masked = [(m.start(), m.end()) for m in re.finditer(r"<mask\b.*?</mask>", svg, re.S)]

    def in_mask(i):
        return any(a <= i < b for a, b in masked)

    out = []
    for m in re.finditer(r"<(rect|ellipse|circle|polygon|path)\b([^>]*)>", svg):
        if in_mask(m.start()):
            continue
        kind, attrs = m.group(1), m.group(2)

        def a(name):
            g = re.search(rf'\s{name}="([^"]*)"', attrs)
            return g.group(1) if g else None

        fill = a("fill")
        if fill is None:
            st = a("style") or ""
            g = re.search(r"fill:\s*([^;]+)", st)
            fill = g.group(1) if g else None
        cls = (a("class") or "").split()
        try:
            if kind == "rect":
                x, y, w, h = (float(a(k) or 0) for k in ("x", "y", "width", "height"))
                bb = (x, y, x + w, y + h)
            elif kind in ("ellipse", "circle"):
                cx, cy = float(a("cx") or 0), float(a("cy") or 0)
                rx = float(a("rx") or a("r") or 0)
                ry = float(a("ry") or a("r") or 0)
                bb = (cx - rx, cy - ry, cx + rx, cy + ry)
            elif kind == "polygon":
                pts = [float(v) for v in re.findall(r"-?[\d.]+", a("points") or "")]
                xs, ys = pts[0::2], pts[1::2]
                if not xs:
                    continue
                bb = (min(xs), min(ys), max(xs), max(ys))
            else:  # path — bbox from all coordinate pairs (approximation)
                pts = [float(v) for v in re.findall(r"-?\d+\.?\d*", a("d") or "")]
                if len(pts) < 4:
                    continue
                xs, ys = pts[0::2], pts[1::2]
                bb = (min(xs), min(ys), max(xs), max(ys))
        except (TypeError, ValueError, IndexError):
            continue
        out.append((m.start(), bb, fill, cls))
    return out


def texts(svg):
    """Yield each <text> plus the class names of every open ancestor <g>.

    Mermaid styles text through ancestor-scoped rules (`.label text{fill:…}`), so a text
    element's own class list is not enough to resolve its colour.
    """
    stack = []          # [(end_pos_of_open_tag, [classes])]
    events = [(m.start(), m) for m in re.finditer(r"<g\b[^>]*>|</g>|<text\b[^>]*>.*?</text>", svg, re.S)]
    for pos, m in events:
        tok = m.group(0)
        if tok.startswith("</g"):
            if stack:
                stack.pop()
            continue
        if tok.startswith("<g"):
            c = re.search(r'\sclass="([^"]*)"', tok)
            stack.append(c.group(1).split() if c else [])
            continue
        m2 = re.match(r"<text\b([^>]*)>(.*?)</text>", tok, re.S)
        if not m2:
            continue
        attrs, inner = m2.group(1), m2.group(2)
        ancestors = [c for lvl in stack for c in lvl]
        content = re.sub(r"<[^>]+>", "", inner).strip()
        if not content:
            continue

        def a(name):
            g = re.search(rf'\s{name}="([^"]*)"', attrs)
            return g.group(1) if g else None

        try:
            x, y = float(a("x") or 0), float(a("y") or 0)
        except ValueError:
            continue
        fs = a("font-size") or ""
        style = a("style") or ""
        if not fs:
            g = re.search(r"font-size:\s*([\d.]+)", style)
            fs = g.group(1) if g else "14"
        fill = a("fill")
        if not fill:
            g = re.search(r"fill:\s*([^;]+)", style)
            fill = g.group(1) if g else None
        own = (a("class") or "").split()
        yield pos, x, y, float(re.sub(r"[^\d.]", "", fs) or 14), fill, own + ancestors, content


def check(path, unit=1.0):
    raw = path.read_text(encoding="utf-8")

    def cls_color(cls, prop):
        for c in reversed(cls):
            if c in rules and prop in rules[c]:
                return rules[c][prop]
        return None

    worst = {}
    for theme, page in (("light", PAGE_LIGHT), ("dark", PAGE_DARK)):
        # resolve the shared palette to this theme's concrete values first, exactly as the
        # page's CSS vars would at render time
        svg = (palette.to_light if theme == "light" else palette.to_dark)(raw)
        rules = css_rules(svg)
        shp = shapes(svg)
        page_rgb = parse_color(page)
        low = None
        for pos, x, y, fs, fill, cls, content in texts(svg):
            fg = parse_color(fill) or parse_color(cls_color(cls, "fill")) or parse_color("#000")
            bg = None
            for spos, (x0, y0, x1, y1), sfill, scls in shp:
                if spos > pos:
                    break
                f = parse_color(sfill) or parse_color(cls_color(scls, "fill"))
                if f is None:
                    continue
                if x0 - 1 <= x <= x1 + 1 and y0 - 1 <= y <= y1 + 1:
                    bg = f
            if bg is None:
                bg = page_rgb
            r = ratio(fg, bg)
            need = 3.0 if fs * unit >= LARGE_PX else 4.5
            if low is None or r < low[0]:
                low = (r, need, content[:30], fs * unit)
        worst[theme] = low
    return worst


def main(paths):
    print(f"{'diagram':<26}{'light':>18}{'dark':>18}   verdict")
    print("-" * 82)
    fails = 0
    for p in paths:
        unit = 4 / 3 if 'pt"' in p.read_text()[:400] else 1.0
        w = check(p, unit)
        cells, bad = [], []
        for theme in ("light", "dark"):
            r = w[theme]
            if not r:
                cells.append(f"{'-':>18}")
                continue
            ok = r[0] >= r[1]
            cells.append(f"{r[0]:>8.2f}:1 {'ok ':>7}" if ok else f"{r[0]:>8.2f}:1 {'FAIL':>7}")
            if not ok:
                bad.append(f"{theme}: {r[0]:.2f}:1 on “{r[2]}”")
        if bad:
            fails += 1
        print(f"{p.stem:<26}{cells[0]}{cells[1]}   {'; '.join(bad) or 'passes AA'}")
    print(f"\n{len(paths)-fails}/{len(paths)} pass WCAG AA in BOTH themes")
    return 1 if fails else 0


if __name__ == "__main__":
    args = sys.argv[1:] or sorted((pathlib.Path(__file__).parent / "out").glob("*.svg"))
    sys.exit(main([pathlib.Path(a) for a in args]))
