"""A pushed fix kept in git, so the diff viewer can show it after the push: the commit the
topic's change started from and the one it ended at, as two refs per topic,

    refs/<skill>/mr<iid>/<topic>/before
    refs/<skill>/mr<iid>/<topic>/after

rework-mr keeps the person's own fix (the MR's head before the topic's first push, and the
head that push made); review-mr the author's (the code a push changed the topic's lines
from, and the head it made). A ref holds its commit against `git gc`, which a force push
would otherwise leave unreachable, so the fix can be shown as long as the MR is open; once
it is merged or closed they are dropped (`drop`), all of the MR's at once.

The same in both skills modulo the skill's name, which is why it is here.
"""
import os
import re
import subprocess

from lib import hunk


def _git(repo, *args, stdin=None):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                       input=stdin)
    return r.stdout if r.returncode == 0 else None


def prefix(skill, iid):
    return f"refs/{skill}/mr{iid}/"


def has_commit(repo, sha):
    return bool(sha) and _git(repo, "cat-file", "-e", f"{sha}^{{commit}}") is not None


def keep(repo, skill, iid, tid, before, after):
    """Keep `before` and `after` as the topic's fix; False where git does not have both
    (a commit never fetched, or already collected), and nothing is written then."""
    if not (repo and has_commit(repo, before) and has_commit(repo, after)):
        return False
    base = f"{prefix(skill, iid)}{tid}/"
    # One transaction: a fix is never left with one end only.
    script = f"start\nupdate {base}before {before}\nupdate {base}after {after}\ncommit\n"
    return _git(repo, "update-ref", "--stdin", stdin=script) is not None


def kept(repo, skill, iid):
    """{topic: (before, after)} for every fix of the MR kept with both ends, in one call."""
    out = _git(repo, "for-each-ref", "--format=%(refname) %(objectname)", prefix(skill, iid)) if repo else None
    ends = {}
    for line in (out or "").splitlines():
        ref, _, sha = line.partition(" ")
        m = re.fullmatch(re.escape(prefix(skill, iid)) + r"([^/]+)/(before|after)", ref)
        if m:
            ends.setdefault(m.group(1), {})[m.group(2)] = sha
    return {t: (e["before"], e["after"]) for t, e in ends.items() if "before" in e and "after" in e}


def patch(repo, ends):
    """The fix as a git-style patch, renames detected, with the `a/` `b/` prefixes the
    viewer and `hunk.diff_stat` read, whatever the repository's own diff settings."""
    before, after = ends
    return _git(repo, "diff", "--no-color", "--no-ext-diff", "-M",
                "--src-prefix=a/", "--dst-prefix=b/", before, after) or ""


def stat(repo, ends):
    """[(path, added, removed)] of the fix, from the patch itself (`hunk.diff_stat`)."""
    return hunk.diff_stat(patch(repo, ends))


def show(repo, ends, path, label):
    """Write the fix to `path` and put it in this agent's diff viewer window: (session_id,
    where), or None where it could not be shown there (see `hunk.show_patch`)."""
    text = patch(repo, ends)
    if not text.strip():
        return None
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)
    return hunk.show_patch(path, label, repo)


def drop(repo, skill, iid):
    """Remove every fix kept for the MR. Returns how many refs went."""
    refs = [ln.strip() for ln in (_git(repo, "for-each-ref", "--format=%(refname)",
                                       prefix(skill, iid)) or "").splitlines() if ln.strip()]
    if refs:
        _git(repo, "update-ref", "--stdin", stdin="".join(f"delete {r}\n" for r in refs))
    return len(refs)


def from_diff_url(url):
    """(start_sha, diff_id) of a saved GitLab diff link, or None: a push from before the
    refs were kept is found again from it (the version's head is GitLab's to say)."""
    m = re.search(r"[?&]diff_id=(\d+)", url or "")
    s = re.search(r"[?&]start_sha=([0-9a-f]{7,40})", url or "")
    return (s.group(1), int(m.group(1))) if m and s else None
