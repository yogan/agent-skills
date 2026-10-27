#!/usr/bin/env python3
"""The author's own review of their change in a Hunk window beside the agent's: one commit
at a time, oldest first, or the uncommitted change as one piece.

Commits are addressed by their POSITION on the branch, 1 = oldest above the merge-base,
never by hash: amending commit 2 rewrites the hash of 2 and of everything after it, while
the positions stay put. The merge-base, not `origin/<default>` itself, is the bottom of the
range, because once the default branch has moved on a diff against its tip would show its
newer commits reversed as part of the change.

  plan            what there is to review: the branch's commits, and uncommitted changes
  show N|work     put commit N, or the uncommitted change, in the window
  notes           the author's diff notes still awaiting an answer, numbered
  reply N TEXT    answer diff note N (as `notes` numbers it) under it in the window
  point FILE LINE TEXT [--old]
                  on the author's request only: start a diff note on a line of the
                  piece, new side unless --old
  amend N         fold the working tree into commit N, then show N again
  ack             the author signed off on what the window holds: clear its notes
  close           close the window

Run: `python3 selfreview.py plan`
"""
import os
import re
import subprocess
import sys
import tempfile

_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from lib import hunk  # noqa: E402
from lib.cli import die  # noqa: E402
from lib.git import default_base, git  # noqa: E402

LABEL = "self-review"
# Recorded on the window: the tree-ish whose new side it shows — the commit, or a snapshot
# of the working tree — so a reload can tell where each noted line went since.
SHOWN = "@self-review-shown"
# The author label of a note the script re-created for the author. The viewer records
# every note the script makes as the agent's, so this label is what keeps it the author's.
YOU = "you"
# Plain, uncoloured diffs with a/ b/ prefixes whatever the user's git config says, so the
# stat parsed from them is the one the viewer reports.
DIFF = ["-c", "diff.noprefix=false", "-c", "color.ui=false"]


def root(cwd=None):
    return os.path.realpath(git("rev-parse", "--show-toplevel", cwd=cwd))


def base(cwd=None):
    return git("merge-base", default_base(cwd), "HEAD", cwd=cwd)


def commits(cwd=None):
    """[(sha, subject)] from the merge-base to HEAD, oldest first."""
    out = git("log", "--reverse", "--format=%H%x00%s", f"{base(cwd)}..HEAD", cwd=cwd)
    return [tuple(line.split("\0", 1)) for line in out.splitlines() if line]


def dirty(cwd=None):
    """`git status --porcelain` lines: staged, unstaged and untracked, ignored files not."""
    return [line for line in (git("status", "--porcelain", cwd=cwd) or "").splitlines()
            if line]


def commit_at(n, cwd=None):
    cs = commits(cwd)
    if not 1 <= n <= len(cs):
        die(f"no commit {n}: the branch has {len(cs)} above its merge-base")
    return cs[n - 1], len(cs)


def commit_stat(sha, cwd=None):
    return hunk.diff_stat(git(*DIFF, "show", "--no-ext-diff", "--format=", sha, cwd=cwd))


def work_stat(cwd=None):
    """What `hunk diff HEAD` shows: staged and unstaged changes, plus untracked files."""
    stat = hunk.diff_stat(git(*DIFF, "diff", "--no-ext-diff", "HEAD", cwd=cwd) or "")
    untracked = git("ls-files", "--others", "--exclude-standard", cwd=cwd) or ""
    for path in untracked.splitlines():
        # --no-index exits 1 when the files differ, which for a new file they always do.
        p = subprocess.run(["git", *DIFF, "diff", "--no-ext-diff", "--no-index", "--",
                            os.devnull, path], cwd=cwd, capture_output=True, text=True)
        stat += hunk.diff_stat(p.stdout)
    return stat


