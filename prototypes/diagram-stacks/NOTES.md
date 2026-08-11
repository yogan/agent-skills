# Prototype notes — diagram stack for `explain-diff`

**Question:** which engine should the diagram renderer be built on (graphviz / d2 / mermaid)?

**Artifact:** `/tmp/diagram-stack-prototype.html` — one MR scenario, 5 diagram types x 3 engines,
one shared warm palette, anchors + scroll-locked switching, inline gate badges.
Gates: `measure.py` (size), `contrast.py` (WCAG, both themes). Rebuild: `./build.sh && python3 gallery.py`.

**Verdict: D2 is viable — it now passes every gate.** Once the palette-alignment requirement
was dropped, D2's contrast problems turned out to be fully solvable, and content discipline
(<=6 nodes per rank, short labels) brings every diagram inside one viewport at >=11px text.

## THE D2 RECIPE (this is the deliverable)

1. `style.fill: transparent` on the root — d2 otherwise paints an opaque theme-coloured page
   rect that shows as a white slab in dark mode. Do NOT strip it in post-processing: that
   breaks d2's `<mask>` and unbalances the XML (`<rect …></rect>` is not self-closing).
2. For `shape: class` / `sql_table`, d2 couples THREE roles to two properties:
   - `fill`       -> border + header background + **member-name text**
   - `stroke`     -> body background
   - `font-color` -> header title
   Give each its own **sentinel literal** so they can theme independently:
   `fill: "<role-dark>"; stroke: "#fffffe"; font-color: "#fffffd"`.
   Then map role-dark light->dark (dark colour -> light colour), `#fffffe` -> surface,
   `#fffffd` -> title. Without separate sentinels there is NO single value that keeps the
   header title, header background and member text all readable in both themes.
3. Edge-label font must be **13, not 11**. At 11 it was the sole cause of every sub-11px
   reading. Raising node font instead is self-defeating (wider diagram -> more downscale ->
   same rendered size).
4. `direction: down` for ER and class diagrams — the default `right` produces 3 columns,
   blowing past the 777px content width and dragging text under 11px.
5. Content discipline: <=6 states, <=7 sequence messages, short labels. Non-negotiable, since
   d2 exposes no ranksep and cannot be compacted after the fact.
6. Use `shape: sql_table` for class diagrams too — `shape: class` wastes ~47px per header.
7. Mark changes with `{constraint: NEW}` (row level) and a stroke-only `new` class (node level).

## Round-4 fixes (the five nitpicks)

| nitpick | verdict | fix |
|---|---|---|
| group box bg visible in light, invisible in dark | **bug in my palette** | dark `--d-grp-bg` was `#1f2229` — identical to the card surface. Now `#262b34`. |
| sequence rows too airy | **d2 is opinionated — no clean fix** | row pitch is a hardcoded ~86px/message: 3 msgs=400px, 5=572px, 7=744px. font-size barely moves it (572->550 at font 10) and `--dagre-nodesep`/`--layout elk` have ZERO effect (sequence has its own layout engine). Only lever is fewer messages. Rewriting y-coords in the SVG would work but is exactly the bad hack to avoid. |
| class headers huge | **fixed, and it also shrank the diagram** | at font 13 a `shape: class` header is **74px** vs **27px** for `shape: sql_table` — ~47px of fixed padding, and `height:` is ignored. Rendering classes as `sql_table` rows (`"+ method()": ReturnType`) cut the diagram from **617x770 to 456x549** with contrast unchanged (5.10/5.66). |
| NEW/CHANGED must stand out | **REJECTED, redone in round 5** | row level: `{constraint: NEW}` renders a real badge in the PK/FK column. Node level: a stroke-only `new` class (`class: [svc; new]`) that composes with any role class. Caveat: `double-border` is rejected on sql_table/class shapes, and the `new` stroke can NOT be used on a `sql_table` because there `stroke` is the body fill. |
| animation | **replaced with one that earns it** | zero-downtime cutover, 4 boards, each a DIFFERENT topology (deploy -> dual-run -> delete old edge). d2 does not render board names into the SVG, so a borderless `caption` node relabelled per step supplies the phase text. 763x390, fits a viewport. |

## Round 5 — marking what the MR changed

Both round-4 mechanisms were rejected and removed:
- a thick accent border says "something here is special" but never says WHAT — no legend, unreadable intent;
- `{constraint: NEW}` puts meta-information in the PK/FK column, so it reads as a *database*
  constraint. Never encode review metadata in a slot the domain already owns.

