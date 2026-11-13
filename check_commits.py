#!/usr/bin/env python3
"""Check each unpushed commit on its own, at the commit level: ruff and the tests that commit's
own change reaches, every commit in its own throwaway worktree.

    python3 check_commits.py            # the commits above the branch's upstream
    python3 check_commits.py <base>     # the commits above <base>

Run it after anything that rewrites unpushed history: a fold, a fixup and autosquash, a
rebase. Every commit must pass alone, and the pre-commit hook cannot tell: it checks the
working tree, once, and a fold commits with hooks off. Folds left broken middle commits three
rounds running (a name imported one commit after its first use, 95 type errors in one commit)
before this existed, each found only when somebody checked commit by commit by hand.

Each commit runs the tests its own change reaches (`run_tests.py --changed-since=<commit>^`),
not the whole suite: the whole suite runs once, on the top commit, before a push
(`check_push.py`). A whole suite per commit cost about 45 seconds each and was paid again on
every fold. A commit whose change cannot be mapped still runs everything.

The commits run side by side, each with its share of the test runner's workers, so the
machine runs about as many processes as one full run. A failing commit prints what failed
(ruff's lines, the runner's `failed:` summary) and where its full log is kept; nothing is
kept for a commit that passed. Exit 0 only when every commit passed.

A worktree gets what git does not carry: each mod's engine types, copied from this checkout,
so its type check runs there too instead of skipping.

A run that is stopped leaves nothing behind: a caught signal (TERM, HUP, INT) removes its
worktrees before it exits, and a run killed outright is cleaned up by the next one, which
removes the worktrees of every earlier run whose process is gone. A run still going, another
session's, is told apart by the process id it records, and never touched.
"""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from lib import parallel  # noqa: E402

RUNNER_WORKERS = 8      # what `run_tests.py` runs with by default; shared out among the commits
PREFIX = "check-commits-"   # a run's folder of worktrees, in the temp folder
OWNER = "owner.pid"         # in it: the process id of the run it belongs to


def git(*args, cwd=HERE):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def ruff_version():
    """The ruff the commit hook pins (`RUFF=`), so both lint with the same release."""
    with open(os.path.join(HERE, ".githooks", "pre-commit"), encoding="utf-8") as fh:
        m = re.search(r"^RUFF=(\S+)", fh.read(), re.M)
    return m.group(1) if m else None


def checks(workers):
    """The commands run in each commit's worktree, in order, as (name, argv). `{commit}` in an
    argument is the commit being checked."""
    version = ruff_version()
    ruff = (["uvx", "--quiet", f"ruff@{version}"] if version and shutil.which("uvx")
            else ["ruff"])
    return [("ruff", [*ruff, "check", "--quiet", "."]),
            ("tests", [sys.executable, "run_tests.py", "--changed-since={commit}^", f"-j{workers}"])]


def runner_selects(tree):
    """Whether the test runner in `tree` knows `--changed-since`."""
    try:
        with open(os.path.join(tree, "run_tests.py"), encoding="utf-8") as fh:
            return "--changed-since=" in fh.read()
    except OSError:
        return True


def copy_untracked(dest, repo=HERE):
    """What a commit's checks need that git does not carry: the mods' engine types, and the test
    runner's temp folder setting (`run_tests.TMPDIR_FILE`)."""
    local = os.path.join(repo, ".run_tests_tmpdir")
    if os.path.isfile(local) and not os.path.exists(os.path.join(dest, ".run_tests_tmpdir")):
        shutil.copy2(local, os.path.join(dest, ".run_tests_tmpdir"))
    mods = os.path.join(repo, "mods")
    for mod in sorted(os.listdir(mods)) if os.path.isdir(mods) else []:
        types = os.path.join(mods, mod, ".claude-plugin", "types")
        target = os.path.join(dest, "mods", mod, ".claude-plugin", "types")
        if os.path.isdir(types) and os.path.isdir(os.path.dirname(target)) \
                and not os.path.exists(target):
            shutil.copytree(types, target, symlinks=True)


def summary(name, output):
    """The lines worth printing for a failed check: ruff's findings, the runner's own list."""
    lines = output.splitlines()
    if name == "tests" and "failed:" in lines:
        return lines[lines.index("failed:"):]
    return lines[-20:]


def check(commit, root, steps, repo=HERE):
    """Run `steps` on `commit` of `repo` in a worktree under `root`. (ok, report, log path)."""
    tree = os.path.join(root, commit[:12])
    add = subprocess.run(["git", "worktree", "add", "-q", "--detach", tree, commit], cwd=repo,
                         capture_output=True, text=True)
    if add.returncode != 0:
        return False, [f"no worktree: {add.stderr.strip()}"], None
    try:
        copy_untracked(tree, repo)
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        log, report = [], []
        for name, argv in steps:
            argv = [a.replace("{commit}", commit) for a in argv]
            if not runner_selects(tree):
                # A commit from before `--changed-since` has a runner that would read it as a
                # path filter, match nothing and fail: it gets the whole suite instead.
                argv = ["--force" if a.startswith("--changed-since=") else a for a in argv]
            r = subprocess.run(argv, cwd=tree, capture_output=True, text=True, env=env)
            log += [f"$ {' '.join(argv)}", r.stdout + r.stderr]
            if r.returncode != 0:
                report += [f"{name} failed:"] + [f"  {ln}" for ln in summary(name, r.stdout + r.stderr)]
        if not report:
            return True, [], None
        path = os.path.join(root, f"{commit[:12]}.log")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(log))
        return False, report, path
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", tree], cwd=repo,
                       capture_output=True)


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True                       # someone else's process, alive
    return True


