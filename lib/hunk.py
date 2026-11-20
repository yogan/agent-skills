"""Show a diff in a live Hunk review session in its own tmux window, instead of pasting
it into the chat message.

The MR skills hand the user a working diff to approve before a fixup and a force-push.
Pasted inline that is fine for a three-line change and unreadable for a real one: a few
hundred lines of unified diff push everything else in the message off the screen, and the
model pays for every one of those lines twice, once to print and once to carry in its own
context. Hunk is a terminal diff viewer with a daemon, so a window can be opened once and
its contents swapped per topic — the chat message keeps the summary and the question, and
the diff itself lives somewhere the user can page through it.

What the caller gets back is either the session it loaded and a way to refer to that
window ("9 (!123)"), or None. None means fall back to printing the diff inline, and it is
the half that matters:
a message pointing at a window that does not hold the diff is worse than a wall of text,
because the user approves a push against something they never actually saw. So every step
that could silently not happen — no tmux, no hunk, a daemon that never answered, a window
the user closed, a reload that came back holding anything other than exactly the files and
line counts the message claims — is checked, and any of them collapses to None rather than
to an optimistic pointer.

Sessions are addressed by their explicit id, never by `--repo`: two agents working in one
repository each have their own window, and `--repo` cannot tell those apart (hunk answers
"Multiple active sessions match"). Which window belongs to which agent is recorded as a
tmux user option on the window itself rather than in a registry file of ours — the
mapping's useful life is exactly the window's, so closing the window forgets it with no
stale entry left to reap, and one agent cannot read another's mark.

The window's life is one topic's fix: opened beside the agent's own window when the first
diff needs showing, reloaded for each further round on that topic, and closed once the push
lands — at which point the fixup and rebase have left the working tree clean and the viewer
would otherwise sit there showing a diff that no longer exists.

review-mr's window is a patch window instead (`show_patch`): a topic's server-side diff,
shown when the user asks to see it in full, and reloaded for the next one. Nothing it shows
is ever pushed, so nothing closes it; the user does, whenever they like.

self-review's window (`show_review`) holds the author's own review of their change, one
commit or the uncommitted change at a time. There the notes are the point: the author's
and the agent's threaded answers stay across every reload, and are cleared only once the
author has signed off on what the window shows.
"""
import json
import os
import re
import shlex
import shutil
import subprocess
import time
from datetime import datetime, timezone

from lib import diffsize, tmux

# Marks a tmux window as belonging to one agent session. See the module docstring for why
# this lives on the window rather than in a file.
OWNER_OPT = "@agent-skills-owner"

# The most diff notes the agent may anchor on one topic's diff. A note on every hunk would
# bury the diff it annotates. Notes are for the places the user would not otherwise spot —
# a change that deviates from the agreed plan, does more than was asked, or is not obvious
# from the diff — and the cap is a number rather than prose because "only where it matters"
# is guidance a model talks itself out of once it has three more things to mention.
MAX_NOTES = 3

# Seconds to wait for any single hunk or tmux command. The daemon answers locally and
# immediately; anything slower than this is a daemon that is not going to answer at all,
# and waiting longer just delays the inline fallback the caller will end up using.
TIMEOUT = 10

# How long to wait for a freshly spawned viewer to register with the daemon: 20 polls a
# quarter-second apart, so 5 seconds in all. It is a TUI starting up, so this is startup
# latency, not work — and the whole budget is only ever spent on a viewer that never
# arrives, which ends in the inline fallback.
SPAWN_TRIES, SPAWN_DELAY = 20, 0.25


