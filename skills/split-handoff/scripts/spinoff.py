#!/usr/bin/env python3
"""Spin a side task off into its own worktree, branch and agent session.

Mid-session a bug or a refactoring turns up that does not belong in the current branch.
The agent writes a handoff for it; this script does the mechanics around that file:

    plan   resolve where everything goes and refuse what would collide, before the agent
           writes anything (so the handoff can name its own branch and path)
    start  fetch, create the worktree, and open the new session in a tmux window beside
           the current one, seeded with a prompt that points at the handoff

The side task may belong to another repository than the session's (`--repo`); everything
below then applies to that one. The new branch starts from the freshly fetched remote
default branch, because spun-off work is meant to become its own merge request.
`--stacked` starts it from the current HEAD instead, for work that needs the current
branch's unmerged commits.

A worktree is only made where the repository already keeps its worktrees in
<main checkout>.wt. Without one there, whether the side task gets a worktree at all is
the user's call, so `plan` refuses until `--new-worktree-dir` records their yes.

`--dir` replaces `--branch` for a side task that gets no branch of its own (an operation
on the machine, work across repos, moving the current checkout, or a repository that is
used where it is checked out): no fetch, no branch, no worktree; the session just starts
in that directory. When the directory is a git checkout, the new session works in it
directly, so the checkout must be clean: what is uncommitted there belongs to someone else.
`--commit-pending` hands those changes over instead, for a side task the user explicitly
asked to commit them.

Paths, all derived rather than configured, except the one personal choice:

    worktree  <main checkout>.wt/<name>, beside the main checkout even when this runs
              inside a linked worktree, so spin-offs of spin-offs do not nest
    handoff   <dir>/<name>-handoff.md, where <dir> is `git config split-handoff.dir`
              (per clone, shared by all its worktrees, never pushed) or the OS temp dir;
              with `--dir` always the OS temp dir, since no clone is involved

Run: `python3 spinoff.py plan --name fix-rounding --branch fix/rounding`
     `python3 spinoff.py plan --name migrate-workspace --dir ~/src`
"""
import argparse
import os
import re
import shlex
import subprocess
import sys
import tempfile

_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from lib.cli import die  # noqa: E402
from lib.git import REMOTE, default_base, git  # noqa: E402

CONFIG_KEY = "split-handoff.dir"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def main_checkout(cwd=None):
    """The main worktree's path: the first entry of `git worktree list`, whichever
    worktree we are called from."""
    out = git("worktree", "list", "--porcelain", cwd=cwd)
    for line in out.splitlines():
        if line.startswith("worktree "):
            return line[len("worktree "):]
    die("not inside a git worktree")


def keeps_worktrees(main):
    """Whether `main` already has a linked worktree in <main>.wt. The directory alone does
    not count: removing a worktree leaves it behind, empty, after the user undid it."""
    out = git("worktree", "list", "--porcelain", cwd=main)
    prefix = os.path.realpath(f"{main}.wt") + os.sep
    return any(os.path.realpath(line[len("worktree "):]).startswith(prefix)
               for line in out.splitlines() if line.startswith("worktree "))


def handoff_dir(cwd=None):
    configured = git("config", "--get", CONFIG_KEY, cwd=cwd, check=False)
    return os.path.expanduser(configured) if configured else tempfile.gettempdir()


def agent_command(prompt, env=None, model=None):
    """The same agent that is running this skill, started with `prompt` as its first
    message. OpenCode sets OPENCODE in its tool environment; anything else is Claude Code.
    `model` keeps the new session on the current one's model: without it Claude Code
    starts on the configured default, which an organisation can pin to another model."""
    env = os.environ if env is None else env
    if env.get("OPENCODE"):
        return ["opencode", "--prompt", prompt]
    return ["claude"] + (["--model", model] if model else []) + [prompt]


def check_name(name):
    if not NAME_RE.match(name):
        die(f"name {name!r}: use lowercase letters, digits and dashes")


def handoff_path(directory, name, handoff_written):
    handoff = os.path.join(directory, f"{name}-handoff.md")
    if handoff_written and not os.path.isfile(handoff):
        die(f"write the handoff to {handoff} first")
    if not handoff_written and os.path.exists(handoff):
        die(f"{handoff} already exists; pick another name")
    return handoff


def plan(name, branch, stacked=False, cwd=None, handoff_written=False,
         new_worktree_dir=False):
    check_name(name)
    if git("check-ref-format", "--branch", branch, cwd=cwd, check=False) is None:
        die(f"{branch!r} is not a valid branch name")
    if git("rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", cwd=cwd, check=False):
        die(f"branch {branch} already exists")
    main = main_checkout(cwd)
    if not new_worktree_dir and not keeps_worktrees(main):
        die(f"{main} keeps no worktrees in {main}.wt; ask the user whether the side task "
            f"works in the checkout itself (--dir {main}) or in a new worktree, which "
            f"starts {main}.wt (--new-worktree-dir)")
    worktree = f"{main}.wt/{name}"
    if os.path.exists(worktree):
        die(f"{worktree} already exists")
    handoff = handoff_path(handoff_dir(cwd), name, handoff_written)
    if stacked:
        base = git("rev-parse", "--short", "HEAD", cwd=cwd)
    elif git("remote", "get-url", REMOTE, cwd=cwd, check=False) is None:
        die(f"this repository has no remote {REMOTE!r} to branch from; pass --stacked to "
            "branch from HEAD, or --dir if the side task is not a branch of it")
    else:
        base = default_base(cwd)
    return {"name": name, "branch": branch, "base": base, "worktree": worktree,
            "handoff": handoff}


