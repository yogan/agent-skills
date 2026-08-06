# agent-skills

A small collection of [Claude Code](https://docs.claude.com/en/docs/claude-code) skills.

## Skills

### explain-diff

Generates a rich, interactive, self-contained HTML explanation of a code change — background, intuition, a code walkthrough, and a quiz — then opens it in the browser. Accepts the current branch, a named local/remote branch, a GitLab MR, or just the last commit. Renders diagrams via Graphviz and syntax-highlighted code client-side, with light/dark mode following the OS preference.

### explain-branch

Same output style as explain-diff, but for a multi-commit branch or MR: structures the page as one chapter per substantial commit, telling the "how the feature was built, step by step" story, with an intro, a summary, and per-chapter mini-quizzes. Falls back to the flat explain-diff format when there's only one commit, or too few substantial ones to justify chapters.

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
- [Graphviz](https://graphviz.org/) (`dot` on `PATH`) for diagrams
- [`glab`](https://gitlab.com/gitlab-org/cli) — authenticated, for `explain-*` `mr:123` targets and all of `rework-mr` / `review-mr`
- macOS for the `rework-mr` / `review-mr` clipboard copy (`pbcopy`)

Restart Claude Code (or start a new session) after adding the symlinks so it picks up the new skills.
