"""The file a topic is about, whole, for the review-ui mod's sidebar: what both skills' `code`
command prints as JSON.

The sidebar shows the current topic's file at the code as it is now — the review worktree at
the MR's tip in review-mr, the user's own working tree in rework-mr — so it never has to cut a
snippet down to fit: the mod shows a window of it and moves it on a key. Two things are worked
out here, since only git knows them:

- **where the topic's line is now.** A thread is anchored on the commit it was posted on; a
  push since moves the code under it, so the line is carried through the diff from that
  commit to the files now.
- **which lines changed since.** The lines that diff adds or rewrites, so a re-review shows
  what the author did in place.

Neither is fatal: a commit the checkout no longer has (a force-push dropped it) leaves the line
as posted and marks nothing.
"""
import difflib
import functools
import hashlib
import os
import re
import subprocess
import tempfile


def _diff_since(repo, sha, path, context=0, commit=None):
    """`git diff -U<context> <sha> -- <path>` against the files on disk (or `commit`), or None
    when git cannot. A file renamed since is diffed against its name now, so the diff holds
    what changed in it rather than every line removed."""
    now = now_at(repo, sha, path, commit)
    if now != path:
        return _pair_diff(repo, sha, path, commit, now, context)
    try:
        r = subprocess.run(["git", "-C", repo, "diff", "--no-color", "-M", f"-U{context}", sha,
                            *([commit] if commit else []), "--", path],
                           capture_output=True, text=True, errors="replace", timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def _pair_diff(repo, old_rev, old_path, new_rev, new_path, context):
    """The unified diff from `old_path` at `old_rev` to `new_path` at `new_rev` (None: the file
    on disk), two names of one file as git's history pairs them (`now_at`). Where one diff
    over the whole range does not pair them — renamed in one commit, rewritten in the next —
    git compares the two versions as named, as `git diff <rev>:<a> <rev>:<b>` does, so the
    diff holds what changed rather than every line of both."""
    diff = _git_out(repo, "diff", "--no-color", "--no-ext-diff", "-M", f"-U{context}", old_rev,
                    *([new_rev] if new_rev else []), "--", old_path, new_path)
    if diff is None or re.search(r"^rename from ", diff, re.M) or not diff.strip():
        return diff
    if new_rev:
        return _git_out(repo, "diff", "--no-color", "--no-ext-diff", f"-U{context}",
                        f"{old_rev}:{old_path}", f"{new_rev}:{new_path}")
    # The file on disk is no object of git's: the old version goes beside it in a file.
    old = _git_out(repo, "show", f"{old_rev}:{old_path}")
    if old is None:
        return None
    with tempfile.TemporaryDirectory(prefix="code-view-") as d:
        was = os.path.join(d, os.path.basename(old_path))
        with open(was, "w", encoding="utf-8") as f:
            f.write(old)
        try:
            r = subprocess.run(["git", "-C", repo, "diff", "--no-index", "--no-color", "--no-ext-diff",
                                f"-U{context}", was, os.path.join(repo, new_path)],
                               capture_output=True, text=True, errors="replace", timeout=20)
        except (OSError, subprocess.SubprocessError):
            return None
    # --no-index answers 1 where the two differ.
    return r.stdout if r.returncode in (0, 1) else None


def now_at(repo, sha, path, commit=None):
    """Where `path` of commit `sha` is in the files on disk (or in `commit`): its name now,
    followed through a rename made since (a fix renaming the file a comment is on, pushed
    before the comment's own topic is reached). `path` itself where the file is still there,
    or git cannot say."""
    if not (repo and sha and path):
        return path
    if commit is None and os.path.isfile(os.path.join(repo, path)):
        return path
    renamed, gone = _renamed_since(repo, sha, commit)
    if path in renamed:
        return renamed[path]
    return _followed(repo, sha, path, commit) if path in gone else path


def removed_since(repo, sha, path, commit=None):
    """Whether `path` of commit `sha` is gone from the files on disk (or `commit`): removed,
    and no rename git sees carries it on. A thread there is outdated, as GitLab calls it."""
    if not (repo and sha and path) or now_at(repo, sha, path, commit) != path:
        return False
    if commit is None:
        return not os.path.isfile(os.path.join(repo, path))
    return path in _renamed_since(repo, sha, commit)[1]


def _pinned(f):
    """`f` cached where its answer is about commits alone; asked again where it is about the
    files on disk (its last argument None), which change under a process that asks twice.
    Every argument is passed, by position, so the last one is always that commit."""
    cached = functools.lru_cache(maxsize=32)(f)

    @functools.wraps(f)
    def call(*args):
        return (cached if args[-1] else f)(*args)
    return call


@_pinned
def _renamed_since(repo, sha, commit=None):
    """Every rename git sees between commit `sha` and the files on disk (or `commit`), old
    name to new, and every file removed since."""
    out = _git_out(repo, "diff", "--no-color", "--no-ext-diff", "-M", "--name-status", sha,
                   *([commit] if commit else []))
    rows = [ln.split("\t") for ln in (out or "").splitlines()]
    return ({f[1]: f[2] for f in rows if f[0].startswith("R") and len(f) == 3},
            {f[1] for f in rows if f[0] == "D" and len(f) == 2})


@_pinned
def _followed(repo, sha, path, commit=None):
    """`path`'s name now, followed one commit at a time where the whole diff since `sha` lost
    it, as `git log --follow` does: each commit since (and, for the files on disk, what is not
    committed yet), through the renames git itself sees in it. A rename in one commit and a
    rewrite in the next are one rename this way, where the diff over both is a file removed
    and another added. A file git sees removed is removed: no new file is guessed to be it.
    `path` where no step carries it to a file that is there now."""
    log = _git_out(repo, "log", "--first-parent", "--diff-merges=first-parent", "--reverse",
                   "-M", "--name-status", "--pretty=tformat:@", f"{sha}..{commit or 'HEAD'}")
    if log is None:
        return path
    steps = [block.splitlines() for block in log.split("@\n")]
    if commit is None:
        steps.append((_git_out(repo, "diff", "--no-color", "--no-ext-diff", "-M", "--name-status",
                               "HEAD") or "").splitlines())
    now = path
    for step in steps:
        rows = [ln.split("\t") for ln in step if "\t" in ln]
        now = next((f[2] for f in rows if f[0].startswith("R") and len(f) == 3 and f[1] == now), now)
    there = (_git_out(repo, "cat-file", "-e", f"{commit}:{now}") is not None if commit
             else os.path.isfile(os.path.join(repo, now)))
    return now if there else path


HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.M)


