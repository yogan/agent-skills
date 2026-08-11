#!/usr/bin/env python3
"""THROWAWAY PROTOTYPE — gallery comparing graphviz / d2 / mermaid for explain-diff.

Question: which stack should the diagram renderer be built on, and clean or sketched?
5 variants, ONE shared warm palette, so only engine + stroke style differ.
Delete once decided. Rebuild: ./build.sh && python3 gallery.py
"""
import pathlib, re, html
import palette
from measure import analyse, AVAIL_W, MAX_H, H2_PX, MIN_READABLE

HERE = pathlib.Path(__file__).parent
OUT = pathlib.Path("/tmp/diagram-stack-prototype.html")

# key, stack, style, how it was produced
VARIANTS = [
    ("d2", "D2", "dagre", "d2 --theme 0 · palette via classes: prelude, literals swapped to CSS vars"),
]

TYPES = [
    ("arch",     "1 · System architecture",
     "Nested containers: browser, k8s cluster, two deployments, Redis, Postgres. Tests grouping + semantic colour."),
    ("sequence", "2 · Sequence",
     "GraphQL join → WS upgrade → Redis fan-out. Tests lifelines, self-calls, notes, return arrows."),
    ("er",       "3 · DB schema (ER)",
     "New presence_sessions table + revision column. Tests real table shapes, PK/FK badges, cardinality."),
    ("class",    "4 · Class relations",
     "Gateway / registry / interface / two impls. Tests compartments, «interface», implements vs owns."),
    ("state",    "5 · State machine",
     "WebSocket lifecycle incl. reconnect backoff. Tests start/end markers, branching, self-transitions."),
]

CSS = palette.css_block() + """
:root{--bg:#fafaf8;--fg:#1a1a1a;--accent:#b5541f;--muted:#6b6b6b;--surface:#fff;--border:#e0ddd6;--code:#f6f8fa}
[data-theme=dark]{--bg:#16181d;--fg:#e8e6e1;--accent:#e0895a;--muted:#9a9a9a;--surface:#1f2229;--border:#3a3d44;--code:#1a1d23}
*{box-sizing:border-box}
/* mirror the real explain-diff page's type scale, so 'no diagram glyph larger
   than an h2' means the same thing here as it will in production */
html{font-size:19px}
body{font-family:Georgia,'Times New Roman',serif;background:var(--bg);color:var(--fg);max-width:1000px;margin:0 auto;padding:2rem 1.5rem 9rem;line-height:1.6}
h1{font-size:1.9rem;border-bottom:3px solid var(--accent);padding-bottom:.4rem}
h2{font-size:1.4rem;color:var(--accent);margin-top:2.8rem}
.lede{color:var(--muted)}
code{font-family:'SF Mono',Consolas,monospace;font-size:.84em;background:var(--code);padding:.1rem .3rem;border-radius:3px}
.hint{color:var(--muted);font-size:.85rem;margin:.1rem 0 .8rem}
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:1.1rem;margin:.6rem 0 0;overflow:hidden}
/* HARD SIZE CONTROL: this is the answer to "can't we control size in HTML?" — yes.
   max-height clamps every diagram to a fraction of the viewport; width:auto keeps aspect. */
.card svg{display:block;max-width:100%;max-height:var(--dmax,72vh);width:auto;height:auto;margin:0 auto}
.dims{font-family:'SF Mono',Consolas,monospace;font-size:.72rem;color:var(--muted);margin-top:.7rem;padding-top:.6rem;border-top:1px dashed var(--border)}
.slot{display:none}.slot.on{display:block}
.missing{color:var(--muted);font-style:italic;padding:1.6rem;text-align:center}
#bar{position:fixed;bottom:1.1rem;left:50%;transform:translateX(-50%);z-index:50;display:flex;align-items:center;gap:.15rem;
  background:#141414;color:#fff;border-radius:999px;padding:.3rem;box-shadow:0 4px 18px rgba(0,0,0,.4);font-family:system-ui,sans-serif}
#bar button{background:none;border:0;color:#fff;font-size:1.1rem;cursor:pointer;padding:.35rem .8rem;border-radius:999px;line-height:1}
#bar button:hover{background:rgba(255,255,255,.18)}
#label{min-width:15rem;text-align:center;font-size:.78rem;padding:0 .3rem;color:#bbb}
#label b{display:block;font-size:.95rem;color:#fff}
#tt{position:fixed;top:1rem;right:1rem;z-index:50;width:2.4rem;height:2.4rem;border-radius:999px;border:1px solid var(--border);
  background:var(--surface);color:var(--fg);font-size:1.1rem;cursor:pointer}

h2{scroll-margin-top:.6rem}
.toc{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:.8rem 1.2rem;margin:1.2rem 0 0;font-family:system-ui,sans-serif;font-size:.8rem}
.toc a{color:var(--accent);text-decoration:none;margin-right:.9rem;white-space:nowrap}
.toc a:hover{text-decoration:underline}
.anchor{font-size:.7em;color:var(--muted);text-decoration:none;margin-left:.4rem;opacity:0}
h2:hover .anchor{opacity:1}
.badge{font-family:system-ui,sans-serif;font-size:.68rem;border-radius:999px;padding:.1rem .5rem;margin-left:.4rem;white-space:nowrap}
.badge.ok{background:#dcfce7;color:#166534}
.badge.bad{background:#fee2e2;color:#991b1b}
.badge.warn{background:#fef3c7;color:#92400e}
.gates{font-family:system-ui,sans-serif;font-size:.72rem;color:var(--muted);margin:.5rem 0 0}
/* d2 renders a `tooltip.near` callout as <foreignObject height="24"><div class="md"><p>…</p></div>.
   Two things break it inside our page:
   1. d2 ships NO paragraph reset, so the browser default `p{margin:1em 0}` shoves the text
      below the 24px box and only the top sliver of the glyphs shows.
   2. the div has no font-family, so it inherits our Georgia — different metrics from the font
      d2 measured the box with, which re-wraps the text and clips it.
   Fix both, and let the box overflow rather than clip. */
/* The tooltip callout is an annotation ABOUT the diagram, so it should sit visually ABOVE it —
   a shadow/glow reads that way, whereas a dashed outline fights the solid pointer welded to the
   box. Slight transparency lets the diagram show through without costing legibility (kept >=0.94;
   contrast.py measures the solid colour, so anything lower would silently drift from the gate). */
.card .d2-callout{fill-opacity:.95;filter:drop-shadow(0 2px 5px rgba(0,0,0,.28))}
[data-theme=dark] .card .d2-callout{fill-opacity:.94;
  filter:drop-shadow(0 0 5px rgba(224,137,90,.40)) drop-shadow(0 2px 4px rgba(0,0,0,.55))}
/* The callout's drop-shadow paints OUTSIDE the viewBox; the <svg> clips it by default, so a
   callout flush with the edge lost its glow. The card has ~21px of padding for it to bleed into.
   Geometry is kept inside the viewBox by the placement pass — this only frees the shadow. */
.card svg{overflow:visible}
.card foreignObject{overflow:visible}
.card foreignObject .md{display:flex;align-items:center;height:100%;
  font-family:system-ui,-apple-system,'Segoe UI',sans-serif}
.card foreignObject .md p{margin:0;line-height:1.2;font-size:12.5px;white-space:nowrap}
.legend{display:flex;gap:.4rem;flex-wrap:wrap;margin:.7rem 0 0}
.pill{font-family:system-ui,sans-serif;font-size:.72rem;border:1px solid var(--border);border-radius:999px;padding:.2rem .7rem;color:var(--muted);cursor:pointer}
.pill.on{background:var(--accent);color:#fff;border-color:var(--accent)}
"""

