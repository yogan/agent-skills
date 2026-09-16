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
"""
import json
import os
import shutil
import subprocess

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


def in_tmux():
    return bool(os.environ.get("TMUX"))


def owner_id():
    """This agent session's identity, used to tell its window from another agent's.

    Falls back to the pane the agent itself runs in: two agents cannot share a pane, so
    that is still a genuine per-agent identity where the session id is not exported.
    """
    return os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("TMUX_PANE") or ""


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


def spawn_session(repo, label, owner, sleep=None):
    """Open a viewer for `repo` in a new background tmux window and return its session id.

    The window is created first and marked before the viewer is looked for, so the mark is
    in place no matter how the startup race resolves, and the new session is identified by
    the pane tmux just told us about rather than by "whichever session appeared" — which
    would be wrong the moment two agents start a window at the same time.
    """
    if sleep is None:
        import time
        sleep = time.sleep
    # `-a` against the agent's own window puts the viewer immediately to its right,
    # shifting the rest along, instead of at the far end of the window list. The two are
    # read together constantly — switch over, read the diff, switch back — and on a busy
    # tmux the far end can be six windows away. Dropped when the window cannot be
    # identified, which only leaves the placement to tmux's default.
    here = _window_of_pane(os.environ.get("TMUX_PANE") or "")
    place = ["-a", "-t", here] if here else []
    rc, out = _run(["tmux", "new-window", "-d", "-P", "-F", "#{pane_id}"] + place
                   + ["-n", label, "-c", repo, "hunk diff"])
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


def reload_working_diff(session_id):
    """Swap the session onto the repository's current working diff; return how many files
    it loaded, or None if the reload did not happen.

    The count is the caller's proof that the window really holds the diff it is about to
    point at — hunk reads the working tree itself rather than taking ours, so the two can
    legitimately be compared and must agree.

    `--exclude-untracked` is what makes them comparable at all: the viewer lists untracked
    files by default and `git diff` never does, so a single new file in the tree — routine
    while reworking — put the two counts permanently out of step, and the mismatch check
    then fell back to an inline diff every time. The window must show exactly the change
    the message summarises, so it is the viewer that is narrowed to git's view.
    """
    data = _json(["hunk", "session", "reload", session_id, "--json", "--", "diff",
                  "--exclude-untracked"])
    if not data:
        return None
    return (data.get("result") or {}).get("fileCount")


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


def add_notes(session_id, notes):
    """Anchor the agent's rationale to lines of the loaded diff. `notes` are dicts of
    `filePath` plus one of `newLine`/`oldLine`, and `summary`.

    Raises ValueError above `MAX_NOTES` rather than quietly dropping the extras: a cap
    that silently truncates teaches nothing, and the point is for the caller to have
    chosen which few notes earn their place.
    """
    if len(notes) > MAX_NOTES:
        raise ValueError(
            f"{len(notes)} notes for one topic, at most {MAX_NOTES} are allowed. A note "
            "belongs only where the change deviates from the agreed plan, does more than "
            "was asked, or is not obvious from the diff; everything else is already "
            "visible in the diff itself.")
    if not notes:
        return 0
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
    return out


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
    if not in_tmux() or not installed():
        return None
    owner = owner or owner_id()
    if not owner:
        return None
    sid = find_session(repo, owner) or spawn_session(repo, label, owner, sleep=sleep)
    if not sid:
        return None
    # Before the reload, not after: clearing is addressed by note id, and a reload can
    # re-anchor or drop a note whose line no longer exists, leaving nothing to clear.
    clear_agent_notes(sid)
    # The file count first because the reload reports it for free, and it also tells a
    # reload that did not happen at all (None) from one that loaded something else.
    if reload_working_diff(sid) != len(expect_stat):
        return None
    if loaded_stat(sid) != sorted(tuple(f) for f in expect_stat):
        return None
    where = window_of(sid)
    return (sid, where) if where else None