def snapshot(cwd=None):
    """A tree object of the working tree as it is, untracked files included, written
    through a throwaway index so the real one is left alone."""
    fd, index = tempfile.mkstemp(prefix="self-review-index-")
    os.close(fd)
    try:
        env = dict(os.environ, GIT_INDEX_FILE=index)
        for args in (["read-tree", "HEAD"], ["add", "-A"]):
            subprocess.run(["git", *args], cwd=cwd, env=env, check=True,
                           capture_output=True)
        return subprocess.run(["git", "write-tree"], cwd=cwd, env=env, check=True,
                              capture_output=True, text=True).stdout.strip()
    finally:
        os.remove(index)


def show(target, cwd=None, after_amend=False):
    """Load `target` ("work" or a position) into the window; print where it is.

    Notes stay in the window across a reload and re-anchor by line number, so moving to
    something else while it holds any would pin them onto unrelated code. Reloading the
    same target keeps them — that is how the author checks a fix against what they wrote —
    and so does the reload after `amend`, whose commit has a new hash but is the same one.
    A note whose code the fix moved or replaced is moved with it (`move_notes`).
    """
    repo = root(cwd)
    if target == "work":
        if not dirty(cwd):
            die("no uncommitted changes to review")
        args, stat, what = ["diff", "HEAD"], work_stat(cwd), "the uncommitted change"
        shows = snapshot(cwd)
    else:
        # Every change in the tree from here on is an agreed fix, which `amend` folds into
        # this commit wholesale — so the tree has to start out clean.
        if dirty(cwd):
            die("uncommitted changes in the tree: review them with `show work` first, or "
                "have the author commit them, before reviewing commits")
        (sha, subject), total = commit_at(int(target), cwd)
        args, stat, shows = ["show", sha], commit_stat(sha, cwd), sha
        what = f"commit {target} of {total}, {sha[:8]} \"{subject}\""
    if not after_amend:
        guard_switch(repo, args, cwd)
    # Taken before the reload: a reload can drop a note whose line no longer exists, and
    # this copy is then all that is left of it.
    sid = hunk.installed() and hunk.find_session(repo, hunk.owner_id())
    if sid:
        # Before the reload: notes on hidden files would not be taken along by `move_notes`.
        require_all_shown(sid, cwd)
    before = (hunk.notes(sid) or []) if sid else []
    shown_before = hunk.mark(sid, SHOWN) if sid else ""
    shown = hunk.show_review(repo, LABEL, args, stat)
    if not shown:
        die("the Hunk window could not be made to show exactly this diff (needs tmux and "
            "hunk; check `hunk session list`)")
    hunk.set_mark(shown[0], SHOWN, shows)
    print(f"window {shown[1]}: {what}, {len(stat)} file(s)")
    if before and shown_before and shown_before != shows:
        files = {p for p, _, _ in stat}
        for line in move_notes(shown[0], before, shown_before, shows, files, cwd):
            print(line)


HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def changes(old, new, path, cwd=None):
    """[(a, b, c, d)] for each hunk of `git diff -U0 old new -- path`: old lines a..a+b-1
    became new lines c..c+d-1. A count of 0 is a pure insertion (b) or deletion (d), placed
    after line a or c."""
    out = git(*DIFF, "diff", "--no-ext-diff", "-U0", old, new, "--", path, cwd=cwd) or ""
    hunks = []
    for line in out.splitlines():
        m = HUNK_HEADER.match(line)
        if m:
            a, b, c, d = m.groups()
            hunks.append((int(a), 1 if b is None else int(b), int(c),
                          1 if d is None else int(d)))
    return hunks


