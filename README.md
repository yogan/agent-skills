# agent-skills

A small collection of [Claude Code](https://docs.claude.com/en/docs/claude-code) skills.

## Skills

### explain-diff

Generates a rich, interactive, self-contained HTML explanation of a code change — background, intuition, a code walkthrough, and a quiz — then opens it in the browser. Accepts the current branch, a named local/remote branch, a GitLab MR, or just the last commit. Renders diagrams via D2 (see `visualize` below — same renderer, same gates) and syntax-highlighted code client-side, with light/dark mode following the OS preference.

### explain-branch

Same output style as explain-diff, but for a multi-commit branch or MR: structures the page as one chapter per substantial commit, telling the "how the feature was built, step by step" story, with an intro, a summary, and per-chapter mini-quizzes. Falls back to the flat explain-diff format when there's only one commit, or too few substantial ones to justify chapters.

### visualize

Draws one diagram that answers a question about the codebase — a database layout, a request or
interaction flow, how a set of classes relate, a state machine, an architecture, or an animated
before/after — then opens it in the browser. It explores the code to get the facts, derives a
spec, renders it with [D2](https://d2lang.com), positions any annotation callouts by measuring
every candidate position in a headless browser, and holds the result to objective gates
(fits a viewport, no glyph below ~11px or above an `h2`, WCAG AA in **both** light and dark,
nothing clipped, every colour themeable). Diagrams follow the page's light/dark toggle with no
redraw. The renderer lives in `lib/diagram/`, so the `explain-*` skills produce identical
figures without this skill being installed.

#### Why D2, and not Graphviz or Mermaid

Decided by prototype rather than preference: one scenario drawn six ways by three engines and
judged on measurable gates. **Mermaid** was rejected on weight and speed — 405 MB plus a 565 MB
Chromium, and 3x D2's render time — not on capability. **Graphviz** is excellent at plain graphs,
needs no post-processing and themes for free, but it cannot draw a sequence diagram at all: `dot`
reorders lifeline columns to minimise edge crossings, so participants come out in the wrong order
with sloped arrows. That is structural, not cosmetic. It also has no notion of an annotation
callout, no native table or class shape, and no animation.

**D2** costs a 35 MB Go binary and needs real post-processing (it bakes in colours, emits no
intrinsic size, and couples three visual roles of a table to two properties), which is why the
gates exist: they turn "does this look right" into a test, so undocumented behaviour is safe to
depend on as long as the version is pinned.

Graphviz was kept alongside D2 for exactly one milestone and then removed. Two engines means two
visual languages, two theming maps and no single source of visual truth — a document mixing them
looks mixed.

### review-branch

Performs a focused, critique-only code review of every commit on the current branch since it diverged from the remote default branch (handles detached HEAD). Output is a flat, prioritized list of concrete flaws with actionable fix hints — severity-tagged, `file:line`-anchored, no praise or summary. Local-only: no GitLab, no MCP. Used standalone, and as the finding seed for `review-mr`.

### rework-mr

Works through the reviewer feedback on a GitLab MR **you authored**: retrieves the open discussion threads and reconciles them against a persistent per-MR plan (so the multi-day re-review cycle survives across sessions), then walks them one topic at a time. Trivial points get a recommendation straight away; complex ones get a grilling-style discussion with alternatives and a recommendation. Per topic it then drives the fix test-first, fixes up into the right branch commit(s) instead of adding new ones, force-pushes, derives a stable diff-between-versions URL (never a commit link, which force-push would rot), and drafts a concise thread reply in the thread's own language. glab-only, no MCP dependency.

### review-mr

Reviews a GitLab MR **someone else authored**, end to end. Optionally generates an explainer first (via `explain-branch`, as a background subagent), seeds findings with `review-branch`, then curates them with you one topic at a time and drafts concise review comments (in the language configured per repo — German by default) — which **you** post in the GitLab UI (the skill is read-only against GitLab, so the tone stays yours). It then runs a persistent, multi-day re-review loop: on each check it reconciles the live threads (author replies, resolutions) and branch pushes (by head-SHA delta, so force-pushes are handled), summarizes what the author addressed, offers the per-topic diff (inline or a stable compare URL), and gates every close behind *your* ack — an author resolving a thread never counts as done on its own. All git work happens in a dedicated review worktree, so your current checkout is untouched. glab-only.

## Setup

Every skill lives in `skills/<name>/`, so you can link them all with one loop:

```bash
git clone git@github.com:yogan/agent-skills.git ~/src/agent-skills
for s in ~/src/agent-skills/skills/*/; do
  ln -sfn "${s%/}" ~/.claude/skills/"$(basename "$s")"
done
```

Or pick individual ones:

```bash
ln -sfn ~/src/agent-skills/skills/explain-diff ~/.claude/skills/explain-diff
ln -sfn ~/src/agent-skills/skills/review-mr   ~/.claude/skills/review-mr
ln -sfn ~/src/agent-skills/skills/visualize   ~/.claude/skills/visualize
```

`review-mr` builds on `explain-branch` and `review-branch`, so link those too if you use it.

Verify nothing dangles — a broken skill link fails silently, the skill just stops being offered:

```bash
for d in ~/.claude/skills/*/; do
  [ -f "$d/SKILL.md" ] || echo "BROKEN: $d"
done
```

### Required for `rework-mr` and `review-mr`: a `Stop` hook

Both skills print blocks — an overview table, a quoted topic with its code, a drafted
comment — and instruct the model to paste them verbatim, because **Claude Code collapses
tool output: the chat message is the user's only window**. The model drops them anyway. It
runs the command, then answers from its own summary, so the user gets "topic t2 needs you"
with no table, no `file:line`, no code.

That is not fixable by documentation — it was tried three times in `review-mr` (a rule at
the top of `SKILL.md`, a single `resume` command so there was no sequence to skip, then an
explicit rule 0 naming the failure) and the model still paraphrased. So both skills share a
`Stop` hook that checks the visible message for the output of any gated command that
actually ran, and blocks the turn until it is really pasted.

**This needs a manual edit to `~/.claude/settings.json`** — skills cannot register their own
hooks. Link the engine, then register it with one gate spec per installed skill:

```bash
mkdir -p ~/.claude/hooks
ln -sfn ~/src/agent-skills/hooks/paste-gate.py ~/.claude/hooks/paste-gate.py
```

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          { "type": "command",
            "command": "python3 ~/.claude/hooks/paste-gate.py ~/.claude/skills/review-mr/scripts/paste-gates.json ~/.claude/skills/rework-mr/scripts/paste-gates.json" }
        ]
      }
    ]
  }
}
```

Use absolute paths if `~` is not expanded in your setup — and don't quote a `~` path, since a
quoted tilde never expands. A spec path that does not exist is skipped, so both skills stay
independently installable with the same hook line. The hook **fails open** — any error, or a
turn where no gated command ran, allows the stop — so a bug in it can never wedge a session,
and it never fires outside those skills. Details and the gate spec format:
[`hooks/README.md`](hooks/README.md).

### Recommended: three read permissions

Claude Code guards `~/.claude/**` separately from the normal permission rules — a blanket
`Read`/`Edit` allow does **not** cover it — so without these you get a prompt every time an
agent reads a skill file or a per-MR state file, and the dialog's "don't ask again" only
remembers it for the repo you happened to be in. Add them to `~/.claude/settings.json`:

```json
{
  "permissions": {
    "allow": [
      "Read(~/.claude/skills/**)",
      "Read(~/.claude/rework-mr/**)",
      "Read(~/.claude/review-mr/**)"
    ]
  }
}
```

The first covers the skills' own files — `SKILL.md` sends the agent to `REFERENCE.md`, and
the scripts get grepped and executed. The other two cover the per-MR state, which the flow
reads (the post step takes the discussion id from `topics.json`).

Two things about the syntax: the `~/` prefix is required, because in *user* settings a bare
`/path` resolves to `~/.claude/path` rather than the filesystem root. And `Read` is enough —
both skills keep state in those directories, but every write goes through their own scripts
rather than a shell redirect. Don't reach for `Write(…)`: file permissions are only checked
against `Read(path)` and `Edit(path)`, so a `Write` path rule is accepted, never consulted,
and warns at startup.

Requirements:

- `python3` (for `render.py`, the `rework-mr` / `review-mr` scripts, and the `Stop` hook)
- [`glab`](https://gitlab.com/gitlab-org/cli) — authenticated, for `explain-*` `mr:123` targets and all of `rework-mr` / `review-mr`
- macOS for the `rework-mr` / `review-mr` clipboard copy (`pbcopy`)

For every diagram — `visualize` and the `explain-*` skills alike:

- [D2](https://d2lang.com) — `brew install d2`. A single 35 MB Go binary. **Pin the version**:
  the renderer depends on a few undocumented D2 behaviours, which is safe only because the gates
  turn them into tests. `lib/diagram/render.py` records the version it was measured against and a
  mismatch fails a test rather than silently drifting.
- `node`, plus `puppeteer-core` — `npm i -g puppeteer-core`. 29 MB, and deliberately *not* the
  full `puppeteer` package, which downloads its own ~550 MB Chromium; this drives the Chrome,
  Chromium or Edge you already have. Override discovery with `PUPPETEER_EXECUTABLE_PATH` /
  `PUPPETEER_CORE`, or `npm i -g puppeteer` if you have no system browser.

  A browser is in the render loop by decision, not convenience: it is the only way to position an
  annotation callout by measurement, and the only way to see one being clipped — a callout's text
  is HTML inside the SVG, and a CSS drop-shadow's spread is invisible to every static checker.
  Without it, `visualize` still renders and still runs every gate that does not need one (size,
  contrast, and theming for an embedded SVG), but it reports the clipping gate as **unable to
  run** rather than passing. The `explain-*` skills report the same way, once per document.

Restart Claude Code (or start a new session) after adding the symlinks so it picks up the new skills.
