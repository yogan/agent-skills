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

It renders every corpus through `figure.draw` and writes one annotated before/after PNG.
The notes file is not optional in spirit: each side says what was wrong and what was done,
so the sheet still means something when re-read later, and gate problems are appended from
the capture — a picture that looks better while a gate complains cannot pass for a win.

**Three corpora, and that is the point.** [`examples.py`](examples.py) is the scenario the
renderer was tuned against, so it is the one a change is least likely to break;
[`examples_repo.py`](examples_repo.py) is an unsteered real run, kept exactly as authored;
[`examples_large.py`](examples_large.py) is one ER diagram three times the area of anything
else, because crowding — two labels competing for one stretch of line, two arrowheads landing
a few px apart — does not exist on a drawing with five boxes in it. A change that improves one
and ruins another is the normal outcome, not the unlikely one.

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
| **An edge label keeps clear of every line but its own** | `edgelabel.FOREIGN_CLEARANCE` in `_key` + `test_edgelabel` |
| Edge labels near the same height share one, where they can | `edgelabel._align_rows` |
| A label never sits on a box, a container border or a container title | `edgelabel._key` |
| Text is never unreadable, clipped, or under a callout | `gates/` |
| **A note's own words are text too** — same legibility floor, same contrast rule as a label | `gates/size` + `gates/contrast`, both reading `<foreignObject>` |
| **No run of an arrow is diagonal** — every straight stretch is vertical or horizontal | `arrows.defects` + `test_arrows`, and `route._square` below its threshold |
| **An arrowhead sits on straight line**, never across the turn it just came round | `d2.ELK_EDGE_LADDER` + `test_arrows` |
| **No gap immediately before an arrowhead**, including the ones d2 cuts for its own labels | `arrows.shortfall` in `edgelabel._key`, `render._climb_layers` + `test_arrows` |
| **No arrow is drawn across a box it does not begin or end at** | `arrows.through` + `test_arrows` |
| **A callout never rests against a line it does not cover** | `route._clear` + `test_route` |
| **Two arrows arriving at one box land on one line, or far enough apart to read as two** | `route._converge` + `test_route` |
| **A callout never covers a corner or an arrowhead** where any of the eight positions avoids it | `place._score`'s landmark rank + `test_place`, and `figure._coverage_advice` says so when none of them does |

**The note-words row took a defect to write down.** A callout's note and a role legend's
labels are HTML in a `<foreignObject>`, not SVG text, so neither gate could see them: a
legend shipped black on a dark page at 1.18:1 while the contrast gate reported the figure at
5.05:1, and a callout's 12.5px words rendered at 9.7px on a scaled figure with every glyph
check passing. Both gates read that markup now, and the size they assume when the file does
not state one is `render.ANNOTATION_PX` — which is why that constant is `d2.BASE_FONT` rather
than the css default it used to be.

**The label-clearance row is the section below on terms that pay for the wrong answer, in its
purest form.** `_key`'s hidden-line term is an AREA, and a label lying ALONG a horizontal run
covers its own width of it where the same label across a vertical run covers only its height —
roughly ten to one on a long cardinality. So the search reliably preferred a crowded vertical
leg to a clear horizontal one, and a mask that is not per-edge did the rest: the gap cut under
those words broke a neighbouring arrow too, and neither arrow owned it. Counting foreign lines
above the area term is what fixed it.

A run drawn on top of the label's own is excluded, or the rule would fight the one below it:
arrows converging on a box share their last run exactly, and a label there breaks one stroke of
ink rather than three arrows.

[`arrows.py`](arrows.py) is where four of them live, because they are about the arrow itself
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

The remedy used to be space, and mostly is not any more. [`route.py`](route.py) slides the last
STEP of a route back along the run it comes off, so the head sits on line the drawing already
had — no page, no layout change, and the corners still d2's. It is the one place here that
moves a line, which is why it is its own module and why `arrows.crosses` is offered every move
before it is kept. Eleven of the twelve short approaches in the two corpora take it.

It also steps a run out from under a callout **resting** on it. `place` decides where a callout
goes by measuring what it OCCLUDES, so a box that stops a fraction of a px short of a line pays
nothing for it and still reads as sitting on it — the reference state machine had 0.4px of gap
over 214px of `user leaves`. The line moves for nothing, and what bounds the move is the channel
beyond: pushed further than it must be, a run starts crowding the next one, and there they are
30px apart.