def remap(hunks, start, end):
    """(line, how) for a note on lines start..end, given the hunks that turned the old
    version of its file into the new one. `line` is where the note goes — the viewer
    anchors a note to one line and shows it below that line — and `how` is one of:

      same       nothing about the noted lines changed, nor above them
      shifted    lines were added or removed above it; same code, new line numbers
      rewritten  the fix replaced noted code: the note goes on the last line of what
                 replaced it, so it shows below the new code rather than inside it
      deleted    the noted code is gone: the note goes where it used to be
    """
    def touches(a, b):
        # A pure insertion sits after line `a`; inside the range only if code follows it.
        return start <= a < end if b == 0 else a <= end and a + b - 1 >= start

    def moved(line):
        """Where an untouched old line ends up."""
        return line + sum(d - b for a, b, c, d in hunks
                          if (a if b == 0 else a + b - 1) < line)

    hit = [h for h in hunks if touches(h[0], h[1])]
    if not hit:
        line = moved(end)
        return line, "same" if line == end else "shifted"
    covered = sum(b for _, b, _, _ in hit)
    if all(d == 0 for _, _, _, d in hit) and covered >= end - start + 1:
        return max(hit[0][2], 1), "deleted"
    last = max(c + d - 1 if d else c for _, _, c, d in hit)
    tail = [line for line in range(start, end + 1)
            if not any(a <= line <= a + b - 1 for a, b, _, _ in hit)]
    return max([last] + [moved(line) for line in tail]), "rewritten"


def span(n):
    return f"{'R' if n['side'] == 'new' else 'L'}{n['line']}" + (
        f"–{n['end']}" if n.get("end") and n["end"] != n["line"] else "")


def move_notes(sid, before, old, new, files, cwd=None):
    """Move each thread whose code changed between `old` and `new` onto where that code
    went; return one line per thread moved or left behind, for the agent to pass on.

    The viewer has no way to move a note, or to create one as the user's, so a moved
    thread is re-created — its first note under the author label `you (moved from …)`,
    its replies under theirs — and only then is the original removed. If the new copy
    cannot be completed it is removed instead, and the original stays where it was.

    Only new-side notes move: the old side of a commit is its parent, and of the
    uncommitted change it is HEAD, neither of which an agreed fix touches.
    """
    report = []
    hunk_cache = {}
    for number, tid, _, _ in threads(before):
        thread = [n for n in before if n["id"] == tid or top_of(n, before) == tid]
        head = thread[0]
        if head["side"] != "new" or head["line"] is None:
            continue
        if head["file"] not in files:
            report.append(f"#{number} {head['file']}:{span(head)} left in place: the file is "
                          "no longer part of this change")
            continue
        if head["file"] not in hunk_cache:
            hunk_cache[head["file"]] = changes(old, new, head["file"], cwd)
        line, how = remap(hunk_cache[head["file"]], head["line"], head["end"] or head["line"])
        if how == "same":
            continue
        where = f"{head['file']}:{span(head)}"
        # The first note keeps whose it was: the author's, or the agent's own `point`.
        who = YOU if is_author(head) else "agent"
        label = f"{who} (moved from {span(head)}" + (", code deleted)" if how == "deleted"
                                                    else ")")
        made = []
        root_id = hunk.add_note(sid, head["file"], line, head["text"], label)
        if root_id:
            made.append(root_id)
            # Each reply goes under the copy of the note it answered, not under the first:
            # flattened, the viewer would list the agent's replies above the author's.
            copy_of = {head["id"]: root_id}
            for n in thread[1:]:
                rid = hunk.reply(sid, copy_of.get(n["parent"], root_id), n["text"],
                                 YOU if is_author(n) else "agent")
                if not rid:
                    break
                copy_of[n["id"]] = rid
                made.append(rid)
        if len(made) < len(thread):
            for nid in reversed(made):
                hunk.drop_note(sid, nid)
            report.append(f"#{number} {where} could not be moved; it is still on its old line")
            continue
        still = {n["id"] for n in hunk.notes(sid) or []}
        for n in reversed(thread):
            if n["id"] in still:
                hunk.drop_note(sid, n["id"])
        report.append(f"#{number} {where} {how}: moved to R{line}")
    return report


