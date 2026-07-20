# agent-skills

A small collection of [Claude Code](https://docs.claude.com/en/docs/claude-code) skills.

## Skills

### explain-diff

Generates a rich, interactive, self-contained HTML explanation of a code change — background, intuition, a code walkthrough, and a quiz — then opens it in the browser. Accepts the current branch, a named local/remote branch, a GitLab MR, or just the last commit. Renders diagrams via Graphviz and syntax-highlighted code client-side, with light/dark mode following the OS preference.

### explain-branch

Same output style as explain-diff, but for a multi-commit branch or MR: structures the page as one chapter per substantial commit, telling the "how the feature was built, step by step" story, with an intro, a summary, and per-chapter mini-quizzes. Falls back to the flat explain-diff format when there's only one commit, or too few substantial ones to justify chapters.

## Setup

Symlink whichever skill(s) you want into your Claude Code skills directory:

```bash
git clone git@github.com:yogan/agent-skills.git ~/src/agent-skills
ln -s ~/src/agent-skills/explain-diff ~/.claude/skills/explain-diff
ln -s ~/src/agent-skills/explain-branch ~/.claude/skills/explain-branch
```

Requirements:

- `python3` (for `render.py`)
- [Graphviz](https://graphviz.org/) (`dot` on `PATH`) for diagrams
- [`glab`](https://gitlab.com/gitlab-org/cli) if you want to resolve `mr:123` targets

Restart Claude Code (or start a new session) after adding the symlinks so it picks up the new skills.
