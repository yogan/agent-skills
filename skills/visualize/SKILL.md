---
name: visualize
description: Draws a diagram of something in the codebase — a database layout, a request or interaction flow, how a set of classes relate, a state machine, or an architecture — by exploring the code, deriving a spec, rendering it with D2, checking it against legibility and contrast gates, and opening the finished image. Use when the user asks to see, draw, diagram, visualize, or get an overview of structure or flow ("show me the DB layout", "visualize what happens when a user logs in", "give me a visual overview of the service classes", "diagram the socket lifecycle"), or invokes /visualize.
---

# Visualize

Turn a question about the codebase into one diagram that answers it.

The rendering is solved and gated — you do not need to think about colour, size, or layout.
Your job is the two things a renderer cannot do: **choose the right kind of diagram for the
question**, and **find out what is actually true in the code**. Everything else is
`lib/diagram`.

Output is descriptive, not evaluative: a picture of how things are, not an opinion about how
they should be. No findings, no recommendations.

## Step 1 — Turn the request into a question, then pick the kind

Diagram kinds are not interchangeable, and the most common failure here is drawing a boxes-
and-arrows picture for a question about *order* or *state*. Work out what the user actually
wants to know, then read it off this table:

| The reader's question | kind |
|---|---|
| What talks to what, and where does this live? | `architecture` |
| What happens, in what order, across components? | `sequence` |
| What does the data look like? | `er` |
| How do these types relate? | `class` |
| What states can this be in, and how does it move between them? | `state` |
| How did we get from the old design to the new one? | `steps` (animated) |

Read the user's words for the giveaways:

- "when X happens", "the flow when", "what happens if" → **sequence**. Anything with an
  order in it. Boxes and arrows cannot express "first, then".
- "layout", "schema", "tables", "columns" → **er**.
- "lifecycle", "can it be in", "transitions", "retry", "states" → **state**.
- "classes", "interfaces", "types", "who implements" → **class**.
- "architecture", "overview", "how it fits together", "where does X run" → **architecture**.
- "migration", "cutover", "before and after" → **steps**, but only if consecutive boards
  differ in *topology*. If nothing moves, a static diagram says it better.