**Use d2 tooltips instead.**
- `x.tooltip: text` alone compiles to a native SVG `<title>` — browser hover, zero JS. But there
  is no visible marker, so nothing tells the reader to hover.
- `x.tooltip.near: top-center` makes the callout **permanently visible** (per d2 docs) — this is
  the one to use. Accepted `near` values are constants; `near: <other object>` is rejected under
  dagre.
- `--force-appendix` is the third option: a numbered badge on the shape plus a legend block
  underneath. Works, but costs vertical space and reads like a footnote; `tooltip.near` is better
  for a blog article. Node-level only — a tooltip on a `sql_table` ROW gets a `<title>` but no
  visible marker, so put the marker on the table and name the column in the text.
- d2 paints the callout `fill="white" stroke="#DEE1EB"`: invisible in dark mode and too timid in
  light. `gallery.embed()` retargets that exact attribute pair to `--d-callout-bg` /
  `--d-callout-br` (the page's own callout colours) before literals become vars.
- **Caveat:** the callout text is rendered in a `<foreignObject>`, i.e. HTML inside SVG. Browsers
  render it; `rsvg-convert` and other rasterisers do not. Same trade-off mermaid has. Fine for a
  self-contained HTML page, not fine for a PDF/PNG export path.

Keep the text to 2-4 words ("added by this MR", "new table", "gains a revision column").

## Round 6 — tooltips everywhere, defaults, header size

- **Every d2 diagram now carries an example tooltip**: arch (2), er (2), sequence, class, state.
  Valid `tooltip.near` constants are exactly: `top-left|top-center|top-right|center-left|
  center-right|bottom-left|bottom-right|bottom-center`. `right-center` and `outside-*` are
  rejected. A callout costs ~49px of height, which pushed the sequence diagram over the
  viewport gate — traded one message for it (7 -> 6).
- **The callout only renders correctly if the HOST PAGE supplies CSS.** d2 emits
  `<foreignObject height="24"><div class="md"><p>…</p></div>` with no paragraph reset, so the
  browser default `p{margin:1em 0}` pushes the text out of the box — that was the "not readable
  at all" bug, a clip, not a colour problem. The page must ship:
  `foreignObject .md p{margin:0}` + a font-family (the div otherwise inherits the page serif and
  re-wraps text d2 measured with its own embedded font). This is a real coupling: the SVG alone,
  opened standalone, still shows clipped tooltips.
- **d2 renders a sql_table/class header at ~1.3x the shape's base font**, ignoring the global
  `**.style.font-size`. At base 13 that is a 17px header — heading-sized next to body copy.
  Set `font-size: 12` per table shape -> 16px header, 12px rows.
- The gallery now **mirrors the real page's type scale** (`html{font-size:19px}`, `h2:1.4rem`
  = 26.6px). Without that the "no glyph larger than an h2" gate was being checked against the
  wrong number (the gallery's old h2 was 19.2px).
- Defaults changed: **D2 first, dark mode on load**.

## Verification loop

`shot.js` drives puppeteer's Chromium (bundled with the mermaid-cli install) to screenshot the
real page. Anything involving `foreignObject` CANNOT be checked with `rsvg-convert` — it silently
drops that content, which is what made the tooltip bug invisible to the earlier raster checks.

## Round 7 — the upscaling bug, and rule R4

**The real cause of "class relations is still too big": d2 SVGs carry NO intrinsic width/height,
only a viewBox.** With `width:auto; max-width:100%` the browser therefore STRETCHES them to the
container. Measured in Chromium: the class diagram rendered at **1.34x**, turning 16px headers
into **21.4px** — larger than the 19px body text. `measure.py` never saw it because it assumed
`scale = min(1, AVAIL/natural)`, i.e. shrink-only, which is simply false for d2.

Fix: `pin_intrinsic()` in `gallery.py` writes `width`/`height` from the viewBox when absent, so
`max-width` becomes a real cap — shrink to fit, never grow. Every diagram now renders at
scale <= 1.00.

**New gate R4** (`measure.py`): the *modal* glyph size — the size most of the diagram's text is
set in (edge labels, table rows, member signatures) — must not exceed body text (19px). Max glyph
still must not exceed an h2 (26.6px). Current d2: modal 11.4-14px, max 11.4-18px. All pass.

Also: d2 renders a `sql_table` header at ~1.3x the shape's base font, so `font-size: 14` on the
shape gives 14px rows and an 18px header — both under body text.

## Clipping needs a THIRD gate, and it must run in a browser

d2 does not grow the canvas to include a `tooltip.near` callout, so an edge-anchored one is
silently cut. Two static attempts at a checker both failed:
- checking only right/bottom missed a callout clipped on the LEFT;
- checking all four edges produced false positives everywhere, because it ignored
  `transform="translate()"` (mermaid uses it on every group) and could not see `foreignObject`.

`clipcheck.js` does it properly: compare each painted element's `getBoundingClientRect()` against
the `<svg>`'s in a real browser, which resolves all transforms. That found exactly one real
clip and cleared every false positive. **Anchor callouts toward the diagram interior**, and note
d2 reserves no space for them, so a callout may overlap a nearby edge label.

Valid `tooltip.near`: `top-left|top-center|top-right|center-left|center-right|bottom-left|
bottom-center|bottom-right`.

## Round 8 — automatic tooltip placement

**Yes, the positions were forced, and d2 cannot do better on its own.** Without `near` a tooltip
is hover-only; `near` is what makes it permanent, and it takes exactly one of 8 fixed anchors
relative to the shape. d2 does no overlap avoidance and does not even grow the canvas to fit the
callout. There is no "let d2 decide" mode.

So the *renderer* decides instead: `place.js` renders all 8 anchors per callout, measures the
result in a browser, and keeps the best. Scoring, in order of importance:

1. **clipping is disqualifying** (`clip * 1e6`) — a cut-off callout is strictly worse than one
   that overlaps something. An earlier version weighted it at 1e3 and happily traded a 31px clip
   for less overlap.
2. **overlap weighted by what it hurts**: text/labels x6, edges x2, shape bodies x0.3, and
   containers larger than half the canvas ignored entirely. A callout lying over a big group
   rectangle costs nothing; one covering a label costs a lot. Without these weights the optimiser
   optimised for the wrong thing.

Greedy per-callout is not always enough: `arch` has two callouts that only fit in a particular
combination, so `joint.js` searches both exhaustively (8x8). Result: **0 clipped**, arch overlap
down to 6440px^2. Where no anchor is clip-free, shorten the tooltip text — a narrower callout
fits where a wide one cannot.

Styling: **dashes were tried and rejected** — d2 welds a solid pointer triangle onto the callout,
and a dashed outline fights it at the junction. What works instead is depth: keep the outline
solid and lift the callout off the canvas.

    .card .d2-callout{fill-opacity:.95;filter:drop-shadow(0 2px 5px rgba(0,0,0,.28))}
    [data-theme=dark] .card .d2-callout{fill-opacity:.94;
      filter:drop-shadow(0 0 5px rgba(224,137,90,.40)) drop-shadow(0 2px 4px rgba(0,0,0,.55))}

A plain drop shadow disappears against a dark background, so dark mode gets an accent GLOW plus a
shadow; light mode gets the shadow alone. d2 exposes no styling hook for the callout at all, so
this is applied by tagging it `class="d2-callout"` during the same attribute retarget that fixes
its colours — i.e. another thing the host page must own.

Keep `fill-opacity` >= 0.94: `contrast.py` measures the solid colour, so heavier transparency
would silently drift away from the measured ratio.

## Round 9 — the shadow had no room, and the checker could not see it

`getBoundingClientRect()` does **not** include a CSS `drop-shadow`'s spread. So a callout sitting
exactly flush with the viewBox edge measured as "fits" (`pastSvgRight = 0`) while its glow was
being cut. The clipping gate reported 0 clipped and was wrong.

Two changes:
- `.card svg{overflow:visible}` — the shadow paints outside the viewBox into the card's ~21px of
  padding. Diagram *geometry* is still kept inside the viewBox by the placement pass; only the
  shadow bleeds.
- `clipcheck.js` now reserves an 8px shadow allowance around callouts **and measures them against
  the CARD** (`overflow:hidden`, the real clipping surface) rather than the svg box. Non-callout
  geometry is still held to the svg box. Measure the surface that actually clips, not a proxy.

Note the failure mode: an earlier patch to add this allowance silently no-op'd because the marker
string it searched for did not exist in that file, and it still printed success. The green result
was meaningless until the change was verified in the file itself.

## Final gates (all three engines, same content)

| engine | contrast AA both themes | size (viewport + >=11px text) |
|---|---|---|
| **D2** | **5/5** | **5/5** (+ animation passes too) |
| Graphviz | **5/5** | 4/5 (`er` at 9.7px) |
| Mermaid | 3/5* | 2/5 |

\* both mermaid failures are checker false positives, verified by eye.

---

### Earlier verdict (superseded, kept for the reasoning)

**Build on Graphviz.** It is the only engine that passes all three gates on every
diagram, needs zero post-processing, and themes for free. Its one real hole is sequence
diagrams, which should be hand-rolled as a separate SVG generator (participants = columns,
messages = rows; no layout search needed).

## Final gate results

| engine | contrast AA (both themes) | size gates | notes |
|---|---|---|---|
| Graphviz | **5/5** | **4/5** | only `er` dips to 9.7px text |
| D2 | 4/5 | 2/5 | `shape: class` unfixable; sequence + state exceed a viewport |
| Mermaid | 3/5 | 1/5 | 2 failures are checker false positives (verified visually) |

## Blockers found, and whether they are fixable

| blocker | cause | fixable? |
|---|---|---|
| d2 white background in dark mode | d2 paints an opaque theme-coloured page rect | **yes** — `style.fill: transparent` on the root. Do NOT strip the rect in post-processing: that breaks d2's `<mask>` and unbalances the XML (d2 writes `<rect …></rect>`) |
| d2 `sql_table` unreadable | `stroke` sets the BODY fill, `fill` sets the HEADER — inverted vs every expectation | **yes** — light `fill`, white `stroke` -> 5.10:1 |
| d2 `shape: class` unreadable | `style.fill` is used for header bg AND member-name text; header title colour is theme-derived; `font-color` does not reach member names | **NO.** Light fill -> invisible member text; dark fill -> invisible header title; no overrides -> theme 0's own title is dark-on-dark. No configuration satisfies all three. |
| sketch unusable with colour | rough.js hachure fill cross-hatches over every filled shape | **no** — dropped entirely (also cost 3.7-4.4x page weight) |
| mermaid black-on-dark text | literal colours not swapped for CSS vars | **yes** — literal -> `var()` substitution; verified visually |
| d2/mermaid wasted whitespace, over-tall graphs | d2 exposes only `--dagre-nodesep`/`--edgesep`, no ranksep; ELK worse | **partially** — only by cutting content |
| animated build-up | pure decoration; flowing arrows explain nothing | **dropped** — animate only where the animation itself carries the explanation |

## Can we control size from HTML? Yes — but it is not free

`max-height: 72vh; width: auto` clamps any diagram to a viewport (now in the gallery CSS).
The cost is text: an over-wide or over-tall diagram is scaled DOWN and its glyphs go with it.
That is the emergent third gate — **text must not fall below ~11px**, and it is the gate that
actually separates the engines:

| diagram | Graphviz | D2 | Mermaid |
|---|---|---|---|
| architecture | 904x516 @11.5-14.9px | 1003x791 @8.5-10.1px | 1000x522 @9.3-14.0px |
| class | 660x628 @13.3-18.7px | 1140x645 @7.5-11.6px | 876x613 @8.9-16.0px |
| state | 436x636 @13.3-16.0px | 341x**1035** | 626x664 @10.0-18.0px |

Graphviz holds the FULL 7-state machine inside one viewport at 13-16px text. d2 needs the
content cut to 5 states to fit at all. Graphviz wins because it exposes `ranksep`/`nodesep`/
per-node `margin`, so the layout can be made compact instead of being scaled down.

## Theming burden (literal colours needing a var() mapping)

graphviz ~0 · mermaid 10 · d2 18. All three are themeable by substitution, but:
**`<mask>` contents must be excluded** — a mask works on luminance, so rewriting `#ffffff`
to a dark value inside one inverts it and blanks the drawing. d2 uses masks; graphviz does not.

## Corrections to earlier rounds

1. ~~"Mermaid doesn't scope its CSS."~~ It does (`#<svgId> .foo{}`); it just reuses `my-svg`
   for every diagram. `mmdc --svgId` fixes it. The giant black ER markers were MY bug (renamed
   the root id, leaving the selectors dangling).
2. ~~"Mermaid strips spaces from labels."~~ No — the SVG stores the space as a leading space in
   the next `<tspan>`. Chromium renders it correctly; `rsvg-convert` drops it. Rasteriser
   artifact, not a Mermaid bug.
3. ~~"R2 (font > h2) is violated."~~ Never violated by anyone once a global font-size is set.
   What looked oversized was d2's default `sql_table` header size.
4. `contrast.py` has known false positives on mermaid edge labels and d2 class headers
   (ancestor-scoped CSS and theme-derived colours). Every FAIL must be confirmed visually
   before it is believed — 3 of the current 3 mermaid/d2 failures were checked by hand.
