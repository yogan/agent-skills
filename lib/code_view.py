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
import functools
import os
import re
import subprocess


def _diff_since(repo, sha, path, context=0, commit=None):
    """`git diff -U<context> <sha> -- <path>` against the files on disk (or `commit`), or None
    when git cannot. A file renamed since is diffed against its name now, so the diff holds
    what changed in it rather than every line removed."""
    now = now_at(repo, sha, path, commit)
    paths = [path] if now == path else [path, now]
    try:
        r = subprocess.run(["git", "-C", repo, "diff", "--no-color", "-M", f"-U{context}", sha,
                            *([commit] if commit else []), "--", *paths],
                           capture_output=True, text=True, errors="replace", timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def now_at(repo, sha, path, commit=None):
    """Where `path` of commit `sha` is in the files on disk (or in `commit`): its name now,
    followed through a rename made since (a fix renaming the file a comment is on, pushed
    before the comment's own topic is reached). `path` itself where the file is still there,
    or git cannot say."""
    if not (repo and sha and path):
        return path
    if commit is None and os.path.isfile(os.path.join(repo, path)):
        return path
    return _renamed_since(repo, sha, commit).get(path, path)


@functools.lru_cache(maxsize=16)
def _renamed_since(repo, sha, commit=None):
    """Every rename git sees between commit `sha` and the files on disk (or `commit`), old
    name to new."""
    out = _git_out(repo, "diff", "--no-color", "--no-ext-diff", "-M", "--name-status", sha,
                   *([commit] if commit else []))
    pairs = (ln.split("\t") for ln in (out or "").splitlines())
    return {f[1]: f[2] for f in pairs if f[0].startswith("R") and len(f) == 3}


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
WHOLE = "-U100000000"


@functools.lru_cache(maxsize=32)
def change_of(repo, path, base, at=None):
    """What the MR does to `path` as a whole: `("added", None)`, `("removed", None)`,
    `("renamed", <old path>)`, or `(None, None)` for a file it only edits (or when git
    cannot say).

    Asked of the whole MR, never of the one path: git finds a rename only where it sees both
    names, so a diff limited to the new name reads a renamed file as an added one, every line
    new."""
    if not (repo and path and base):
        return None, None
    out = _git_out(repo, "diff", "--no-color", "--no-ext-diff", "-M", "--name-status", base,
                   *([at] if at else []))
    for ln in (out or "").splitlines():
        f = ln.split("\t")
        if f[0] == "A" and f[1:] == [path]:
            return "added", None
        if f[0] == "D" and f[1:] == [path]:
            return "removed", None
        if f[0].startswith("R") and len(f) == 3 and f[2] == path:
            return "renamed", f[1]
    return None, None


def rows_of(repo, path, base, at=None):
    """The file as the MR changes it, every line: `[kind, old, new, text]` rows in the order
    a unified diff reads, `kind` one of " ", "-", "+", `old`/`new` the line's number on that
    side (None where it has none). `base` is the MR's diff base; `at` the commit to read the
    file at, or None for the files on disk. None when git cannot say. A renamed file is
    diffed against its old name, so only what the MR changed in it is marked."""
    if not (repo and path and base):
        return None
    kind, old_path = change_of(repo, path, base, at)
    paths = [old_path, path] if kind == "renamed" else [path]
    diff = _git_out(repo, "diff", "--no-color", "--no-ext-diff", "-M", WHOLE, base,
                    *([at] if at else []), "--", *paths)
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


def view(repo, path, line, since=None, start=None, side="new", base=None, commit=None):
    """The JSON the sidebar draws, or None when the file is neither in the checkout nor in
    the MR's diff:

    - `rows`: the file as the MR changes it (`rows_of`), or as it is, unmarked, when the
      MR's base is not known;
    - `at`: the first and last row the comment is on, or None;
    - `changed`: the rows that changed since the comment;
    - `file`, `side`, `line`, `start`, `moved`: where the comment is, in line numbers of
      its side (`start` where a comment on a range of lines begins, `line` its end).

    `commit`: the commit the file is read at, against `base`, instead of the files on disk (a
    pushed fix, from the commit before it to the one it made).

    A comment on the new side is carried through the changes since it was posted; one on
    a line the MR removed (`side` "old") stays where it was."""
    # Read where the file is now: a rename since the comment moved it.
    posted, path = path, now_at(repo, since, path, commit) if since and side != "old" else path
    full = os.path.join(repo or "", path or "")
    rows = rows_of(repo, path, base, commit) if os.path.isfile(full) or base else None
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
    kind = change_of(repo, path, base, commit)[0] if base else None
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
    changed in it, under its name now."""
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
    inserted just before or after it does not."""
    old = new = 0
    hit, shift_first, shift_last = False, None, None
    for ln in (diff or "").splitlines():
        m = HUNK.match(ln)
        if m:
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
            old += 1
        elif ln.startswith("+"):
            if first < old <= last:
                hit = True
            new += 1
        else:
            if old == first:
                shift_first = new - old
            if old == last:
                shift_last = new - old
            old += 1
            new += 1
    if hit:
        return True, first, last
    # Lines the diff shows as context, or does not show, moved by the hunks above them.
    def moved(line, known):
        if known is not None:
            return line + known
        return carry(diff, line)[0]
    return False, moved(first, shift_first), moved(last, shift_last)


def commented(repo, thread):
    """The file `thread`'s comment is on, under its name in the files on disk (a rename
    since followed), or None for a comment on no file."""
    return now_at(repo, thread.get("head_sha"), thread.get("file")) if thread.get("file") else None


def fix_view(repo, thread, files, pick=None, base="HEAD", commit=None):
    """A fix's file for the sidebar: `files` is the fix's `[path, added, removed]` each; the
    one shown is `pick` where it is one of them, else the comment's file where the fix
    touches it, else the fix's first. Its change from `base` (to `commit`, or the files on
    disk), the comment's lines carried through it where it is the comment's file; elsewhere
    the first changed row stands for where to look. None for a fix with no files.

    Adds `fix_files` (all of `files`) and `commented` (the comment's file now), so the
    sidebar lists the fix and marks the file the comment is on."""
    names = [f for f, _a, _r in files]
    if not names:
        return None
    now = commented(repo, thread)
    want = pick if pick in names else now if now in names else names[0]
    its = want == now
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
    v.update(fix_files=files, commented=now)
    return v