JS = """
const V=%s;
let i=Math.max(0,V.indexOf(new URLSearchParams(location.search).get('variant')));
function render(){
  const v=V[i], m=document.querySelector('#meta-'+v);
  document.querySelectorAll('.slot').forEach(e=>e.classList.toggle('on',e.dataset.variant===v));
  document.getElementById('label').innerHTML='<b>'+m.dataset.stack+'</b>'+m.dataset.style;
  document.querySelectorAll('.pill').forEach(p=>p.classList.toggle('on',p.dataset.variant===v));
  history.replaceState(null,'','?variant='+v);
}
function activeHeading(){
  const hs=[...document.querySelectorAll('h2[id]')];
  let cur=hs[0];
  for(const h of hs){ if(h.getBoundingClientRect().top<=80) cur=h; }
  return cur;
}
function switchTo(n){
  const h=activeHeading();
  i=n; render();
  if(h) h.scrollIntoView({block:'start'});   // same diagram stays pinned at the top
}
function go(d){switchTo((i+d+V.length)%%V.length);}
document.getElementById('prev').onclick=()=>go(-1);
document.getElementById('next').onclick=()=>go(1);
document.querySelectorAll('.pill').forEach(p=>p.onclick=()=>switchTo(V.indexOf(p.dataset.variant)));
addEventListener('keydown',e=>{
  if(/^(INPUT|TEXTAREA)$/.test(e.target.tagName)||e.target.isContentEditable)return;
  if(e.key==='ArrowLeft'){e.preventDefault();go(-1)} if(e.key==='ArrowRight'){e.preventDefault();go(1)}
});
const tt=document.getElementById('tt');
tt.onclick=()=>{const d=document.documentElement.getAttribute('data-theme')==='dark'?'light':'dark';
  document.documentElement.setAttribute('data-theme',d);tt.textContent=d==='dark'?'\\u2600\\ufe0f':'\\ud83c\\udf19';};
render();
"""


