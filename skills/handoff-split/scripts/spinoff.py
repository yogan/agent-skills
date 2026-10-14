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

Paths, all derived rather than configured, except the one personal choice:

    worktree  <main checkout>.wt/<name>, beside the main checkout even when this runs
              inside a linked worktree, so spin-offs of spin-offs do not nest
    handoff   <dir>/<name>-handoff.md, where <dir> is `git config handoff-split.dir`
              (per clone, shared by all its worktrees, never pushed) or the OS temp dir

Run: `python3 spinoff.py plan --name fix-rounding --branch fix/rounding`
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

CONFIG_KEY = "handoff-split.dir"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def main_checkout(cwd=None):
    """The main worktree's path: the first entry of `git worktree list`, whichever
    worktree we are called from."""
    out = git("worktree", "list", "--porcelain", cwd=cwd)
    for line in out.splitlines():
        if line.startswith("worktree "):
            return line[len("worktree "):]
    die("not inside a git worktree")


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


def plan(name, branch, stacked=False, cwd=None, handoff_written=False):
    if not NAME_RE.match(name):
        die(f"name {name!r}: use lowercase letters, digits and dashes")
    if git("check-ref-format", "--branch", branch, cwd=cwd, check=False) is None:
        die(f"{branch!r} is not a valid branch name")
    if git("rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", cwd=cwd, check=False):
        die(f"branch {branch} already exists")
    worktree = f"{main_checkout(cwd)}.wt/{name}"
    if os.path.exists(worktree):
        die(f"{worktree} already exists")
    handoff = os.path.join(handoff_dir(cwd), f"{name}-handoff.md")
    if handoff_written and not os.path.isfile(handoff):
        die(f"write the handoff to {handoff} first")
    if not handoff_written and os.path.exists(handoff):
        die(f"{handoff} already exists; pick another name")
    base = git("rev-parse", "--short", "HEAD", cwd=cwd) if stacked else default_base(cwd)
    return {"name": name, "branch": branch, "base": base, "worktree": worktree,
            "handoff": handoff}


def prompt_for(p):
    return (f"Read the handoff at {p['handoff']} and continue the work it describes. "
            f"This session runs in its own worktree on branch {p['branch']}, "
            f"started from {p['base']}.")


def launch(p, env=None, run=subprocess.run, model=None):
    """Open the agent in a tmux window right after the current one, without switching to
    it. Returns the line to report; outside tmux, the command for the user to run."""
    env = os.environ if env is None else env
    cmd = agent_command(prompt_for(p), env, model)
    if not env.get("TMUX"):
        return f"not in tmux; start it yourself:\n  cd {shlex.quote(p['worktree'])} && {shlex.join(cmd)}"
    place = []
    pane = env.get("TMUX_PANE")
    if pane:
        here = run(["tmux", "display-message", "-p", "-t", pane, "#{window_id}"],
                   capture_output=True, text=True).stdout.strip()
        if here:
            place = ["-a", "-t", here]
    argv = (["tmux", "new-window", "-d", "-P", "-F", "#{window_index}"] + place
            + ["-n", p["name"], "-c", p["worktree"], shlex.join(cmd)])
    proc = run(argv, capture_output=True, text=True)
    if proc.returncode != 0:
        return (f"tmux refused ({proc.stderr.strip()}); start it yourself:\n"
                f"  cd {shlex.quote(p['worktree'])} && {shlex.join(cmd)}")
    return f"started in tmux window {proc.stdout.strip()} ({p['name']})"


def start(name, branch, stacked=False, cwd=None, env=None, run=subprocess.run, model=None):
    # Stacked work starts from local HEAD, so nothing needs fetching for it.
    if not stacked:
        git("fetch", "--quiet", REMOTE, cwd=cwd)
    p = plan(name, branch, stacked, cwd, handoff_written=True)
    git("worktree", "add", "--quiet", "--no-track", "-b", branch, p["worktree"], p["base"],
        cwd=cwd)
    return p, launch(p, env, run, model)


def _print(p):
    for key in ("name", "branch", "base", "worktree", "handoff"):
        print(f"{key}={p[key]}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("action", choices=["plan", "start"])
    ap.add_argument("--name", required=True, help="slug for the worktree, window and file")
    ap.add_argument("--branch", required=True, help="new branch, e.g. fix/rounding")
    ap.add_argument("--stacked", action="store_true", help="start from HEAD, not the default branch")
    ap.add_argument("--repo", help="repository the side task belongs to (default: this one)")
    ap.add_argument("--model", help="model for the new Claude Code session, e.g. this one's")
    a = ap.parse_args(argv)
    cwd = os.path.expanduser(a.repo) if a.repo else None
    if cwd and not os.path.isdir(cwd):
        die(f"{a.repo} is not a directory")
    if a.action == "plan":
        _print(plan(a.name, a.branch, a.stacked, cwd))
    else:
        p, outcome = start(a.name, a.branch, a.stacked, cwd, model=a.model)
        _print(p)
        print(outcome)


if __name__ == "__main__":
    main()