def _run(argv, timeout=TIMEOUT, stdin=None):
    """(returncode, stdout). EVERY hunk and tmux call goes through here: one place for a
    test to replace, and one place where a missing binary, a hung daemon or a killed
    window degrades into "not available" instead of raising into a skill's render path."""
    try:
        p = subprocess.run(argv, input=stdin, capture_output=True, text=True,
                           timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return p.returncode, p.stdout


def _json(argv):
    rc, out = _run(argv)
    if rc != 0:
        return None
    try:
        return json.loads(out)
    except ValueError:
        return None


def installed():
    """Both binaries present. Checked before anything is spawned, not discovered by trying:
    without it, a machine with tmux but no viewer opens a window, watches the command die,
    then polls a daemon that will never answer for the whole spawn timeout — several
    seconds added to every topic, for every user who has not installed the viewer, which
    is most of them."""
    return bool(shutil.which("hunk") and shutil.which("tmux"))


def _own_pane():
    """The pane this agent runs in, also in a session that lost `TMUX_PANE` (see
    lib/tmux.py), looked up through `_run` like every other tmux call."""
    return tmux.own_pane(run=_run)


def in_tmux():
    return bool(os.environ.get("TMUX")) or bool(_own_pane())


def owner_id():
    """This agent session's identity, used to tell its window from another agent's.

    Falls back to the pane the agent itself runs in: two agents cannot share a pane, so
    that is still a genuine per-agent identity where the session id is not exported.
    """
    return os.environ.get("CLAUDE_CODE_SESSION_ID") or _own_pane()


def sessions():
    """Every live Hunk session the daemon knows about, or [] if it cannot be reached."""
    data = _json(["hunk", "session", "list", "--json"])
    return (data or {}).get("sessions") or []


def _pane_of(session):
    """The tmux pane a session's viewer is running in, if it is running in one at all."""
    for loc in (session.get("terminal") or {}).get("locations") or []:
        if loc.get("source") == "tmux" and loc.get("paneId"):
            return loc["paneId"]
    return None


def _window_of_pane(pane):
    """tmux's id for the window a pane sits in, or "".

    Needed because `new-window -t` is the one target in this module that will NOT accept a
    pane — it names the index to create at, and refuses a pane id outright ("can't specify
    pane here"). Every other `-t` here resolves a pane upward by itself.
    """
    if not pane:
        return ""
    rc, out = _run(["tmux", "display-message", "-p", "-t", pane, "#{window_id}"])
    return out.strip() if rc == 0 else ""


def _owner_of(pane):
    rc, out = _run(["tmux", "show-options", "-w", "-t", pane, "-v", OWNER_OPT])
    return out.strip() if rc == 0 else ""


def find_session(repo, owner):
    """This agent's own Hunk session id for `repo`, or None.

    Matching is on the owner mark, not on the repository: a window the user opened
    themselves, or one belonging to another agent in the same checkout, must never be
    reloaded out from under them.

    `repo=None` finds a patch window (`show_patch`), which has no repository.

    An empty `owner` matches nothing. Without that guard it would match everything: an
    unmarked window — precisely the user's own — reads back as the empty string too, so an
    agent that could not identify itself would adopt the first viewer it found, reload the
    user's diff away and read their comments as answers to its own question.
    """
    if not owner:
        return None
    for s in sessions():
        if s.get("repoRoot") != repo:
            continue
        pane = _pane_of(s)
        if pane and _owner_of(pane) == owner:
            return s.get("sessionId")
    return None


def spawn_session(repo, label, owner, sleep=None, cmd="hunk diff HEAD"):
    """Open a viewer for `repo` in a new background tmux window and return its session id.

    `cmd` is what the window runs: the working diff by default, or `hunk patch <file>`
    for a diff that exists only as text (see `show_patch`).

    The window is created first and marked before the viewer is looked for, so the mark is
    in place no matter how the startup race resolves, and the new session is identified by
    the pane tmux just told us about rather than by "whichever session appeared" — which
    would be wrong the moment two agents start a window at the same time.
    """
    if sleep is None:
        sleep = time.sleep
    # `-a` against the agent's own window puts the viewer immediately to its right,
    # shifting the rest along, instead of at the far end of the window list. The two are
    # read together constantly — switch over, read the diff, switch back — and on a busy
    # tmux the far end can be six windows away. Dropped when the window cannot be
    # identified, which only leaves the placement to tmux's default.
    here = _window_of_pane(_own_pane())
    place = ["-a", "-t", here] if here else []
    # The tag lets `closed_viewer_notes` tell this agent's viewers' files (the keep-notes
    # extension's) from those of a viewer the user opened by hand in the same repository.
    rc, out = _run(["tmux", "new-window", "-d", "-P", "-F", "#{pane_id}"] + place
                   + ["-e", f"{KEEP_TAG_ENV}={owner}", "-n", label, "-c", repo, cmd])
    pane = out.strip()
    if rc != 0 or not pane:
        return None
    # The mark is this window's whole identity, so a failure here is not cosmetic: the
    # poll below matches on the PANE, so the spawn would still succeed and the pointer
    # still be valid — and from the next topic on, `find_session` would skip the window
    # for ever, spawning another beside it, and `hunk-close` (which only reaches a window
    # through `find_session`) would never close any of them. Unrecoverable without closing
    # windows by hand, so the window is closed now instead.
    rc, _ = _run(["tmux", "set-option", "-w", "-t", pane, OWNER_OPT, owner])
    if rc != 0:
        _run(["tmux", "kill-window", "-t", pane])
        return None
    for _ in range(SPAWN_TRIES):
        for s in sessions():
            if _pane_of(s) == pane:
                return s.get("sessionId")
        sleep(SPAWN_DELAY)
    # The viewer never registered, so this window holds no diff anyone was told to look
    # at — and because it has no session, the next topic would not find it either and
    # would open another one beside it. Close the one we opened rather than leave a row
    # of dead windows behind. Only ever this pane, which tmux just handed us.
    _run(["tmux", "kill-window", "-t", pane])
    return None


def close_session(session_id, owner):
    """Close this agent's viewer window. True if it went away.

    The window's useful life ends with the push: the fixup and rebase leave the working
    tree clean, so the viewer is left holding a diff that no longer exists anywhere, beside
    a conversation that has moved to the next topic. Nothing is lost by closing it — the
    diff is in the branch, and the next topic opens a window of its own — while leaving it
    open costs a window per topic, each showing a stale change.

    `owner` is re-checked against the window's mark even though the caller looked the
    session up by it. Every other operation here is recoverable by reloading; this one
    destroys a window, and the window it must never destroy — the user's own viewer, or
    another agent's — is exactly the one an out-of-date session id could point at.
    """
    if not owner:
        return False
    for s in sessions():
        if s.get("sessionId") != session_id:
            continue
        pane = _pane_of(s)
        if not pane or _owner_of(pane) != owner:
            return False
        rc, _ = _run(["tmux", "kill-window", "-t", pane])
        return rc == 0
    return False


def window_of(session_id):
    """How to tell the user where to look — "9 (!123)" — or None if the window is gone."""
    for s in sessions():
        if s.get("sessionId") != session_id:
            continue
        pane = _pane_of(s)
        if not pane:
            return None
        rc, out = _run(["tmux", "display-message", "-p", "-t", pane,
                        "#{window_index} (#{window_name})"])
        return out.strip() if rc == 0 and out.strip() else None
    return None


def focus_window(session_id):
    """Switch tmux to the window a session's viewer runs in, for a person who asked to see
    it: the window, then its client's session where that is another one. False where there
    is no window to switch to."""
    for s in sessions():
        if s.get("sessionId") != session_id:
            continue
        pane = _pane_of(s)
        if not pane:
            return False
        rc, _ = _run(["tmux", "switch-client", "-t", pane])
        if rc != 0:
            rc, _ = _run(["tmux", "select-window", "-t", pane])
        return rc == 0
    return False


def reload_working_diff(session_id):
    """Swap the session onto the repository's current working diff; return how many files
    it loaded, or None if the reload did not happen.

    The count is the caller's proof that the window really holds the diff it is about to
    point at — hunk reads the working tree itself rather than taking ours, so the two can
    legitimately be compared and must agree.

    Against `HEAD`, as the callers' own `git diff HEAD`: a plain `hunk diff` is the changes
    not yet staged, so a fix the agent had staged (`git add`) loaded as nothing at all, and
    the window stood empty beside a card counting four files.

    `--exclude-untracked` is what makes them comparable at all: the viewer lists untracked
    files by default and `git diff` never does, so a single new file in the tree — routine
    while reworking — put the two counts permanently out of step, and the mismatch check
    then fell back to an inline diff every time. The window must show exactly the change
    the message summarises, so it is the viewer that is narrowed to git's view.
    """
    return reload_review(session_id, WORKING_DIFF)


# What `reload_working_diff` loads, as `hunk session reload` takes it.
WORKING_DIFF = ["diff", "HEAD", "--exclude-untracked"]


def loaded_stat(session_id):
    """What the viewer currently holds, as a sorted [(path, added, removed)], or None if it
    could not be read.

    This is the evidence behind the pointer. Counting the FILES alone — which is all the
    reload reports — passes any window holding the same number of different files, or the
    same files at a different revision: a file saved between the two reads, an edit that
    landed after the diff was captured. The per-file line counts catch that, and they were
    measured to agree with `diff_stat`'s reading of `git diff` on every awkward case there
    is — binary, mode-only, no-newline-at-EOF, deletion, rename, partially staged — so a
    disagreement here is a real disagreement, not a counting convention.

    Sorted because the two sides order their files independently, and an ordering
    difference would refuse a window that holds exactly the right diff.
    """
    data = _json(["hunk", "session", "review", session_id, "--json"])
    files = ((data or {}).get("review") or {}).get("files")
    if files is None:
        return None
    return sorted((f.get("path") or "", f.get("additions") or 0, f.get("deletions") or 0)
                  for f in files)


def clear_agent_notes(session_id):
    """Drop the notes the AGENT anchored in an earlier round; leave the user's alone.

    A session outlives the diff in it — the window is opened once and reloaded per round —
    so without this every rework round adds its rationale on top of the last one's. Two
    things go wrong then, and the cosmetic one is the lesser: notes accumulate until they
    bury the diff, and, because the viewer re-anchors by line number, a note written about
    code that has since been rewritten lands on whatever now occupies that line. A stale
    note pointing confidently at unrelated code is worse than no note.

    The user's own notes are deliberately NOT cleared here. They are destroyed only by the
    read that hands them to the model (`user_notes(consume=True)`), so a note left while
    the model is working — after its last read, before this reload — survives to be
    answered instead of vanishing unseen.
    """
    rc, _ = _run(["hunk", "session", "comment", "clear", session_id, "--yes", "--json"])
    return rc == 0


def add_notes(session_id, notes, args=WORKING_DIFF):
    """Anchor the agent's rationale to lines of the loaded diff. `notes` are dicts of
    `filePath` plus one of `newLine`/`oldLine`, and `summary`.

    Raises ValueError above `MAX_NOTES` rather than quietly dropping the extras: a cap
    that silently truncates teaches nothing, and the point is for the caller to have
    chosen which few notes earn their place.

    A file the window hides as read is shown again first (`show_unread`, with `args` what
    the window holds): the viewer takes no note on a file it does not show, and refuses the
    whole batch for it.
    """
    if len(notes) > MAX_NOTES:
        raise ValueError(
            f"{len(notes)} notes for one topic, at most {MAX_NOTES} are allowed. A note "
            "belongs only where the change deviates from the agreed plan, does more than "
            "was asked, or is not obvious from the diff; everything else is already "
            "visible in the diff itself.")
    if not notes:
        return 0
    show_unread(session_id, {n.get("filePath") for n in notes}, args)
    rc, _ = _run(["hunk", "session", "comment", "apply", session_id, "--stdin", "--json"],
                 stdin=json.dumps({"comments": list(notes)}))
    return len(notes) if rc == 0 else 0


def user_notes(session_id):
    """What the USER wrote on the diff's own lines, as [(file, side, line, text, note_id)],
    or **None if the viewer could not be asked**.

    That distinction is the whole safety of this function. Its callers treat an empty list
    as "nothing to answer, go ahead and push", so a daemon that timed out, a mangled reply
    or a session that has gone must NOT arrive looking the same as a clean window: the user
    writes "don't push this yet" on a line, one `comment list` hiccups, and silence reads as
    consent for a force-push. Everywhere else in this module an uncertain answer collapses
    to the safe outcome; the empty list here was the one place it collapsed to the
    destructive one.

    `side` is "new" or "old", and is carried rather than flattened away because the two
    mean different files: a note on a REMOVED line names a line number that, after the
    change, belongs to unrelated code. Reporting it as a bare number invites the model to
    edit the wrong place.

    Deleting a note that has been answered is `drop_note`, deliberately a separate call —
    see there for why it cannot be folded in here.

    `--type user` serialises differently from the viewer's default listing, and neither
    shape uses the field names `comment add` TAKES: the input is `newLine`/`oldLine` and
    `summary`, while this output carries `newRange`/`oldRange` (a [start, end] pair) and
    `body`. Reading it by the input's names — which is what a hand-written fixture
    invites — silently yields a line number of None for every comment, so both spellings
    are accepted here and the test uses a fixture captured from the real CLI.

    Notes on files hidden from the review are added from the keep-notes extension's file
    for this viewer, where it is installed (see `_unlisted`): `comment list` leaves them out.
    """
    data = _json(["hunk", "session", "comment", "list", session_id,
                  "--type", "user", "--json"])
    if data is None or not isinstance(data.get("comments"), list):
        return None
    out = []
    for c in data["comments"]:
        if c.get("newRange") or c.get("newLine"):
            side, rng = "new", c.get("newRange") or []
        elif c.get("oldRange") or c.get("oldLine"):
            side, rng = "old", c.get("oldRange") or []
        else:
            side, rng = "new", []
        line = rng[0] if rng else (c.get("line") or c.get("newLine")
                                   or c.get("oldLine"))
        text = (c.get("body") or c.get("summary") or "").strip()
        out.append((c.get("filePath") or "", side, line, text,
                    c.get("noteId") or c.get("id")))
    return out + [_kept_tuple(n) for n in _unlisted(session_id, {n[4] for n in out})
                  if _is_users(n)]


def _kept(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def keep_notes(path, session_id, notes):
    """Keep at `path` a copy of what the user wrote in the viewer `session_id`, as
    `user_notes` just read it (their edits and deletions follow: the copy is replaced).

    The viewer holds its notes in memory alone: once its window closes, the daemon forgets
    the session and every note in it. A note is the user's only copy of a request, and one
    saying "stop, this is wrong" read as "no notes, nothing blocks the push" after they
    closed the window. So every read of a live viewer leaves this copy, which outlives it
    (`kept_notes`) until the notes are handed to the model (`forget_notes`)."""
    data = _kept(path)
    if notes:
        data[session_id] = [list(n) for n in notes]
    else:
        data.pop(session_id, None)
    if not data:
        forget_notes(path)
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


def kept_notes(path, but=None):
    """The notes kept at `path` from viewers other than `but` (the one open now, whose notes
    are read live): what the user wrote in a viewer that has closed since, in `user_notes`'
    shape."""
    return [tuple(n) for sid, notes in _kept(path).items() if sid != but for n in notes]


def forget_notes(path):
    """The kept copy goes, once its notes have reached the model."""
    try:
        os.remove(path)
    except OSError:
        pass


# The keep-notes Hunk extension (hunk-extensions/keep-notes) saves every note the user
# writes, edits or deletes the moment they do, one file per viewer process. Two things are
# read from those files that the viewer cannot give: notes on files hidden from the review
# (hunk-mark-read's "hide read files" filters them out of the diff, and `comment list`
# leaves their notes out, though the viewer still holds them), and notes of a viewer that
# has closed since. Without the extension there are no files, nothing here calls anything,
# and every reader behaves as it did before.
KEEP_DIR_ENV, KEEP_SWITCH_ENV, KEEP_TAG_ENV = ("HUNK_KEEP_NOTES_DIR", "HUNK_KEEP_NOTES",
                                               "HUNK_KEEP_NOTES_TAG")
_KEPT_NAME = re.compile(r"^(\d+)-(\d+)\.json$")
_KEPT_FORMAT = 1
# How far apart a viewer's process start (the file's `startedAt`) and its registration with
# the daemon (the session's `launchedAt`) may be and still be one viewer: an earlier process
# that had the same pid started long before. The start is ~0.3s ahead of the registration.
_SAME_VIEWER_SECONDS = 600


def keep_dir():
    """Where the extension writes, or None when it is switched off.

    Mirrors hunk-extensions/keep-notes/src/disk.ts's `keepDir`; a change to either is a
    change to both. Read from this process's environment, which for the agent's own viewers
    is the one they were started from."""
    if os.environ.get(KEEP_SWITCH_ENV, "").lower() == "off":
        return None
    own = os.environ.get(KEEP_DIR_ENV)
    if own and os.path.isabs(own):
        return own
    state = os.environ.get("XDG_STATE_HOME")
    base = state if state and os.path.isabs(state) else os.path.join(
        os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "hunk-keep-notes")


def _viewer_files():
    """[(path, file's content)] for every file the extension wrote that can be read."""
    d = keep_dir()
    try:
        names = sorted(os.listdir(d)) if d else []
    except OSError:
        return []
    out = []
    for name in names:
        m = _KEPT_NAME.match(name)
        if not m:
            continue
        path = os.path.join(d, name)
        doc = _kept(path)
        if (doc.get("version") == _KEPT_FORMAT and isinstance(doc.get("notes"), list)
                and doc.get("pid") == int(m.group(1))):
            out.append((path, doc))
    return out


def _seconds(text):
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _file_of(session, files):
    """Which of `files` the viewer of `session` (a `sessions()` entry) writes, or None."""
    launched = _seconds(session.get("launchedAt") or "")
    for path, doc in files:
        if doc.get("pid") != session.get("pid"):
            continue
        started = _seconds(doc.get("startedAt") or "")
        if launched is None or started is None \
                or abs(launched - started) <= _SAME_VIEWER_SECONDS:
            return path
    return None


def _kept_tuple(n):
    """A note from an extension file, in `user_notes`' shape."""
    side = n.get("side") if n.get("side") in ("new", "old") else "new"
    return (n.get("file") or "", side, n.get("line"), (n.get("text") or "").strip(),
            n.get("id"))


def _is_users(n):
    """A note from an extension file is the user's; a file from before the extension kept
    the agent's notes too holds no `source`, and only the user's notes."""
    return n.get("source", "user") == "user"


def _own_doc(session_id):
    """The content of the extension file the viewer of `session_id` writes, or None: no
    extension, or the daemon cannot say which viewer that is. Asks the daemon only when
    there is a file to choose from, so without the extension nothing more is called."""
    files = _viewer_files()
    if not files:
        return None
    s = next((s for s in sessions() if s.get("sessionId") == session_id), None)
    own = s and _file_of(s, files)
    return dict(files)[own] if own else None


def _unlisted(session_id, listed):
    """The notes the viewer of `session_id` saved to its file that `comment list` did not
    return (`listed` is the ids it did): those on files hidden from the review."""
    doc = _own_doc(session_id)
    return [n for n in (doc or {}).get("notes") or []
            if isinstance(n, dict) and n.get("id") and n["id"] not in listed]


def whole_review(session_id, since=None):
    """What the viewer of `session_id` loaded, hidden files included, as the keep-notes
    extension recorded it at its latest load: `(everything, shown, hidden)`, the first two
    sorted [(path, added, removed)] like `loaded_stat`, `hidden` the paths an extension's
    filter (hunk-mark-read's "hide read files") left out. None where there is no record, or
    none from a load finished at or after `since` (a `time.time()`): a record of an earlier
    load says nothing about this one.

    The extension records the changeset as it reached its own filter-free transform, which
    Hunk runs before the filter of an extension loaded after it — one installed under
    `~/.config/hunk/extensions/`, while keep-notes is loaded through `[extensions] paths`.
    Loaded the other way round it records only what is shown, and nothing reads as hidden.
    """
    review = ((_own_doc(session_id) or {}).get("review"))
    if not isinstance(review, dict) or not isinstance(review.get("files"), list):
        return None
    loaded_at = review.get("loadedAt")
    if since is not None and not (isinstance(loaded_at, (int, float))
                                  and loaded_at >= since * 1000):
        return None
    files = [f for f in review["files"] if isinstance(f, dict)]
    row = lambda f: (f.get("path") or "", f.get("additions") or 0, f.get("deletions") or 0)  # noqa: E731
    return (sorted(row(f) for f in files), sorted(row(f) for f in files if not f.get("hidden")),
            sorted(f.get("path") or "" for f in files if f.get("hidden")))


# hunk-mark-read (github.com/meldron/hunk-mark-read) hides the files a user marked read, and
# the viewer then takes no note and no reply on them. It keeps its marks in files any process
# may add to: one JSONL file per writer in a shared folder, every line an event
# {"t": ms since the epoch, "key": …, "read": bool}, the latest event for a key winning, all
# of them read again at every load of a viewer. A key is `<path>\0<previous path>\0<sha256
# of the diff>`. So the agent takes a file's mark away the way another viewer would: by
# writing an event of its own, then loading the window again. Mirrors src/sessionLog.ts of
# hunk-mark-read 0.1.0 (commit 1a76ae8); should it change, the reload shows the file still
# hidden, and the caller says so rather than posting into nothing.
_MARK_READ_WRITER = "agent-note"


def mark_read_dir():
    """The folder hunk-mark-read keeps its marks in. Mirrors its `defaultSessionDir`."""
    state = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state")
    return os.path.join(state, "hunk-mark-read", "sessions")


def _mark_events(d):
    """Every well-formed event in `d`, in hunk-mark-read's replay order: by time, then by
    file and line."""
    try:
        names = sorted(n for n in os.listdir(d) if n.endswith(".jsonl"))
    except OSError:
        return []
    events = []
    for name in names:
        try:
            with open(os.path.join(d, name), encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if isinstance(e, dict) and isinstance(e.get("t"), (int, float)) \
                    and not isinstance(e["t"], bool) and isinstance(e.get("key"), str) \
                    and isinstance(e.get("read"), bool):
                events.append(e)
    return sorted(events, key=lambda e: e["t"])


def unmark_read(paths):
    """Take away every read mark hunk-mark-read holds on `paths`; how many it took.

    Every mark of the path goes, not only the one for the diff shown now: a mark is keyed by
    the diff's content, and the agent cannot compute the key the viewer would. A mark on an
    earlier version of the file only mattered if the file changed back to it."""
    d = mark_read_dir()
    latest = {}
    for e in _mark_events(d):
        latest[e["key"]] = e
    gone = [e for key, e in latest.items() if e["read"] and key.split("\0", 1)[0] in paths]
    if not gone:
        return 0
    t = max([int(time.time() * 1000)] + [int(e["t"]) + 1 for e in gone])
    # Named as hunk-mark-read names its own (UTC start, label, pid), so its pruning applies.
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    try:
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"{stamp}_{_MARK_READ_WRITER}_{os.getpid()}.jsonl"), "a",
                  encoding="utf-8") as fh:
            fh.write("".join(json.dumps({"t": t, "key": e["key"], "read": False},
                                        separators=(",", ":")) + "\n" for e in gone))
    except OSError:
        return 0
    return len(gone)


def show_unread(session_id, paths, args):
    """Show again those of `paths` the viewer of `session_id` hides as read, so a note can
    go on them: their read marks go (`unmark_read`) and the window loads `args` — what it
    holds, as `hunk session reload` takes it — again. A note the agent adds is something the
    user has not read, so the file is unread again, as a change to it would make it.

    True when none of `paths` is hidden afterwards, by the keep-notes extension's record of
    that reload; False when one still is: the marks are not on disk (hunk-mark-read set to
    `persist = false`, or a version that keeps them elsewhere), or the reload failed.
    Without keep-notes nothing is known to be hidden, and nothing is done."""
    whole = whole_review(session_id)
    want = set(paths) & set(whole[2] if whole else ())
    if not want:
        return True
    if not unmark_read(want):
        return False
    since = time.time()
    if reload_review(session_id, args) is None:
        return False
    after = whole_review(session_id, since)
    return bool(after) and not want & set(after[2])


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (OSError, OverflowError, TypeError):
        return True
    return True


# How far apart the process start `ps` reports (to the second) and the file's `startedAt` (the
# viewer's runtime start) may be and still be one process.
_SAME_PROCESS_SECONDS = 5


def _process_start(pid):
    """When process `pid` started, in epoch seconds, or None when `ps` cannot say."""
    try:
        r = subprocess.run(["ps", "-o", "lstart=", "-p", str(int(pid))], capture_output=True,
                           text=True, timeout=5, env={**os.environ, "LC_ALL": "C"})
        return time.mktime(time.strptime(" ".join(r.stdout.split()), "%a %b %d %H:%M:%S %Y"))
    except (OSError, ValueError, TypeError, OverflowError, subprocess.SubprocessError):
        return None


def _still_running(doc):
    """Whether the viewer that wrote `doc` is running: its pid is alive and that process
    started when the file says. Alive alone is not enough, since a pid is reused: a closed
    viewer's file would look running for as long as the unrelated process lived, and its notes
    would count nowhere, neither live nor closed. Where `ps` cannot say, alive decides."""
    pid = doc.get("pid")
    if not _alive(pid):
        return False
    began, said = _process_start(pid), _seconds(doc.get("startedAt") or "")
    return began is None or said is None or abs(began - said) <= _SAME_PROCESS_SECONDS


def closed_viewer_notes(repo, tag):
    """[(path, notes)] — what viewers that are no longer running left in their files, for
    `repo` and opened with `tag` (`spawn_session` tags with the owner), notes in
    `user_notes`' shape.

    The tag keeps a viewer the user opened by hand in the same repository out: those notes
    were never written to this agent, and would hold its push for as long as they lasted.
    "No longer running" is "no session of the daemon's writes that file, and the process
    that wrote it is gone": a viewer missing from the daemon's list may only not have
    registered yet, or not again after the daemon restarted, and calling it closed hands its
    notes over while they stay in the viewer, to be reported a second time. A daemon that does
    not answer cannot say, so the process alone then decides.
    """
    files = [(p, d) for p, d in _viewer_files()
             if repo and d.get("repoRoot") == repo and tag and d.get("tag") == tag]
    if not files:
        return []
    data = _json(["hunk", "session", "list", "--json"])
    live = (data or {}).get("sessions") if isinstance(data, dict) else None
    if isinstance(live, list):
        running = {_file_of(s, files) for s in live}
        closed = [(p, d) for p, d in files if p not in running and not _still_running(d)]
    else:
        closed = [(p, d) for p, d in files if not _still_running(d)]
    return [(p, [_kept_tuple(n) for n in d["notes"] if isinstance(n, dict) and _is_users(n)])
            for p, d in closed]


def forget_viewer_files(paths):
    """Remove exactly these extension files, once every note in them has reached the model.
    Only ever files `closed_viewer_notes` handed back: a running viewer's file is its own."""
    for path in paths:
        forget_notes(path)


def drop_note(session_id, note_id):
    """Remove one note the model has now been given. True if it went.

    Separate from `user_notes` because the ordering is the point: a note is the user's only
    copy of a request, and it must not be destroyed until it has actually reached them on
    screen. Reading and deleting in one call cannot promise that — the read returns, the
    deletes run, and only afterwards does the caller print. Anything that goes wrong in
    between loses the request silently. So the caller prints first and calls this after,
    per note.

    Deleting at all is what stops an answered note being re-reported for ever: any note
    forbids the push, so a note that outlives its answer makes the push unreachable.
    """
    if not note_id:
        return False
    rc, _ = _run(["hunk", "session", "comment", "rm", session_id, note_id, "--json"])
    return rc == 0


def reload_patch(session_id, path):
    """Swap the session onto the patch at `path`; return how many files it loaded, or None
    if the reload did not happen."""
    data = _json(["hunk", "session", "reload", session_id, "--json", "--", "patch", path])
    if not data:
        return None
    return (data.get("result") or {}).get("fileCount")


def show_patch(path, label, cwd, owner=None, sleep=None):
    """Put the patch at `path` in this agent's own window; return (session_id, where) or
    None to mean "show the diff inline instead".

    For a diff that exists only as text — review-mr's is GitLab's server-side compare, which
    survives the force-pushes that prune its baseline locally, so there is no working tree
    or revision range to point the viewer at. The same guarantee as `show_working_diff`
    holds: the window must come back holding exactly the files and line counts of the
    patch, or no pointer is given.

    A patch session has no repository, so it is found by the owner mark alone
    (`find_session(None, …)`): one window per agent, reloaded for each diff it shows.
    """
    if not installed() or not in_tmux():
        return None
    owner = owner or owner_id()
    if not owner:
        return None
    with open(path) as fh:
        expect = sorted(tuple(f) for f in diff_stat(fh.read()))
    if not expect:
        return None
    sid = find_session(None, owner)
    since = time.time()
    if sid:
        count = reload_patch(sid, path)
    else:
        sid = spawn_session(cwd, label, owner, sleep=sleep,
                            cmd=f"hunk patch {shlex.quote(path)}")
        if not sid:
            return None
        count = len(loaded_stat(sid) or [])
    return _confirm(sid, count, expect, since)


def not_shown():
    """Why a diff could not be put in the viewer, said for the person: what is missing, or
    that the window did not come up holding exactly the diff."""
    if not installed():
        return "the diff viewer needs `hunk` and `tmux` installed"
    if not in_tmux():
        return tmux.background_hint() if tmux.in_background() else "the diff viewer needs this session to run inside tmux"
    return "the diff viewer did not come up holding exactly this diff (its files and line counts)"


def show_working_diff(repo, label, expect_stat, owner=None, sleep=None):
    """Put the repo's working diff in this agent's own window; return (session_id, where)
    or None to mean "show the diff inline instead".

    `expect_stat` is the caller's own [(path, added, removed)] for the diff it is about to
    summarise. The window has to come back holding exactly that — same files, same line
    counts — or the pointer is refused, because the entire value of the pointer is that it
    is trustworthy: the user is being asked to approve a force-push against whatever is in
    there, and a window holding a near-miss is worse than a wall of inline text.

    A round starts the window clean of the agent's own notes (see `clear_agent_notes`):
    what is anchored in there always describes the diff currently loaded, never the one it
    replaced.
    """
    sid = _own_window(repo, label, owner, sleep)
    if not sid:
        return None
    # Before the reload, not after: clearing is addressed by note id, and a reload can
    # re-anchor or drop a note whose line no longer exists, leaving nothing to clear.
    clear_agent_notes(sid)
    since = time.time()
    return _confirm(sid, reload_working_diff(sid), expect_stat, since)


def show_review(repo, label, args, expect_stat, owner=None, sleep=None):
    """Put `hunk <args>` — `["show", sha]` for one commit, `["diff", "HEAD"]` for the
    uncommitted change — in this agent's own window; return (session_id, where), or None
    when the window could not be made to hold exactly `expect_stat`.

    Unlike `show_working_diff`, every note stays. This is the author's own review: their
    notes and the agent's threaded answers are the conversation about the change, and a
    reload after an agreed fix is how they check that fix against what they wrote.
    """
    sid = _own_window(repo, label, owner, sleep)
    if not sid:
        return None
    since = time.time()
    return _confirm(sid, reload_review(sid, args), expect_stat, since)


def _own_window(repo, label, owner, sleep):
    """This agent's viewer for `repo`, opened beside it if there is none yet, or None."""
    if not installed() or not in_tmux():
        return None
    owner = owner or owner_id()
    if not owner:
        return None
    return find_session(repo, owner) or spawn_session(repo, label, owner, sleep=sleep)


def _confirm(sid, file_count, expect_stat, since=None):
    """(sid, where) once the window provably holds `expect_stat`, else None.

    The file count first because the reload reports it for free, and it also tells a
    reload that did not happen at all (None) from one that loaded something else.

    A window that hides files (hunk-mark-read's "hide read files") reports only the files it
    shows. It still holds `expect_stat` when the keep-notes extension's record of the load
    that finished after `since` says so: everything loaded is exactly `expect_stat`, and what
    it shows is exactly what the window itself reports. Nothing else counts as hidden.
    """
    expect = sorted(tuple(f) for f in expect_stat)
    if file_count is None:
        return None
    shown = loaded_stat(sid)
    if shown is None:
        return None
    if (file_count, shown) != (len(expect), expect):
        whole = whole_review(sid, since) if since is not None else None
        if not whole or whole[0] != expect or whole[1] != shown or file_count != len(shown):
            return None
    where = window_of(sid)
    return (sid, where) if where else None


def loaded_input(session_id):
    """(input kind, revision) of what the window holds — ("show", "<full sha>") for a
    commit, ("vcs", None) for a working diff — or None if the viewer could not be asked."""
    data = _json(["hunk", "session", "get", session_id, "--json"])
    s = (data or {}).get("session")
    if not s:
        return None
    return s.get("inputKind"), (s.get("review") or {}).get("revision")


def reload_review(session_id, args):
    """Swap the session onto `hunk <args>`; return how many files it loaded, or None if
    the reload did not happen."""
    data = _json(["hunk", "session", "reload", session_id, "--json", "--", *args])
    if not data:
        return None
    return (data.get("result") or {}).get("fileCount")


def notes(session_id):
    """Every note in the window, the author's and the agent's, oldest first, as dicts of
    id, parent (the note a reply answers, else None), source ("user" or "agent"), file,
    side, line and text — or None if the viewer could not be asked.

    None and [] must stay apart for the same reason as in `user_notes`: an empty list lets
    a review move on, and a daemon that did not answer must not look like an author who
    has nothing left to say.

    `source`, not `author`, tells who wrote a note: `author` is free text on `comment add`,
    while a note typed in the viewer is the only kind whose source is "user".

    The user's notes on files hidden from the review come from the keep-notes extension's
    file, as in `user_notes`; the agent's on those files are not kept there, so a thread on
    a hidden file shows its user notes and none of the agent's answers.
    """
    data = _json(["hunk", "session", "comment", "list", session_id,
                  "--type", "all", "--json"])
    if data is None or not isinstance(data.get("comments"), list):
        return None
    out = []
    for c in data["comments"]:
        side = "old" if c.get("oldRange") and not c.get("newRange") else "new"
        rng = c.get(f"{side}Range") or []
        out.append({"id": c.get("noteId"), "parent": c.get("parentId"),
                    "source": c.get("source"), "author": c.get("author") or "",
                    "file": c.get("filePath") or "", "side": side,
                    "line": rng[0] if rng else None, "end": rng[-1] if rng else None,
                    "text": (c.get("body") or "").strip(), "at": c.get("createdAt") or ""})
    for n in _unlisted(session_id, {n["id"] for n in out}):
        file, side, line, text, nid = _kept_tuple(n)
        out.append({"id": nid, "parent": n.get("parentId"), "source": n.get("source", "user"),
                    "author": n.get("author") or ("user" if _is_users(n) else ""),
                    "file": file, "side": side, "line": line, "end": n.get("end"),
                    "text": text, "at": n.get("createdAt") or ""})
    return sorted(out, key=lambda n: n["at"])


def _added(argv):
    """The id of the note a `comment add` created, or None."""
    data = _json(argv)
    return ((data or {}).get("result") or {}).get("commentId")


def reply(session_id, parent_id, text, author="agent"):
    """Answer a note in its own thread, under it in the window; the new note's id, or None
    if it did not land."""
    return _added(["hunk", "session", "comment", "add", session_id, "--reply-to", parent_id,
                   "--summary", text, "--author", author, "--json"])


def add_note(session_id, path, line, text, author, side="new"):
    """Start a thread on `line` of `path`, on its new or old side; its id, or None.

    Whatever `author` says, the viewer records the note as the agent's: it has no way to
    create one as the user's, and only the user can edit or delete it in the window. A
    line no hunk of the loaded diff shows is refused, so a note can only go where the
    reader can see it.
    """
    return _added(["hunk", "session", "comment", "add", session_id, "--file", path,
                   f"--{side}-line", str(line), "--summary", text, "--author", author,
                   "--json"])


def mark(session_id, key):
    """A value recorded on the session's tmux window by `set_mark`, or "".

    On the window for the same reason as the owner mark: what it records is only true for
    as long as the window exists.
    """
    pane = next((_pane_of(s) for s in sessions() if s.get("sessionId") == session_id), None)
    if not pane:
        return ""
    rc, out = _run(["tmux", "show-options", "-w", "-t", pane, "-v", key])
    return out.strip() if rc == 0 else ""


def set_mark(session_id, key, value):
    pane = next((_pane_of(s) for s in sessions() if s.get("sessionId") == session_id), None)
    if not pane:
        return False
    rc, _ = _run(["tmux", "set-option", "-w", "-t", pane, key, value])
    return rc == 0


def clear_all_notes(session_id):
    """Drop every note, the user's included. Only for a review the user has signed off:
    once the window moves on, notes would re-anchor by line number onto unrelated code."""
    rc, _ = _run(["hunk", "session", "comment", "clear", session_id, "--all", "--yes",
                  "--json"])
    return rc == 0


def _header_path(rest):
    """The file a `diff --git` line is about, given everything after that prefix.

    Always consulted, but only load-bearing for a file whose diff has no `+++`/`---`
    headers to override it — a BINARY file, which would otherwise appear in the summary
    with no name at all. The line carries both paths, so the prefixed form is split on its
    ` b/`, and the prefix-less form (`diff.noprefix`) on the fact that its two halves are
    the same path twice.
    """
    if rest.startswith("a/") and " b/" in rest:
        return rest.split(" b/", 1)[1]
    half = len(rest) // 2
    if rest[:half].strip() == rest[half:].strip():
        return rest[:half].strip()
    return rest.rsplit(" ", 1)[-1]


_C_ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}