def carry(diff, line):
    """Where `line` of the old file sits in the new one, and the new file's changed lines.

    A line inside a rewritten stretch lands on that stretch's first new line; one inside a
    deletion lands on the line after it. `-a,0` is an insertion after old line a, `+c,0` a
    deletion after new line c."""
    changed, delta, landed = [], 0, None
    for m in HUNK.finditer(diff or ""):
        old, old_n = int(m[1]), int(m[2]) if m[2] is not None else 1
        new, new_n = int(m[3]), int(m[4]) if m[4] is not None else 1
        changed += range(new, new + new_n)
        if line is None or landed is not None:
            continue
        if old_n == 0:
            if line > old:
                delta += new_n
        elif line < old:
            landed = line + delta
        elif line < old + old_n:
            landed = new if new_n else new + 1
        else:
            delta += new_n - old_n
    if line is not None and landed is None:
        landed = line + delta
    return landed, changed


def _git_out(repo, *args):
    try:
        r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                           errors="replace", timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


# Context wide enough that the diff of a file is one hunk holding every line of it.
WHOLE_LINES = 100000000
WHOLE = f"-U{WHOLE_LINES}"


def change_of(repo, path, base, at=None, was=None):
    """What the MR does to `path` as a whole (`_change_of`)."""
    return _change_of(repo, path, base, was, at)


@_pinned
def _change_of(repo, path, base, was, at):
    """What the MR does to `path` as a whole: `("added", None)`, `("removed", None)`,
    `("renamed", <old path>)`, or `(None, None)` for a file it only edits (or when git
    cannot say). `at`: the commit to read the MR at, None for the files on disk.

    Asked of the whole MR, never of the one path: git finds a rename only where it sees both
    names, so a diff limited to the new name reads a renamed file as an added one, every line
    new. `was`: a name the caller followed the file from (`now_at`), for a rename git does not
    pair; the MR renamed the file from it where it removes `was` and adds `path`."""
    if not (repo and path and base):
        return None, None
    out = _git_out(repo, "diff", "--no-color", "--no-ext-diff", "-M", "--name-status", base,
                   *([at] if at else []))
    rows = [ln.split("\t") for ln in (out or "").splitlines()]
    for f in rows:
        if f[0] == "A" and f[1:] == [path]:
            if was and ["D", was] in rows:
                return "renamed", was
            return "added", None
        if f[0] == "D" and f[1:] == [path]:
            return "removed", None
        if f[0].startswith("R") and len(f) == 3 and f[2] == path:
            return "renamed", f[1]
    return None, None