def guard_switch(repo, args, cwd=None):
    """Refuse to load `args` over a window that holds notes about something else."""
    sid = hunk.installed() and hunk.find_session(repo, hunk.owner_id())
    if not sid:
        return
    got = hunk.notes(sid)
    if got is None:
        die("the Hunk window did not answer; nothing was loaded")
    if not got:
        return
    now = hunk.loaded_input(sid)
    if now == ("vcs", None) and args[0] == "diff" or now == ("show", args[-1]):
        return
    die("the window still holds diff notes on what it shows; finish that review with `ack` "
        "before moving on")


def top_of(n, all_notes):
    """The id of the first note of the thread `n` is in."""
    by_id = {m["id"]: m for m in all_notes}
    while n["parent"] in by_id:
        n = by_id[n["parent"]]
    return n["id"]


def is_author(n):
    """Written by the author: typed in the viewer, or re-created for them by `move_notes`."""
    return n["source"] == "user" or n.get("author", "").startswith(YOU)


def threads(all_notes):
    """[(number, root_id, location, waiting)] for every thread in the window, numbered from 1
    in the order they were started.

    A thread is a note plus the replies under it, however deep. `waiting` is what the author
    has written since the agent last answered in it — empty once answered, and non-empty
    again when the author answers back inside an answered thread.

    The number is how a thread is referred to. A note's id (`user:1792059099183-1`) has to
    be copied exactly, and a model copying it drops the `user:` prefix often enough to
    matter; a small number does not get mangled. It stays put while the author adds notes,
    which only ever adds a number at the end — but a thread that `move_notes` re-created
    counts as started then, so after an amend the numbers are read afresh.
    """
    by_id = {n["id"]: n for n in all_notes}
    grouped = {}
    for n in all_notes:  # oldest first, so a thread's root comes before its replies
        grouped.setdefault(top_of(n, all_notes), []).append(n)
    out = []
    for number, (tid, thread) in enumerate(grouped.items(), 1):
        waiting = []
        for n in thread:
            waiting = waiting + [n["text"]] if is_author(n) else []
        head = by_id[tid]
        side = "" if head["side"] == "new" else " (removed line)"
        out.append((number, tid, f"{head['file']}:{head['line']}{side}", waiting))
    return out


def open_threads(all_notes):
    """The threads whose last word is the author's."""
    return [t for t in threads(all_notes) if t[3]]


def resolve(ref, all_notes):
    """The root id of the thread `ref` names: its number (`2` or `#2`), or the id of any
    note in it, with or without the part up to its first colon. None if nothing matches."""
    ts = threads(all_notes)
    num = ref.lstrip("#")
    if num.isdigit():
        return next((tid for n, tid, _, _ in ts if n == int(num)), None)
    for n in all_notes:
        if ref in (n["id"], n["id"].split(":", 1)[-1]):
            return top_of(n, all_notes)
    return None


def own_session(cwd=None):
    sid = hunk.installed() and hunk.find_session(root(cwd), hunk.owner_id())
    if not sid:
        die("no self-review window of this agent is open; run `show` first")
    return sid


def hidden(sid, cwd=None):
    """Files of the shown piece that the window does not hold, sorted.

    An extension can drop files from the loaded review — hunk-mark-read hides files marked
    read — and the viewer then lists no notes on them: a note there would read as no note
    at all, and an empty list is the author's ACK. Measured against what was shown (the
    commit, or the snapshot the mark records), not the tree as it is now, so a fix made
    since adds nothing here.
    """
    shows = hunk.mark(sid, SHOWN)
    loaded = hunk.loaded_stat(sid)
    if not shows or loaded is None:
        return []
    if hunk.loaded_input(sid) == ("show", shows):
        stat = commit_stat(shows, cwd)
    else:
        stat = hunk.diff_stat(git(*DIFF, "diff", "--no-ext-diff", "HEAD", shows, cwd=cwd)
                              or "")
    return sorted({p for p, _, _ in stat} - {p for p, _, _ in loaded})


