#!/usr/bin/env python3
"""THROWAWAY — objective gate for prototype diagrams.

Two hard rules:
  R1  rendered height must fit one viewport      (<= MAX_H px)
  R2  no glyph may render larger than an <h2>    (<= H2_PX px)

The renderer's real content width is what decides both, because an over-wide diagram is
scaled DOWN to fit (shrinking text, R2 safe but unreadable) while a narrow one renders at
natural size (text stays big, R2 at risk).

explain-diff page geometry: body max-width 880px, padding 1.5rem*2 @19px root = 57px,
.diagram padding 1.2rem*2 = 45.6px  ->  ~777px of usable drawing width.
h2 = 1.4rem @ 19px root = 26.6px.
"""
import re, sys, pathlib

AVAIL_W = 777.0    # usable px inside a .diagram card on the real page
MAX_H = 800.0      # one viewport height (their screenshots were ~846 tall; leave chrome room)
H2_PX = 26.6       # 1.4rem at 19px root
MIN_READABLE = 11.0  # below this, text is effectively unreadable in body copy
BODY_PX = 19.0       # the page's body text (1rem at a 19px root)
# R4: the diagram's MODAL glyph size — the size most of its text is set in (edge labels, table
# rows, member signatures) — should sit near body text and must never exceed it. A diagram whose
# ordinary text out-sizes the prose reads as a poster dropped into the article.


def analyse(path: pathlib.Path):
    s = path.read_text(encoding="utf-8")
    i = s.find("<svg")
    tag = s[i:i + s[i:].find(">") + 1]

    wm = re.search(r'\swidth="([\d.]+)(pt|px)?"', tag)
    hm = re.search(r'\sheight="([\d.]+)(pt|px)?"', tag)
    vb = re.search(r'viewBox="[-\d.]+ [-\d.]+ ([\d.]+) ([\d.]+)"', tag)
    mw = re.search(r'max-width:\s*([\d.]+)px', tag)

    unit = 4 / 3 if (wm and wm.group(2) == "pt") else 1.0   # pt -> css px
    if wm and hm and 'width="100%"' not in tag:
        nat_w, nat_h = float(wm.group(1)) * unit, float(hm.group(1)) * unit
    elif vb:
        nat_w, nat_h = float(vb.group(1)), float(vb.group(2))
        if mw:
            nat_w = float(mw.group(1))
    else:
        return None

    scale = min(1.0, AVAIL_W / nat_w)          # max-width:100% shrinks but never enlarges
    rend_w, rend_h = nat_w * scale, nat_h * scale

    # every font-size the SVG declares, in user units
    fs = [float(x) for x in re.findall(r'font-size="([\d.]+)', s)]
    fs += [float(x) for x in re.findall(r'font-size:\s*([\d.]+)px', s)]
    fs = [f for f in fs if f > 0]
    import collections
    modal_decl = collections.Counter(fs).most_common(1)[0][0] if fs else 0
    # user unit -> css px is `unit`; then the page's downscale applies
    rend_fonts = [f * unit * scale for f in fs] or [0]

    return dict(name=path.stem, nat_w=nat_w, nat_h=nat_h, scale=scale, rend_w=rend_w,
                rend_h=rend_h, fmax=max(rend_fonts), fmin=min(rend_fonts),
                fmodal=modal_decl * unit * scale)


def main(paths):
    rows = [r for r in (analyse(p) for p in paths) if r]
    rows.sort(key=lambda r: r["name"])
    print(f"gate: height <= {MAX_H:.0f}px   any glyph <= {H2_PX:.1f}px (h2)   "
          f"modal glyph <= {BODY_PX:.0f}px (body)   width {AVAIL_W:.0f}px\n")
    print(f"{'diagram':<26}{'natural':>12}{'rendered':>12}{'scale':>7}"
          f"{'modal':>6}{'range':>10}  verdict")
    print("-" * 88)
    bad = 0
    for r in rows:
        flags = []
        if r["rend_h"] > MAX_H:
            flags.append(f"TALL {r['rend_h']:.0f}px")
        if r["fmax"] > H2_PX:
            flags.append(f"FONT {r['fmax']:.1f}px > h2")
        if r["fmodal"] > BODY_PX:
            flags.append(f"BODY {r['fmodal']:.1f}px > body {BODY_PX:.0f}px")
        if r["fmin"] < MIN_READABLE and r["fmin"] > 0:
            flags.append(f"tiny {r['fmin']:.1f}px")
        if flags:
            bad += 1
        print(f"{r['name']:<26}{r['nat_w']:>5.0f}x{r['nat_h']:<6.0f}"
              f"{r['rend_w']:>5.0f}x{r['rend_h']:<6.0f}{r['scale']:>7.2f}"
              f"{r['fmodal']:>6.1f}{r['fmin']:>5.1f}-{r['fmax']:<4.1f}  {'  '.join(flags) or 'ok'}")
    print(f"\n{len(rows) - bad}/{len(rows)} pass both gates")
    return 1 if bad else 0


if __name__ == "__main__":
    args = sys.argv[1:] or sorted((pathlib.Path(__file__).parent / "out").glob("*.svg"))
    sys.exit(main([pathlib.Path(a) for a in args]))
