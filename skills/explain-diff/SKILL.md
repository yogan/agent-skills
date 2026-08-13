---
name: explain-diff
description: Generates a rich, interactive, self-contained HTML explanation of a code change — background, intuition, code walkthrough, and a quiz — then opens it in the browser. Accepts the current branch, a named local/remote branch, a GitLab MR (e.g. "MR !123", resolved via glab), or just the last commit. Use when the user wants a diff, branch, commit, or MR explained, turned into a learning doc, or invokes /explain-diff.
---

# Explain Diff

<!-- Source: adapted from https://gist.github.com/geoffreylitt/a29df1b5f9865506e8952488eac3d524
     (explain-diff-html.md), with the render.py extraction + quiz-randomization
     fix from https://gist.github.com/ankitg12/8e808d387799de4e9839bc393f8e6405.
     Dropped the Notion export step; reworked target resolution to reuse this
     project's detached-HEAD-safe branch/MR handling (see ~/.claude/skills/review-branch). -->

Output is critique-free: a teaching document, not a review. No findings, no severity — just explanation.

## Workflow

### Step 1 — Resolve the target

Translate the user's request to one canonical spec:

| User says | Spec |
|---|---|
| nothing, "this branch", "this diff" | `""` (blank) |
| "branch foo/bar", "remote branch feat/bar" | `branch:foo/bar` |
| "MR !123", "PR 123", "merge request 123" | `mr:123` |
| "last commit", "most recent commit" | `commit:HEAD` |
| "commit abc1234" | `commit:abc1234` |

Run the bundled script (handles detached HEAD transparently, same as `/review-branch`):

```bash
python3 ~/.claude/skills/explain-diff/scripts/resolve-target.py "<spec>"
```

Source the output variables: `BASE`, `HEAD_REF`, `LABEL`, `IS_SINGLE_COMMIT`, `MR_NUM`, `MR_URL`, `MR_TITLE`. The last three are only set for an `mr:` spec (empty otherwise) — no need to query the MR again later to build a link.

If the script errors, report the error and stop.

### Step 2 — Collect raw material

Run in parallel:

```bash
git log --oneline "$BASE".."$HEAD_REF"
git diff "$BASE".."$HEAD_REF"
git diff --name-only "$BASE".."$HEAD_REF"
git diff --shortstat "$BASE".."$HEAD_REF"
```

### Step 3 — Explore surrounding code

For the **Background** section you need more than the diff. Read the touched files in full (not just hunks) and skim the modules/functions that call into or are called by the changed code — enough to explain the system the change lives in, not just the change itself.

### Step 4 — Write the content spec (don't hand-write HTML)

**Use `render.py`** (bundled at `~/.claude/skills/explain-diff/scripts/render.py`) instead of writing the full HTML page by hand — it owns all page boilerplate, so regenerating that per invocation wastes tokens and drifts in quality. Run `python3 ~/.claude/skills/explain-diff/scripts/render.py --help` if you need the exact JSON schema.

Write only a small JSON content spec covering these sections, in this order:

