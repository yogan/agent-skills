#!/usr/bin/env python3
"""A screen the driver captured with its colours (`.ansi`), as an image of how Ghostty draws it.

    python3 e2e/screenshot.py <capture.ansi|run folder> [--region screen|panel|main] [--out DIR]

A run folder gives one PNG per step capture, next to it (or in `--out`). `--region panel`
crops to the sidebar, `main` to the chat beside it, as drive.py's `region_of` splits them.

Built to look like the owner's terminal, so a capture can be judged without running the
session again: Ghostty's defaults (its palette, background and foreground, dim text at half
strength, JetBrains Mono with the Nerd Font symbols it embeds). Ghostty ships its fonts
inside the app, so the same two public fonts are fetched once into a cache outside the repo.
What it cannot match exactly is a glyph's fine placement (Ghostty sizes a symbol to the
cell; a browser draws it at the font's own size), and the pointer and cursor, which a
capture does not hold.
"""
import argparse
import base64
import html
import os
import re
import sys
import unicodedata
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from lib.diagram import browser  # noqa: E402

FONTS = os.path.expanduser("~/.cache/agent-skills/fonts")
SOURCES = {
    "JetBrainsMono.zip": "https://github.com/JetBrains/JetBrainsMono/releases/download/v2.304/JetBrainsMono-2.304.zip",
    "NerdFontsSymbolsOnly.zip": "https://github.com/ryanoasis/nerd-fonts/releases/download/v3.4.0/NerdFontsSymbolsOnly.zip",
}
# The faces used, by where each sits in its archive.
FACES = {
    ("JetBrainsMono.zip", "fonts/ttf/JetBrainsMono-Regular.ttf"): ("Mono", 400, "normal"),
    ("JetBrainsMono.zip", "fonts/ttf/JetBrainsMono-Bold.ttf"): ("Mono", 700, "normal"),
    ("JetBrainsMono.zip", "fonts/ttf/JetBrainsMono-Italic.ttf"): ("Mono", 400, "italic"),
    ("JetBrainsMono.zip", "fonts/ttf/JetBrainsMono-BoldItalic.ttf"): ("Mono", 700, "italic"),
    ("NerdFontsSymbolsOnly.zip", "SymbolsNerdFontMono-Regular.ttf"): ("Symbols", 400, "normal"),
}

# Ghostty's defaults (`ghostty +show-config --default`): the first 16 colours, the rest the
# usual 6x6x6 cube and grey ramp.
BACKGROUND = "#282c34"
FOREGROUND = "#ffffff"
PALETTE16 = ["#1d1f21", "#cc6666", "#b5bd68", "#f0c674", "#81a2be", "#b294bb", "#8abeb7", "#c5c8c6",
             "#666666", "#d54e53", "#b9ca4a", "#e7c547", "#7aa6da", "#c397d8", "#70c0b1", "#eaeaea"]
FAINT = 0.5          # Ghostty's faint-opacity
FONT_PX = 14         # Ghostty's 21pt is the owner's; the image is scaled, so only the ratio matters
LINE = 1.3           # the cell's height over the font size, close to Ghostty's for this font
SCALE = 2


