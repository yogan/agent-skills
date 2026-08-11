# Handoff — a `visualize` skill built on D2

Working document for the `feat/visualize-skill` branch. It is **scaffolding, not a deliverable**:
this file and `prototypes/` are both deleted before the branch merges to `main` — see
[Definition of done](#definition-of-done--before-merging-to-main) at the bottom. Anything durable
belongs in `README.md`, the skill's own `SKILL.md`, or a comment next to the code it explains.

## Decision

**Build diagram rendering on [D2](https://d2lang.com), extract it into its own `visualize`
skill, and retire Graphviz once the D2 path covers what `explain-diff` needs today.**

Reached by prototype, not by preference: one MR scenario drawn five ways by three engines, judged
by measurable gates rather than taste. Everything below is measured. Full write-up and the
artefacts are in `prototypes/diagram-stacks/` (start with its `NOTES.md`).

| | Graphviz | **D2** | Mermaid |
|---|---|---|---|
| install | 8.2 MB (current dep) | **35 MB single Go binary** | 405 MB + 565 MB Chromium |
| render, 5 diagrams | 276 ms | 660 ms | 1936 ms |
| page weight, 5 diagrams | 50 KB | 178 KB | 212 KB |
| size gate | 4/5 | **6/6** | 2/5 |
| contrast gate, both themes | 5/5 | **6/6** | 3/5 |
| sequence diagrams | **impossible** | native | native |
| DB tables / class models | verbose HTML labels | native `sql_table` | native |
| change annotations | none | `tooltip.near` callouts | none |
| animation | none | `steps:` → one CSS-animated SVG | none |

Mermaid was rejected on dependency weight and render time, not capability. Graphviz is excellent
at plain graphs and needs zero post-processing, but cannot draw a sequence diagram at all: `dot`
reorders lifeline columns to minimise edge crossings, so participants come out in the wrong order
with sloped arrows. That is structural, not cosmetic.

### Why Graphviz goes rather than staying alongside D2

Keeping both was considered and rejected during the prototype, for the reason it was rejected the
first time it came up: **two engines means two visual languages, two theming maps, two sets of
undocumented quirks, and no single source of visual truth.** A document that mixes them will look
mixed. The only honest arguments for keeping it are that it is already installed and that it can
be compacted (`ranksep`/`nodesep`) where D2 cannot — and content limits handle the latter.

Plan: leave `render.py`'s Graphviz path in place until the D2 path renders every diagram type the
explainers use, then delete it and drop Graphviz from the README requirements. Do not ship both
long-term.

## Architecture

`lib/` is importable from a skill even when that skill is symlinked into `~/.claude/skills/`,
because the scripts resolve the repo root through `os.path.realpath(__file__)` (see
`skills/review-mr/scripts/findings.py`). That is the existing cross-skill sharing mechanism and it
is what this design leans on.

    lib/diagram/            pure Python, no skill knows about any other skill
      spec.py               the JSON spec: diagram kinds, roles, annotations
      d2.py                 spec -> .d2 source (the recipe below lives here)
      render.py             run d2, post-process the SVG (see "host-side fixes")
      palette.py            literal colour -> CSS var map, light/dark  [prototype has this]
      gates/size.py         viewport + glyph-size rules              [measure.py]
      gates/contrast.py     WCAG AA, both themes                     [contrast.py]

    skills/visualize/       NEW — standalone skill
      SKILL.md              the playbook: which diagram type for which question
      scripts/visualize.py  thin CLI over lib/diagram

    skills/explain-diff/scripts/render.py    keeps owning the HTML page;
                                             imports lib/diagram for {{diagram:name}}

`explain-branch` and `review-mr` inherit whatever `explain-diff` does — they already share
`render.py`.

**Watch out:** if `visualize` ends up invoked by path from another skill rather than through
`lib/`, the user must have symlinked *both* skills. Prefer `lib/` for shared code and keep
`skills/visualize/scripts/` a thin wrapper, so no skill depends on another skill's installation.

## `visualize` as a standalone skill

The point of extracting it: diagrams are useful when nothing has changed. Target questions —

- "show me the DB layout"
- "visualize the user interaction flow when logging in"
- "give me a visual overview of the customer service and repository classes in the backend"

so the skill needs its own **exploration** step (read the schema / routes / classes and derive the
spec) that the explainers do not need, because they start from a diff. Keep the spec + renderer
shared; keep exploration in the skill.

Output modes worth supporting: standalone HTML page (like the explainers), and a bare SVG for
embedding elsewhere.

## The D2 recipe (non-obvious, all measured)

Long-form in `prototypes/diagram-stacks/NOTES.md`. The parts that cost the most to find:

1. `style.fill: transparent` on the root — d2 otherwise paints an opaque page rect that shows as a
   white slab in dark mode. Do **not** strip that rect in post-processing: it breaks d2's `<mask>`
   and unbalances the XML (`<rect …></rect>` is not self-closing).
2. `sql_table` / `class` couple **three** roles to two properties:
   `fill` = border + header background + **member text**, `stroke` = body background,
   `font-color` = header title. Give each its own **sentinel literal** so they can theme
   independently — no single value keeps all three readable in both themes.
3. Colour → CSS var substitution must **skip `<mask>` contents**. A mask works on luminance, so
   rewriting `#ffffff` to a dark value inside one inverts it and blanks the drawing.
4. **Pin `width`/`height` from the viewBox.** d2 emits neither, so `width:auto` lets the browser
   *upscale* the drawing and inflate every glyph (measured: class diagram at 1.34×, 16px headers
   rendering at 21.4px — larger than body text).
5. A `sql_table` header renders at ~1.3× the shape's base font, ignoring the global font setting.
   Use `shape: sql_table` for class diagrams too — `shape: class` wastes ~47px per header.
6. `direction: down` for tables/classes; edge labels at 13, table base font 14.
7. Callout text is a `<foreignObject>`; d2 ships no paragraph reset, so the host page must supply
   `foreignObject .md p{margin:0}` plus a `font-family`, or the text is clipped out of its box.
8. Annotate changes with `tooltip` + `tooltip.near` (permanently visible). **Do not** use a thick
   border (no legend, meaningless) or `{constraint: NEW}` (reads as a DB constraint) — both were
   tried and rejected.
9. Content discipline is not optional: ≤6 states, ≤7 sequence messages, short labels. d2 exposes
   no `ranksep`, so a sprawling diagram cannot be compacted afterwards — only authored smaller.

**Risk:** items 2, 4 and 5 are undocumented d2 behaviour and could change on upgrade. That is
tolerable *only* because the gates below turn "does this look right" into a test. Pin the d2
version and let the gates catch the drift.

## The gates are the real deliverable

Port these before porting features. Prototype versions in `prototypes/diagram-stacks/`.

| gate | rule | needs a browser? |
|---|---|---|
| size | rendered height ≤ one viewport | no |
| glyph | no glyph larger than an `h2` (26.6px at a 19px root) | no |
| body | **modal** glyph size ≤ body text (19px) — the size most of the diagram is set in | no |
| legibility | no glyph below ~11px | no |
| contrast | WCAG AA in **both** themes | no |
| clipping | nothing cut off, shadow spread included | **yes** |

Two lessons encoded in them, both of which produced wrong green results first:

- `rsvg-convert` silently drops `<foreignObject>`, so rasterised checks cannot see callout text.
- A static SVG clip check is not viable: it ignores `transform="translate()"` and cannot account
  for a CSS `drop-shadow`'s spread. `clipcheck.js` measures `getBoundingClientRect()` in Chromium
  against the element that actually clips (the card, `overflow:hidden`).

**Make a gate fail loudly when it cannot run.** During the prototype a patch to a checker silently
no-op'd (its marker string did not exist) and still printed success; the green result was
meaningless until verified by reading the file.

## Decided: a headless browser IS in the render loop

So we get permanently-visible `tooltip.near` callouts, automatic placement (`place.js` renders all
8 anchors and keeps the best; `joint.js` searches two callouts exhaustively), and the clipping
gate. There was no credible middle option — the static clipping checker was tried and does not
work (it ignores `transform="translate()"` and cannot see a CSS `drop-shadow`'s spread).

Measured cost on this machine, so the next session does not have to re-derive it:

| step | time |
|---|---|
| d2 render, 6 diagrams | 754 ms |
| automatic placement, 7 callouts (8 renders + a browser measure each) | 10.0 s |
| joint search for one 2-callout diagram (64 combinations) | 12.1 s |
| clipping gate, all diagrams | 3.6 s |
| size + contrast gates | 0.14 s |

A realistic document — a handful of diagrams, a few callouts — lands under ~10 s end to end, one
browser launch. Acceptable for one-shot document generation; it would not be acceptable per
keystroke, so keep placement in the build step, never in a preview path.

**Toolchain, already installed and verified on this machine:**

| | version | note |
|---|---|---|
| `d2` | 0.8.1 | `brew install d2`. **Pin it** — items 2, 4 and 5 above are undocumented behaviour. |
| `puppeteer-core` | 25.5.0 | `npm i -g puppeteer-core`, 29 MB, **no bundled browser** |
| Chrome | system install | driven via `executablePath`; Chromium and Edge also detected |

Deliberately *not* the full `puppeteer` package: it downloads its own ~550 MB Chromium. Driving
the system browser through `puppeteer-core` costs 29 MB instead. `prototypes/diagram-stacks/browser.js`
holds the resolution logic (env overrides `PUPPETEER_CORE` / `PUPPETEER_EXECUTABLE_PATH`, else the
usual macOS app paths) — carry that idea into `lib/diagram`, and fall back to advising
`npm i -g puppeteer` when no system browser exists.

Removed after the comparison finished: `@mermaid-js/mermaid-cli` (405 MB) and the
puppeteer-managed browser cache (551 MB). Graphviz is still installed because `render.py` still
uses it; it goes with the last step.

## Final step: integrating `visualize` into the `explain-*` skills

This is the last milestone, and the one most easily done badly. The explainers must **decide for
themselves** what to draw and how much — the value is a judgement call, not a quota.

**No fixed counts.** Do not write "at least one diagram" or "at most four" into the SKILL.md, even
though such numbers sound reasonable. They cause padding on trivial changes and truncation on
complex ones. A one-file rename may deserve no diagram at all; a cross-service refactor may
deserve several. The instruction must be to match the shape of the change.

The test a diagram has to pass, in this order:

1. **Does it answer a question the prose cannot?** Structure, flow, relationships and
   before/after are hard in sentences and easy in a picture. A fact that fits in one clear
   sentence should stay a sentence.
2. **Would the reader misunderstand the change without it?** If not, it is decoration.
3. **Is it a different question from the diagram before it?** Two views of the same thing is
   worse than one good view. Prefer deleting the weaker one.

Then choose the *kind* by the question being answered — that mapping is the core of the playbook:

| the reader's question | diagram |
|---|---|
| what talks to what, and where does this live? | architecture with containers |
| what happens, in what order, across services? | sequence |
| what does the data look like now? | ER / tables |
| how do these types relate? | class |
| what states can this be in, and how does it move? | state machine |
| how did we get from the old design to the new one? | animated `steps:` |

Two more rules carried over from the prototype, both load-bearing:

- **Reuse a small visual vocabulary across a document.** A colour or shape should mean the same
  thing in every figure. This is most of why the Netflix diagrams that started this work cohere.
- **Mark what the MR changed** with `tooltip.near` callouts, in the reader's words ("new service",
  "gains a revision column") — not with ad-hoc styling. Keep them to a few words.

Content limits (≤6 states, ≤7 sequence messages, short labels) are a *rendering* constraint, not
an editorial one: d2 cannot compact a sprawling diagram after the fact. If the subject genuinely
needs more, split it into two diagrams that each answer a narrower question rather than shrinking
one past legibility. The gates will catch it either way.

## Suggested order of work

1. `lib/diagram/` with the spec + d2 emitter + post-processing, and the non-browser gates as
   `test_*.py` next to them (repo convention: plain `unittest`, colocated).
2. Placement + the clipping gate (browser in the loop — decided, see above).
3. `skills/visualize/` — SKILL.md playbook + exploration step + standalone output. **Ship and use
   it standalone before wiring anything else into it**; the explainers are a consumer, not the
   reason it exists.
4. Port `explain-diff`'s `{{diagram:name}}` path onto `lib/diagram`, Graphviz still present.
   Regenerate a real explainer (MR !588 was the reference) and compare against the current output.
5. The judgement layer in the `explain-*` SKILL.md (previous section). Last, deliberately: it is
   editorial guidance, and it can only be tuned once real explainers are being produced.
6. Delete the Graphviz path, drop it from the README requirements, add `d2` and `puppeteer`.

Steps 1–2 and 4 are mostly mechanical. Steps 3 and 5 are the *writing* jobs and carry the actual
risk: a playbook that picks the wrong diagram type, or an explainer that pads out diagrams it does
not need, fails the reader in a way no gate can detect.

## Re-running the prototype

    cd prototypes/diagram-stacks
    ./build.sh && python3 gallery.py     # writes /tmp/diagram-stack-prototype.html
    python3 measure.py && python3 contrast.py && node clipcheck.js

Expected: `6/6 pass both gates`, `6/6 pass WCAG AA in BOTH themes`, `0 clipped`. Run this **before**
writing any of `lib/diagram` — if d2 has drifted, you want to know that first, not after building
an emitter on top of it.

The graphviz and mermaid arms have been stripped from `build.sh` and `gallery.py` now that the
engine is decided; the d2 sources under `src/d2/` are the worked reference examples.

## Definition of done — before merging to main

The branch is not finished when the code works; it is finished when the scaffolding is gone.

- [ ] `lib/diagram/` in place, with colocated `test_*.py` (run via `python3 run_tests.py`)
- [ ] `skills/visualize/` shipped and used standalone at least once for real
- [ ] `explain-diff` / `explain-branch` / `review-mr` render through `lib/diagram`
- [ ] the judgement layer written into the `explain-*` SKILL.md — **and no diagram-count numbers**
- [ ] Graphviz path deleted; README requirements updated (`d2` + `puppeteer` in, Graphviz out)
- [ ] **`rm -rf prototypes/` — the comparison gallery and its build scripts are throwaway**
- [ ] **`rm HANDOFF.md` — this file**

Migrate before deleting, not after. Anything in here still worth knowing has a permanent home:

| what | where it belongs |
|---|---|
| the d2 recipe (the 9 non-obvious behaviours) | comments in `lib/diagram/d2.py`, next to the code each one explains |
| the gates and their thresholds | the gate modules themselves + their tests |
| why d2 over graphviz/mermaid | one short paragraph in `README.md`, or a commit message on the branch |
| which diagram answers which question | `skills/visualize/SKILL.md` — that is the playbook |
| the "judgement, not quotas" rule | the `explain-*` SKILL.md files |

If something in this document has no home in that table, it was working notes and should die with
the file. Do not create a `docs/` folder just to keep it alive.
