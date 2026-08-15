# The diagram renderer

Shared by the explainer skills and `visualize`, so a change here changes every skill that
draws. Why D2 and not Mermaid or Graphviz is in [`__init__.py`](__init__.py); the measured
recipe for driving it is in [`d2.py`](d2.py).

## `figure.draw()` is the entry point

**A skill calls that and nothing else.** It says what the picture is FOR — `target="embed"`
for a host page, `"file"` for a standalone image — and gets back the SVG plus what is wrong
with it. Which gates that implies, whether the layer spacing needs escalating, where each
callout goes, how many browser launches it takes: all of it is behind that call.

A skill importing `gates`, `place` or `spec` directly is re-deciding something `figure`
already decided. Both skills used to, they disagreed about which gates apply, and the one
`explain-diff` omitted was the only one that can see a label buried under a callout.

`render` is fair game for `HOST_CSS` / `page_css` — that is what a host page must ship, which
really is the caller's business.

## Changing it

`compare_figures.py` (repo root) is the loop for any change with a look to it:

    python3 compare_figures.py capture before          # BEFORE the first edit
    python3 compare_figures.py capture after
    python3 compare_figures.py sheet before after notes.json

It renders both corpora through `figure.draw` and writes one annotated before/after PNG.
The notes file is not optional in spirit: each side says what was wrong and what was done,
so the sheet still means something when re-read later, and gate problems are appended from
the capture — a picture that looks better while a gate complains cannot pass for a win.

**Two corpora, and that is the point.** [`examples.py`](examples.py) is the scenario the
renderer was tuned against, so it is the one a change is least likely to break;
[`examples_repo.py`](examples_repo.py) is an unsteered real run, kept exactly as authored.
A change that improves one and ruins the other is the normal outcome, not the unlikely one.

Use this rather than eyeballing one figure. Every defect fixed here by hand — a label masking
the arrow it sits on, a cardinality inside a table, an arrow through a container's title —
passed every gate, and several were introduced by a change that improved a different figure.

## What the output must satisfy

Rules about the finished drawing, with what enforces each. An unenforced rule is a gap, not a
preference: the goal is for every line here to be a check.

| rule | enforced by |
|---|---|
| An arrow is broken where it crosses text that is not its own label | `edgelabel.title_boxes` + `test_edgelabel` |
| A gap in an arrow leaves visible line on both sides — never a detached arrowhead | `arrows.leaves_a_stub` + `test_arrows` |
| An edge label sits centred ON its arrow, never beside it | `edgelabel._candidates` |
| Edge labels near the same height share one, where they can | `edgelabel._align_rows` |
| A label never sits on a box, a container border or a container title | `edgelabel._key` |
| Text is never unreadable, clipped, or under a callout | `gates/` |
| **No run of an arrow is diagonal** — every straight stretch is vertical or horizontal | `arrows.defects` + `test_arrows` |
| **An arrowhead sits on straight line**, never across the turn it just came round | `d2.ELK_EDGE_LADDER` + `test_arrows` |
| **No gap immediately before an arrowhead**, including the ones d2 cuts for its own labels | `arrows.shortfall` in `edgelabel._key` + `test_arrows` |

[`arrows.py`](arrows.py) is where the last three live, because they are about the arrow itself
rather than about the words on it. It **measures and never redraws**: a route is ELK's, and
what a corner looks like is d2's. Squaring off the corner under an arrowhead was tried and is a
worse drawing — the rounding is wanted, and a hard 90° turn 12px from a box reads as a mistake.

So the remedy is space. `d2.ELK_EDGE_LADDER` widens the gap ELK leaves between an edge and the
box it points at, per figure, only where there is something to fix and only as far as the size
gate still passes (`render._afford`). It costs 40 to 100px of page. Two figures cannot pay:
the reference architecture is 958px tall at the wider rung against an 840px ceiling, and the
repo state machine's callout then lands somewhere that pushes the drawing off its own canvas —
`figure._settle` catches that one after placement, which is the only moment it is visible.

What is left over is reported rather than paid for: four arrowheads on a curve and one gap,
named in `test_arrows`, which fails if the list grows. Ranking arrow defects above height in
`render._pick_at_spacing` was also tried and reverted — it removes one stranded head from the
repo architecture by breaking every edge label in the figure across four lines, and a wrapped
diagram is the defect a reader meets first.

A figure may exceed the page-height gate by `gates/size.RESCUE_H`, and only to buy one of the
fixes above; `render._pick_at_spacing` ranks unfixable crossings above height, so among
candidates that read equally well the shortest still wins and the budget goes unspent.

## Costs worth knowing before you change something

- The browser is in the render loop by decision, not accident — see
  [`browser.py`](browser.py) for the three things that have no substitute.
- Callout placement is a measured search over eight anchors; `test_place_slow.py` covers it
  and takes minutes. Run `--slow` once, at the end, and only if you touched placement,
  callout geometry or the harness.
- The corpus renders end to end in about half a minute. If a change appears to cost much more
  than that, something is re-deciding a layout that was already decided.