That figure's callout is no longer below the box at all, and the reason is worth reading with
the paragraph above: what a callout is charged for used to be the BOX of each thing it sat
inside, which counted the empty middle of every L-shaped route, so a real overlap came out
inflated more than tenfold and a page-height saving could always outbid one. Charged for the
line it actually hides, the anchor above the box — free of every route, 48px taller — is what
the search picks. `place.HEIGHT_PRICE` is what prices that trade — and a corner or an
arrowhead is not in it at all: those are ranked above everything tradeable, so a position that
covers one loses to any position that does not, whatever the page costs.

It also moves a whole TAIL, and that repair is about a PAIR of arrows rather than about either
one of them. ELK gives edges pointing at one row of one table the same port and they arrive
exactly on top of each other, which is how a reader knows they are a bundle — but not
reliably, and one arrival a few px off the bundle is neither one line nor two followable ones.
`route._converge` brings them onto one.

**Which of them moves is decided by what it would cost the rest of the route**, not by counting
heads. Moving a tail is free — the last run slides across and the run feeding it grows to
match — but a route drawn as a single straight line has no corner to absorb that, so moving it
drags its start off the middle of the row it leaves. A route that cannot move its tail alone is
therefore the one the others come to, whatever the count.

The same module repairs one other thing, and the tell for it is a single character. d2 draws an
orthogonal corner as an `S` and a diagonal one as a `C`, so **a cubic in a connection path is a
route that moved on both axes at once** — a run that is neither vertical nor horizontal, which
`arrows.defects` cannot report because `CORNER` has to tolerate 20px of off-axis before it calls
anything diagonal. There is exactly one in either corpus: `fan-out` leaving Redis 6px across and
6px down, drawn as a cubic whose control points run out past the turn and back, which is the
small bump on that line. `route._square` gives it `STEP_DEPTH` to turn twice in, in the
direction the route was already heading, so no rule is needed for which way to go.

`d2.ELK_EDGE_LADDER` is the fallback for the twelfth. It widens the gap ELK leaves between an
edge and the box it points at, per figure, only where there is something left to fix and only
as far as the size gate still passes (`render._afford`), and it costs 40 to 100px of page.

**Keep it.** It looks like dead weight — one figure in either corpus reaches its second step — but
what it exists for is a shape, not a figure, and the skill runs on whatever a reader's codebase
turns out to be. The shape is *an edge whose label nearly fills the leg it sits on*. `route._slide`
would fix the arrowhead by moving the step back, and its own arithmetic then refuses: the room it
may take is the leg minus the label's extent minus `edgelabel.CENTRE_SLACK`, and what it needs is
`arrows.APPROACH` minus the straight line the head already has. Both are single-digit numbers on a
figure like that, so the two land within a pixel of each other and which way is luck.

The corpus example is the repo state machine, and it misses by half a pixel: 6.0px of room against
6.5px needed. Measured both ways at the same scale a reader sees, side by side, and the 47px of
extra width was accepted — a detached arrowhead breaks a rule the whole corpus is held to, where
the width costs a fraction of a glyph. Do not read the half pixel as the interesting number: it
says the two constraints are the same size on this kind of edge, which is the reason to keep a
lever for it at all.

**What a figure cannot pay is width, not height** — the reference architecture is the one that
showed it, and the margin is now one step rather than none. Its two arrowheads wanted 24px of
spacing between an edge and the box it points at, where the drawing comes out 1079px wide; the
832px of drawing room inside a card scales that to 0.77 and its smallest text lands at 10.0px,
exactly on the floor. One step further, 25px of spacing, is 1091px wide and 9.9px — refused.
Height never moves at any step.

Two earlier readings of this same figure are worth knowing about, because both were wrong in a way
that read as a finding. One said it was 958px tall against the ceiling, measured against a ceiling
and a layout that no longer exist. The other said the text landed at 9.4px and was refused
outright, which was true only against a content column 55px narrower than the real one — see
`gates/size.AVAIL_W`, which was stated rather than derived for a long time.

A gap that lands on an arrowhead is the same shortage seen from the other end, and it is
bought with the OTHER ladder. ELK sizes the space between two layers to hold the edge label and
no more, so a long label can end up on a leg with no spot that both leaves the line a beginning
and keeps clear of the head — the repo ER had 126px of label on a 146px leg, needing 148, and
whichever end it gave up the words ended against a 5px stub of line. `d2.ELK_SPACING_LADDER`
already climbed for unreadable text; `render._climb_layers` climbs it for this too, on the same
`_afford` test, and that figure is 60px wider for it.

What is left over is reported rather than paid for, in `test_arrows.KNOWN`, which fails if the
list grows — and which is empty now that the step can move. Ranking arrow defects above height in
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
- Note placement is a measured search over eight anchors per note, swept until a pass moves
  nothing; `test_place_slow.py` covers it end to end in a real browser. Run `--slow` once, at
  the end, and only if you touched placement, callout geometry or the harness.
