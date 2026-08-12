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
- Set the spec's `"subtitle"` to a short metadata line (source ref + commit) — same backtick-for-`<code>` convention as quiz text, e.g. `` `fix/drop-legacy-auth-adapter` · commit `a1b2c3d4` ``. When `MR_URL` is set, link the MR instead of writing it as plain text or backticked, and use its title as the link label: `` [MR !$MR_NUM: $MR_TITLE]($MR_URL) · `branch` · commit `hash` ``.
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
   worse than one good view. Delete the weaker one.

Then pick the kind by the question being answered — this mapping is the whole game, and the
common mistake is drawing boxes and arrows for a question about *order* or *state*:

| The reader's question | kind |
|---|---|
| What talks to what, and where does this live? | `architecture` |
| What happens, in what order, across components? | `sequence` |
| What does the data look like now? | `er` |
| How do these types relate? | `class` |
| What states can this be in, and how does it move? | `state` |
| How did we get from the old design to the new one? | `steps` (animated) |

Two rules that make a set of figures read as one system rather than several unrelated pictures:

- **Reuse a small visual vocabulary.** A `role` (`client` · `svc` · `store` · `cache` · `ext` ·
  `neutral`) must mean the same thing in every figure of the document.
- **Mark what this change touched with `note`**, in the reader's words — "new service", "gains a
  revision column" — not with ad-hoc styling. There is no legend, so a colour or a thick border
  says "something here is special" without ever saying what. Two to four words.

Content limits (≤6 states, ≤7 sequence messages, only the columns the change is about) are a
*rendering* constraint, not an editorial one: D2 cannot compact a diagram after the fact. If the
subject genuinely needs more, **split it into two diagrams that each answer a narrower question**
rather than shrinking one past legibility. The gates will catch it either way, and they report to
stderr when you render.

Full field reference, with a worked example of every kind:
`~/.claude/skills/visualize/REFERENCE.md` (or `skills/visualize/REFERENCE.md` in the repo). You do
not need the `visualize` skill installed — the renderer is shared code.

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