def rows_of(repo, path, base, at=None, was=None):
    """The file as the MR changes it, every line: `[kind, old, new, text]` rows in the order
    a unified diff reads, `kind` one of " ", "-", "+", `old`/`new` the line's number on that
    side (None where it has none). `base` is the MR's diff base; `at` the commit to read the
    file at, or None for the files on disk. None when git cannot say. A renamed file is
    diffed against its old name, so only what the MR changed in it is marked; `was` as for
    `change_of`."""
    if not (repo and path and base):
        return None
    kind, old_path = change_of(repo, path, base, at, was)
    if kind == "renamed":
        diff = _pair_diff(repo, base, old_path, at, path, WHOLE_LINES)
    else:
        diff = _git_out(repo, "diff", "--no-color", "--no-ext-diff", "-M", WHOLE, base,
                        *([at] if at else []), "--", path)
    if diff is None:
        return None
    lines = diff.splitlines()
    # No change to its lines: none at all, or only of its name or mode. The file as it is.
    if not any(HUNK.match(ln) for ln in lines) and not any(ln.startswith("Binary files ") for ln in lines):
        text = (_git_out(repo, "show", f"{at}:{path}") if at
                else _read(os.path.join(repo, path)))
        if text is None:
            return None
        return [[" ", i, i, t] for i, t in enumerate(text.splitlines(), 1)]
    rows, old, new, inside = [], 0, 0, False
    for ln in lines:
        m = HUNK.match(ln)
        if m:
            # The counters stand on the line before the hunk's first: `-a,0` already names
            # the line before an insertion, every other start the hunk's own first line.
            old = int(m[1]) - (m[2] != "0")
            new = int(m[3]) - (m[4] != "0")
            inside = True
            continue
        if not inside or not ln or ln[0] not in " -+":
            continue
        k = ln[0]
        old += k != "+"
        new += k != "-"
        rows.append([k, old if k != "+" else None, new if k != "-" else None, ln[1:]])
    return rows