def palette(n):
    if n < 16:
        return PALETTE16[n]
    if n < 232:
        n -= 16
        steps = [0, 95, 135, 175, 215, 255]
        return "#%02x%02x%02x" % (steps[n // 36], steps[n // 6 % 6], steps[n % 6])
    v = 8 + 10 * (n - 232)
    return "#%02x%02x%02x" % (v, v, v)


def fetch_fonts():
    """The faces, as {(family, weight, style): ttf bytes}, fetched into the cache once."""
    os.makedirs(FONTS, exist_ok=True)
    out = {}
    for (archive, member), face in FACES.items():
        path = os.path.join(FONTS, archive)
        if not os.path.exists(path):
            print(f"fetching {SOURCES[archive]}", file=sys.stderr)
            with urllib.request.urlopen(SOURCES[archive], timeout=60) as r:
                data = r.read()
            with open(path + ".part", "wb") as fh:
                fh.write(data)
            os.replace(path + ".part", path)
        with zipfile.ZipFile(path) as z:
            out[face] = z.read(member)
    return out


def width(ch):
    """The cells a character takes: two for wide East Asian forms and emoji, one otherwise
    (a Nerd Font symbol included: it is drawn in one cell)."""
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 2
    return 0 if unicodedata.combining(ch) else 1


SGR = re.compile(r"\x1b\[([0-9;:]*)m")
OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
OTHER = re.compile(r"\x1b\[[0-9;?]*[A-Za-ln-z]")


def cells(ansi):
    """The capture as rows of cells: (char, style), a wide char followed by a None cell."""
    text = OTHER.sub("", OSC.sub("", ansi))
    style = {}
    rows = []
    for line in text.split("\n"):
        row = []
        pos = 0
        for m in SGR.finditer(line + "\x1b[m"):
            for ch in line[pos:m.start()]:
                w = width(ch)
                if w == 0 and row:
                    row[-1] = (row[-1][0] + ch, row[-1][1])
                    continue
                row.append((ch, dict(style)))
                if w == 2:
                    row.append(None)
            pos = m.end()
            style = apply(style, m.group(1))
        rows.append(row)
    while rows and not rows[-1]:
        rows.pop()
    return rows


def apply(style, params):
    codes = [int(x) if x else 0 for x in re.split("[;:]", params)] if params else [0]
    s = dict(style)
    i = 0
    while i < len(codes):
        c = codes[i]
        if c == 0:
            s = {}
        elif c in (1, 2, 3, 4, 7, 9):
            s[{1: "bold", 2: "faint", 3: "italic", 4: "under", 7: "reverse", 9: "strike"}[c]] = True
        elif c == 22:
            s.pop("bold", None)
            s.pop("faint", None)
        elif c in (23, 24, 27, 29):
            s.pop({23: "italic", 24: "under", 27: "reverse", 29: "strike"}[c], None)
        elif c in (38, 48) and i + 1 < len(codes):
            key = "fg" if c == 38 else "bg"
            if codes[i + 1] == 5 and i + 2 < len(codes):
                s[key] = palette(codes[i + 2])
                i += 2
            elif codes[i + 1] == 2 and i + 4 < len(codes):
                s[key] = "#%02x%02x%02x" % tuple(codes[i + 2:i + 5])
                i += 4
        elif c in (39, 49):
            s.pop("fg" if c == 39 else "bg", None)
        elif 30 <= c <= 37 or 90 <= c <= 97:
            s["fg"] = PALETTE16[c - 30 if c < 90 else c - 90 + 8]
        elif 40 <= c <= 47 or 100 <= c <= 107:
            s["bg"] = PALETTE16[c - 40 if c < 100 else c - 100 + 8]
        i += 1
    return s


def colours(style):
    """A cell's text and background colour, reverse video swapping them."""
    fg, bg = style.get("fg", FOREGROUND), style.get("bg")
    return (bg or BACKGROUND, fg) if style.get("reverse") else (fg, bg)


def css(style):
    """The text's own look; its background is its cell's, drawn whole."""
    out = [f"color:{colours(style)[0]}"]
    if style.get("faint"):
        out.append(f"opacity:{FAINT}")
    if style.get("bold"):
        out.append("font-weight:700")
    if style.get("italic"):
        out.append("font-style:italic")
    deco = [d for k, d in (("under", "underline"), ("strike", "line-through")) if style.get(k)]
    if deco:
        out.append("text-decoration:" + " ".join(deco))
    return ";".join(out)


def page(rows, fonts):
    """The rows as one page: every cell in place on a grid of the font's own cell width."""
    faces = "".join(
        f"@font-face{{font-family:{fam};font-weight:{w};font-style:{st};"
        f"src:url(data:font/ttf;base64,{base64.b64encode(data).decode()})}}"
        for (fam, w, st), data in fonts.items())
    lines = []
    for row in rows:
        spans = []
        for col, cell in enumerate(row):
            if cell is None:
                continue
            ch, style = cell
            wide = col + 1 < len(row) and row[col + 1] is None
            # The background fills the whole cell, at full strength even under faint text.
            bg = colours(style)[1]
            box = f"left:{col}ch;width:{2 if wide else 1}ch" + (f";background:{bg}" if bg else "")
            inner = "" if ch == " " else f'<i style="{css(style)}">{html.escape(ch)}</i>'
            spans.append(f'<b style="{box}">{inner}</b>')
        lines.append(f'<div>{"".join(spans)}</div>')
    cols = max((len(r) for r in rows), default=1)
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{faces}
html,body{{margin:0;background:{BACKGROUND}}}
#t{{font-family:Mono,Symbols;font-size:{FONT_PX}px;line-height:{LINE};padding:8px;
  width:{cols}ch;font-variant-ligatures:contextual;color:{FOREGROUND}}}
#t div{{position:relative;height:{LINE}em;white-space:pre}}
#t b{{position:absolute;top:0;bottom:0;font-weight:inherit;display:flex;justify-content:center;overflow:visible}}
#t i{{font-style:inherit}}
</style></head><body><div id="t">{"".join(lines)}</div></body></html>""", cols


def crop(rows, region):
    """`rows` cut to the sidebar or the chat beside it, at the divider drive.py finds."""
    if region == "screen":
        return rows
    import drive
    plain = "\n".join("".join(c[0] if c else "" for c in row) for row in rows)
    at = drive.divider_column(plain)
    if at is None:
        return rows if region == "main" else []
    # Only the rows the divider runs down: below it the prompt spans the whole screen.
    rows = [row for row in rows if len(row) > at and row[at] and row[at][0] == "│"]
    return [row[at + 1:] for row in rows] if region == "panel" else [row[:at] for row in rows]


def shoot(src, out, region, fonts):
    with open(src, encoding="utf-8", errors="replace") as fh:
        rows = crop(cells(fh.read()), region)
    doc, cols = page(rows, fonts)
    # The page's own size, measured as Ghostty's cells would be: a JetBrains Mono cell is
    # 0.6 of the font size wide.
    w = int(cols * FONT_PX * 0.6) + 16
    h = int(len(rows) * FONT_PX * LINE) + 16
    browser.rasterise(doc, out, w, h, scale=SCALE)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("src", help="an .ansi capture, or a run folder of them")
    ap.add_argument("--region", choices=("screen", "panel", "main"), default="screen")
    ap.add_argument("--out", help="folder for the images (default: next to each capture)")
    a = ap.parse_args(argv)
    srcs = sorted(os.path.join(a.src, f) for f in os.listdir(a.src) if f.endswith(".ansi")) \
        if os.path.isdir(a.src) else [a.src]
    fonts = fetch_fonts()
    for src in srcs:
        base = os.path.splitext(os.path.basename(src))[0] + ("" if a.region == "screen" else f"-{a.region}")
        out = os.path.join(a.out or os.path.dirname(os.path.abspath(src)), base + ".png")
        if a.out:
            os.makedirs(a.out, exist_ok=True)
        print(shoot(src, out, a.region, fonts))


if __name__ == "__main__":
    main()