def _owner_gone(root):
    """Whether the run that made `root` has ended: its process is gone. A folder from before
    runs recorded their process counts as ended once it is an hour old."""
    try:
        with open(os.path.join(root, OWNER), encoding="utf-8") as fh:
            return not _alive(int(fh.read().strip()))
    except (OSError, ValueError):
        try:
            return time.time() - os.path.getmtime(root) > 3600
        except OSError:
            return True


def remove_worktrees(repo, roots):
    """Remove the worktrees `repo` has registered inside any of `roots`, then forget the
    missing ones. A root keeps its failure logs; one with nothing else left goes too."""
    listing = git("worktree", "list", "--porcelain", cwd=repo) or ""
    trees = [ln[len("worktree "):] for ln in listing.splitlines() if ln.startswith("worktree ")]
    for tree in trees:
        if os.path.dirname(os.path.realpath(tree)) in {os.path.realpath(r) for r in roots}:
            subprocess.run(["git", "worktree", "remove", "--force", tree], cwd=repo,
                           capture_output=True)
    subprocess.run(["git", "worktree", "prune"], cwd=repo, capture_output=True)
    for root in roots:
        left = [f for f in (os.listdir(root) if os.path.isdir(root) else []) if f != OWNER]
        if not left:
            shutil.rmtree(root, ignore_errors=True)


def sweep(repo=HERE, tmp=None):
    """The worktrees of earlier runs that ended without cleaning up (killed outright)."""
    tmp = tmp or tempfile.gettempdir()
    roots = [os.path.join(tmp, d) for d in os.listdir(tmp) if d.startswith(PREFIX)]
    remove_worktrees(repo, [r for r in roots if _owner_gone(r)])


def on_stop(repo, root):
    """A caught signal: this run's worktrees removed, then out. The checks still running in
    them fail at once, their folder gone; nothing waits for them."""
    def stop(signum, _frame):
        remove_worktrees(repo, [root])
        os._exit(128 + signum)
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(sig, stop)


PASSED = os.path.join(HERE, ".check_commits_passed.json")


def passed_today(path=None):
    """The commits recorded as passing today."""
    try:
        with open(path or PASSED, encoding="utf-8") as fh:
            rec = json.load(fh)
    except (OSError, ValueError):
        return set()
    return set(rec.get("commits", [])) if rec.get("day") == time.strftime("%Y-%m-%d") else set()


def record_passes(commits, path=None):
    keep = passed_today(path) | set(commits)
    with open(path or PASSED, "w", encoding="utf-8") as fh:
        json.dump({"day": time.strftime("%Y-%m-%d"), "commits": sorted(keep)}, fh, indent=0)


def main(argv):
    base = argv[0] if argv else (git("rev-parse", "--abbrev-ref", "@{u}") or "origin/main")
    commits = (git("rev-list", "--reverse", f"{base}..HEAD") or "").split()
    if not commits:
        print(f"no commits above {base}")
        return 0
    # A commit that passed today is not checked again: its hash covers its whole content and
    # history, so the same hash is the same code. Bounded to the day like the runner's record
    # of a whole run, since a tool updated overnight can change a result.
    known = passed_today()
    todo = [c for c in commits if c not in known]
    steps = checks(max(1, RUNNER_WORKERS // max(1, len(todo))))
    sweep()
    root = tempfile.mkdtemp(prefix=PREFIX)
    with open(os.path.join(root, OWNER), "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()))
    on_stop(HERE, root)
    ran = dict(zip(todo, parallel.each(lambda c: check(c, root, steps), todo,
                                       workers=max(1, len(todo)))))
    results = [ran.get(c, (True, ["passed today already: not checked again"], None))
               for c in commits]
    # The owner's private words, in each commit's added lines and its message: the hooks
    # check the same, but a fold commits with them off.
    for i, commit in enumerate(commits):
        r = subprocess.run([sys.executable, os.path.join(HERE, "private_terms.py"), "check",
                            commit], cwd=HERE, capture_output=True, text=True)
        if r.returncode != 0:
            ok, report, log = results[i]
            results[i] = (False, report + ["private words:"] + [f"  {ln}" for ln in
                                                                 r.stdout.splitlines()], log)
    failed = 0
    for commit, (ok, report, log) in zip(commits, results):
        print(f"{'ok  ' if ok else 'FAIL'}  {git('log', '-1', '--format=%h %s', commit)}")
        for line in report:
            print(f"      {line}")
        if log:
            print(f"      full log: {log}")
        failed += not ok
    record_passes([c for c, (ok, _, _) in zip(commits, results) if ok and c in ran])
    if not failed:
        shutil.rmtree(root, ignore_errors=True)
    print(f"{len(commits) - failed} of {len(commits)} commit(s) above {base} pass on their own")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
