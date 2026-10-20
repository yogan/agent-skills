---
name: split-handoff
description: Spin a side task found mid-session (a bug, a refactoring, a chore on the machine) off into its own agent session in a new tmux window, seeded with a handoff, on its own worktree and branch when the repo already keeps worktrees or the user opts in.
disable-model-invocation: true
argument-hint: "What is the side task? (optional when the conversation makes it obvious)"
---

# split-handoff

The current session stays on its branch. The side task gets a handoff and a new agent session
that starts on it right away. Repo work gets a fresh worktree on its own branch, so it
becomes its own merge request, but only where the repository already keeps its worktrees
beside the main checkout, in `<main checkout>.wt/`, or where the user opts in.

The script lives at `~/.claude/skills/split-handoff/scripts/spinoff.py`; below it is `spinoff.py`.

## Step 1: Scope the side task

Pick the side task from the argument, or from the conversation when there is no argument. When
the conversation holds more than one candidate, ask which one.

Decide its **kind** by what the side task changes, not by where this session runs:

- **branch**: its result is commits for one git repository (this one, or another via `--repo`),
  and it leaves that repository's checkouts in place, unless it is a live checkout (below).
- **dir**: everything else: no repository, changes spanning several, operations on files or
  the machine, or anything that moves or deletes a checkout. It gets no worktree and no branch.
  So does a **live checkout**: a repository used where it is checked out, its files linked
  into `~` or `~/.claude` (this skills repository, `~/dotfiles`, `~/env`). Its work happens on
  the checked-out branch, so it takes effect at once.

Choose a **name**: a short slug for the tmux window and the handoff file (and the worktree),
e.g. `fix-rounding`.

For **dir**, choose the **dir** the new session starts in: the live checkout itself, or else a
folder outside everything the task moves or deletes, e.g. the parent of a folder it moves. In
Steps 2 and 4 pass `--dir <dir>` in place of `--branch`, `--stacked`, `--repo` and
`--new-worktree-dir`.

For **branch**, choose:

- **repo**: the repository the side task belongs to. Usually this one; when it is another
  (a tool, a shared library, your dotfiles), pass `--repo <path>` to both commands below, and
  the worktree, branch and base all come from that repository.
- **branch**: `<type>/<slug>`, with `type` one of `feat`, `fix`, `refactor`, `perf`, `docs`,
  `test`, `chore`, matching the work, e.g. `fix/rounding`.
- **base**: the remote default branch, always, unless the side task cannot be done without
  this branch's unmerged commits. Then it is **stacked**: say which commits it needs and why,
  and get the user's yes before going on.

A worktree is never the default. When the repository has no worktree in `<main checkout>.wt/`
yet (an empty folder left by a removed one does not count), Step 2 refuses, and the user
chooses between:

- **in the checkout itself**: the side task becomes **dir** with `--dir <main checkout>` and
  works on its checked-out branch, once that checkout is clean.
- **in a new worktree**: pass `--new-worktree-dir` to Steps 2 and 4, which starts
  `<main checkout>.wt/`.

Ask, with neither option recommended, unless the user already said which one in this session.

## Step 2: Plan

```bash
python3 ~/.claude/skills/split-handoff/scripts/spinoff.py plan --name <name> --branch <branch> [--stacked] [--repo <path>] [--new-worktree-dir]
```

It prints `name`, `branch`, `base`, `worktree` and `handoff` (with `--dir`: `name`, `dir` and
`handoff`, plus `branch` in a checkout and `pending` with `--commit-pending`), or refuses with the reason (taken branch, existing
worktree or handoff file, no worktrees kept yet, no remote, uncommitted changes in a checkout given as `--dir`). On a refusal, follow its hint or pick another name or branch, and re-run. On uncommitted
changes, ask the user to commit them first. Only when the user explicitly asked that the side
task commit them, re-run with `--commit-pending` instead, in Step 4 too, and name the files in
the handoff's **Task**.

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
  something only exists on the current branch, say so and name the branch. For **dir**: the
  start directory and the state of what the task operates on.
- **Suggested skills**: which skills the new agent should call, if any.

Reference existing artifacts (issues, ADRs, plans, commits) by path or URL instead of copying
them. Leave out secrets and personal data.

## Step 4: Start

```bash
python3 ~/.claude/skills/split-handoff/scripts/spinoff.py start --name <name> --branch <branch> [--stacked] [--repo <path>] [--new-worktree-dir] --model <your model id>
```

It fetches (unless stacked) and creates the worktree on the new branch (with `--dir`, neither),
then opens the same agent (Claude Code or OpenCode) in a tmux window right after the current
one, without switching to it. Outside tmux it prints the command for the user to run instead.

`--model` takes your own model id, so the new Claude Code session runs on the same model as
this one rather than the configured default. OpenCode ignores it.

Report in three lines: the tmux window (or the command to run), the branch and base (or the
start directory), and the handoff path. Then continue the current task.