def require_all_shown(sid, cwd=None):
    gone = hidden(sid, cwd)
    if gone:
        die(f"{len(gone)} file(s) of this piece are hidden in the window, likely marked read "
            f"({', '.join(gone)}); notes on them cannot be read. Ask the author to show "
            "hidden files again (hunk-mark-read: \"Hide/show read files\") and say done")


def notes(cwd=None):
    sid = own_session(cwd)
    require_all_shown(sid, cwd)
    got = hunk.notes(sid)
    if got is None:
        die("the Hunk window did not answer; nothing can be concluded about its diff notes")
    waiting = open_threads(got)
    if not waiting:
        print("no diff notes awaiting an answer")
    for number, _, where, texts in waiting:
        print(f"#{number}  {where}")
        for t in texts:
            print(f"    {t}")


def answering(tid, all_notes):
    """The note an answer to thread `tid` goes under: the author's latest in it, or its
    first note when the author has written nothing there.

    Under the first note it would be a sibling of every reply already there, and the
    viewer shows an agent's replies above the author's among siblings, whatever their
    age — so an answer to the author's reply would appear above the reply it answers.
    Nested under that reply, it reads in order.
    """
    mine = [n for n in all_notes if top_of(n, all_notes) == tid and is_author(n)]
    return mine[-1]["id"] if mine else tid


def reply(ref, text, cwd=None):
    """Answer thread `ref` under the note it answers (`answering`), then confirm the
    answer is there: a reply the viewer silently dropped would leave the author's note
    looking ignored."""
    sid = own_session(cwd)
    got = hunk.notes(sid)
    if got is None:
        die("the Hunk window did not answer; nothing was posted")
    tid = resolve(ref, got)
    if tid is None:
        listed = ", ".join(f"#{n} {where}" for n, _, where, _ in threads(got)) or "none"
        die(f"no diff note {ref} in the window; its diff notes: {listed}")
    target = answering(tid, got)
    if not hunk.reply(sid, target, text):
        die(f"the reply to {ref} was refused by the viewer")
    before = {n["id"] for n in got}
    after = hunk.notes(sid)
    if after is None or not any(n["parent"] == target and n["id"] not in before
                                for n in after):
        die(f"the reply to {ref} cannot be found in the window after posting it")
    print(f"answered {ref}")


def point(path, line, text, side="new", cwd=None):
    """Start a diff note of the agent's own on `path`:`line` — only ever because the
    author asked for something to be pointed out, never as a review of the agent's own.

    The viewer refuses a line no hunk of the loaded piece shows; that place has to be
    named in the chat instead, which is what the error says.
    """
    sid = own_session(cwd)
    got = hunk.notes(sid)
    if got is None:
        die("the Hunk window did not answer; nothing was posted")
    nid = hunk.add_note(sid, path, line, text, "agent", side=side)
    if not nid:
        die(f"{path}:{line} cannot carry a note: it is not a line this piece's diff shows "
            "(or not a file in it). Name that place in the chat instead.")
    after = hunk.notes(sid)
    found = next((t for t in threads(after or []) if t[1] == nid), None)
    if not found:
        die(f"the note on {path}:{line} cannot be found in the window after posting it")
    print(f"pointed out #{found[0]} {found[2]}")


