---
name: handoff-split
description: Spin a side task found mid-session (a bug, a refactoring) off into its own worktree, branch and agent session in a new tmux window, seeded with a handoff.
disable-model-invocation: true
argument-hint: "What is the side task? (optional when the conversation makes it obvious)"
---

# handoff-split

The current session stays on its branch. The side task gets a handoff, a fresh worktree on its
own branch, and a new agent session that starts on it right away, so it becomes its own merge
request.

The script lives at `~/.claude/skills/handoff-split/scripts/spinoff.py`; below it is `spinoff.py`.

## Step 1: Scope the side task

Pick the side task from the argument, or from the conversation when there is no argument. When
the conversation holds more than one candidate, ask which one.

Choose:

- **repo**: the repository the side task belongs to. Usually this one; when it is another
  (a tool, a shared library, your dotfiles), pass `--repo <path>` to both commands below, and
  the worktree, branch and base all come from that repository.
- **name**: a short slug for the worktree, the tmux window and the handoff file, e.g. `fix-rounding`.
- **branch**: `<type>/<slug>`, with `type` one of `feat`, `fix`, `refactor`, `perf`, `docs`,
  `test`, `chore`, matching the work, e.g. `fix/rounding`.
- **base**: the remote default branch, always, unless the side task cannot be done without
  this branch's unmerged commits. Then it is **stacked**: say which commits it needs and why,
  and get the user's yes before going on.

## Step 2: Plan

```bash
python3 ~/.claude/skills/handoff-split/scripts/spinoff.py plan --name <name> --branch <branch> [--stacked] [--repo <path>]
```

It prints `name`, `branch`, `base`, `worktree` and `handoff`, or refuses with the reason (taken
branch, existing worktree or handoff file). On a refusal, pick another name or branch and re-run.

## Step 3: Write the handoff

Write it to the printed `handoff` path. It briefs a fresh agent that has none of this
conversation, and is done when that agent could start on the task without asking anything this
session already knows:

- **Task**: the bug or refactoring, the evidence for it (`file:line`, failing command, observed
  vs expected), and what done looks like.
- **Decided**: what was already settled in this session, with the reason.
- **Open**: what is still unknown or up to the user.
- **Out of scope**: what belongs to the current branch, so the new session leaves it alone.
- **Starting point**: the new session starts from `base`. Describe code as it is there. When
  something only exists on the current branch, say so and name the branch.
- **Suggested skills**: which skills the new agent should call, if any.

Reference existing artifacts (issues, ADRs, plans, commits) by path or URL instead of copying
them. Leave out secrets and personal data.

## Step 4: Start

```bash
python3 ~/.claude/skills/handoff-split/scripts/spinoff.py start --name <name> --branch <branch> [--stacked] [--repo <path>] --model <your model id>
```

It fetches (unless stacked), creates the worktree on the new branch, and opens the same agent (Claude Code or
OpenCode) in a tmux window right after the current one, without switching to it. Outside tmux
it prints the command for the user to run instead.

`--model` takes your own model id, so the new Claude Code session runs on the same model as
this one rather than the configured default. OpenCode ignores it.

Report in three lines: the tmux window (or the command to run), the branch and base, and the
handoff path. Then continue the current task.
