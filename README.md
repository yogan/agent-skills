# agent-skills

A small collection of [Claude Code](https://docs.claude.com/en/docs/claude-code) skills.

## Skills

### explain-diff

Generates a rich, interactive, self-contained HTML explanation of a code change — background, intuition, a code walkthrough, and a quiz — then opens it in the browser. Accepts the current branch, a named local/remote branch, a GitLab MR, or just the last commit. Renders diagrams via Graphviz and syntax-highlighted code client-side, with light/dark mode following the OS preference.

### explain-branch

Same output style as explain-diff, but for a multi-commit branch or MR: structures the page as one chapter per substantial commit, telling the "how the feature was built, step by step" story, with an intro, a summary, and per-chapter mini-quizzes. Falls back to the flat explain-diff format when there's only one commit, or too few substantial ones to justify chapters.

### rework-mr

Works through the reviewer feedback on a GitLab MR **you authored**: retrieves the open discussion threads and reconciles them against a persistent per-MR plan (so the multi-day re-review cycle survives across sessions), then walks them one topic at a time. Trivial points get a recommendation straight away; complex ones get a grilling-style discussion with alternatives and a recommendation. Per topic it then drives the fix test-first, fixes up into the right branch commit(s) instead of adding new ones, force-pushes, derives a stable diff-between-versions URL (never a commit link, which force-push would rot), and drafts a concise thread reply in the thread's own language. glab-only, no MCP dependency.

## Setup

Symlink whichever skill(s) you want into your Claude Code skills directory:

```bash
git clone git@github.com:yogan/agent-skills.git ~/src/agent-skills
ln -s ~/src/agent-skills/explain-diff ~/.claude/skills/explain-diff
ln -s ~/src/agent-skills/explain-branch ~/.claude/skills/explain-branch
ln -s ~/src/agent-skills/rework-mr ~/.claude/skills/rework-mr
```

Requirements:

- `python3` (for `render.py` and the `rework-mr` scripts)
- [Graphviz](https://graphviz.org/) (`dot` on `PATH`) for diagrams
- [`glab`](https://gitlab.com/gitlab-org/cli) — authenticated, for `explain-*` `mr:123` targets and all of `rework-mr`
- macOS for `rework-mr`'s clipboard copy (`pbcopy`)

Restart Claude Code (or start a new session) after adding the symlinks so it picks up the new skills.
