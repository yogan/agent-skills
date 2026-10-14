"""Running git from a skill script, and the one question about a repository that more than
one skill asks: which branch is the default one.
"""
import subprocess

from lib.cli import die

REMOTE = "origin"


def git(*args, cwd=None, check=True):
    """git's stdout, stripped; None when it failed and `check` is off. With `check` on, a
    failure ends the script with git's own message."""
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and proc.returncode != 0:
        die(f"git {' '.join(args)}: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout.strip() if proc.returncode == 0 else None


def default_base(cwd=None):
    """`origin/<default branch>`. origin/HEAD is only set by a clone, so a repo whose
    remote was added later falls back to the usual names."""
    ref = git("symbolic-ref", "--short", f"refs/remotes/{REMOTE}/HEAD", cwd=cwd, check=False)
    if ref:
        return ref
    for name in ("main", "master"):
        if git("rev-parse", "--verify", "--quiet", f"{REMOTE}/{name}", cwd=cwd, check=False):
            return f"{REMOTE}/{name}"
    die(f"cannot tell {REMOTE}'s default branch; run `git remote set-head {REMOTE} --auto`")
