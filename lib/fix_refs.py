"""A pushed fix kept in git, so the diff viewer can show it after the push: the commit the
topic's change started from and the one it ended at, as two refs per topic,

    refs/<skill>/mr<iid>/<topic>/before
    refs/<skill>/mr<iid>/<topic>/after

or, where a topic keeps each push of its own (rework-mr), one pair per push under
`<topic>/push<N>/`: the key a fix is kept under is whatever stands between the MR's prefix
and the two ends, `t1` or `t1/push2`.

rework-mr keeps the person's own fix (the MR's head before the topic's first push, and the
head that push made); review-mr the author's (the code a push changed the topic's lines
from, and the head it made). A ref holds its commit against `git gc`, which a force push
would otherwise leave unreachable, so the fix can be shown as long as the MR is open; once
it is merged or closed they are dropped (`drop`), all of the MR's at once. A push that came
with a rebase keeps, as its start, the head before it rebased onto the new base (`rebased`):
a topic's diff holds what the push changed, never what the rebase brought in from the target
branch.

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


def is_ancestor(repo, a, b):
    return subprocess.run(["git", "-C", repo, "merge-base", "--is-ancestor", a, b],
                          capture_output=True).returncode == 0


def rebased(repo, before, onto):
    """`before` as it reads rebased onto `onto`: a commit on `onto` whose files are `onto`'s
    with the branch's own change up to `before` merged in. Diffed against the head a push
    after a rebase made, it shows what the push changed and nothing the target branch
    brought in, a file both of them changed included. `before` itself where no rebase lies
    between (`onto` is already in it), where the merge conflicts (then nobody can say whose
    a line is) or where git cannot merge without a checkout (older than 2.40). The commit is
    the same for the same two every time, so a later keep finds it unchanged."""
    if not onto or not has_commit(repo, onto) or is_ancestor(repo, onto, before):
        return before
    base = (_git(repo, "merge-base", before, onto) or "").strip()
    if not base:
        return before
    tree = (_git(repo, "merge-tree", "--write-tree", f"--merge-base={base}", onto, before) or "").strip()
    if not re.fullmatch(r"[0-9a-f]{40,64}", tree):
        return before
    when = (_git(repo, "log", "-1", "--format=%ad", "--date=raw", onto) or "").strip()
    env = {**os.environ, "GIT_AUTHOR_NAME": "fix_refs", "GIT_AUTHOR_EMAIL": "fix_refs@localhost",
           "GIT_COMMITTER_NAME": "fix_refs", "GIT_COMMITTER_EMAIL": "fix_refs@localhost",
           "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
    r = subprocess.run(["git", "-C", repo, "commit-tree", tree, "-p", onto, "-m",
                        f"{before} rebased onto {onto}"], capture_output=True, text=True, env=env)
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else before


def keep(repo, skill, iid, tid, before, after, onto=None):
    """Keep `before` and `after` as the fix under key `tid` (a topic, or a topic's push); False where git does not have both
    (a commit never fetched, or already collected), and nothing is written then. `onto`:
    what `after` starts from on the target branch (GitLab's base of the version it made),
    so that a push which came with a rebase keeps `before` rebased onto it (`rebased`)."""
    if not (repo and has_commit(repo, before) and has_commit(repo, after)):
        return False
    before = rebased(repo, before, onto)
    base = f"{prefix(skill, iid)}{tid}/"
    # One transaction: a fix is never left with one end only.
    script = f"start\nupdate {base}before {before}\nupdate {base}after {after}\ncommit\n"
    return _git(repo, "update-ref", "--stdin", stdin=script) is not None


def kept(repo, skill, iid):
    """{key: (before, after)} for every fix of the MR kept with both ends, in one call."""
    out = _git(repo, "for-each-ref", "--format=%(refname) %(objectname)", prefix(skill, iid)) if repo else None
    ends = {}
    for line in (out or "").splitlines():
        ref, _, sha = line.partition(" ")
        m = re.fullmatch(re.escape(prefix(skill, iid)) + r"(.+)/(before|after)", ref)
        if m:
            ends.setdefault(m.group(1), {})[m.group(2)] = sha
    return {t: (e["before"], e["after"]) for t, e in ends.items() if "before" in e and "after" in e}


def patch(repo, ends, paths=None):
    """The fix as a git-style patch, renames detected, with the `a/` `b/` prefixes the
    viewer and `hunk.diff_stat` read, whatever the repository's own diff settings. `paths`:
    only these files, where the fix came with a rebase and the rest is the target branch's."""
    before, after = ends
    return _git(repo, "diff", "--no-color", "--no-ext-diff", "-M",
                "--src-prefix=a/", "--dst-prefix=b/", before, after,
                *(["--", *paths] if paths else [])) or ""


def stat(repo, ends, paths=None):
    """[(path, added, removed)] of the fix, from the patch itself (`hunk.diff_stat`)."""
    return hunk.diff_stat(patch(repo, ends, paths))


def view_args(ends, paths=None):
    """What the diff viewer loads for the fix, as `hunk session reload` takes it: the commit
    range in the repository the ends are kept in. It holds exactly `patch`: the viewer
    detects renames as `-M` does, and takes `paths` as the same pathspec (measured with hunk
    0.25 on a rename with and without a change, a deletion, a new, a binary and a mode-only
    file; a renamed file named by its new path alone is an added file in both)."""
    before, after = ends
    return ["diff", f"{before}..{after}", *(["--", *paths] if paths else [])]


def show(repo, ends, name, paths=None):
    """Put the fix in this agent's diff viewer window, as the commit range it is in `repo`
    (`view_args`), named `name` ((title, meanwhile), see `hunk.rename`): (session_id,
    where), or None where it could not be shown there (see `hunk.show_view`). A pushed fix
    is always a plain range, a rebased push included: its start is kept already rebased."""
    expect = stat(repo, ends, paths)
    if not expect:
        return None
    return hunk.show_view(view_args(ends, paths), expect, name, repo)


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