def _unquoted(path):
    """A diff header's path as the file is named. git quotes one holding a special or (under
    its default `core.quotePath`) a non-ASCII character — `"b/caf\\303\\251.txt"` — with C
    escapes, octal for each byte of UTF-8; anything unquoted is returned as it is."""
    if len(path) < 2 or path[0] != '"' or path[-1] != '"':
        return path
    body, out, i = path[1:-1], bytearray(), 0
    while i < len(body):
        c = body[i]
        if c != "\\" or i + 1 == len(body):
            out += c.encode("utf-8")
            i += 1
        elif body[i + 1] in "01234567":
            out.append(int(body[i + 1:i + 4], 8) & 0xFF)
            i += 4
        else:
            out.append(_C_ESCAPES.get(body[i + 1], ord(body[i + 1])))
            i += 2
    return out.decode("utf-8", errors="replace")


# Mirrors skills/review-mr/scripts/findings.py's `_gl_compare` churn count, and stays
# separate on purpose: that one walks GitLab's already-per-file diff payloads, so it never
# has to find a file boundary and treats every `+++`/`---` as noise. This one parses one raw
# stream — `git diff`, or review-mr's `topic_patch` — where those same lines are sometimes a
# header and sometimes content. Same arithmetic, different problem — a change to either is worth checking against
# the other.
def diff_stat(diff):
    """[(path, added, removed)] for a unified diff, in the order it lists the files.

    Parsed from the text it is given, never re-derived with a second `git` call: the
    summary the user reads and the diff handed to the viewer then cannot describe two
    different states of a tree that is still being edited.

    The name is taken from the `+++`/`---` headers wherever they exist, and only from the
    `diff --git` line when they do not: that line carries BOTH paths, so reading it means
    guessing where one ends and the other begins (see `_header_path`), while the headers
    each carry exactly one. `---` arrives first and `+++` overrides it, so a rename lands
    under its new name and a deletion — whose `+++` is `/dev/null` — keeps its old one.
    """
    files, in_body = [], False
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            files.append([_header_path(line[11:].strip()), 0, 0])
            in_body = False
        elif line.startswith("diff --cc ") or line.startswith("diff --combined "):
            # A COMBINED diff — what `git diff` emits for an unmerged path, which
            # `rebase --autosquash` produces the moment a fixup conflicts, squarely inside
            # rework-mr's own flow. Without this the stanza is not a file boundary at all:
            # it vanishes from the summary and its `---`/`+++` headers are charged to the
            # previous file as a removal and an addition. Its body uses two prefix columns,
            # so the +/- counts below are approximate — but a named file with rough counts
            # beats a missing one, and the viewer's own count will disagree and force the
            # inline shape anyway.
            files.append([line.split(" ", 2)[2].strip(), 0, 0])
            in_body = False
        elif not files:
            continue
        elif line.startswith("@@"):
            # Headers only exist before the first hunk. Past it every `+++`/`---` is
            # CONTENT: a diffed file whose own line starts with `++` or `--` — a patch
            # fixture, this repo's own docs — arrives here looking exactly like a header,
            # and would otherwise rename the file to whatever that line said.
            in_body = True
        elif not in_body and (line.startswith("+++ ") or line.startswith("--- ")):
            path = _unquoted(line[4:].strip())
            # `---` arrives first and sets the name; `+++` then overrides it, so a rename
            # is reported under its new path. A deletion's `+++` is /dev/null, skipped,
            # which is what leaves the `---` name standing.
            if path != "/dev/null":
                files[-1][0] = path[2:] if path[:2] in ("a/", "b/") else path
        elif line.startswith("+"):
            files[-1][1] += 1
        elif line.startswith("-"):
            files[-1][2] += 1
    return [(p or "?", a, r) for p, a, r in files]


def stat_rows(stat):
    """A diff's per-file counts as `path:  +a −r` rows (lib/diffsize.py's `counts`), padded
    to the longest path.

    Fenced as YAML by the callers: a pasted block's only available colour is whatever the
    reader's markdown renderer gives the fence language — it travels through a model
    message, so terminal escapes cannot survive it — and read as YAML each row is a key and
    a value, which tints the path apart from its counts. The pad is capped so one deeply
    nested file cannot push every count off the far side of a terminal.
    """
    width = min(max((len(p) for p, _, _ in stat), default=0), 60)
    digits = max((len(str(a)) for _, a, _ in stat), default=0)
    return [f"{p + ':':<{width + 2}} {diffsize.counts(a, r, digits)}" for p, a, r in stat]