- **Background** — Explain the existing system relevant to this change (from Step 3's exploration). Assume the reader's prior knowledge is unknown: include a deep background for beginners (note it can be skipped by the already-familiar), then a narrower background directly relevant to the change.
- **Intuition** — Explain the core intuition behind the change. Focus on the essence, not every detail. Use concrete examples with toy data. This is usually where a diagram earns its place, if one does.
- **Code** — A high-level walkthrough of the changes, grouped/ordered so it reads as a narrative rather than a file-by-file dump.
- **Quiz** — Five questions that test understanding, medium difficulty (must understand the substance to answer, no gotchas). Give each a `question` and `options` (each `{text, correct}`) — list options in whatever order reads naturally; `render.py` randomizes their displayed order itself, so don't try to fake randomness by hand. `question`/`text` are plain text: wrap identifiers, branch names, function calls, etc. in backticks (e.g. `` `origin/main` ``, `` `HEAD_REF` ``) — the renderer turns those into inline `<code>` automatically.

Content rules for each section's `html` field (raw HTML, not markdown):

- The backtick-to-`<code>` auto-conversion is quiz `question`/`text` and `subtitle` only. Section `html` is raw HTML — write inline identifiers as literal `<code>foo</code>`, never backticks; backticks left in `html` render as literal characters.
- Write with the clarity and flow of Martin Kleppmann — engaging, classic technical-writing style, smooth transitions between sections.
- Diagrams: see [Diagrams](#diagrams--judgement-not-quota) below. Define each in the spec's top-level `"diagrams"` dict and drop it into a section's `html` via a bare `{{diagram:name}}` token. Never ASCII art, and never hand-written flowchart HTML — the diagrams dict is the only path.
- Code blocks: use `<pre><code class="language-XXX">` for syntax highlighting — `<pre>` alone still works but loses highlighting. For a diff-style snippet, use `language-diff-XXX` so `+`/`-` lines get diff coloring *and* nested syntax highlighting (plain `language-diff` if the language isn't worth specifying); partial/incomplete snippets tokenize fine, no need for valid complete syntax. See `render.py --help` for the full language list and diff-line format.
- Use `.callout` divs for key concepts, definitions, and important edge cases; plain `<table>` for comparisons.
- Use a real ellipsis character ("…") wherever one is called for — never three dots ("...").
- If `IS_SINGLE_COMMIT` is `true`, the Background/Code sections should scope to just that commit's change, not the whole branch history.
- Set the spec's `"slug"` to `$LABEL` so the output filename matches the resolved target.
- Set the spec's `"subtitle"` to a short metadata line (source ref + commit) — same backtick-for-`<code>` convention as quiz text, e.g. `` `fix/drop-legacy-auth-adapter` · commit `a1b2c3d4` ``. When `MR_URL` is set, link the MR instead of writing it as plain text or backticked, and use its title as the link label: `` [MR !$MR_NUM: $MR_TITLE]($MR_URL) · `branch` · commit `hash` ``. For a `commit:` target there is no branch to name, so the whole subtitle is just the commit: `` commit `a1b2c3d4` · <the commit's subject line> ``. Don't invent a source ref to fill the slot.
- Sections in a flat explain-diff document don't take a `"commit"` field — that is explain-branch's per-chapter citation, and here it would print the same hash and diffstat the subtitle already carries.
- Parse Step 2's `git diff --shortstat` line into the spec's top-level `"diffstat"` field (`{"files", "insertions", "deletions"}` — either count can be absent when zero, then omit that key). `render.py` appends it to the end of the subtitle automatically; don't hand-write it into `"subtitle"` yourself.

Dark/light mode is handled entirely by the renderer. No spec fields needed for this.

#### Diagrams — judgement, not quota

**There is no target number of diagrams.** Not "at least one", not "at most four". A one-file
rename may deserve none; a cross-service refactor may deserve several. Match the shape of the
change — a quota produces padding on trivial changes and truncation on complex ones, and both
fail the reader.

Before adding one, it has to pass all three, in this order:

1. **Does it answer a question the prose cannot?** Structure, ordering, relationships and
   before/after are hard in sentences and easy in a picture. A fact that fits in one clear
   sentence should stay a sentence.
2. **Would the reader misunderstand the change without it?** If not, it is decoration.
3. **Is it a different question from the diagram before it?** Two views of the same thing are
   worse than one good view. Delete the weaker one. The one exception is a deliberate
   before/after pair — see below, where sameness is the point.

Then pick the kind by the question being answered — this mapping is the whole game, and the
common mistake is drawing boxes and arrows for a question about *order* or *state*:

| The reader's question | kind |
|---|---|
| What talks to what, and where does this live? | `architecture` |
| What happens, in what order, across components? | `sequence` |
| What does the data look like now? | `er` |
| How do these types relate? | `class` |
| What states can this be in, and how does it move? | `state` |
| How did we get from the old design to the new one? | **two** diagrams, before and after |

**"X was replaced by Y" is two figures, and it is the one place two are better than one.** It is
the commonest branch shape there is. Draw the same kind twice — before and after — with the same
node ids, the same roles and the same layout, and put the two `{{diagram:…}}` tokens **back to
back with at most one sentence between them**, so the reader compares by moving their eyes rather
than by remembering. What changed then reads off the difference: a box that is in one and not the
other, an edge that moved.

There is no animated kind, deliberately. It existed, it drew each phase as a board and cycled
them in place, and two readers independently said the same thing: with one board on screen at a
time you have to hold the previous one in your head, which is precisely the comparison you wanted
to see. Two figures, adjacent.

**Draw only what you read.** An absent edge is a gap; a wrong edge is a lie the reader cannot
see, because a plausible invented arrow looks exactly like a verified one. Step 3's exploration is
what the figure is made of — if you could not work out whether one thing calls another, leave the
edge out rather than drawing what the framework "usually" does.

Full field reference, with a worked example of every kind:
`~/.claude/skills/visualize/REFERENCE.md` (or `skills/visualize/REFERENCE.md` in the repo). You do
not need the `visualize` skill installed — the renderer is shared code. Read it rather than
guessing at field names; what follows is only the editorial part.

##### A figure on a page, not an image in a viewer

This is the one place the guidance differs from the `visualize` skill, which draws standalone
images and can therefore ignore width. Here the diagram lives in the article's **777px content
column**, and the page scales anything wider down — every glyph with it. Past roughly 900px of
natural canvas the smallest text drops under 11px, the size gate says `TINY`, and the figure is
genuinely unreadable in place. So:

- **Leave `direction` out, and do not hand-wrap an edge label.** The renderer draws the figure
  both ways round, measures each, and keeps the one that stays legible with less height —
  wrapping long edge labels itself if that is what makes the wider layout fit. It is the same
  measure-and-retry loop you would otherwise run by hand, and it is better at it: on one
  four-box chain it found a 912×93 landscape where the stacked version was 164×643. Setting
  `direction` yourself turns it off.
- **Width is set by text, not by box count.** The caps below (≤6 states, ≤7 messages, ≤6 boxes
  per container) are necessary and *not* sufficient: a five-table ER diagram inside every one of
  them still failed, because column names, type strings and edge labels are what the layout
  engine adds up. When the gate says `TINY` it tells you how many px too wide the drawing is —
  take it out of the widest row first (shorter labels, fewer columns), and only then drop a box,
  which removes a whole column at once.
- **In a `sequence`, the lane boxes set the width.** Measured, four lanes and four messages: no
  details 568px; a 15-character detail on every lane 679px; a 34-character detail on two lanes
  816px; on four lanes 1114px, which is the diagram scaled to 0.70 and unreadable. Shortening
  the *messages* moved it by nothing — a sequence is the one kind whose labels the renderer
  cannot wrap for you, because they sit along the arrows. So keep each `detail` to about one
  module name (`DocumentService`, not `api/documents.py · DocumentService · ValidationService`),
  and put it only on the lanes whose real names the reader needs.
- **A `title` is not drawn into an embedded SVG** — it only names the ids inside it, so leave it
  out and let the prose be the caption. Say what the reader is looking at in the sentence before
  the `{{diagram:…}}` token.

##### The vocabulary, which is enforced per kind

Reuse it identically across every figure in one document — a colour meaning the same thing
everywhere is most of what makes a set of figures read as one system.

- **Boxes** (`architecture`, `er`, `class`) take a `role` for what the thing *is*, never
  for the colour you want: `client` · `svc` · `store` · `cache` · `ext` · `neutral`. `neutral` is
  the quietest of them — inside a container it is a pale box on a pale background — so it is the
  wrong home for the box the change *added*. If nothing in the list fits that box, it is usually
  a `svc`: the thing that does the work.
- **A `state` has its own set**, because a state is not a datastore: `working` (in progress) ·
  `steady` (settled) · `transient` (retrying, about to move) · `terminal` · `neutral`. **The
  architectural roles are rejected on a state and vice versa** — this is a hard error, not advice.
- **A `sequence` colours by `group`, not by role.** Give each lane the side of the wire it is on
  — `browser`/`server`, `cli`/`daemon`, `browser`/`server`/`db` — and every lane in that group
  shares one colour, so the reader sees where the crossing happens. Group names are free text;
  two or three of them is the useful range, one per side of a boundary the reader cares about. A per-lane role paints six colours that each mean
  something different, none of it what a reader of a flow wants. A lane carrying both a `group`
  and a `role` is rejected. A group's colour is assigned by order of first appearance, so if the
  document has two sequence diagrams, write the groups in the same order in both — otherwise the
  browser is blue in one figure and purple in the next.
- **`detail`** (any box) is a smaller second line under the name: the real module names under a
  lane that is named at the altitude of the question.
- **`push`** (sequence only) draws a message the receiver never asked for — a server push, a
  subscription firing — dashed with an open arrowhead. Label it with what triggered it (`on peer
  join`). An ordinary call you happen to be waiting on is not a push.
- **`outcome`** (sequence only) colours a message red (`error`) or green (`ok`). Reach for it
  when the flow you are drawing can end two ways and the change is *about* which — a validation
  gate returning 409 against the path that writes and returns 200. It replaces the callout you
  would otherwise write to say so, and it is the answer to "where does this get turned back?"
  that a reader has to trace by hand otherwise. Colour the fork, not the whole flow.
- **Mark the box the change added with `new: true` and a one-word `note`.** The box is drawn in
  the accent colour — a border, or the fill on a table, which has none — and the note says what
  the accent means, because there is no legend. Two words are plenty: `"new"`, `"added"`. Do not
  write `"new table"` on a table; that it is a table is on the drawing already.
- **Anything else worth pointing at is a `note` on its own**, in the reader's words — "gains a
  revision column", "no index on user_id". Two to four words, and only where the reader would
  otherwise miss or misread something; a note that says what a box already *is* ("join table")
  is a caption, and it covers the edge labels underneath it. Never reach for styling to say
  something a note could say instead: `new` is the one exception, and only because it always
  travels with its words.

##### In a `sequence`, an arrow means "A calls B"

The single most common way an explainer's diagram asserts something false. When a component
*reacts* to state changing somewhere else, nothing calls anything to make that happen — and the
tempting fix is an arrow between two things that never speak. Put the medium on the canvas
instead — the cache entry, the table row, the queue topic, the file, the flag — and both halves
become real calls: whoever wrote it *called* the write, and the reactor *calls* the read. A
participant with nothing but a self-call is the symptom of having skipped this.

The database is the ordinary case: a lane for PostgreSQL, receiving `SELECT` and `INSERT`, is
both a real call and the medium — draw it whenever the flow's point is what got written.

Drawing the medium adds lanes, and the width budget pushes back. When they collide, raise the
**altitude of the lanes** rather than dropping an edge: two collaborators that are one subsystem
at the level the question is asked belong in one lane, with the real names in `detail`. Two things
never to merge, because both hide what you did not check: anything across the boundary the change
is *about*, and a medium with its reader or its writer.

Those two are absolutes and they can still both bind at once — a change to a service that also
reads a table it does not own, at three lanes. The order when something has to give:

1. Keep the boundary the change is about. It is the reason the figure exists.
2. Keep a medium that carries a *reaction* — one party writes, another reads. Fold that away and
   the reaction becomes an arrow between things that never speak, which is the lie.
3. Everything else may merge, including a store that is only read on a straight path. Say in the
   prose that it was read; a lane that receives one `SELECT` and never answers anything the
   reader wondered about is the cheapest lane to spend.

##### What to keep tight

- **≤6 states, ≤7 sequence messages, and only the columns or members the change is about.** The
  message cap is editorial, not arithmetic — the renderer re-stacks a sequence's rows after D2 has
  laid them out, so a long one is not tall, it is just a program the reader has to execute. A
  state machine past six states is usually two questions wearing one coat.
- **Label every relationship.** An `er` edge carries a cardinality that names its entities — `1
  doc : n edits`, not a bare `n : 1` that leaves the reader working out which end is which. A
  `state` transition carries its trigger. A `class` edge carries the relationship, dashed for
  implements/uses.
- **Write an arrow inside a label as `→`, never `->`.** Everything around it is typeset, so ASCII
  reads as source code that escaped.
- **If it still does not fit, split it into two figures** that each answer a narrower question,
  and where the split cuts a link, leave a `note` on both sides naming the other figure with the
  direction in it (`→ then the retry path`). Splitting is more often right here than it is for a
  standalone image, because the column is a real limit. But splitting is not free either: when
  both halves would come out thin — three boxes each, nothing to compare — draw the *one* figure
  that answers the question the reader actually asked and let the prose carry the rest. One good
  figure and a paragraph beats two weak figures.

`render.py` reports both kinds of feedback to stderr when it renders: **problems** (measured on
the drawn SVG — `TINY`, `TALL`, a clipped callout) and **advisories** (editorial, read off the
spec — a bare ER ratio, an eighth message, an ASCII arrow). Read them and fix the spec; neither
blocks the page, and a document shipped with a `TINY` figure in it has an unreadable figure in it.

### Step 5 — Render & open

```bash
python3 ~/.claude/skills/explain-diff/scripts/render.py spec.json
```

It may refuse to render over quiz option-length bias (see its own error for specifics). If it errors, adjust lengths in the flagged questions and re-run; don't reach for `--allow-length-bias` unless you're sure the flagged cases are fine.

It prints the path it wrote. Open that path:

```bash
open "$FILE_PATH"
```

(macOS `open` launches the default browser; this is a throwaway artifact, not a repo file — never save inside the project working tree.)