def natural(tag):
    wm = re.search(r'\swidth="([\d.]+)(pt|px)?"', tag)
    hm = re.search(r'\sheight="([\d.]+)(pt|px)?"', tag)
    vb = re.search(r'viewBox="[-\d.]+ [-\d.]+ ([\d.]+) ([\d.]+)"', tag)
    if wm and hm:
        w, h = float(wm.group(1)), float(hm.group(1))
        if wm.group(2) == "pt":
            w, h = w * 4 / 3, h * 4 / 3
        return w, h
    if vb:
        return float(vb.group(1)), float(vb.group(2))
    return None, None


def pin_intrinsic(svg):
    """Give the <svg> explicit width/height from its viewBox when it has none.

    d2 (and mermaid) emit only a viewBox. With `width:auto; max-width:100%` the browser then
    STRETCHES the drawing to the container, scaling every glyph up with it — the class diagram
    was rendering at 1.34x, turning 16px headers into 21.4px, larger than body text. Pinning the
    intrinsic size makes max-width a genuine cap: shrink to fit, never grow.
    """
    tag_end = svg.find(">") + 1
    tag = svg[:tag_end]
    if re.search(r'\swidth="[\d.]+(pt|px)?"', tag) and 'width="100%"' not in tag:
        return svg
    vb = re.search(r'viewBox="[-\d.]+ [-\d.]+ ([\d.]+) ([\d.]+)"', tag)
    if not vb:
        return svg
    w, h = float(vb.group(1)), float(vb.group(2))
    new = re.sub(r'\swidth="[^"]*"', "", tag)
    new = re.sub(r'\sheight="[^"]*"', "", new)
    new = new[:-1].rstrip() + f' width="{w:.0f}" height="{h:.0f}">'
    return new + svg[tag_end:]


def embed(name, stack):
    """Per-stack embedding treatment — each engine needs something different.

    graphviz : emits no CSS at all; ids are decorative -> strip them (what render.py does today).
    d2       : self-scopes CSS as .d2-<hash>, but marker/pattern ids are global -> namespace
               ids + url(#) refs. @font-face blocks must survive untouched.
    mermaid  : scopes its ENTIRE theme as `#<svgId> .foo{}` rules, so renaming the root id
               breaks every rule (that was the giant-black-artefact bug). Build passes a unique
               --svgId instead, so nothing here needs rewriting at all.
    """
    p = HERE / "out" / f"{name}.svg"
    if not p.exists():
        return '<div class="missing">not produced by this stack</div>', None
    s = p.read_text(encoding="utf-8")
    s = s[s.index("<svg"):]
    tag = s[:s.find(">") + 1]
    dims = natural(tag)

    if stack == "Graphviz":
        s = re.sub(r'\sid="[^"]*"', "", s, count=0)
    elif stack == "D2":
        # d2 paints an opaque white page rect (class="fill-N7"); it must go or dark mode
        # shows a white slab behind the drawing.
        # NB: do NOT strip d2's background rect here. Deleting rects breaks the <mask>
        # elements d2 uses for class/table headers (they render as black slabs) and d2
        # writes `<rect …></rect>`, so naive removal also unbalances the XML.
        # The clean fix lives in the source: `style.fill: transparent` on the root.
        s = re.sub(r'\sid="([^"]*)"', lambda m: f' id="{name}-{m.group(1)}"', s)
        s = re.sub(r'url\(#([^)]*)\)', lambda m: f'url(#{name}-{m.group(1)})', s)
        s = re.sub(r'(href="#)([^"]*)"', lambda m: f'{m.group(1)}{name}-{m.group(2)}"', s)
        # `tooltip.near` callout: d2 paints it plain white with a grey hairline, which is both
        # invisible in dark mode and too timid to read as "look here". Retarget that exact
        # attribute pair to the page's callout colours before literals become vars.
        # dashed border so a callout reads as annotation ABOUT the diagram, not as one of
        # its boxes. (d2 has no styling hook for the callout, hence the attribute retarget.)
        s = s.replace('fill="white" stroke="#DEE1EB"',
                      'class="d2-callout" fill="var(--d-callout-bg)" '
                      'stroke="var(--d-callout-br)"')
    s = pin_intrinsic(s)
    # mermaid: structure intentionally untouched (its own #svgId scoping stays valid)
    s = palette.to_vars(s)   # every known literal -> var(--x) so the page toggle drives it
    return s, dims