def plan_dir(name, start_dir, handoff_written=False, commit_pending=False):
    """A side task with no branch: it starts in `start_dir`, and its handoff goes to the
    temp dir, which is outside the clones and folders such a task tends to move."""
    check_name(name)
    start_dir = os.path.realpath(os.path.expanduser(start_dir))
    if not os.path.isdir(start_dir):
        die(f"{start_dir} is not a directory")
    p = {"name": name, "dir": start_dir}
    if git("rev-parse", "--is-inside-work-tree", cwd=start_dir, check=False) == "true":
        dirty = git("status", "--short", "--no-renames", "--porcelain=v1", cwd=start_dir)
        dirty = [line.split(maxsplit=1)[-1] for line in dirty.splitlines()]
        if dirty and not commit_pending:
            listed = "".join(f"\n  {path}" for path in dirty)
            die(f"{start_dir} has uncommitted changes; commit them before handing off, so "
                "the new session starts clean, or pass --commit-pending if the user asked "
                f"the side task to commit them:{listed}")
        p["branch"] = (git("branch", "--show-current", cwd=start_dir)
                       or git("rev-parse", "--short", "HEAD", cwd=start_dir))
        if dirty:
            p["pending"] = " ".join(dirty)
    elif commit_pending:
        die(f"--commit-pending: {start_dir} is not a git checkout")
    p["handoff"] = handoff_path(tempfile.gettempdir(), name, handoff_written)
    return p


def prompt_for(p):
    if "dir" not in p:
        where = f"in its own worktree on branch {p['branch']}, started from {p['base']}"
    elif "branch" in p:
        where = (f"in the checkout {p['dir']} itself, on branch {p['branch']}, "
                 "which another session may be working in too")
        if "pending" in p:
            where += ("; its uncommitted changes were handed to you to commit, "
                      "as the handoff describes")
    else:
        where = f"in {p['dir']}, with no worktree or branch of its own"
    return (f"Read the handoff at {p['handoff']} and continue the work it describes. "
            f"This session runs {where}.")


def launch(p, env=None, run=subprocess.run, model=None):
    """Open the agent in a tmux window right after the current one, without switching to
    it. Returns the line to report; outside tmux, the command for the user to run."""
    env = os.environ if env is None else env
    cmd = agent_command(prompt_for(p), env, model)
    where = p.get("worktree") or p["dir"]
    if not env.get("TMUX"):
        return f"not in tmux; start it yourself:\n  cd {shlex.quote(where)} && {shlex.join(cmd)}"
    place = []
    pane = env.get("TMUX_PANE")
    if pane:
        here = run(["tmux", "display-message", "-p", "-t", pane, "#{window_id}"],
                   capture_output=True, text=True).stdout.strip()
        if here:
            place = ["-a", "-t", here]
    argv = (["tmux", "new-window", "-d", "-P", "-F", "#{window_index}"] + place
            + ["-n", p["name"], "-c", where, shlex.join(cmd)])
    proc = run(argv, capture_output=True, text=True)
    if proc.returncode != 0:
        return (f"tmux refused ({proc.stderr.strip()}); start it yourself:\n"
                f"  cd {shlex.quote(where)} && {shlex.join(cmd)}")
    return f"started in tmux window {proc.stdout.strip()} ({p['name']})"


def start(name, branch, stacked=False, cwd=None, env=None, run=subprocess.run, model=None,
          new_worktree_dir=False):
    # Stacked work starts from local HEAD, so nothing needs fetching for it.
    if not stacked:
        git("fetch", "--quiet", REMOTE, cwd=cwd)
    p = plan(name, branch, stacked, cwd, handoff_written=True,
             new_worktree_dir=new_worktree_dir)
    git("worktree", "add", "--quiet", "--no-track", "-b", branch, p["worktree"], p["base"],
        cwd=cwd)
    return p, launch(p, env, run, model)


def _print(p):
    for key, value in p.items():
        print(f"{key}={value}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("action", choices=["plan", "start"])
    ap.add_argument("--name", required=True, help="slug for the worktree, window and file")
    kind = ap.add_mutually_exclusive_group(required=True)
    kind.add_argument("--branch", help="new branch, e.g. fix/rounding")
    kind.add_argument("--dir", help="start directory of a side task that is no branch")
    ap.add_argument("--stacked", action="store_true", help="start from HEAD, not the default branch")
    ap.add_argument("--repo", help="repository the side task belongs to (default: this one)")
    ap.add_argument("--new-worktree-dir", action="store_true",
                    help="start <main checkout>.wt when the repository keeps no worktrees yet")
    ap.add_argument("--commit-pending", action="store_true",
                    help="with --dir: the side task commits the checkout's uncommitted changes")
    ap.add_argument("--model", help="model for the new Claude Code session, e.g. this one's")
    a = ap.parse_args(argv)
    if a.commit_pending and not a.dir:
        ap.error("--commit-pending needs --dir: a new worktree has nothing pending")
    if a.dir:
        if a.stacked or a.repo or a.new_worktree_dir:
            ap.error("--dir takes no --stacked, --repo or --new-worktree-dir: there is no branch")
        p = plan_dir(a.name, a.dir, a.action == "start", a.commit_pending)
        _print(p)
        if a.action == "start":
            print(launch(p, model=a.model))
        return
    cwd = os.path.expanduser(a.repo) if a.repo else None
    if cwd and not os.path.isdir(cwd):
        die(f"{a.repo} is not a directory")
    if a.action == "plan":
        _print(plan(a.name, a.branch, a.stacked, cwd, new_worktree_dir=a.new_worktree_dir))
    else:
        p, outcome = start(a.name, a.branch, a.stacked, cwd, model=a.model,
                           new_worktree_dir=a.new_worktree_dir)
        _print(p)
        print(outcome)


if __name__ == "__main__":
    main()
