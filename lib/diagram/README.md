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
| **No gap immediately before an arrowhead**, including the ones d2 cuts for its own labels | `arrows.shortfall` in `edgelabel._key`, `render._climb_layers` + `test_arrows` |
| **No arrow is drawn across a box it does not begin or end at** | `arrows.through` + `test_arrows` |

[`arrows.py`](arrows.py) is where the last four live, because they are about the arrow itself
rather than about the words on it.

The last of them is an INVARIANT and not a defect, which is why it is not in `arrows.defects`.
The other three are traded: `render._pick_at_spacing` ranks candidates on how many each has,
and two figures ship with one because they cannot afford the fix. A line through a box has no
remedy and no price — ELK does not do it, so the check reads zero on everything here. It exists
against the day something in this repo moves a route itself, and `test_arrows` therefore also
pins that it can still report a non-zero answer: a check whose only observed value is zero has
not been shown to be capable of another. `arrows.through` takes its obstacle boxes rather than
reading them, as `shortfall` takes its zones — what counts as a shape needs d2's palette, which
`arrows` deliberately does not know. `edgelabel.route_obstacles` is that answer, and it excludes
containers (an edge out of a nested node crosses its parent by design) and callouts (`place`
already prices covering a line against everything else it weighs).

`arrows.py` **measures and never redraws**: a route is ELK's, and
what a corner looks like is d2's. Squaring off the corner under an arrowhead was tried and is a
worse drawing — the rounding is wanted, and a hard 90° turn 12px from a box reads as a mistake.

So the remedy is space. `d2.ELK_EDGE_LADDER` widens the gap ELK leaves between an edge and the
box it points at, per figure, only where there is something to fix and only as far as the size
gate still passes (`render._afford`). It costs 40 to 100px of page. Two figures cannot pay:
the reference architecture is 958px tall at the wider spacing against a 900px ceiling, and the
repo state machine's callout then lands somewhere that pushes the drawing off its own canvas —
`figure._settle` catches that one after placement, which is the only moment it is visible.

A gap that lands on an arrowhead is the same shortage seen from the other end, and it is
bought with the OTHER ladder. ELK sizes the space between two layers to hold the edge label and
no more, so a long label can end up on a leg with no spot that both leaves the line a beginning
and keeps clear of the head — the repo ER had 126px of label on a 146px leg, needing 148, and
whichever end it gave up the words ended against a 5px stub of line. `d2.ELK_SPACING_LADDER`
already climbed for unreadable text; `render._climb_layers` climbs it for this too, on the same
`_afford` test, and that figure is 60px wider for it.

What is left over is reported rather than paid for: the arrowheads on a curve named in
`test_arrows`, which fails if the list grows. Ranking arrow defects above height in
`render._pick_at_spacing` was also tried and reverted — it removes one stranded head from the
repo architecture by breaking every edge label in the figure across four lines, and a wrapped
diagram is the defect a reader meets first.

A figure may exceed the page-height gate by `gates/size.RESCUE_H`, and only to buy one of the
fixes above; `render._pick_at_spacing` ranks unfixable crossings above height, so among
candidates that read equally well the shortest still wins and the budget goes unspent.

## When it puts something in an obviously wrong place

Look for the term in the score that PAYS for it, before proposing a cause.

Most of this module is a search that ranks candidates term by term, and a term that MINIMISES a
quantity can always be satisfied by leaving the domain that quantity measures. `edgelabel`
charges a label for how much of its own arrow it hides — so a label half past the end of its leg
covers less of it, and scored better for being in the wrong place. Off the leg it then had no
leg to slide along, which silently removed it from the row alignment. One term, ranked so that a
legal position beats the quantity being minimised, fixed four separate complaints at once.

Three causes were guessed before that one was measured, and two of them were built and reverted.
The measurement that found it took a minute: compare where the thing actually is against the
range it was allowed to be in. Do that first.

## Costs worth knowing before you change something

- The browser is in the render loop by decision, not accident — see
  [`browser.py`](browser.py) for the three things that have no substitute.
- Callout placement is a measured search over eight anchors; `test_place_slow.py` covers it
  and takes minutes. Run `--slow` once, at the end, and only if you touched placement,
  callout geometry or the harness.
- The corpus renders end to end in about 40 seconds: 68 d2 compiles at ~370ms and 14 browser
  launches at ~1.2s. If a change appears to cost much more than that, something is re-deciding a
  layout that was already decided.
- **A standalone render is never scaled.** `gates/size.analyse(standalone=True)` fixes the scale
  at 1.0, because a file is shown at natural size and zoomed by the reader. So every rule about
  a file's text is a rule about what was AUTHORED, and the text floors — which only ever bite
  through downscaling — cannot fire on that target at all.

## Dead ends — measured, and not to be retried

Each of these was built and reverted. None of them is visible in the code, which is why they are
written down.

- **Squaring off the corner an arrowhead is painted over.** It does free the head, and it is a
  worse drawing: the rounding is wanted, and a hard 90° turn 12px from a box reads as a mistake.
- **Ranking arrow defects above height** in `render._pick_at_spacing`. Buys real fixes at a price
  no reader would accept — every edge label in the repo architecture broken across four lines,
  and two other figures roughly doubled in height.
- **Raising `edgeNodeBetweenLayers` globally.** At 20 nothing is fixed and the reference
  architecture flips to a 1006px landscape whose text fails the floor; at 30 the corpus grows
  20–25%.
- **A containment guard on alignment** ("a label may not cross a container border"). It blocks
  the good move too — pulling a label back OUT of a container is exactly what keeps a pair level.
- **Capping alignment on distance from the leg's centre** rather than on travel. A label slid off
  its centre for a good reason then has budget to be dragged a long way back.
- **Clamping `_targets` into the range the whole row can reach.** It deletes the only useful
  proposals whenever one distant member is in the run.
- **Pinning the layer spacing across edge spacings.** Saves nothing: the layer ladder never
  iterates, every figure in both corpora resolves at the first rung.
- **Tightening `edgeNodeBetweenLayers` to 5** — ELK abandons orthogonal routing, and a diagonal
  run is not acceptable at any saving. **`nodeNodeBetweenLayers`** has no effect on the
  architecture figure at any value 8–15.
- **Offsetting a label beside its line** hides no line at all and reads worse: beside a line a
  label stops saying which line it belongs to.
- **Two-sweep label alignment** oscillates — with its own position excluded, a pair swaps
  heights on every pass.
- **Fixing a crossing by changing the spec** (fewer nested containers) is refused: the structure
  is the owner's, and the renderer adjusts the arrangement instead.
