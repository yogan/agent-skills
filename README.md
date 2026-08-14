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
spec, renders it with [D2](https://d2lang.com), places any annotation callouts by measuring them
in a headless browser, and holds the result to objective gates: fits a viewport, nothing
unreadably small or clipped, WCAG AA in **both** light and dark, every colour themeable. Diagrams
follow the page's light/dark toggle with no redraw. The renderer lives in `lib/diagram/`, so the
`explain-*` skills produce identical figures without this skill being installed.

### review-branch

Performs a focused, critique-only code review of every commit on the current branch since it diverged from the remote default branch (handles detached HEAD). Output is a flat, prioritized list of concrete flaws with actionable fix hints — severity-tagged, `file:line`-anchored, no praise or summary. Local-only: no GitLab, no MCP. Used standalone, and as the finding seed for `review-mr`.

### rework-mr

Works through the reviewer feedback on a GitLab MR **you authored**: retrieves the open discussion threads, keeps a persistent per-MR plan so a multi-day cycle survives across sessions, and walks the threads one topic at a time — a recommendation straight away for trivial points, a grilling-style discussion for complex ones. Per topic it then drives the fix test-first, fixes up into the right existing commit instead of adding new ones, force-pushes, and drafts a concise thread reply in the thread's own language. glab-only, no MCP dependency.

### review-mr

Reviews a GitLab MR **someone else authored**, end to end: optionally generates an explainer first (via `explain-branch`), seeds findings with `review-branch`, then curates them with you one topic at a time and drafts concise review comments (in the language configured per repo — German by default) — which **you** post in the GitLab UI, so the tone stays yours. It then runs a persistent, multi-day re-review loop that reconciles author replies, resolutions and new pushes on each check, summarizes what the author addressed, offers the per-topic diff, and gates every close behind *your* ack. All git work happens in a dedicated worktree, so your current checkout is untouched. glab-only, read-only against GitLab.

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

Restart Claude Code (or start a new session) afterwards so it picks up the new skills.

### Required for `rework-mr` and `review-mr`: a `Stop` hook

Both skills print blocks meant for you — an overview table, a quoted topic with its code, a
drafted comment — because **Claude Code collapses tool output: the chat message is your only
window**. Told to paste them verbatim, the model paraphrases them away anyway, and no amount of
documentation fixes it. So both skills share a `Stop` hook that checks the visible message for
the output of any gated command that ran, and blocks the turn until it is really pasted. It
**fails open**, so a bug in it can never wedge a session, and it never fires outside those skills.

**Installing it needs a manual edit to `~/.claude/settings.json`** — skills cannot register their
own hooks. One symlink and one `Stop` entry, both copy-pasteable:
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

The first covers the skills' own files, the other two the per-MR state both skills keep. The
`~/` prefix is required: in *user* settings a bare `/path` resolves to `~/.claude/path`, not to
the filesystem root. `Read` is enough — every write goes through the skills' own scripts, and a
`Write(…)` path rule is accepted but never consulted, so it only warns at startup.

## Requirements

- `python3` (for `render.py` / `visualize.py`, the `rework-mr` / `review-mr` scripts, and the `Stop` hook)
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

  The browser is what places callouts by measurement and what catches a clipped one. Without it
  the diagrams still render and every other gate still runs; the clipping gate then reports
  **unable to run** rather than passing.