def amend(n, cwd=None):
    """Fold every change in the tree into commit `n`, then show it again.

    The newest commit is amended in place; an older one gets a fixup commit squashed into
    it by a non-interactive autosquash rebase. A rebase that conflicts is aborted and the
    fixup undone, which leaves the fix in the tree exactly as it was before the call.
    """
    if not dirty(cwd):
        die("nothing to amend: the tree is clean")
    if git("rev-list", "--merges", f"{base(cwd)}..HEAD", cwd=cwd):
        die("the branch contains merge commits; amend by hand")
    (sha, _), total = commit_at(n, cwd)
    git("add", "-A", cwd=cwd)
    if n == total:
        ok = subprocess.run(["git", "commit", "-q", "--amend", "--no-edit"], cwd=cwd)
    else:
        ok = subprocess.run(["git", "commit", "-q", f"--fixup={sha}"], cwd=cwd)
        if ok.returncode == 0:
            env = dict(os.environ, GIT_SEQUENCE_EDITOR="true")
            ok = subprocess.run(["git", "rebase", "-q", "-i", "--autosquash", f"{sha}^"],
                                cwd=cwd, env=env, capture_output=True, text=True)
            if ok.returncode != 0:
                git("rebase", "--abort", cwd=cwd, check=False)
                git("reset", "-q", "--soft", "HEAD~1", cwd=cwd)
                git("reset", "-q", cwd=cwd)
                die(f"the fix conflicts with a later commit, so commit {n} was not changed; "
                    "the fix is back in the tree, unstaged")
    if ok.returncode != 0:
        git("reset", "-q", cwd=cwd)
        die("the commit was refused (see the hook output above); the fix is unstaged")
    upstream = git("rev-parse", "--abbrev-ref", "@{upstream}", cwd=cwd, check=False)
    if upstream and git("merge-base", "--is-ancestor", sha, upstream, cwd=cwd, check=False) \
            is not None:
        print(f"note: commit {n} was already on {upstream}; publishing this needs a "
              "force-push")
    show(str(n), cwd, after_amend=True)


def ack(cwd=None):
    sid = own_session(cwd)
    require_all_shown(sid, cwd)
    got = hunk.notes(sid)
    if got is None:
        die("the Hunk window did not answer; nothing was cleared")
    waiting = open_threads(got)
    if waiting:
        die(f"{len(waiting)} diff note(s) still await an answer; run `notes`")
    if not hunk.clear_all_notes(sid):
        die("the diff notes could not be cleared")
    print(f"cleared {len(threads(got))} diff note(s)")


def close(cwd=None):
    """Close the window. Notes exist only inside it, so one still awaiting an answer
    would be lost unseen: that refuses."""
    sid = own_session(cwd)
    require_all_shown(sid, cwd)
    got = hunk.notes(sid)
    if got and open_threads(got):
        die("diff notes still await an answer; run `notes` first")
    if not hunk.close_session(sid, hunk.owner_id()):
        die("the window could not be closed")


def plan(cwd=None):
    print(f"base: {base(cwd)[:8]} (merge-base with {default_base(cwd)})")
    cs = commits(cwd)
    for i, (sha, subject) in enumerate(cs, 1):
        print(f"{i}. {sha[:8]} {subject}  ({len(commit_stat(sha, cwd))} file(s))")
    if not cs:
        print("no commits above the merge-base")
    d = dirty(cwd)
    print(f"uncommitted: {len(d)} path(s)" if d else "uncommitted: none")


def main(argv):
    if not argv:
        print(__doc__.strip().split("\n\n")[2])
        return
    cmd, rest = argv[0], argv[1:]
    if cmd == "plan" and not rest:
        return plan()
    if cmd == "show" and len(rest) == 1 and (rest[0] == "work" or rest[0].isdigit()):
        return show(rest[0])
    if cmd == "notes" and not rest:
        return notes()
    if cmd == "reply" and len(rest) == 2:
        return reply(rest[0], rest[1])
    if cmd == "point" and len(rest) in (3, 4) and rest[1].isdigit() \
            and (len(rest) == 3 or rest[3] == "--old"):
        return point(rest[0], int(rest[1]), rest[2], "old" if len(rest) == 4 else "new")
    if cmd == "amend" and len(rest) == 1 and rest[0].isdigit():
        return amend(int(rest[0]))
    if cmd == "ack" and not rest:
        return ack()
    if cmd == "close" and not rest:
        return close()
    die(f"unknown command or arguments: {' '.join(argv)}")


if __name__ == "__main__":
    main(sys.argv[1:])