- **`measure_speed.py` (repo root) is the answer to "did that make it slower?"** — don't hand-roll
  a timing script, and don't trust one that was. It times four jobs against `speed_baseline.json`,
  shows what moved, and its docstring carries the rules that make a timing honest (warm up and
  discard the first run; best of N; alternate when comparing two versions; trust the counts over
  the seconds). Every one of those is a mistake somebody here already made — a cold-vs-warm
  comparison once reported a 34% win that was really 41%.

      python3 measure_speed.py            # ~4 minutes, writes and opens a report
      python3 measure_speed.py --update   # record this run as the new baseline

- **Say which of these two a cost figure is for** — they differ by nearly 3x, and quoting one
  for the other is how the old number here came to read 40s. Warm, all three sample sets, and
  read them out of `speed_baseline.json` rather than from here if the number matters:

  | | wall | layout candidates | browser starts |
  |---|---|---|---|
  | eleven diagrams arranged, no notes placed, no gates | ~6s | 68 | 1 |
  | the same eleven, plus note placement and gates | ~13s | 112 | 5 |

  **Note placement is most of the difference between those two rows, and it scales with the
  diagram**, because every anchor tried is a d2 compile of the whole graph plus a page to
  inspect. Counted, that is about 8 candidates for the first note on a diagram and about 14 for
  each one after it — a settling round, plus the round that confirms the earlier notes against
  where this one landed. It used to be 8^n up to two notes, which made two the dearest count a
  diagram could have, dearer than three or four. **A note is the lever** if this ever needs to
  be cheaper; the arrangement search is not, being a tenth of the same figure. `examples_large`
  carried four notes when it was transcribed and carries one for this reason.

  A first render in a fresh process costs ~25s more than a warm one. If a change appears to cost
  much more than the figures above, something is re-deciding a layout that was already decided.
- **Starting a browser costs ~20x what inspecting one more page in a running one does**, so the
  renderer keeps one and reuses it — see [`browser.py`](browser.py). Read the pair out of
  `speed_baseline.json`; quoted anywhere else they go stale, and twice have.

  What follows from that is which count to watch. **Browser starts are now nearly flat in the
  amount of work**: five of them serve all thirty-one batches of a full check, so a change that
  adds batches is not thereby adding launches, and a change that adds a launch has done
  something structural — a pool reset, or work escaping onto a thread the pool cannot reach.
  `browser.SHARD_MIN` no longer governs this and has not since the pool arrived.
- **A standalone render is never scaled.** `gates/size.analyse(standalone=True)` fixes the scale
  at 1.0, because a file is shown at natural size and zoomed by the reader. So every rule about
  a file's text is a rule about what was AUTHORED, and the text floors — which only ever bite
  through downscaling — cannot fire on that target at all.

## Dead ends — measured, and not to be retried

Most of these were built and reverted; the rest were measured and refused. None of them is
visible in the code, which is why they are written down.

- **Moving the page's limits to give the drawing more room** — a `<body>` wider than 880px, or a
  floor lower than 10px under primary text. The floors are a legibility result, not a budget:
  derived by showing both corpora in the real column against the real body prose, 14px down to 8px.
  The lightbox is not an argument against them — needing the click means the inline figure already
  failed. Figures that come up short miss by very little (24px of edge spacing wanted against 20
  affordable, twice), which is two outliers, not a wall.
- **Asking ELK for a particular arrangement of the boxes.** `repo/er`'s four tables would read best
  as a 2x2, `note`/`edges` above `spec`/`nodes`. `layered` puts every box in a layer such that
  every edge crosses to the next — columns under `direction: right`, rows under `down` — and that
  2x2 needs `nodes -> spec` to run *within* a row. **0 of the 36 candidates** produce it (2
  directions x 3 wraps x 3 layer spacings x 2 edge spacings); only three arrangements exist at all.
  Another algorithm is no way out: `layered` is the only ELK one that routes ORTHOGONALLY and every
  other draws a diagonal. Seven of the twelve also crash d2 — four on any edge at all, and
  `force`/`stress`/`mrtree` on a self-loop, which this figure has.
- **d2's `grid` layout for that same 2x2.** It does place it, keeps row anchoring, and wins on
  every size number: 606x486 against 1002x689, unscaled, 13.0px text against 11.6px. Refused for
  two things that cannot be worked around. A grid draws straight lines rather than routes — four
  arrow defects including a 53x40px diagonal, and `route.py` fixes none of them because there are
  no runs or corners to adjust. And **d2 refuses an edge from a grid cell to itself**, so this
  figure's `n children : 1 container` is silently absent. Getting the 2x2 means writing an
  orthogonal router; placement was never the hard half.
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