def _read(full):
    try:
        with open(full, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def row_of(rows, new):
    """The 1-based row holding line `new` of the file as it is now, or None."""
    return next((i for i, r in enumerate(rows, 1) if r[2] == new), None)


def line_anchor(rows, path, line):
    """The anchor GitLab gives line `line` (new side) of `path` on an MR's Changes page, as
    its "copy link to line" does: `<sha1 of the path>_<old>_<new>`, from the file's `rows`
    (`rows_of`). An added line has no old number; GitLab counts it as the old line that
    comes next. None where the line is not in the rows."""
    old = 0
    for kind, o, n, _text in rows or []:
        if o is not None:
            old = o
        if n == line:
            return f"{hashlib.sha1(path.encode()).hexdigest()}_{o if o is not None else old + 1}_{n}"
    return None


def view(repo, path, line, since=None, start=None, side="new", base=None, commit=None):
    """The JSON the sidebar draws, or None when the file is neither in the checkout nor in
    the MR's diff:

    - `rows`: the file as the MR changes it (`rows_of`), or as it is, unmarked, when the
      MR's base is not known;
    - `at`: the first and last row the comment is on, or None;
    - `changed`: the rows that changed since the comment;
    - `file`, `side`, `line`, `start`, `moved`: where the comment is, in line numbers of
      its side (`start` where a comment on a range of lines begins, `line` its end);
    - `gone`: the file was removed since the comment (no rename carries it on): the rows are
      the file as the comment saw it, the lines it is on marked, as GitLab shows an outdated
      thread. A comment on code is never shown without code.

    `commit`: the commit the file is read at, against `base`, instead of the files on disk (a
    pushed fix, from the commit before it to the one it made).

    A comment on the new side is carried through the changes since it was posted; one on
    a line the MR removed (`side` "old") stays where it was."""
    if since and side != "old" and commit is None and removed_since(repo, since, path):
        was_there = _git_out(repo, "show", f"{since}:{path}")
        if was_there is not None:
            rows = [[" ", i, i, t] for i, t in enumerate(was_there.splitlines(), 1)]
            try:
                n = int(line) if line else None
                first = int(start) if start else n
            except (TypeError, ValueError):
                n = first = None
            if not (n and first and 1 <= first < n):
                first = n
            at = rows_at(rows, "new", first, n)
            return {"file": path, "side": "new", "line": n if at else None, "start": first if at else None,
                    "moved": False, "rows": rows, "at": at, "renamed_from": None, "changed": [],
                    "whole": None, "gone": True}
    # Read where the file is now: a rename since the comment moved it.
    posted, path = path, now_at(repo, since, path, commit) if since and side != "old" else path
    full = os.path.join(repo or "", path or "")
    was = posted if posted != path else None
    rows = rows_of(repo, path, base, commit, was) if os.path.isfile(full) or base else None
    if rows is None:
        if not (repo and path and os.path.isfile(full)):
            return None
        text = _read(full)
        if text is None:
            return None
        rows = [[" ", i, i, t] for i, t in enumerate(text.splitlines(), 1)]
    if not rows:
        return None

    def num(v):
        try:
            return int(v) if v else None
        except (TypeError, ValueError):
            return None

    is_old = side == "old"
    n, first = num(line), num(start)
    changed, moved = [], False
    if since and not is_old:
        diff = _diff_since(repo, since, posted, commit=commit)
        if diff is not None:
            at, changed = carry(diff, n)
            first = carry(diff, first)[0] if first else None
            moved = n is not None and at != n
            n = at
    if not (n and first and 1 <= first < n):
        first = n
    at = rows_at(rows, "old" if is_old else "new", first, n)
    if not at:
        n = first = None
    gone = set(changed)
    # A file the MR adds or removes whole is all one colour, which marks nothing: the sidebar
    # draws it plain and says so beside its name instead.
    kind = change_of(repo, path, base, commit, was)[0] if base else None
    return {"file": path, "side": "old" if is_old else "new", "line": n, "start": first,
            "moved": moved, "rows": rows, "at": at,
            "renamed_from": posted if posted != path else None,
            "changed": [i for i, r in enumerate(rows, 1) if r[2] in gone],
            "whole": kind if kind in ("added", "removed") else None}


def rows_at(rows, side, first, last):
    """`[a, b]`, the first and last row holding lines `first`..`last` of `side` ("old" or
    "new"), or None. On the old side the lines the MR removed are meant, as GitLab anchors
    a comment there only on those; a line it kept counts where none was removed."""
    if not last:
        return None
    first = first or last
    slot = 1 if side == "old" else 2
    hit = [i for i, r in enumerate(rows, 1)
           if r[slot] is not None and first <= r[slot] <= last and (slot == 2 or r[0] == "-")]
    if not hit and slot == 1:
        hit = [i for i, r in enumerate(rows, 1) if r[1] is not None and first <= r[1] <= last]
    return [hit[0], hit[-1]] if hit else None


def new_lines(v):
    """The file as it is now, as lines, from a `view`."""
    return [r[3] for r in v["rows"] if r[2] is not None]


def hunk_header(rows, lo, hi):
    """The `@@ -a,b +c,d @@` line for rows `lo`..`hi` (1-based) of `rows`: a hunk with no
    line on a side starts that side at the line before it, as unified diff counts."""
    def side(k):
        nums = [r[k] for r in rows[lo - 1:hi] if r[k] is not None]
        if nums:
            return nums[0], len(nums)
        before = [r[k] for r in rows[:lo - 1] if r[k] is not None]
        return (before[-1] if before else 0), 0
    (a, b), (c, d) = side(1), side(2)
    return f"@@ -{a},{b} +{c},{d} @@"


def diff_fence(v, lo, hi):
    """Rows `lo`..`hi` of a `view` as a ```diff block, the rows the comment is on a hunk of
    their own: that is what marks them where the block is read as plain markdown, and what
    the review-ui mod draws on a background. The info string carries the file (for the
    highlighter) and which hunk is the comment's (`at=<n>`, 1-based; none without one).
    Each line of it is returned apart, so a caller can mark it."""
    a = v.get("at") or [0, -1]
    cuts = [x for x in (lo, max(lo, a[0]), min(hi, a[1]) + 1, hi + 1) if lo <= x <= hi + 1]
    cuts = sorted(set(cuts))
    out, at = [], None
    for i, (x, y) in enumerate(zip(cuts, cuts[1:]), 1):
        if x == a[0]:
            at = i
        out.append(hunk_header(v["rows"], x, y - 1))
        out += [f"{r[0]}{r[3]}" for r in v["rows"][x - 1:y - 1]]
    info = f"diff path={v['file']}" + (f" at={at}" if at else "")
    return [f"```{info}", *out, "```"]


def since_fence(repo, sha, path, first, last, context=1):
    """What changed in `path` since commit `sha` (the code a comment was posted on) around
    its lines `first`..`last`, as a ```diff block of the hunks that reach them, or None when
    git cannot say or nothing there changed. The card of a topic the author answered in
    code shows it under what they did. A file renamed since is followed: its block holds what
    changed in it, under its name now. A file removed since has no block: every line of it
    removed says nothing a line cannot (`removed_since`)."""
    if removed_since(repo, sha, path):
        return None
    out = _diff_since(repo, sha, path, context)
    if not out:
        return None
    hunks, cur = [], None
    for ln in out.splitlines():
        m = HUNK.match(ln)
        if m:
            old, n = int(m[1]), int(m[2]) if m[2] is not None else 1
            cur = [ln] if old <= last + context and old + max(n, 1) >= first - context else None
            if cur is not None:
                hunks.append(cur)
        elif cur is not None and not ln.startswith(("---", "+++", "\\")):
            cur.append(ln)
    if not hunks:
        return None
    return [f"```diff path={now_at(repo, sha, path)}", *[ln for h in hunks for ln in h], "```"]


def touched(diff, first, last):
    """Whether `diff` (one file's unified diff, with or without context lines) changes old
    lines `first`..`last`, and where those lines sit in the new file: `(hit, first, last)`.

    What the update check asks of a push: a comment is on a stretch of lines, and the push
    either rewrote some of them (the author may have answered in code) or only moved them.
    A line inserted between two of the stretch's lines counts as a change to it; one
    inserted just before or after it does not. A rewritten stretch's new lines are the
    first and last of what took its place, so the next push is checked against the code
    the author left there rather than against the numbers it had before; a stretch removed
    with nothing in its place lands on the line after it, as `carry` has it."""
    old = new = 0
    hit = False
    at_first = at_last = None             # their new lines, where the diff shows them
    last_gone = False                     # `last` removed: it ends where its block ends
    for ln in (diff or "").splitlines():
        m = HUNK.match(ln)
        if m:
            if last_gone:
                at_last, last_gone = new - 1, False
            old, new = int(m[1]), int(m[3])
            # `-a,0` / `+c,0` name the line BEFORE an empty side, as unified diff counts.
            if m[2] == "0":
                old += 1
            if m[4] == "0":
                new += 1
            continue
        if not old or ln.startswith(("---", "+++", "\\")):
            continue
        if ln.startswith("-"):
            if first <= old <= last:
                hit = True
            if old == first:
                at_first = new            # the first line put in its place, or the one after
            if old == last:
                last_gone = True
            old += 1
        elif ln.startswith("+"):
            if first < old <= last:
                hit = True
            new += 1
        else:
            if last_gone:
                at_last, last_gone = new - 1, False
            if old == first:
                at_first = new
            if old == last:
                at_last = new
            old += 1
            new += 1
    if last_gone:
        at_last = new - 1
    # Lines the diff does not show moved by the hunks above them.
    a = at_first if at_first is not None else carry(diff, first)[0]
    b = at_last if at_last is not None else carry(diff, last)[0]
    return hit, a, max(a, b)


# Where a push removed a comment's lines, they may have gone somewhere else rather than
# away: a function moved into another file, or further down its own. They count as moved
# where the block they were removed with turns up among the lines the same push added, at
# least this much of it matching line for line (whitespace aside): three lines that say
# something, with forty letters and digits between them. Fewer is what recurs by chance in
# one push — a `return result`, a log call, a closing pair of braces — and a deletion on
# purpose must not be taken for a move because a line like it was added somewhere else.
# Lines that say nothing (`MOVE_TRIVIAL`) never count towards it, though they still match,
# so a moved block keeps its shape.
MOVE_MIN_LINES = 3
MOVE_MIN_CHARS = 40
# A line that says nothing on its own: blank, punctuation, one bare word (`pass`, `else:`,
# `return;`, `}`), or an import, which any file may add.
MOVE_TRIVIAL = re.compile(r"^\W*\w*\W*$|^(import|from|#include|#import|using|package|require)\b")
# How far apart two matching stretches may lie and still be one block moved: the lines
# between them are what the author edited on the way, so the gap may differ between the two
# sides by this much (a line rewritten as several).
MOVE_GAP = 5
# How far beyond the matching lines a comment's line may lie and still go along with them,
# counted from the nearest one: a commented signature renamed in the move matches nothing
# itself, but the body under it does.
MOVE_EDGE = 3


def _norm(text):
    return " ".join(text.split())


def _diff_rows(diff):
    """`diff`'s body lines, each `[kind, old, new, text, block]`: kind " ", "-" or "+", the
    line's number on each side it is on (else None), and which block of changed lines it is
    in — a run with no unchanged line between, numbered from 1; None for an unchanged one."""
    rows, old, new, block, n = [], 0, 0, None, 0
    for ln in (diff or "").splitlines():
        m = HUNK.match(ln)
        if m:
            old, new, block = int(m[1]), int(m[3]), None
            if m[2] == "0":
                old += 1
            if m[4] == "0":
                new += 1
            continue
        if not old or ln.startswith(("---", "+++", "\\")):
            continue
        kind = ln[:1] if ln[:1] in ("-", "+") else " "
        if kind == " ":
            rows.append([" ", old, new, ln[1:], None])
            old, new, block = old + 1, new + 1, None
            continue
        if block is None:
            n += 1
            block = n
        rows.append([kind, old if kind == "-" else None, new if kind == "+" else None, ln[1:], block])
        if kind == "-":
            old += 1
        else:
            new += 1
    return rows


def moved_to(diff, first, last, others):
    """Where a push moved old lines `first`..`last` of a file whose change is `diff`: a list
    of `{"file", "first", "last"}`, their new lines in each file they went to (more than one
    where the code was split up), or [] where they did not move: kept, rewritten in place,
    or deleted. `others`: `(path, diff, same)` for every file the push changed, `same` for
    the file itself (code moved further down it).

    The lines removed with them — every block of changed lines they are in — are looked for
    among the lines the push added, enough of them to tell (`MOVE_MIN_LINES`); a commented
    line that matches nothing itself goes along with the matching lines around it."""
    rows = _diff_rows(diff)
    blocks = {r[4] for r in rows if r[0] == "-" and first <= r[1] <= last}
    gone = [r for r in rows if r[0] == "-" and r[4] in blocks]
    marks = [k for k, r in enumerate(gone) if first <= r[1] <= last]
    if not marks:
        return []
    src = [_norm(r[3]) for r in gone]
    out = []
    for path, other, same in others:
        dest = [r for r in _diff_rows(other) if r[0] != "-"]
        # Lines the file had already match nothing: the code did not move there. Nor do the
        # lines that took the removed ones' own place, which is a rewrite, not a move.
        usable = [r[0] == "+" and not (same and r[4] in blocks) for r in dest]
        sm = difflib.SequenceMatcher(None, src, [_norm(r[3]) for r in dest], autojunk=False)
        pairs = [(a + k, b + k) for a, b, size in sm.get_matching_blocks() for k in range(size)
                 if usable[b + k]]
        for run in _move_runs(pairs):
            useful = [src[i] for i, _ in run if not MOVE_TRIVIAL.match(src[i])]
            if (len(useful) < MOVE_MIN_LINES
                    or sum(c.isalnum() for line in useful for c in line) < MOVE_MIN_CHARS):
                continue
            got = [j for j in (_carried(run, i) for i in marks) if j is not None and 0 <= j < len(dest)]
            if got:
                out.append({"file": path, "first": dest[min(got)][2], "last": dest[max(got)][2]})
    return out


def all_removed(diff, first, last):
    """Whether `diff` removes every one of old lines `first`..`last`: none of them is left
    where it was."""
    gone = {r[1] for r in _diff_rows(diff) if r[0] == "-"}
    return all(n in gone for n in range(first, last + 1))


def _move_runs(pairs):
    """`pairs` (source line, destination line, both ascending) cut wherever the two sides
    drift apart by more than `MOVE_GAP`: each run is one block that moved together."""
    runs = []
    for i, j in pairs:
        if runs and abs((j - runs[-1][-1][1]) - (i - runs[-1][-1][0])) <= MOVE_GAP:
            runs[-1].append((i, j))
        else:
            runs.append([(i, j)])
    return runs


def _carried(run, i):
    """Where source line `i` landed by the matching lines of `run`: its own match, else by
    its distance from the nearest one, within the run or `MOVE_EDGE` beyond its ends; None
    further out."""
    for a, b in run:
        if a == i:
            return b
    before = [(a, b) for a, b in run if a < i]
    after = [(a, b) for a, b in run if a > i]
    if before and after:
        return min(before[-1][1] + i - before[-1][0], after[0][1] - 1)
    if before and i - before[-1][0] <= MOVE_EDGE:
        return before[-1][1] + i - before[-1][0]
    if after and after[0][0] - i <= MOVE_EDGE:
        return after[0][1] - (after[0][0] - i)
    return None


def commented(repo, thread):
    """The file `thread`'s comment is on, under its name in the files on disk (a rename
    since followed), or None for a comment on no file."""
    return now_at(repo, thread.get("head_sha"), thread.get("file")) if thread.get("file") else None


def place_now(repo, thread):
    """`path:line` of where `thread`'s comment is now, for a card's heading: the file under
    its name now and the line carried through the changes since the comment, as the sidebar
    beside the card shows them. A card naming the old path, or the line as posted, beside a
    sidebar that follows both reads as another place. A comment on a removed line, and one
    git cannot follow, keeps the place it was posted on; one on the whole file is its path alone.
    "" for a comment on no file."""
    path, line = thread.get("file"), thread.get("line")
    if not path:
        return ""
    now = commented(repo, thread) if repo else path
    if repo and line and thread.get("head_sha") and thread.get("side") != "old":
        diff = _diff_since(repo, thread.get("head_sha"), path)
        if diff is not None:
            try:
                line = carry(diff, int(line))[0]
            except (TypeError, ValueError):
                pass
    return f"{now}:{line}" if line else now


def fix_view(repo, thread, files, pick=None, base="HEAD", commit=None, moved=()):
    """A fix's file for the sidebar: `files` is the fix's `[path, added, removed]` each, and
    `moved` the places it moved the commented code to (`{"file", "first", "last"}` in the
    code at `commit`; see `moved_to`). The one shown is `pick` where it is one of them, else
    where the code went where the fix moved it (the comment's file then shows it removed,
    and nothing of what became of it), else the comment's file where the fix touches it,
    else the fix's first. Its change from `base` (to `commit`, or the files on disk), the
    comment's lines carried through it where it is the comment's file, or marked where the
    code went; elsewhere the first changed row stands for where to look. None for a fix
    with no files.

    Adds `fix_files` (all of `files`, the comment's file first where the fix touches it, then
    where its code went: the files the topic is about lead the list it opens on) and
    `commented` (the comment's file now), so the sidebar lists the fix and marks the file
    the comment is on."""
    names = [f for f, _a, _r in files]
    if not names:
        return None
    now = commented(repo, thread)
    went = [q for q in moved if q["file"] in names]
    want = (pick if pick in names else went[0]["file"] if went else now if now in names
            else names[0])
    there = next((q for q in went if q["file"] == want), None)
    its = want == now and not there
    if there:
        # Lines in the code at `commit` already: read where they are, nothing to carry.
        v = view(repo, want, there["last"], commit, start=there["first"], base=base, commit=commit)
    else:
        v = view(repo, thread.get("file") if its else want,
                 thread.get("line_end") or thread.get("line") if its else None,
                 thread.get("head_sha") if its else None,
                 start=thread.get("line_start") if its else None,
                 side=thread.get("side") or "new", base=base, commit=commit)
    if not v:
        return None
    if v["at"] is None and v["rows"]:
        hit = next((i for i, r in enumerate(v["rows"], 1) if r[0] != " "), None)
        v["at"] = [hit, hit] if hit else None
    lead = [now] + [q["file"] for q in went]
    v.update(fix_files=sorted(files, key=lambda f: lead.index(f[0]) if f[0] in lead else len(lead)),
             commented=now)
    return v
