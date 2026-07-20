---
name: explain-branch
description: Generates a rich, interactive HTML explanation of a multi-commit branch or MR, structured as a chapter per substantial commit (telling the "how the feature was built, step by step" story) with an intro, a summary, and per-chapter mini-quizzes. Falls back to the flat explain-diff format when the range has only one commit, or too few substantial ones to justify chapters. Use when the user wants a branch or MR explained as a build-up story, or invokes /explain-branch.
---

# Explain Branch

Builds on [explain-diff](../explain-diff/SKILL.md): same target resolution, same rendered
page (CSS/JS/quiz mechanics), same writing voice. The difference is structural — a branch
with several commits usually tells a story of how a feature was built step by step, and the
article should read that way: one chapter per meaningful step, not one undifferentiated diff.

Output is still critique-free: a teaching document, not a review.

## Workflow

### Step 1 — Resolve the target

Same spec syntax as explain-diff (blank/"this branch", `branch:foo/bar`, `mr:123`, `commit:abc`).
Run the **same script**, unmodified:

```bash
bash ~/.claude/skills/explain-diff/scripts/resolve-target.sh "<spec>"
```

Source `BASE`, `HEAD_REF`, `LABEL`, `IS_SINGLE_COMMIT`. If `IS_SINGLE_COMMIT` is `true` (the
user gave a `commit:` spec), there is no multi-commit story to tell — stop here and just run
the **explain-diff** skill instead of this one.

### Step 2 — List the commits and classify each one

```bash
git log --reverse --format='%H %s' "$BASE".."$HEAD_REF"
```

This is the chronological build order (oldest first) — the order the chapters will follow.
For each commit, look at its subject and its actual diff (`git show <hash>`) and classify it:

- **Substantial** — introduces or changes behavior, adds a new file/service, wires things
  together, fixes a real bug, meaningfully reshapes a schema/contract. Deserves its own chapter.
- **Trivial** — pure rename/move with no logic change, formatting/lint-only, a typo fix, a
  docs-only edit, a generated-file regen with no accompanying hand-written change. Does not get
  its own chapter or quiz question. This repo uses Conventional Commits (see the project's
  `/commit` skill) — a `chore`/`docs`/`style` type is a hint worth checking, not a verdict; a
  `chore` can still be substantial (e.g. deleting a real feature), and a `refactor` can still be
  trivial (e.g. a rename). Judge from the diff, not the prefix.

If a trivial commit is directly relevant to a neighboring substantial chapter (e.g. a rename
that sets up the next commit's change), fold one sentence about it into that chapter's prose
instead of giving it a heading.

**Fallback rule:** if there is only one substantial commit (0 or 1), there's no real
step-by-step story — stop and run the **explain-diff** skill instead, treating `$BASE..$HEAD_REF`
as one flat diff. Chaptering only pays off with 2+ substantial steps.

### Step 3 — Explore surrounding code

Same as explain-diff Step 3: read touched files in full, skim callers/callees, enough to
explain the system each chapter's change lives in — not just the lines that changed.

### Step 4 — Write the content spec

Use `render.py` — the same one, unmodified path:

```bash
python3 ~/.claude/skills/explain-diff/scripts/render.py spec.json
```

It now additionally supports a `"quiz"` array **on a section itself** (not just top-level):
when present, it renders as a "Check your understanding" mini-quiz right under that section,
instead of a trailing global quiz block. See its `--help` for the full section/quiz JSON shape.

Structure the spec's `"sections"` as:

1. **Intro** (`id: "intro"`) — the same kind of Background+Intuition content explain-diff
   would write for the *whole* feature: the problem being solved end to end, why it's worth
   knowing, and a one-line roadmap of the chapters to come (e.g. "This branch gets there in
   three steps: first ..., then ..., finally ..."). This is what keeps the "high-level view"
   intact even though the detail is now chaptered.
2. **One chapter per substantial commit**, in build order, `id: "chapter-N"`, heading titled
   by what the commit *does* (not its raw subject line) — e.g. "Chapter 2: Making `x-extract-method`
   mandatory", not "Chapter 2: 9f8e7d6". Directly under the `<h2>`, add one small muted line
   citing the real commit for traceability:
   `<p style="color:var(--muted); margin-top:-.5rem; font-size:.85em;">commit <code>9f8e7d6</code> — refactor(ABC-123): make x-extract-method explicit, skip fields without one</p>`
   Each chapter's `html` covers that commit's own intuition + a code walkthrough of *its* diff
   only (use `git diff <hash>^..<hash>` to get exactly that commit's change) — do not re-explain
   ground already covered by an earlier chapter.
   Attach that chapter's own `"quiz"` array (1–3 questions) **only if there's something
   commit-specific worth testing** — skip the quiz on a chapter that's mostly scene-setting.
3. **Summary** (`id: "summary"`) — how the chapters combine into the finished feature; a
   final diagram if useful; loose ends the commits themselves flagged (TODOs, follow-up
   tickets).

Leave the spec's top-level `"quiz"` empty — all questions live on their chapter.

Content rules (diagrams, code-block language classes, `.callout`/`.diagram` divs, Kleppmann-ish
voice) are identical to explain-diff's — see its `SKILL.md` for the full list; not repeated here.

Set `"slug"` to `$LABEL`, same convention as explain-diff.

### Step 5 — Render & open

```bash
python3 ~/.claude/skills/explain-diff/scripts/render.py spec.json
open "$FILE_PATH"
```

Same length-bias guard as explain-diff, now checked across *all* questions (top-level plus
every chapter's) as one pool — don't reach for `--allow-length-bias` unless you're sure the
flagged questions are fine.