If the request genuinely spans two questions ("show me the schema and how a write flows
through it"), draw **two diagrams**, each answering one. Do not merge them: a diagram that
answers two questions answers neither clearly, and both will be too big to read.

If the request is ambiguous between two kinds, say which you picked and why in one sentence,
then draw it. Do not stop to ask unless the two readings would send you exploring completely
different parts of the codebase.

## Step 2 — Explore, and get the facts right

This is the step with no shortcuts, and the one that decides whether the diagram is worth
anything. A beautiful diagram of a structure that does not exist is worse than no diagram.
**Never infer structure from names alone** — open the file.

Where to look, by kind:

| kind | read |
|---|---|
| `er` | migrations (the latest state, not the first), schema dumps, ORM model classes, `CREATE TABLE`. Prefer a migration directory's *end state* over a stale `schema.sql`. |
| `sequence` | the entry point (route, handler, controller, event listener) and then follow the calls. The order is the content, so trace it; do not guess it. Decide explicitly whether the failure path belongs — for "what happens when X" it usually does, and if you leave it out, say so. |
| `class` | the type/class declarations themselves, plus what they implement or extend. Method signatures, not bodies. |
| `state` | the enum or union of states, and every place it is assigned or transitioned. Look for the error and retry paths — they are the ones people forget, and usually the interesting part. |
| `architecture` | deployment manifests, service definitions, client config, and the calls that cross a process boundary. |

Four rules that keep a diagram honest:

- **Show only what you verified.** If you could not work out whether an edge exists, leave it
  out rather than drawing a guess. An absent edge is a gap; a wrong edge is a lie.
- **In a `sequence`, an arrow means "A calls B" — so if a component *reacts* to state changing
  somewhere else, draw the state.** This is the failure that looks most like success: the
  interesting part of a flow is often "and now the app knows", nothing calls anything to make
  that happen, and the tempting fix is an arrow between two things that never speak. Put the
  medium on the canvas instead — the cache entry, the table row, the queue topic, the file, the
  flag — and both halves become real calls: whoever wrote it *called* the write, and the
  reactor *calls* the read. A participant with nothing but a self-call is the symptom of having
  skipped this.

  What is left over after that is a genuine **push**: the receiver never asked, and there is no
  medium to point at, because the sender simply sends. Mark it `"push": true` (dashed, open
  arrowhead) and label it with what triggered it — `on peer join`, `on row change`. Do not use
  it for an ordinary call you happen to be waiting on.

- **A lane is a role in the interaction, not necessarily one module.** Drawing the medium adds
  participants, and the ≤7 message budget pushes back — when they collide, raise the *altitude
  of the lanes* before you drop an edge or split the diagram. Two collaborators that are one
  subsystem at the level the question is asked belong in one lane, and then the handoff between
  them is internal: there is no arrow to omit and nothing to invent. Put the real names in
  `detail`, a smaller second line, so the reader can still grep for something.

  Two things not to merge, because both turn the abstraction into a way of hiding what you did
  not check:

  - **Never merge across a boundary the question is about.** For "what happens when a user logs
    in", the internals of the routing layer are not the question but the API boundary is.
  - **Never merge a medium with its reader or its writer.** The medium earns its own lane
    precisely because two different parties touch it; fold it in and the reaction stops being a
    call and goes invisible again — which is the problem you were solving.
- **Label with the code's own words.** Use the real table, column, class and method names.
  A reader has to be able to grep for what they see.
- **Label every relationship.** An unlabelled arrow says two things are connected and nothing
  about how, which is the least interesting half of the answer. In particular:
  - **`er` edges carry a cardinality that names its entities** — `1 doc : n edits`, `1 doc :
    0..1 claim`, `n : m via email_documents`. **Not a bare ratio:** `n : 1` is technically
    correct and hard to read, because it makes the reader work out which end is which and then
    map it back to the table names. The named form reads on its own. Point the edge at the
    specific column (`documents.owner_id -> users.id`) so it is obvious which key it is.
  - **`state` transitions carry their trigger** — what causes the move.
  - **`class` edges carry the relationship** — `owns`, `implements`, `emits` (dashed for
    implements/uses, solid for ownership).

## Step 3 — Write the spec, small

The spec is JSON. Full field reference: [REFERENCE.md](REFERENCE.md).

**The output is a standalone image, so it has no width to fit into.** A reader opens it and
zooms; a nine-table schema at full size is fine. Do **not** split a diagram because it looks
wide, and do not split one to satisfy a size warning — the warnings are advisory, and the
standalone gates deliberately do not enforce a page width or a viewport height. The renderer
leans *into* that width: a standalone `er`, `class` or `state` diagram is laid out wide,
because it is opened full-screen on a landscape monitor. Leave `direction` alone and let it.

Split only for an editorial reason: the diagram is answering **two different questions** and
each deserves its own. "All the tables" is one question, even with nine tables in it.

What is still worth keeping tight, because it is about legibility rather than fitting:

- **Only the columns and members the question is about.** A table has 30 columns; the question
  is about 4. Show 4. This is the single biggest lever you have, and it is about signal, not
  size.
- **≤6 states, ≤7 sequence messages.** Both are editorial, not arithmetic: the renderer
  compacts a sequence diagram's rows for you, so a long one is not *tall* — it is just a
  program the reader has to execute in their head. A state machine past six states is usually
  two questions wearing one coat.
- **Short edge labels when the diagram also has callouts.** They compete for the same
  whitespace, and D2 reserves no room for a callout, so a long edge label beside one is the
  likeliest thing to end up half-covered.

The renderer prints advisory warnings before it draws. Read them, but weigh them — for a
standalone image, "wide" is not a problem.

### Use the roles as a vocabulary

Pick a `role` for what a thing *is*, never for the colour you want:

`client` · `svc` (logic) · `store` (persistent) · `cache` (transient) · `ext` (outside our
control) · `neutral`

A `state` diagram uses a different set, for the same reason — a state is not a datastore:
`working` (in progress) · `steady` (settled, the happy resting place) · `transient` (retrying,
about to move) · `terminal` (the end of the line) · `neutral`. The colours land on the
traffic-light reading a reader already has, which is the one place colour carries meaning here
without a legend.

The point is consistency: if `store` means Postgres in one diagram, it must not mean "the
important one" in the next. When you draw several diagrams for one request, reuse the roles
identically across them — that consistency is most of what makes a set of figures feel like
one system rather than several unrelated pictures.

### Notes are the exception. Default to none

**Most diagrams should carry no `note` at all.** A callout points at something; if the user
asked for a plain overview — "show me the DB layout" — there *is* nothing to point at, and one
callout per table is noise that covers the edge labels underneath it.

Add a note only when something in the request or the material makes a specific thing worth
singling out:

| the request | notes? |
|---|---|
| "show me the DB layout", "overview of the classes" | **none.** The diagram is the answer. |
| "what changed in this MR" | yes — on the new or changed things, sparingly |
| "where is the flaw in this schema", "what looks wrong here" | yes — on the flaw |
| "why is this slow" | yes — on the hot path or the missing index |

**A note must not describe what a box already is.** That is the failure mode to watch for, and
it is easy to slip into, because glossing each table *feels* helpful:

| | |
|---|---|
| `document_claims` → "leased, expiring" | **no.** A caption. It says what the table is for, which its own name and columns already say. |
| `email_documents` → "join table" | **no.** Two foreign keys and no other columns already say that. |
| `documents` → "gains a revision column" | **yes.** A change the reader cannot see. |
| `sessions` → "no index on user_id" | **yes.** A flaw, and the answer to what was asked. |

The test: would the reader **miss or misread** something without it? If the fact is intrinsic to
the thing, it belongs in the label, or it is already visible in the columns. If it is a change, a
flaw, or the specific answer to the question, it is a note.

When you do add one, keep it to **2–4 words** in the reader's own language. Two or three across a
whole diagram is plenty; annotating most of the boxes makes the annotations mean nothing.

Do not reach for styling to convey meaning instead. There is no legend, so a thick border or an
odd colour says "something here is special" without ever saying what.

## Step 4 — Render

**While you are still iterating, always pass `--no-open`:**

```bash
python3 ~/.claude/skills/visualize/scripts/visualize.py spec.json --no-open
```

Every run without it opens the image, so three refinement passes leave the user with three
windows to close. Open exactly once, at the end, when you are satisfied:

```bash
python3 ~/.claude/skills/visualize/scripts/visualize.py spec.json
```

That writes a **standalone SVG** — colours baked (light by default, since the image is viewed
inside a frame it cannot paint; `--theme dark` for the other), the background painted, and the
CSS its callout text needs carried inside the file —
places every callout by measuring all eight candidate positions in a real browser, runs the
gates, and opens it. Full size, so the reader can zoom; there is no page and no width limit.

Expect a few seconds per callout: the placement search is the cost, and it is why you never
position anything by hand. A diagram with no notes skips it entirely and is fast.

If the user asked for several diagrams, render them all with `--no-open`, then open each
finished one — or just tell them the paths.

The other modes are for feeding a page rather than a person:

```bash
python3 .../visualize.py spec.json --format embed  # themeable SVG, follows a page's toggle
python3 .../visualize.py --format css             # the CSS that page must then ship
```

`--format embed` is **not** a preview: its colours are `var()` references and its callout CSS
lives in the page, so opened on its own it renders unpainted shapes and clipped labels.

## Step 5 — If a gate fails

The script prints a table and exits non-zero. It still opens the page, so you can see what
it is complaining about. Fix the **spec**, not the styling:

| complaint | what it means | fix |
|---|---|---|
| `TINY … < 11px` | something renders too small to read | shorten labels; fewer columns |
| `CLIPPED` | a callout is cut off at the canvas edge | shorten its note text — a narrower callout fits where a wide one cannot |
| `contrast … FAIL` | a colour is unreadable on the baked background | you should not be able to cause this from a spec; report it rather than working around it |
| `no palette mapping` | D2 emitted a colour the theme cannot bake | ditto — report it |
| `GATE COULD NOT RUN` | the clipping gate has no browser | **not a pass.** Say so; it is the only gate that can see a callout cut off |

Note what is *not* in that table: nothing about width or height. A standalone image has no page
to fit, so "too wide" and "too tall" are not failures and splitting to fix them is wrong.

## Step 6 — Tell the user what they are looking at

One or two sentences: what the diagram shows, and the one thing worth noticing in it. If you
left something out to keep it legible, say so — that is information, not an apology.

If you drew more than one diagram, say what question each one answers.