def gate_html(dims_path):
    """Inline pass/fail against the three gates, computed from the real page geometry."""
    r = analyse(dims_path) if dims_path.exists() else None
    if not r:
        return "", ""
    b = []
    if r["rend_h"] > MAX_H:
        b.append(("bad", f"taller than viewport ({r['rend_h']:.0f}px)"))
    if r["fmax"] > H2_PX:
        b.append(("bad", f"font {r['fmax']:.0f}px &gt; h2"))
    if 0 < r["fmin"] < MIN_READABLE:
        b.append(("warn", f"text down to {r['fmin']:.1f}px"))
    if not b:
        b.append(("ok", "fits viewport · text legible"))
    badges = "".join(f'<span class="badge {c}">{t}</span>' for c, t in b)
    info = (f'natural {r["nat_w"]:.0f}\u00d7{r["nat_h"]:.0f}px \u2192 rendered '
            f'{r["rend_w"]:.0f}\u00d7{r["rend_h"]:.0f}px at {r["scale"]:.2f}\u00d7 '
            f'\u00b7 glyphs {r["fmin"]:.1f}\u2013{r["fmax"]:.1f}px')
    return badges, info


parts = [f"""<!DOCTYPE html><html lang="en" data-theme="dark"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Diagram stack prototype — graphviz · d2 · mermaid</title><style>{CSS}</style></head><body>
<button id="tt">\u2600\ufe0f</button>
<h1>Diagram stack prototype</h1>
<p class="lede">One MR — <em>“add live presence to the collaborative document editor”</em> (React FE, GraphQL API,
new WebSocket gateway, Redis fan-out, Postgres, k8s) — drawn five ways by three engines.
<b>All five variants share one warm palette</b>, so what differs is the engine's layout and the
stroke style (professional vs sketch), not the colours.
Use <b>&larr; &rarr;</b> or the bar below; scroll position is kept, so park on a diagram and cycle.
Each card prints its <b>natural pixel size</b> — that is the compactness comparison.</p>
<div class="legend">"""]
for key, stack, style, _ in VARIANTS:
    parts.append(f'<span class="pill" data-variant="{key}">{html.escape(stack)} · {html.escape(style)}</span>')
parts.append("</div>")
parts.append('<div class="toc"><b>Jump to:</b> ' + 
    "".join(f'<a href="#{k}">{html.escape(t.split(chr(183))[0].strip())}</a>' for k, t, _ in TYPES)
    + '<a href="#animated">Bonus · animation</a></div>')
for key, stack, style, _ in VARIANTS:
    parts.append(f'<span id="meta-{key}" data-stack="{html.escape(stack)}" data-style="{html.escape(style)}" hidden></span>')

for tkey, ttitle, tdesc in TYPES:
    parts.append(f'<h2 id="{tkey}">{html.escape(ttitle)}<a class="anchor" href="#{tkey}">#</a></h2>'
                 f"<p class='hint'>{html.escape(tdesc)}</p>")
    for key, stack, style, cmd in VARIANTS:
        body, _ = embed(f"{key}--{tkey}", stack)
        badges, info = gate_html(HERE / "out" / f"{key}--{tkey}.svg")
        parts.append(
            f'<div class="slot" data-variant="{key}"><div class="card">{body}'
            f'<div class="dims">{info or "size n/a"}<br>{html.escape(cmd)}</div>'
            f'<div class="gates">{badges}</div></div></div>'
        )

parts.append('<h2 id="animated">Bonus · animated build-up (D2 only)'
             '<a class="anchor" href="#animated">#</a></h2>'
             "<p class='hint'>"
             "Four <code>steps:</code> boards packaged as one self-contained CSS-animated SVG "
             "(<code>--animate-interval 1400</code>) — no JS, no GIF. Rewritten wide-and-short so it "
             "fits on one screen. Neither Graphviz nor Mermaid has any equivalent.</p>")
for key, stack, style, cmd in VARIANTS:
    if stack == "D2":
        body, _ = embed(f"{key}--animated", stack)
        badges, info = gate_html(HERE / "out" / f"{key}--animated.svg")
        parts.append(f'<div class="slot" data-variant="{key}"><div class="card">{body}'
                     f'<div class="dims">{info}</div><div class="gates">{badges}</div></div></div>')
    else:
        parts.append(f'<div class="slot" data-variant="{key}"><div class="card">'
                     f'<div class="missing">{html.escape(stack)} cannot animate — static only</div></div></div>')

parts.append('<div id="bar"><button id="prev">&larr;</button><span id="label"></span><button id="next">&rarr;</button></div>')
parts.append("<script>" + JS % str([v[0] for v in VARIANTS]) + "</script></body></html>")

OUT.write_text("".join(parts), encoding="utf-8")
print(OUT, f"{OUT.stat().st_size/1024/1024:.2f} MB")
