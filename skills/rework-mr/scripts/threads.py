#!/usr/bin/env python3
"""Fetch MR review threads, reconcile them against a persistent per-MR topic
file, and render them as markdown (which the agent chat styles).

State lives at ~/.claude/rework-mr/<project-slug>--mr<iid>/topics.json — keyed by
project + MR iid, so it survives across sessions (the re-review cycle spans days)
and never collides with another MR's rework. Two sessions on the *same* MR can
race; that's the only unhandled case.

Status:
  ● done          — every thread resolved by the reviewer  (mechanical)
  ✎ reply-pending — code already fixed AND pushed (a `diff_url` is stored); only the
                    thread reply is left. Derived, so a resume never re-implements it.
  ○ open          — your turn: reviewer spoke last, OR you spoke last but haven't
                    addressed it yet and nothing is pushed (the default — never hides work)
  ◐ waiting       — you fully addressed it (replied after pushing / gave a complete answer)
                    and it only needs the reviewer now. Set via `set <t> --state waiting`.

Subcommands:
  sync        fetch + reconcile, render the overview           (default; also for "status")
  todo        fetch + reconcile, render only what needs you (open + reply-pending)
  present     overview table + the current topic's comment  (the opener; no fetch)
  bodies      print each open thread's first and last note (to summarize from; no fetch)
  plans       print recorded decisions/plans for open topics  (resume; no fetch)
  quote <t>   a topic in full: the code the comment is anchored to (the reviewer's own
              line range when they marked one), then the whole thread — original + every
              reply  (no fetch). `--focus` makes it the current topic (lib/focus.py);
              a topic that is not current is shown for research only
  url <t>     direct URL(s) to the topic's thread (to click & post)  (no fetch)
  reply-view <t>  code + thread + your drafted reply + URL, one paste  (no fetch)
              `--refine` drops the code and thread — for re-showing a reworded draft
              on a topic whose context is already on screen, and unchanged since
  reply <t>   the drafted reply BODY only — the payload for clip.sh / glab
  set <t> --reply -   store a reply body from stdin (quoted heredoc; never a
              scratch file — see reply_body())
  set <t> …   update a topic's fields (state open|waiting, summary, decision, plan,
              start-sha, diff-url, reply)
  merge <into> <o…>   fold other topics' threads into <into>
  path        print the state-file path
  change-view <t> [file]  render a change illustration — reads the change from stdin
              (the documented path: no file, so no protected-path prompt) or from FILE
  diff-view <t>   render a working diff read from stdin (diff-view.sh's body)
  hunk-notes  print the notes the USER left on the diff, and take them out of the viewer
  hunk-close  close the viewer window once the push has landed
  checks      whether this repository's checks are worked out; what to read where not
              (`--set -` stores the reading, JSON from stdin)
  verify <t>  run the checks on <t>'s fix: its hooks ahead of time, then the fast local
              checks of the parts of the repository it touches
  push <t>    commit the checked fix as a fixup, squash and force-push, store the diff URL
  check-handles   internal — used by guard-reply.sh, no MR context needed
"""

import argparse
import datetime
import glob
import hashlib
import json
import os
import re
import secrets
import shlex
import sys
import time

# Repo root, 4 levels up from skills/rework-mr/scripts/threads.py — needed so `lib/`,
# which lives outside this skill's own directory, is importable regardless of how this
# script is invoked (direct, or symlinked into ~/.claude/skills/).
_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from lib import change as change_of, checks, code_view, critical_manifest, diffsize, fix_refs, focus, hunk, posted, ui  # noqa: E402
from lib.ui import analysis_heading  # noqa: E402
from lib.hunk import diff_stat  # noqa: E402
from lib.gitlab import (
    api,
    api_post,
    context,
    current_user,
    remember_user,
    die,
    file_at,
    mr_base,
    mr_object,
    mr_status,
    mr_view,  # noqa: E402
    project_slug,
    run,
    versions,
    web_base,
)
from lib.mr_common import (
    mr_json,
    mr_news,
    topic_handles,
    DEFAULT_LANG,
    MR_LEVEL,
    TOPIC_ICON,
    first_name,  # noqa: E402
    hold,
    load,
    loc_md,
    num,
    reads_as,
    save as _save,
    short_summary,
    state_file,
    topic_for,
    tref,
)
from lib.fences import (  # noqa: E402
    FENCE_RE,
    LINK_ICON,
    YOU,
    block_lang,
    code_block,
    diff_info,
    fence,
    lang_for,
    looks_like_diff,
    note_md,
    quoted_fence,
    recount,
    segments,
    when_of,
)
from lib.snippet import MAX_BACKTRACK, open_construct  # noqa: E402

# Internal topic handles (t5, t6, t10 …) must never reach a GitLab comment. The pattern
# lives in lib/mr_common.py, shared with review-mr; guard-reply.sh reaches it through
# `check-handles` below instead of reimplementing it in bash.
find_handles = topic_handles


STATE_ROOT = os.path.expanduser("~/.claude/rework-mr")
# Single source for the three per-status lookups below — this diff previously
# needed to touch three separate dicts (plus the `keep` sets in `_rows`) just to
# add one status; keying off one table keeps that to one place.
STATUSES = {
    "reply_pending": (0, "✎", "reply-pending"),
    "open": (1, "○", "open"),
    "waiting": (2, "◐", "waiting"),
    "done": (3, "●", "done"),
}
STATUS_ORDER = {k: v[0] for k, v in STATUSES.items()}
GLYPH = {k: v[1] for k, v in STATUSES.items()}
WORD = {k: v[2] for k, v in STATUSES.items()}


def new_state(ctx, mr):
    return {
        "project": ctx["path"],
        "slug": ctx["slug"],
        "iid": mr["iid"],
        "mr_web_url": mr.get("web_url"),
        "title": mr.get("title"),
        "source_branch": mr.get("source_branch"),
        "threads": {},
        "topics": [],
    }


# Keys on a thread record that are OURS and must survive a fetch. Everything else in
# `state["threads"]` belongs to GitLab and is replaced on every sync (see sync()). Empty
# today — the local overlay lives on `topics` — but named so the reconcile stays a denylist
# of local fields rather than an allowlist of fetched ones, which is what rotted before.
# (review-mr's findings.py has the same constant, for the same reason.)
LOCAL_THREAD_FIELDS = ()


def _range_keys(pos, on_new):
    """`line_start`/`line_end` from a position's `line_range`, when it carries a usable one.

    Only stored when both ends resolve on the side the comment is on and the span is
    ordered; a partial or cross-side range is dropped rather than guessed at, and
    `render_code_context` then falls back to the single anchor line.
    """
    lr = pos.get("line_range") or {}
    key = "new_line" if on_new else "old_line"
    a = (lr.get("start") or {}).get(key)
    b = (lr.get("end") or {}).get(key)
    if not isinstance(a, int) or not isinstance(b, int) or a > b:
        return {}
    return {"line_start": a, "line_end": b}


def me_of(state):
    """Who the user is on GitLab, the last known answer when GitLab cannot say now."""
    return remember_user(state, current_user())


def resync(state, ctx, iid):
    """Fetch the threads and reconcile them, with the comments on the MR itself; who the
    user is back. None, and `state` untouched, while that was never known: without it their
    own replies would read as a reviewer's."""
    me = me_of(state)
    if not me:
        print("warning: GitLab could not say who you are (is `glab auth status` OK?): "
              "the threads are as they were", file=sys.stderr)
        return None
    mr_comments = []
    sync(state, fetch_threads(ctx, iid, me, mr_comments))
    state["mr_comments"] = mr_comments
    return me


def fetch_threads(ctx, iid, me, mr_comments=None):
    """Every resolvable thread on the MR, keyed by discussion id. A list passed as
    `mr_comments` also receives the comments made on the MR itself, outside any thread
    (they are not resolvable, so no topic is made of them, but they are part of the review
    and the review-ui panel shows them)."""
    disc = api(
        f"projects/{ctx['enc']}/merge_requests/{iid}/discussions?per_page=100",
        paginate=True,
    )
    out = {}
    for d in disc:
        notes = [n for n in (d.get("notes") or []) if not n.get("system")]
        if not notes:
            continue
        first = notes[0]
        if not first.get("resolvable"):
            if mr_comments is not None:
                mr_comments += [_note(n, me) for n in notes]
            continue
        resolvable = [n for n in notes if n.get("resolvable")]
        resolved = bool(resolvable) and all(n.get("resolved") for n in resolvable)
        pos = first.get("position") or {}
        last = notes[-1]
        last_user = (last.get("author") or {}).get("username")
        # Which SIDE of the diff the comment hangs on decides both the path and the blob
        # to read the code from: a comment on a line the MR removed only exists in the
        # old file at the diff's base. Pairing new_line with old_path (or with the head
        # sha) renders unrelated lines.
        on_new = pos.get("new_line") is not None
        out[d["id"]] = {
            "author": (first.get("author") or {}).get("name")
            or (first.get("author") or {}).get("username"),
            "file": (pos.get("new_path") if on_new else pos.get("old_path"))
            or pos.get("new_path")
            or pos.get("old_path"),
            "line": pos.get("new_line") if on_new else pos.get("old_line"),
            "side": "new" if on_new else "old",
            # GitLab's `line_range` is the reviewer's actual selection ("Comment on lines
            # +12 to +22"). `new_line` alone is only its END, so a comment marking a whole
            # function used to render as its closing brace plus whatever followed.
            **_range_keys(pos, on_new),
            # The exact blobs the position names, so `quote` can show the code the reviewer
            # actually pointed at instead of guessing against a working tree that may have
            # moved on since.
            "head_sha": pos.get("head_sha"),
            "base_sha": pos.get("start_sha") or pos.get("base_sha"),
            "body": first.get("body"),
            "resolved": resolved,
            "url": f"{ctx['web']}/-/merge_requests/{iid}#note_{first.get('id')}",
            "note_count": len(notes),
            "last_author": (last.get("author") or {}).get("name") or last_user,
            "last_body": last.get("body"),
            "awaiting": "reviewer" if (me and last_user == me) else "you",
            # full thread in order, so `quote` can show the whole discussion
            # (original + every reply), not just first + last.
            "notes": [_note(n, me, f"{ctx['web']}/-/merge_requests/{iid}#note_{n.get('id')}")
                      for n in notes],
        }
    return out


def _note(n, me, url=None):
    """One comment as the views and the review-ui panel read it: who wrote it (and
    whether that is you), when, what it says, and its link."""
    username = (n.get("author") or {}).get("username")
    return {
        "author": (n.get("author") or {}).get("name") or username,
        "body": n.get("body"),
        "at": n.get("created_at"),
        "mine": bool(me) and username == me,
        "url": url,
    }


def next_tid(state):
    """review-mr's findings.py has a `next_tid` that looks the same but isn't: it
    persists a `seq` counter so a topic id is never reused once freed by a drop/merge.
    This one starts the scan from 1 every time, so a dropped `t3` CAN be reused — a real
    behavioral gap, not something to silently unify; worth closing here if it's ever
    seen to bite. See CLAUDE.md's "Sharing vs. duplication"."""
    used = {t["id"] for t in state["topics"]}
    n = 1
    while f"t{n}" in used:
        n += 1
    return f"t{n}"


def topic_status(state, t):
    """done = reviewer resolved it. Otherwise: if the reviewer spoke last it is
    your turn — but distinguish `reply_pending` (the code is already fixed AND
    pushed, only the thread reply is left) from `open` (still needs work), via
    the stored `diff_url` (set only after a push). Without that, a resume can't
    tell an already-implemented topic from a merely-planned one and re-implements
    it. If YOU spoke last it is ambiguous — you may have fixed it (waiting) or
    merely acknowledged it (still open) — so it defaults to open and only shows
    'waiting' when semantically classified via `set --state waiting`.

    review-mr's findings.py's `topic_status` does the equivalent job there, with a
    genuinely different status vocabulary (no `reply_pending`/`waiting` distinction
    here has a counterpart in its ack/wontfix overlay). It's the root of why so much
    downstream rendering (_rows, render_table, render_bodies, render_present) stays
    duplicated rather than shared — see CLAUDE.md's "Sharing vs. duplication"."""
    thr = [state["threads"].get(x) for x in t["thread_ids"]]
    thr = [x for x in thr if x]
    if not thr:
        return "reply_pending" if t.get("diff_url") else "open"
    if all(x["resolved"] for x in thr):
        return "done"
    if t.get("state") == "waiting":
        # you fully addressed it and marked it so; sync clears this back to open
        # if the reviewer has since re-commented, so a surviving 'waiting' is real
        return "waiting"
    unresolved = [x for x in thr if not x["resolved"]]
    if any(x.get("awaiting") == "you" for x in unresolved):
        # reviewer spoke last → your turn, even if a diff_url is stored from an
        # earlier push: unlike `state`, diff_url is never cleared, so it must not
        # outrank fresh reviewer feedback or a re-comment would be hidden behind
        # a stale "reply_pending" forever.
        return "open"
    if t.get("diff_url"):
        # code for this topic is already fixed AND pushed (diff_url is set only
        # after a push) and the reviewer hasn't spoken since — only the reply is
        # left. Takes precedence over "you spoke last, no push", so a resume
        # never mistakes a pushed topic for unstarted work.
        return "reply_pending"
    return t.get("state") or "open"  # you spoke last, no push → LLM decides


def sync(state, live):
    """Reconcile the stored threads against what GitLab reports.

    A live thread's record is REPLACED wholesale (bar the local fields above). This was an
    allowlist of fetched keys to copy over, and it rotted exactly as you'd expect: every
    field added to `fetch_threads` afterwards — `side`, `head_sha`, `base_sha`, and then the
    `line_range` keys — reached brand-new threads only, so an ongoing rework kept rendering
    from the old shape and `quote` fell back to "(working tree)" with no span, session after
    session. Replacing also drops keys the fetch no longer produces, which an `update()`
    would leave behind as a stale range.
    """
    for tid, rec in live.items():
        if tid in state["threads"]:
            local = {
                k: v
                for k, v in state["threads"][tid].items()
                if k in LOCAL_THREAD_FIELDS
            }
            state["threads"][tid] = {**rec, **local}
        else:
            state["threads"][tid] = rec
            state["topics"].append(
                {
                    "id": next_tid(state),
                    "thread_ids": [tid],
                    "summary": None,
                    "state": None,
                    "decision": None,
                    "plan": None,
                    "start_sha": None,
                    "diff_url": None,
                }
            )
    for tid in state["threads"]:
        if tid not in live:
            state["threads"][tid]["resolved"] = True
    # a reviewer note since your last one (or a reopen) makes any stored
    # "waiting" stale — clear it so the thread re-derives to open. One set while the
    # reviewer had the last word ("already handled", a comment that asks nothing) carries
    # what they had said then, and holds until they say more.
    for t in state["topics"]:
        thr = [state["threads"].get(x) for x in t["thread_ids"]]
        unresolved = [x for x in thr if x and not x["resolved"]]
        if any(x.get("awaiting") == "you" for x in unresolved):
            if t.get("waiting_on") and t["waiting_on"] == reviewer_mark(state, t):
                continue
            t["state"] = None
            t.pop("waiting_on", None)


def _rows(state, scope, show_done=False):
    """Mirrors review-mr's findings.py's `_rows` — same shape (status/counts/ordering for
    render_table), but the `keep` sets below are this skill's own status vocabulary
    (open/reply_pending/...), not findings.py's (draft/needs_ack/...). Stays duplicated:
    see CLAUDE.md's "Sharing vs. duplication"."""
    topics = state["topics"]
    st = {t["id"]: topic_status(state, t) for t in topics}
    counts = {s: sum(1 for t in topics if st[t["id"]] == s) for s in GLYPH}
    ordered = sorted(topics, key=lambda t: (STATUS_ORDER[st[t["id"]]], num(t["id"])))
    keep = (
        {"open", "reply_pending"}
        if scope == "mine"
        else ({"open", "reply_pending", "waiting"} | ({"done"} if show_done else set()))
    )
    return st, counts, [t for t in ordered if st[t["id"]] in keep]


def _loc(state, t):
    """Mirrors review-mr's findings.py's `_loc` — same `basename:line`, and the same
    "" when no thread of the topic has a diff position (a discussion on the merge
    request itself has none). Stays duplicated: this table also carries the count of
    the topic's further threads, which review-mr's does not."""
    thr = [state["threads"].get(x, {}) for x in t["thread_ids"]]
    return next(
        (
            f"{os.path.basename(x['file'])}:{x.get('line') or ''}"
            for x in thr
            if x.get("file")
        ),
        "",
    )


def _loc_cell(state, t):
    """The Location column: the location, plus how many further threads this topic
    folds in. The count sits OUTSIDE the code span — it is not part of the path, and
    a topic whose threads are all MR-level has no path to put it inside."""
    n = len(t["thread_ids"])
    return loc_md(_loc(state, t)) + (f" (+{n - 1})" if n > 1 else "")


def needs_summary(state, t):
    """Whether nobody has written this topic a one-line title yet.

    Derived rather than stored: every topic here starts life as a reviewer's thread, so
    an absent `summary` can only mean none was authored. (review-mr keeps a `needs_title`
    flag because it also creates topics itself, and has to tell those two apart.)

    A `done` topic is exempt. It is closed, nothing acts on it, and its row only appears
    with `sync --all` — demanding a title for work that is over would make the opener of
    a long-running rework a chore with no reader.
    """
    return not t.get("summary") and topic_status(state, t) != "done"


def needs_summary_warning(state, t):
    """Said wherever a topic is rendered as a heading, so a missing title cannot pass as
    a finished one. The heading shows the handle alone in that case — better than the raw
    comment, and still nothing the user can recognise the topic by."""
    if not needs_summary(state, t):
        return None
    return (
        f"⚠️ **needs an English summary** — `{t['id']}` has no authored `summary` "
        f'yet. Read the thread and run `set {t["id"]} --summary "<short English '
        f'line>"` before working this topic.'
    )


def summary_language_warning(state, topics):
    """Name the topics whose summary was written in the thread's language.

    Mirrors review-mr's function of the same name — same rule, and the detection is
    shared (`reads_as`) — but the sentence differs, because what the other language
    governs differs: there it is the comment a reviewer is about to post, here it is the
    reply into a thread. Stays duplicated for that reason; see CLAUDE.md's "Sharing vs.
    duplication".

    It is checked rather than only documented because the summary sits right next to a
    German thread the whole time, which is exactly the pull that produced a review with
    all ten summaries in the wrong language.
    """
    ids = [
        t["id"]
        for t in topics
        if t.get("summary") and reads_as(t["summary"], DEFAULT_LANG)
    ]
    if not ids:
        return None
    return (
        f"⚠️ **summaries must be English** — {', '.join(tref(x) for x in ids)} "
        f"read as {DEFAULT_LANG}. The thread's own language governs one thing, the "
        f"body of a reply you post into it; the table and every topic heading are "
        f'English. Reword each with `set <t> --summary "<short English line>"`, '
        f"then re-render."
    )


def ui_state(state, t, status=None):
    """A topic's state in the vocabulary both skills share where the review-ui mod draws
    (lib/ui.py). Your move on a thread nobody answered is `new`; once you have replied in it
    and the reviewer wrote back, `answered`. Mirrors findings.py's `ui_state`, over this
    skill's own statuses."""
    s = status or topic_status(state, t)
    if s == "open":
        replied = any(n.get("mine") for th in t["thread_ids"]
                      for n in state["threads"].get(th, {}).get("notes") or [])
        return "answered" if replied else "new"
    return {"reply_pending": "draft"}.get(s, s)


def title_md(state, t, summ):
    """A topic's title, bold, and where the mod draws, its state icon in front (the icon is
    the topic marker there). Mirrors findings.py's `title_md`."""
    if ui.drawn():
        # The topic's label as the panel and the band draw it: state, then handle.
        return (f"{ui.STATE_ICON[ui_state(state, t)]} **{t['id']}**"
                + (f" **{summ}**" if summ else ""))
    return f"**{tref(t['id'])}" + (f" — {summ}**" if summ else "**")


def render_table(state, scope="all", show_done=False):
    st, counts, shown = _rows(state, scope, show_done)
    out = [f"**MR !{state['iid']}** — {state.get('title') or ''}", ""]
    if shown:
        out += ["| Status | Topic | Location | Summary |", "|---|---|---|---|"]
        for t in shown:
            tid = t["id"]
            s = st[tid]
            summ = short_summary(state, t, width=72).replace("|", "\\|")
            if needs_summary(state, t):
                summ = f"✍️ _needs summary:_ {summ}"  # raw quote, not an authored title
            cell = ui.state_cell(ui_state(state, t, s)) if ui.drawn() else f"{GLYPH[s]} {WORD[s]}"
            out.append(
                critical_manifest.mark(
                    f"| {cell} | {tref(f'**{tid}**')} "
                    f"| {_loc_cell(state, t)} | {summ} |"
                )
            )
    else:
        out.append(
            "_✓ nothing needs you — open threads are waiting on the reviewer_"
            if scope == "mine"
            else "_✓ no open threads_"
        )
    warn = summary_language_warning(state, shown)
    if warn:
        out += ["", warn]
    footer = [f"{counts['done']} of {len(state['topics'])} topics done"]
    if counts["reply_pending"]:
        footer.append(f"{counts['reply_pending']} pushed, reply pending")
    if counts["waiting"]:
        footer.append(f"{counts['waiting']} waiting for reply")
    return "\n".join(out + ["", "_" + " · ".join(footer) + "._"])


# ------------------------------------------------------- fences & code context


def render_change(text, path=None):
    """A change illustration as markdown that actually renders: every code block fenced
    with a language so it is highlighted (or diff-coloured), no nesting, nothing dangling.

    The model writes these freehand, so all three shapes turn up: a bare diff, a bare
    snippet, or prose interleaved with its own ```diff blocks. Already-fenced content is
    passed through (wrapping it again is the bug above) with untagged fences given a
    language; unfenced content is wrapped once.
    """
    lines = (text or "").rstrip("\n").splitlines()
    if not any(FENCE_RE.match(ln) for ln in lines):
        return fence(text, "diff" if looks_like_diff(text) else lang_for(path))
    out = []
    for kind, info, body in segments(lines):
        if kind == "text":
            out += body
        else:
            out.append(code_block("\n".join(body), info, path))
    return "\n".join(out).strip("\n")


def _git(*args):
    """git stdout, or None for anything that goes wrong — a missing object, no repo, a
    file git cannot decode. `quote` must degrade to "no code shown", never crash: it is
    the view the whole flow runs through."""
    try:
        r = run(["git", *args])
    except (OSError, UnicodeDecodeError):
        return None
    return r.stdout if r.returncode == 0 else None


def _repo_file(path):
    """The working-tree text of a repo-relative path, read from the repo ROOT — every
    shell call tends to reset cwd, so a relative open() would read the wrong place."""
    root = (_git("rev-parse", "--show-toplevel") or "").strip()
    if not root:
        return None
    try:
        with open(os.path.join(root, path), encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _blob_text(sha, path):
    if not (sha and path):
        return None
    return _git("show", f"{sha}:{path}")


# Mirrors review-mr's findings.py's `_comment_source`: the same side-aware pick of the
# blob, read here from the repository you are reworking, there from the review worktree.
def _comment_source(state, x):
    """The file as the comment saw it, as lines — what a ```suggestion's offsets count
    from — or None. Read only when the thread holds a suggestion, since it costs a call:
    from this repository when it still has that commit, else from GitLab, which keeps it
    after your own fixup and force-push have replaced it. Never the working tree: that is
    your own edit, not the lines the reviewer proposed to replace."""
    bodies = [n.get("body") or "" for n in x.get("notes") or []] + [
        x.get("body") or "",
        x.get("last_body") or "",
    ]
    if not any("```suggestion" in b for b in bodies):
        return None
    on_new = (x.get("side") or "new") == "new"
    sha = x.get("head_sha") if on_new else x.get("base_sha")
    text = _blob_text(sha, x.get("file"))
    if text is None:
        text = file_at(state.get("project"), sha, x.get("file"))
    return text.splitlines() if text is not None else None


COMMENT_LINE = re.compile(r"^\s*(//|/\*|\*|#|--|<!--)")
MARK_SPAN, MARK_LINE = "┃", "►"
# One-line anchors get a symmetric window; a range the reviewer selected already IS the
# region, so it only needs a little air above (the signature or doc comment it hangs off)
# and almost none below — that trailing context is what turned a comment on one function
# into a listing of the next declaration.
CTX_SINGLE, CTX_ABOVE, CTX_BELOW = 6, 3, 1
DOC_PULL, MAX_BODY = 8, 60


def _anchor_span(x, n, last):
    """(start, end) of the code the comment is about, and whether it is a real span.

    GitLab reports a multi-line comment as `line_range` start→end with `new_line` as the
    END, so the range is the only way to know the reviewer marked "lines +12 to +22" rather
    than line 22. The range is sanity-checked before it is trusted: inside the file, and
    containing the anchor line — if it doesn't, the position and the range disagree and the
    anchor is the safer of the two.
    """
    a, b = x.get("line_start"), x.get("line_end")
    if not isinstance(a, int) or not isinstance(b, int):
        return n, n, False
    if not (1 <= a <= b <= last and a <= n <= b):
        return n, n, False
    return a, b, b > a


def render_code_context(x, base=None, drift=True):
    """The code the reviewer's comment hangs on, ready to show ABOVE their note.

    Without it the user is asked to judge a comment about code they cannot see, and the
    agent's research reads as unsourced assertion — the reviewer says "ist äquivalent zu …"
    and the reply is a verdict about lines nobody displayed.

    Read from the exact blob the comment is anchored to (`git show <sha>:<path>` for the
    side the position names), NOT from the working tree: the line number belongs to that
    version, so once the author starts fixing things a working-tree read would silently
    render unrelated lines. Where your working tree has since changed the lines the comment
    is on, what changed on them follows as a diff; a change elsewhere in the file, or one
    that only moved them, says nothing (`_since_comment`). Falls back to the working tree
    for threads stored before the shas were recorded. None when there is nothing
    trustworthy to show. `drift=False` leaves that diff to the caller (`fix_since`).

    What gets shown follows the reviewer: their selected range first (marked `┃`), else the
    single anchor line (marked `►`). The marker is never a `►` on one line of a span — it
    claimed a precision the position did not have, and pointed at a closing brace.
    """
    path, line = x.get("file"), x.get("line")
    if not path or not line:
        return None  # not a diff comment (or no position)
    try:
        n = int(line)
    except (TypeError, ValueError):
        return None
    on_new = (x.get("side") or "new") == "new"
    sha = x.get("head_sha") if on_new else x.get("base_sha")
    text, source = _blob_text(sha, path), f"as reviewed, `{(sha or '')[:8]}`"
    if text is None:
        text, source = _repo_file(path), "working tree"
    if text is None:
        return None
    lines = text.splitlines()
    if n > len(lines):
        return None  # anchor outside the file → show nothing
    a, b, span = _anchor_span(x, n, len(lines))
    above, below = (CTX_ABOVE, CTX_BELOW) if span else (CTX_SINGLE, CTX_SINGLE)
    lo, hi = max(1, a - above), min(len(lines), b + below)
    # Grow upward through a doc comment that sits directly on top: `/** … */` or a `#` block
    # explains the marked code, and stopping one line short of it is the difference between
    # context and a fragment. A blank line ends the block — that comment belongs to
    # something else.
    while lo > 1 and COMMENT_LINE.match(lines[lo - 2]) and a - lo < above + DOC_PULL:
        lo -= 1
    # The window above is a fixed line count with no notion of syntax state — see
    # lib.snippet.open_construct's docstring for what that breaks (a highlighter given
    # just this window can misread a docstring/comment/template-literal's CLOSING
    # delimiter as an OPENING one) and review-mr's findings.py's code_snippet, which
    # has the same hazard and routes through the same shared scanner.
    lang = lang_for(path)
    opened = open_construct(lines, lo)
    if opened:
        if lo - opened[1] <= MAX_BACKTRACK:
            lo = opened[1]
        else:
            lang = ""
    where = f"{path}:{a}–{b}" if span else f"{path}:{n}"
    drift = _since_comment(sha, path, a, b) if drift and on_new and source != "working tree" else []
    # The MR as the comment saw it is its diff up to the commit it was posted on, whichever
    # side the comment is on.
    diff = _diff_context(path, x.get("head_sha"), base, "new" if on_new else "old", lo, hi, a, b)
    if diff:
        out = [f"_Code the comment is on — `{where}` ({source}):_", "", diff]
        return "\n".join(out + drift)
    body = []
    for i in range(lo, hi + 1):
        if hi - lo + 1 > MAX_BODY and lo + MAX_BODY - 10 < i < hi - 8:
            if body and body[-1] is not None:  # collapse the middle of a huge span once
                body.append(None)
            continue
        body.append(i)
    width = len(str(hi))
    rendered = []
    for i in body:
        if i is None:
            rendered.append(f"  {'…'.rjust(width)} | … {b - a + 1} lines in total …")
            continue
        mark = (
            MARK_SPAN
            if span and a <= i <= b
            else (MARK_LINE if not span and i == n else " ")
        )
        rendered.append(f"{mark} {str(i).rjust(width)} | {lines[i - 1]}")
    out = [
        f"_Code the comment is on — `{where}` ({source}):_",
        "",
        fence("\n".join(rendered), lang),
    ]
    return "\n".join(out + drift)


def _since_comment(sha, path, first, last):
    """What your working tree changed on lines `first`..`last` of `path` since commit `sha`
    (the code the comment is on), as lines to put under the code: nothing where those lines
    are untouched, which is also the answer whenever git cannot tell."""
    root = (_git("rev-parse", "--show-toplevel") or "").strip()
    if not (root and sha):
        return []
    # A file renamed since (another topic's fix, say) is followed, and said in a line: the
    # rename alone is no change to the comment's lines.
    now = code_view.now_at(root, sha, path)
    moved = [f"_Since renamed to `{now}`._"] if now != path else []
    hit, _, _ = code_view.touched(code_view._diff_since(root, sha, path), first, last)
    lines = code_view.since_fence(root, sha, path, first, last) if hit else None
    if not lines:
        return ["", *moved] if moved else []
    path = now
    # Where the change stands: still in the working tree, or committed (and pushed, once the
    # reply comes) already, where "in your working tree" would send the reader looking for
    # files that are clean.
    pending = bool((_git("diff", "--no-color", "HEAD", "--", path) or "").strip())
    head = "Changed since the comment, in your working tree:" if pending else "Changed since the comment:"
    return ["", *moved, *([""] if moved else []), f"_{head}_", "", *lines]


def _diff_context(path, sha, base, side, lo, hi, a, b):
    """Lines `lo`..`hi` (of the comment's side) as the MR changed them at the commit the
    comment was on, lines `a`..`b` the ones it is on: the same diff the sidebar shows
    (`lib/code_view.py`), so code reads one way everywhere. None when the MR's diff base is
    not known or git cannot read that commit; the plain excerpt stands in then.

    Unlike the plain excerpt, a long selection is shown whole: cutting rows out of a diff
    would leave hunks whose numbers no longer add up."""
    root = (_git("rev-parse", "--show-toplevel") or "").strip()
    rows = code_view.rows_of(root, path, base, at=sha) if base and sha else None
    if not rows:
        return None
    slot = 2 if side == "new" else 1
    inside = [i for i, r in enumerate(rows, 1) if r[slot] is not None and lo <= r[slot] <= hi]
    at = code_view.rows_at(rows, side, a, b)
    if not (inside and at):
        return None
    lines = code_view.diff_fence({"file": path, "rows": rows, "at": at}, inside[0], inside[-1])
    return "\n".join(lines)


def render_change_view(tid, text, path=None):
    """The whole `change-preview.sh` block: header, the illustration, the `Agreed?`.

    Stateless on purpose — no glab call, no state file — so showing a change can never
    fail on MR resolution. `path` (optional) only supplies the fence language for an
    unfenced snippet that isn't a diff.
    """
    return "\n".join(
        [f"**Change ({tref(tid)}):**", "", render_change(text, path), "", "Agreed?"]
    )


# The closing line of both diff-view shapes, and the paste gate's signature for them. The
# alternatives to an ACK ride on this one line rather than following it, so the ask reads
# as a single line wherever it lands (the `fixup-ack` rule anchors it to a line start).
#
# They are spelled out because the ask alone reads as a yes/no: a user who has annotated the
# diff in the viewer window has no way of knowing from "ACK to fix up and push?" that saying
# so is a supported answer rather than an interruption.
ACK_ASK = "ACK to fix up and push? — or say what to change"


def _notice(text):
    """A one-line warning to sit just above the ACK question, or nothing.

    Marked critical, because it is the line that stops the user trusting an absence:
    the prose above the block says the agent flagged something on the diff, and this is
    the only thing that says it is not actually in the window.
    """
    if not text:
        return []
    critical_manifest.mark(text)
    return ["", text]


def render_diff_view(tid, diff, notice=""):
    """The whole `diff-view.sh` block: header, the working diff, the ACK question.

    Also stateless. The fence widens itself when the diff touches a file that contains
    fences (a markdown file, this repo's own docs) — otherwise those ``` lines would close
    the block and the ACK question would render inside the diff.

    This is the fallback shape, used whenever the diff could not be put in a viewer — see
    `render_diff_pointer`, which replaces it when it could. Its closing line offers one
    fewer answer than the pointer's: with no viewer window there is nothing to annotate.
    """
    return "\n".join(
        [f"**Diff ({tref(tid)}):**", "", fence(diff, "diff")]
        + _notice(notice)
        + ["", ACK_ASK + "."]
    )


def render_diff_pointer(tid, stat, where, notice=""):
    """The `diff-view.sh` block when the diff went to a viewer: what changed and how big,
    where to look at it, and the same ACK question.

    The per-file counts are the point of keeping anything in the message at all. They are
    what lets a small change be approved without switching windows, and they say how much
    there is to read before the user decides to — neither of which a bare "see window 9"
    can do. The fixup target and the summary of what changed are deliberately NOT here: the
    skill has the model state those in its own prose above this block, because only the
    model knows them.

    The rows are `hunk.stat_rows`, fenced as YAML — see there for why.
    """
    # Not marked critical here — `fence` marks every line it wraps, and marking twice puts
    # each row in the manifest twice.
    rows = hunk.stat_rows(stat)
    head = f"**Diff ({tref(tid)})** — {diffsize.of_stat(stat)} · {where}"
    # Marked critical explicitly, unlike the rows, which `fence` marks for us. This is the
    # ONLY line naming where the diff is, so under the hook's one-dropped-line tolerance it
    # was the one line a message could lose and still pass — leaving a list of filenames, a
    # closing question mentioning "that window", and no window named anywhere.
    critical_manifest.mark(head)
    return "\n".join(
        [head, "", fence("\n".join(rows), "yaml")]
        + _notice(notice)
        + ["", ACK_ASK + ", here or as notes on the diff in that window."]
    )


def _state_mtime(state_dir):
    """When this MR's rework was last touched: its `topics.json`, falling back to the
    directory only where the file is missing (a half-created state directory)."""
    try:
        return os.path.getmtime(os.path.join(state_dir, "topics.json"))
    except OSError:
        try:
            return os.path.getmtime(state_dir)
        except OSError:
            return 0


def _checked_out_branch():
    """The branch checked out here, or the branch being rebased where HEAD is detached
    mid-rebase (as the push's autosquash leaves it); "" where there is neither."""
    branch = (_git("branch", "--show-current") or "").strip()
    if branch:
        return branch
    for name in ("rebase-merge/head-name", "rebase-apply/head-name"):
        path = (_git("rev-parse", "--git-path", name) or "").strip()
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read().strip().removeprefix("refs/heads/")
        except OSError:
            continue
    return ""


def own_mr(slug):
    """The MR number of this checkout's rework, read off the local state alone: the MR whose
    source branch is the branch checked out, or None where no state says so.

    Every MR of a project shares the one state folder, and sessions on two of them run side
    by side, each in a worktree of its own. Picking the state touched last there handed a
    session its sibling's MR, whose topics then went onto its cards and whose state took its
    writes. A state that names another branch is therefore never taken; one that names none
    (written before the branch was kept) is taken, the newest first, only where no state
    names this branch.

    Ranked by each state FILE's mtime, never the directory's: `save()` rewrites
    `topics.json` in place, so a directory keeps the mtime it was created with."""
    dirs = glob.glob(os.path.join(STATE_ROOT, f"{slug}--mr*"))
    branch = _checked_out_branch()
    mine, unknown = [], []
    for d in dirs:
        named = (load(os.path.join(d, "topics.json")) or {}).get("source_branch")
        if not named:
            unknown.append(d)
        elif branch and named == branch:
            mine.append(d)
    pick = mine or unknown
    if not pick:
        return None
    # basename first: splitting the full path would read any `--mr` that happens to sit
    # in a directory name above it.
    return os.path.basename(max(pick, key=_state_mtime)).rsplit("--mr", 1)[1]


def window_label(default="diff"):
    """The name to give this agent's viewer window: its MR's number where it can be had
    locally (`own_mr`), the branch otherwise.

    Read off the state directory's own naming rather than by asking GitLab, and via
    `project_slug()` rather than `context()`, which would shell out to glab for the host's
    API scheme. `diff-view` is deliberately independent of glab and the network (see
    `main`), and a window's NAME is cosmetic enough that it must not be the thing that
    reintroduces a round-trip — or a hang, since that helper takes no timeout — per topic.
    """
    try:
        # Checked before the slug is derived rather than caught after: with no origin
        # remote `remote_url()` calls `die()`, which prints to stderr on the way out.
        # Catching the SystemExit would still leave that stray line in the terminal, on a
        # path where nothing is actually wrong.
        if (_git("remote", "get-url", "origin") or "").strip():
            iid = own_mr(project_slug())
            if iid:
                return TOPIC_ICON + "!" + iid
    except (Exception, SystemExit):  # noqa: BLE001 — no remote/state → use branch
        pass
    branch = (_git("rev-parse", "--abbrev-ref", "HEAD") or "").strip()
    return f"{TOPIC_ICON} {branch or default}"


def diff_view_block(tid, diff, plain=False, notes=()):
    """`diff-view.sh`'s whole output: the pointer block when the working diff could be put
    in a viewer window, the inline diff when it could not.

    Falling back is a normal outcome, not an error, and it is silent on purpose — no tmux,
    no viewer installed, a window the user closed, a viewer that came back holding a
    different set of files. What the user is asked to approve is the same either way; only
    the shape of the block changes, and an inline diff is a worse read, not a wrong one.

    `plain` forces it, and `diff-view.sh` passes it whenever the caller narrowed the diff
    with its own git arguments: the viewer is reloaded from the working tree rather than
    from the text piped in here, so a narrowed diff and the window would be showing two
    different things.

    `notes` are applied only once the viewer actually holds the diff, because the viewer
    validates each one against the loaded files and would reject it otherwise. They are
    dropped without comment when the diff went inline instead — there is nowhere to anchor
    a note in a fenced block, and the rationale belongs in the prose around it there.
    """
    stat = diff_stat(diff)
    # An empty diff is refused rather than rendered. Both shapes would otherwise ask for a
    # fixup+force-push ACK over a block showing nothing — and the gate cannot catch it,
    # because an empty fence marks no critical lines and the signature strings are still
    # there. It happens for real: the edits were staged (`git diff` is unstaged-only), or
    # the tree was already cleaned by a fixup and rebase, or the whole change is a new
    # untracked file.
    if not stat and not diff.strip():
        die(
            "`git diff` is empty — nothing to show and nothing to approve. The change is "
            "probably staged (`git diff` reads unstaged only: pass `-- --cached`), already "
            "committed, or entirely in untracked files. Never ask for the fixup ACK over an "
            "empty diff."
        )
    root = (_git("rev-parse", "--show-toplevel") or "").strip()
    if plain or not stat or not root:
        return render_diff_view(tid, diff)
    # Whether the notes actually landed is reported, never assumed. The batch is
    # all-or-nothing in the viewer, so one bad anchor drops all of them — and the prose
    # above this block has already told the user the agent flagged something.
    dropped = (
        "The notes could not be anchored on the diff — read the points in the prose "
        "above instead."
        if notes
        else ""
    )
    shown = hunk.show_working_diff(root, window_label(), stat)
    if not shown:
        return render_diff_view(tid, diff, dropped)
    if notes:
        try:
            landed = hunk.add_notes(shown[0], notes)
        except ValueError as exc:
            die(str(exc))
        if landed:
            dropped = ""
    return render_diff_pointer(tid, stat, f"tmux window {shown[1]}", dropped)


def hunk_note_lines(notes):
    """`hunk-notes`' report for what `hunk.user_notes` handed back.

    The side is named only for a note on a REMOVED line, where it changes what the number
    means: that line is gone from the file the model is about to edit, so the number alone
    would send it to whatever the change left in its place.
    """
    out = []
    for path, side, line, text, _nid in notes:
        where = f"{path}:{line}" if line else path
        out.append(f"{where}{' (on a removed line)' if side == 'old' else ''}: {text}")
    return out


def hunk_close_decision(notes):
    """(what to say, whether to close) for `hunk-close`, given `hunk.user_notes`' answer.

    Three outcomes, and only one of them closes the window. The dangerous pair is the other
    two: "no notes" and "could not ask" must never collapse together, because closing on
    the second destroys an unread request and the diff it was anchored to.
    """
    if notes is None:
        return (
            "Could not read the viewer's notes, so the window is left open rather than "
            "closed over something unread. Harmless — close it yourself, or it goes on "
            "the next push.",
            False,
        )
    if notes:
        return (
            "Notes were left in the viewer since the last read — the window is still "
            "open. Run `threads.py hunk-notes` and answer them; they are a new change "
            "on this topic, on top of the one just pushed.",
            False,
        )
    return (None, True)


def parse_note(text):
    """`FILE:LINE:TEXT` as the note payload the viewer wants, or None if it is malformed.

    Split from the left exactly twice, so a colon in the note's own prose is kept and only
    a colon in the FILE part (which git paths effectively never contain) could confuse it.
    """
    parts = (text or "").split(":", 2)
    if len(parts) != 3 or not parts[0].strip() or not parts[2].strip():
        return None
    try:
        line = int(parts[1])
    except ValueError:
        return None
    return {"filePath": parts[0].strip(), "newLine": line, "summary": parts[2].strip()}


def _digest(text):
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def render_quote(state, tid, remember=True):
    """A topic in full: the code the comment is anchored to, then the whole thread. All of
    it is the topic's context, which is why `--refine` drops this render entirely.

    `remember` records a digest of what this returned on the topic, so `reply-view
    --refine` can tell whether the context it is about to leave out is still the one the
    user is looking at. Off for the comparison itself (see `context_digest`), which must
    not overwrite the thing it compares against.
    """
    t = topic_for(state, tid) or die(f"no topic {tid}")
    summ = t.get("summary") or ""
    out = []
    for i, th in enumerate(t["thread_ids"]):
        x = state["threads"].get(th, {})
        # An MR-level thread carries no diff position at all, so there is no path to
        # print — `None:` is what an unguarded f-string produced there.
        path = f"{x['file']}:{x.get('line') or ''}" if x.get("file") else ""
        if i == 0:
            title = title_md(state, t, summ)
            out.append(f"{title} · {loc_md(path)}")
            for w in (
                needs_summary_warning(state, t),
                summary_language_warning(state, [t]),
            ):
                if w:
                    out.append(w)
        else:
            out.append(loc_md(path))
        # Drawn, each comment links itself, as review-mr's do.
        if x.get("url") and not ui.drawn():
            out.append(x["url"])
        # Code FIRST, comment second: the reviewer's note is about these lines, and a
        # verdict on code the user cannot see is unreviewable. Once the topic's fix is pushed,
        # what changed on them is that fix, which the card's steps link and the diff viewer
        # shows whole (`pushed_steps`): a diff of it here would be a second, partial copy.
        code = render_code_context(x, state.get("diff_base"), drift=not (ui.drawn() and t.get("diff_url")))
        if code:
            out += ["", code]
        # The file, anchor and the file as the comment saw it let a reviewer's
        # ```suggestion be fenced in the file's language, as a diff against what it replaces.
        f, ln, src = x.get("file"), x.get("line"), _comment_source(state, x)
        notes = x.get("notes")
        if notes:  # whole thread, in order
            # Drawn, each comment is one tight block, headed by its link and time: the
            # form the mod reads a comment in (and leaves out where the sidebar shows it).
            tight = ui.drawn()
            for n in notes:
                out += ["", note_md(n.get("author"), n.get("body"), f, ln, src, compact=tight,
                                    url=n.get("url") if tight else None,
                                    mine=tight and bool(n.get("mine")),
                                    when=when_of(n.get("at")) if tight else None)]
        else:  # pre-`notes` state: first + last only
            out += ["", note_md(x.get("author"), x.get("body"), f, ln, src)]
            if x.get("note_count", 1) > 1:
                skipped = x["note_count"] - 2
                if skipped > 0:
                    out += ["", f"_… {skipped} more …_"]
                out += ["", note_md(x.get("last_author"), x.get("last_body"), f, ln, src)]
        out.append("")
    # The context is what `--refine` leaves out: the header, the code and the thread. The
    # step's parts after it (the plan, the fix, the draft) are not, or a reworded draft would
    # read as a changed context and bring the whole topic back.
    context = "\n".join(out).strip()
    if remember:
        t["shown"] = _digest(context)
    if ui.drawn():
        out += _step_parts(state, t)
    return "\n".join(out).strip()


OUTCOME_WORD = {"fix": "Fix", "reply": "Reply only", "push-back": "Push back",
                "question": "Answer the question"}


def differs_now(t):
    """Whether a correction of the reply the user posted by hand waits: stored, and its
    draft not yet posted or recorded as posted (either ends it). Mirrors findings.py's."""
    return bool(t.get("post_differs") and t.get("reply"))


def _step_parts(state, t):
    """What a topic's card says beyond its thread, where the mod draws: the plan proposed
    (the agent's analysis, its recommendation, the change it would make), the plan agreed,
    the fix in the working tree, or the reply drafted, whichever step the topic is at."""
    st = step_of(state, t)
    out = []
    p = proposal_of(state, t)
    if st == "plan" and p:
        out += [analysis_heading(), "", ui.with_recommendation(p["why"].strip()), ""]
        if p.get("says") == "needs" and p.get("plan"):
            out += [ui.heading_md(ui.ASKED_ICON, f"Plan: {OUTCOME_WORD.get(p.get('outcome'), 'Fix')}"),
                    "", p["plan"].strip(), ""]
            body = _fenced_diff(p.get("change"))
            if body:
                out += [*_change_blocks(body), ""]
    elif is_pushed_fix(t) and st in ("reply", "none") and is_fix(t):
        out += [*pushed_steps(state, t), ""]
    elif st in ("fix", "reply") and t.get("plan") and t.get("decision"):
        if is_pushed_fix(t):
            out += [*pushed_steps(state, t), ""]      # the fix the reply defends
        word = OUTCOME_WORD.get(t.get("outcome") or "fix", "Fix")
        out += [ui.heading_md(ui.ASKED_ICON, f"Agreed plan: {word}"), "", t["plan"].strip(), ""]
        kept = t.get("kept") or {}
        if kept.get("on") and kept["on"] == t.get("plan_on"):
            out += [f"_{kept['why'].strip().rstrip('.')}: the plan holds._", ""]
    if st == "fixed":
        out += [render_fix(state, t["id"]).split("\n", 2)[-1], ""]
    if differs_now(t):
        # A reply posted by hand that no longer holds: its correction is the card, at any step.
        return out + [posted.card_md(t, "reply"), ""]
    if st == "reply" and t.get("reply"):
        x = state["threads"].get((t["thread_ids"] or [None])[0]) or {}
        out += [ui.heading_md(ui.DRAFT_ICON, "Draft reply"), "",
                _quote_draft(t["reply"], x.get("file")), ""]
    return out


_FILE_LINE = re.compile(r"^(?:diff --git |index |--- |\+\+\+ |new file mode |deleted file mode )")


def _change_blocks(diff):
    """A proposal's change as the card shows it: where the mod draws, one ```diff block per
    file (lib/code_view.py's `diff_fence` form), which it draws as the sidebar draws a file,
    the file and its lines on one line above the hunks; the `---`/`+++` lines go, as that
    line says it. Elsewhere, and for a diff without file headers, the diff as it is. The hunks' counts are
    taken from the hunks, so one written by hand with a wrong count still draws as a diff."""
    diff = recount(diff)
    plain = [fence(diff.strip("\n"), "diff")]
    if not ui.drawn():
        return plain
    files, path = [], None
    for ln in diff.strip("\n").splitlines():
        if ln.startswith("+++ "):
            path = re.sub(r"^b/", "", ln[4:].split("\t")[0].strip())
            files.append([path, []])
        elif _FILE_LINE.match(ln):
            continue
        elif not files:
            return plain               # hunks with no file named: nothing to head them with
        else:
            files[-1][1].append(ln)
    out = []
    for path, lines in files:
        if path == "/dev/null" or not any(x.startswith("@@") for x in lines) or "```" in "\n".join(lines):
            return plain
        # `loc`: the card heads the block with the file and its lines, nothing else does.
        out += [f"```{diff_info(path)} loc", *lines, "```", ""]
    return out[:-1] or plain


def is_pushed_fix(t):
    """Whether a fix of `t`'s is pushed (its diff link is stored), whatever the plan agreed
    since: a reply defending it still has the fix it defends."""
    return bool(t.get("diff_url"))


def push_key(tid, n):
    """Where the topic's `n`th push (1-based) is kept in git, under the MR's prefix."""
    return f"{tid}/push{n}"


def fixes_of(t):
    """The topic's pushed fixes, oldest first, one per push (`t["pushes"]`), never with
    another topic's change: consecutive pushes of it, with nothing pushed between them and
    nothing but the fix in either, are one fix. Each is `{"pushes": [n, …], "before",
    "after", "url", "mixed"}`: `before` and `after` the commits its change lies between,
    `url` GitLab's diff of it (the last push's version, from where the first one started),
    `mixed` where that link may show more than the fix (a push recorded before pushes were
    kept one by one, or one that carried a rebase or other commits along)."""
    out = []
    for n, p in enumerate(t.get("pushes") or [], 1):
        last = out[-1] if out else None
        if last and not last["mixed"] and p.get("clean") and p.get("start") == last["after"]:
            last["pushes"].append(n)
            last["after"] = p["after"]
            last["url"] = (re.sub(r"start_sha=[^&#]+", f"start_sha={last['start']}", p["url"])
                           if p.get("url") and last["url"] else None)
            continue
        out.append({"pushes": [n], "before": p["before"], "start": p.get("start"),
                    "after": p["after"], "url": p.get("url"), "mixed": not p.get("clean")})
    return out


def kept_fixes(state, t, kept=None):
    """`fixes_of(t)`, each with `ends` added: its two commits as kept in git
    (`lib/fix_refs.py`), or None where they are not (no longer in git, or never fetched).
    `kept`: the MR's kept fixes, where the caller read them for every topic at once."""
    if kept is None:
        root = (_git("rev-parse", "--show-toplevel") or "").strip()
        kept = fix_refs.kept(root, SKILL, state.get("iid")) if root and t.get("pushes") else {}
    out = []
    for f in fixes_of(t):
        first, last = kept.get(push_key(t["id"], f["pushes"][0])), kept.get(push_key(t["id"], f["pushes"][-1]))
        out.append({**f, "ends": (first[0], last[1]) if first and last else None})
    return out


def kept_fix(state, t, n=None, kept=None):
    """The pushed fix's two ends as kept in git (`lib/fix_refs.py`): its `n`th fix (1-based),
    else its latest; None where it is not kept, or the topic's fix is not pushed."""
    if not is_pushed_fix(t):
        return None
    fixes = kept_fixes(state, t, kept)
    if not fixes or (n is not None and not 1 <= n <= len(fixes)):
        return None
    return fixes[(n or len(fixes)) - 1]["ends"]


def fixed_links(state, t):
    """The links a `Fixed:` reply gives: of the topic's fixes since the last one already
    linked in its threads (a reply of the user's naming its version), each fix's own; every
    fix where none is linked yet. A link spanning several fixes is never made: between them
    lies another topic's push, or a rebase, which GitLab's diff would show too."""
    pushes = t.get("pushes") or []
    said = "\n".join(n.get("body") or "" for x in t["thread_ids"]
                      for n in (state["threads"].get(x) or {}).get("notes") or [] if n.get("mine"))
    ids = [re.search(r"diff_id=(\d+)", p.get("url") or "") for p in pushes]
    linked = [n for n, m in enumerate(ids, 1) if m and re.search(rf"diff_id={m.group(1)}\b", said)]
    since = linked[-1] if linked else 0
    links = [f["url"] for f in fixes_of({"pushes": [p if n > since else {**p, "clean": False}
                                                    for n, p in enumerate(pushes, 1)]})
             if f["pushes"][0] > since and f["url"]]
    return links or ([t["diff_url"]] if t.get("diff_url") else [])


def fixed_body(state, t):
    """The `Fixed:` reply: one link on its line, several as a list."""
    links = fixed_links(state, t)
    if len(links) == 1:
        return f"Fixed: {links[0]}\n"
    return "Fixed:\n" + "".join(f"- {u}\n" for u in links)


def _one_line(text, width=72):
    """The first line of `text`, cut at a word to `width` with an ellipsis."""
    line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    if len(line) <= width:
        return line
    return line[:width].rsplit(" ", 1)[0].rstrip(" ,;:.") + "…"


def reply_posted(state, t):
    """The link of the reply the user posted after the fix, or None: the one stored by the
    Post press, else the thread's last comment of theirs where the topic waits on the
    reviewer since (posted by hand, or before the link was stored)."""
    if t.get("reply_posted"):
        return t["reply_posted"]
    if t.get("state") != "waiting":
        return None
    x = state["threads"].get((t["thread_ids"] or [None])[0]) or {}
    notes = x.get("notes") or []
    return notes[-1].get("url") if notes and notes[-1].get("mine") else None


def reviewer_of(state, t):
    """Who the topic's thread is from: the first name of its first comment's author."""
    x = state["threads"].get((t["thread_ids"] or [None])[0]) or {}
    first = next((n for n in x.get("notes") or [] if not n.get("mine")), None)
    return first_name((first or {}).get("author") or x.get("author")) or "the reviewer"


def pushed_steps(state, t):
    """A pushed fix's card, step by step, one line each: the plan agreed (its first line),
    the push (its size, where the fix is kept in git, and GitLab's link to it), the reply
    posted since and whom it waits on now. What the fix changed is the diff viewer's to
    show (Show fix in hunk), not a copy here."""
    done = ui.STEP_DONE_ICON
    out = [f"{done} Plan agreed: {_one_line(t.get('plan'))}" if t.get("plan") else f"{done} Plan agreed"]
    root = (_git("rev-parse", "--show-toplevel") or "").strip()

    def said(f):
        stat = fix_refs.stat(root, f["ends"]) if f.get("ends") else None
        size = f" · {diffsize.of_stat(stat)}" if stat else ""
        link = f" · [the diff {LINK_ICON}]({f['url']})" if f.get("url") else ""
        return size + link + (" · may include other topics' changes" if f.get("mixed") else "")

    # A topic pushed before its pushes were kept one by one has its link alone.
    fixes = kept_fixes(state, t) or [{"url": t["diff_url"]}]
    if len(fixes) == 1:
        out.append(f"{done} Fix pushed{said(fixes[0])}")
    else:
        out.append(f"{done} Fix pushed · {len(fixes)} pushes")
        # Under it, one line per push, indented by no-break spaces: a card's markdown drops
        # the plain ones that lead a line.
        out += [f"\u00a0\u00a0{ui.PUSHED_ICON} push {k}{said(f)}" for k, f in enumerate(fixes, 1)]
    url = reply_posted(state, t)
    if url:
        out.append(f"{done} Reply posted · [your reply {LINK_ICON}]({url})")
    resolved = bool(t["thread_ids"]) and all(
        (state["threads"].get(x) or {}).get("resolved") for x in t["thread_ids"])
    if url and not resolved:
        out.append(f"{ui.STEP_WAITS_ICON} Waits on {reviewer_of(state, t)}")
    return out


def context_digest(state, tid):
    """A fingerprint of the topic's context as it renders RIGHT NOW.

    Hashing the rendered text, not a list of the fields that feed it: a field list rots —
    `sync`'s own allowlist did, silently, for every field added to the fetch after it —
    while whatever `render_quote` puts on screen is by definition what the user saw.

    Marking is suspended because this render is thrown away. Its code lines would
    otherwise land in the block manifest's critical lines, and the Stop hook would
    demand lines that appear nowhere in the message.
    """
    with critical_manifest.suspended():
        t = topic_for(state, tid)
        before = t.get("shown")
        render_quote(state, tid)          # records the digest of its context
        now, t["shown"] = t.get("shown"), before
        return now


def render_url(state, tid):
    """Direct URL(s) to the topic's thread — the reviewer's first note anchor.
    Shown with the reply draft so the user can click straight to the thread and
    post. Several URLs only when threads were merged into one topic."""
    t = topic_for(state, tid) or die(f"no topic {tid}")
    urls = [state["threads"].get(th, {}).get("url") for th in t["thread_ids"]]
    urls = [u for u in urls if u]
    if not urls:
        die(f"topic {tid} has no thread url yet (run `sync`)")
    return "\n".join(urls)


def reply_body(state, tid, legacy_dir=None):
    """The drafted reply body for a topic — the raw text that gets posted.

    Kept in the state file (`set <t> --reply -`), not in a scratch `reply-<t>.md`: a
    heredoc into a file under `~/.claude/` trips Claude Code's protected-path prompt on
    every single topic, the file outlives its use, and a draft is per-topic state like
    every other field here. This also matches review-mr, where drafts have always lived in
    the state file.

    Guarded HERE rather than by the caller: an internal topic handle (`t5`) must never
    reach GitLab, and `reply <t>` is the only way to get the body out — so the check cannot
    be forgotten or bypassed the way a separate shell guard could be.

    `legacy_dir` reads a pre-existing `reply-<t>.md` when the state carries no draft, so a
    session already in flight when this changed keeps working.
    """
    t = topic_for(state, tid) or die(f"no topic {tid}")
    body = t.get("reply")
    if not body and legacy_dir:
        f = os.path.join(legacy_dir, f"reply-{tid}.md")
        if os.path.exists(f):
            with open(f, encoding="utf-8", errors="replace") as fh:
                body = fh.read()
    if not body:
        die(
            f"no draft for {tid} yet — store one with:\n"
            f"  python3 threads.py set {tid} --reply - <<'REPLY_EOF'\n"
            f"  <the reply body>\n  REPLY_EOF"
        )
    hits = find_handles(body)
    if hits:
        die(
            f"draft has internal topic handle(s): {' '.join(hits)} — reword "
            f"(link the other thread via `url <other-t>`), then re-run"
        )
    return body


FIXED_LINE = re.compile(r"^Fixed: (https?://\S+)\s*$")


def _short_fixed(line):
    """A draft's `Fixed: <diff url>` line, where the mod draws, as a short link: the URL is
    long and says nothing to read. Display only; the text posted keeps the URL."""
    m = FIXED_LINE.match(line)
    return f"Fixed: [the diff {LINK_ICON}]({m.group(1)})" if m and ui.drawn() else line


def _quote_draft(body, path=None):
    """The draft as it should be DISPLAYED: blockquoted whole, so it reads as the one
    artefact being posted — its fenced blocks inside the quote too, where they still render
    highlighted. (review-mr's render_draft does the same.) A ```suggestion in the reply is
    re-fenced to the file's language for display only; the body that is posted keeps it.
    """
    blocks = []
    for kind, info, seg in segments((body or "").rstrip("\n").splitlines()):
        if kind == "code":
            content = "\n".join(seg)
            lang = block_lang(content, info, path)
            blocks.append("\n".join(quoted_fence(content, lang)))
            continue
        # A blank line at a text segment's edge would double the `>` line the join below
        # already puts between blocks.
        while seg and not seg[0].strip():
            seg = seg[1:]
        while seg and not seg[-1].strip():
            seg = seg[:-1]
        if seg:
            blocks.append(
                "\n".join(f"> {_short_fixed(ln)}".rstrip() if ln.strip() else ">" for ln in seg)
            )
    # A `>` line, not a blank one, between blocks: a blank line would end the quote.
    return "\n>\n".join(blocks)


def render_reply_view(state, tid, body, refine=False):
    """The whole reply block, as ONE paste so none of its parts can be dropped:
    the code the comment is on, the full thread (original + every reply), then the drafted
    body as a `> ` blockquote (matches the thread's rendering), then the thread URL, then
    the c/p/n prompt. `body` comes from `reply_body()`, which guards it.

    `refine=True` drops the context — the topic header, the code and the thread — and
    keeps only the draft, the thread URL and the prompt. That is the render for a REWORDED
    draft on a topic the user is already looking at: the context was pasted in full when
    the topic came up, it has not changed since, and repeating it pushes the one thing that
    did change (the draft) off the screen. The URL and the prompt stay because they are the
    ask, not context. The topic handle moves into the draft label so the block still says
    which topic it belongs to in a single line.

    It is **checked, not trusted**: a `sync` can bring a reviewer note and a reviewer can
    re-anchor their comment, and leaving out a context that has moved would answer a
    comment the user is not looking at. When the digest of the context does not match what
    was last shown — or nothing was ever shown — this renders in full anyway and says why.
    So `refine` is always safe to pass when re-showing; it gives itself up when it must.
    """
    t = topic_for(state, tid) or die(f"no topic {tid}")
    path = next(
        (
            state["threads"].get(th, {}).get("file")
            for th in t["thread_ids"]
            if state["threads"].get(th, {}).get("file")
        ),
        None,
    )
    out = []
    if refine and t.get("shown") != context_digest(state, tid):
        out.append(
            "_(the code or the thread changed since this was last shown — in full "
            "again)_"
            if t.get("shown")
            else "_(this topic's context has not been shown yet — in full)_"
        )
        refine = False
    if not refine:
        quote = render_quote(state, tid)
        if ui.drawn() and t.get("reply"):
            # The draft follows below, as the view's own part: not twice.
            quote = quote.split(f"\n{ui.heading_md(ui.DRAFT_ICON, 'Draft reply')}", 1)[0].rstrip()
        out += [quote, ""]
    out += [
        # Where the mod draws, headed as every draft is in both skills, a Copy beside it.
        ui.heading_md(ui.DRAFT_ICON, "Draft reply") if ui.drawn()
        else f"**Draft reply — {tref(tid)}:**" if refine else "**Draft reply:**",
        "",
        _quote_draft(body, path),
    ]
    if not ui.drawn():
        # Where the mod draws, the card posts it; Copy says where it goes.
        out += ["", f"Thread (to post on): {render_url(state, tid)}"]
    if not ui.drawn():
        # Where the mod draws, the card's own buttons ask this.
        out += ["", "**`c`** copy to clipboard · **`p`** post on GitLab · "
                "**`n`** next topic (already replied/resolved) · "
                "or just type your thoughts to refine it."]
    return "\n".join(out)


def first_open(state):
    opens = [t for t in state["topics"] if topic_status(state, t) == "open"]
    return min(opens, key=lambda t: num(t["id"])) if opens else None


# Statuses that are the user's to act on — what keeps a topic current (lib/focus.py).
NEEDS_YOU = ("open", "reply_pending")


def next_topic(state):
    """The topic the opener shows: the first open one, else the first whose reply is
    still to be written."""
    pending = [t for t in state["topics"] if topic_status(state, t) == "reply_pending"]
    return first_open(state) or (min(pending, key=lambda t: num(t["id"])) if pending
                                 else None)




def current_topic(state):
    """The current topic — a jump that still holds, else the next that needs you."""
    return CURRENT.get(state)


def view_context(state):
    """Which review a view belongs to, for the review-ui mod: the script and the folder to
    run it in and the MR, plus the overview the model reads where the mod's panel took the
    table out of the opener. Mirrors findings.py's `view_context`."""
    return {
        "review": {"skill": "rework-mr", "script": os.path.abspath(__file__),
                   "cwd": os.getcwd(), "iid": state.get("iid")},
        "topics": [f"{x['id']} {ui_state(state, x)}: {x.get('summary') or ''}"
                   for x in state["topics"]],
    }


def emit(state, block, topic=None):
    """Print a gated view with its manifest, naming the topic it is about and the current
    one, so the paste gate enforces only what belongs to the topic being decided."""
    extra = view_context(state)
    t = topic_for(state, topic) if topic else None
    if t and t.get("reply"):
        # The version of the draft the view shows: what a Post press under it posts.
        extra["draft"] = reply_version(t["reply"])
    if t:
        # The step it shows the topic at: a turn that moved the topic on without a view of
        # its own (the model finishing a push it was handed) leaves the card a step behind.
        extra["step"] = step_of(state, t)
    ui.record({"review": extra["review"], "topic": topic, "focus": current_topic(state),
               "draft": extra.get("draft")})
    print(
        critical_manifest.with_manifest(
            block, topic=topic, focus=current_topic(state), extra=extra
        )
    )


def reload_open_viewer(notes):
    """Where this session's diff viewer for the repository is open, put the fix just shown
    in it: a fix changed after a look in the viewer (its notes answered) is shown again, and
    the window would otherwise go on showing the fix as it was, beside a card about the new
    one. The agent's notes of the earlier showing go, this showing's come in; the user's
    own are dropped only by the read that hands them to the model (`hunk-notes`). Never
    opens a window: that is the user's press (`hunk-show`)."""
    root = (_git("rev-parse", "--show-toplevel") or "").strip()
    sid = root and hunk.installed() and hunk.find_session(root, hunk.owner_id())
    if not sid:
        return
    hunk.clear_agent_notes(sid)
    if hunk.reload_working_diff(sid) is not None and notes:
        try:
            hunk.add_notes(sid, notes)
        except ValueError:
            pass                  # the card says what the notes would have; the diff is in


def hunk_window():
    """The diff viewer's window for the fix shown, where this session opened one: whether it
    is open, and how many notes the user left on it (None where it could not be asked).
    Asked only while a fix waits for its push, where the card offers to read the notes."""
    root = (_git("rev-parse", "--show-toplevel") or "").strip()
    sid = root and hunk.installed() and hunk.find_session(root, hunk.owner_id())
    if not sid:
        return {"open": False, "notes": 0}
    notes = hunk.user_notes(sid)
    return {"open": True, "notes": None if notes is None else len(notes)}


def you_last(state, t):
    """Whether the user wrote last in `t`'s open threads (a comment made on GitLab by hand,
    such as a question back) while nothing here says the topic waits: the card then offers
    to wait. A reply posted ahead of the fix is known, and not asked about again."""
    unresolved = [x for x in (state["threads"].get(i) for i in t["thread_ids"])
                  if x and not x.get("resolved")]
    return (bool(unresolved) and all(x.get("awaiting") == "reviewer" for x in unresolved)
            and topic_status(state, t) == "open" and not t.get("early_posted")
            and step_of(state, t) != "fixed")


def state_json(state):
    """The rework as the review-ui mod reads it: one record per topic, the current one, the
    whole of the current topic's threads (every reply, every thread merged into it), and
    the comments made on the MR itself. Mirrors findings.py's `state_json`."""
    cur = current_topic(state)
    topics = []
    root = (_git("rev-parse", "--show-toplevel") or "").strip()
    kept = fix_refs.kept(root, SKILL, state.get("iid")) if root and any(map(is_pushed_fix, state["topics"])) else {}
    for t in state["topics"]:
        kinds, news_at = news_of(state, t)
        p = proposal_of(state, t)
        f = t.get("fix") or {}
        topics.append({
            "id": t["id"], "status": topic_status(state, t), "state": ui_state(state, t),
            "summary": t.get("summary"), "file": _loc_cell(state, t).strip("`") or None,
            "posted": True, "draft": reply_version(t["reply"]) if t.get("reply") else None,
            "inbox": inbox_of(state, t), "news": kinds, "news_at": news_at,
            "step": step_of(state, t),
            # The plan proposed for it, which Agree plan stores without a model turn.
            "proposal": ({k: p.get(k) for k in ("says", "outcome", "plan", "answer_now")} if p else None),
            "outcome": t.get("outcome") or ("fix" if t.get("decision") else None),
            "pushed": bool(t.get("diff_url")),
            "diff_url": t.get("diff_url"),
            # The pushed fix kept in git, which Show fix in hunk opens (its latest push), and
            # how many pushes it has: the sidebar picks one of them where there are several.
            "fix_kept": bool(kept_fix(state, t, kept=kept)),
            "pushes": len(fixes_of(t)) if is_pushed_fix(t) else 0,
            # A reply drafted to post ahead of the fix, and whether one went up already.
            "early": early_reply(t) and bool(t.get("reply")),
            "early_posted": bool(t.get("early_posted")),
            # You wrote last in its threads, by hand on GitLab, and nothing says it waits.
            "you_last": you_last(state, t),
            # A fix dropped earlier, kept in git: a new fix may start from it.
            "dropped": any(d.get("key") for d in t.get("dropped") or []),
            # A reply posted by hand that no longer holds: its corrected version waits.
            "post_differs": differs_now(t),
            # Whether the fix differs from the plan agreed: a reply then explains it.
            "differs": bool((f.get("compare") or {}).get("differs")),
            # Whether the plan agreed answers the reviewer: a reply then says what was chosen.
            "answer_now": bool(t.get("answer_now")),
            "checks": checks_of(t) if step_of(state, t) == "fixed" else None,
            "resolved": bool(t["thread_ids"]) and all(
                (state["threads"].get(x) or {}).get("resolved") for x in t["thread_ids"]),
            # Its thread on GitLab, where the box's line about it links. Mirrors findings.py's.
            "thread_url": next((u for x in t["thread_ids"] if (u := (state["threads"].get(x) or {}).get("url"))), None),
        })
    thread = []
    t = topic_for(state, cur) if cur else None
    for th in (t or {}).get("thread_ids") or []:
        x = state["threads"].get(th) or {}
        thread.append({"url": x.get("url"), "file": x.get("file"), "line": x.get("line"),
                       "resolved": x.get("resolved"),
                       "notes": [{**n, "author": YOU if n.get("mine") else first_name(n.get("author"))}
                                 for n in x.get("notes") or []]})
    return {"skill": "rework-mr", "iid": state.get("iid"), "title": state.get("title"),
            "url": state.get("mr_web_url"),
            "current": cur, "topics": topics, "thread": thread,
            "hunk": hunk_window() if t and step_of(state, t) == "fixed" else None,
            "round": round_order(state) if ui.drawn() else [],
            "pass": pass_now(state), "in_round": bool(state.get("in_round")),
            "mr": mr_json(state.get("mr_status")),
            "inbox": inbox_json(state, topics), "judge": judge_list(state),
            "mr_comments": [{**n, "author": YOU if n.get("mine") else first_name(n.get("author"))}
                            for n in state.get("mr_comments") or []]}


def linked_worktree():
    """`{main, path, branch}` when running in a linked worktree, else None. The main
    checkout is the first entry of `git worktree list`, whichever worktree asks."""
    git_dir = (_git("rev-parse", "--absolute-git-dir") or "").strip()
    common = (_git("rev-parse", "--path-format=absolute", "--git-common-dir") or "").strip()
    if not git_dir or git_dir == common:
        return None
    listing = (_git("worktree", "list", "--porcelain") or "").splitlines()
    main = next((ln[len("worktree "):] for ln in listing if ln.startswith("worktree ")), "")
    path = (_git("rev-parse", "--show-toplevel") or "").strip()
    branch = (_git("branch", "--show-current") or "").strip()
    return {"main": main, "path": path, "branch": branch} if main and path else None


def cleanup_facts(ctx, iid):
    """What decides whether this worktree can go, or None outside a linked worktree.
    The MR is only fetched inside one, so a main-checkout run pays nothing for this."""
    wt = linked_worktree()
    if not wt:
        return None
    mr = mr_object(ctx, iid) or {}
    return dict(wt, state=mr.get("state"), source=mr.get("source_branch"),
                mr_sha=mr.get("sha"), tip=(_git("rev-parse", "HEAD") or "").strip(),
                dirty=bool((_git("status", "--porcelain") or "").strip()))


def cleanup_blocker(f):
    """Why the worktree must stay, or None when removing it and its branch loses nothing.
    `branch -D` is needed because a squash merge leaves the branch unmerged as far as git
    can tell; the tip matching the MR's last commit is what makes forcing it safe."""
    if f["dirty"]:
        return "it has uncommitted or untracked changes"
    if f["tip"] != f["mr_sha"]:
        return "the local branch tip differs from the MR's last commit"
    return None


def render_cleanup(f):
    """The line under the table once the MR is merged; empty when there is nothing to say:
    not a worktree, not merged yet, or a worktree on some other branch."""
    if not f or f["state"] != "merged" or f["branch"] != f["source"]:
        return ""
    where = f"This worktree (`{f['path']}`) and its local branch `{f['branch']}`"
    blocker = cleanup_blocker(f)
    if blocker:
        return f"**MR merged.** {where} stay for now: {blocker}."
    return f"**MR merged.** {where} are no longer needed and can be removed."


def cleanup_worktree(ctx, iid):
    """Remove this worktree and its branch from the main checkout, re-checking first.
    Ignored files (build output, a local `.env`) go with the worktree: git does not count
    them as changes, so nothing above can hold back for them."""
    f = cleanup_facts(ctx, iid)
    if not f or f["state"] != "merged" or f["branch"] != f["source"]:
        die("not a linked worktree on this MR's branch, or the MR is not merged")
    blocker = cleanup_blocker(f)
    if blocker:
        die(f"keeping {f['path']}: {blocker}")
    for args in (("worktree", "remove", f["path"]), ("branch", "-D", f["branch"])):
        r = run(["git", "-C", f["main"], *args])
        if r.returncode != 0:
            die(f"git {' '.join(args)}: {r.stderr.strip()}")
    return f"Removed {f['path']} and branch {f['branch']}."


def render_present(state, cleanup=""):
    """The opener the user should see: overview table + the current topic's reviewer
    comment, ready to reproduce in one reply.

    Mirrors review-mr's findings.py's `render_present` almost exactly. Which topic is
    current follows each skill's own status vocabulary, so it stays duplicated: see
    CLAUDE.md's "Sharing vs. duplication". The `cleanup` line is this skill's alone: a
    review runs on someone else's branch, so there is never a worktree of ours to remove."""
    tid = current_topic(state)
    if ui.drawn():
        # The mod's sidebar lists every topic, so the opener is the current one alone, and
        # with none current there is nothing to show but the cleanup line.
        return "\n".join(([cleanup, "\n---\n"] if cleanup and tid else [cleanup] if cleanup else [])
                         + ([render_quote(state, tid)] if tid else []))
    parts = [render_table(state, "all")] + ([f"\n{cleanup}"] if cleanup else [])
    if tid:
        parts += ["\n---\n", render_quote(state, tid)]
    return "\n".join(parts)


def render_plans(state):
    """Recorded decisions/plans for the open topics — for resuming after a break
    or a fresh session, so you don't re-grill what's already decided."""
    out = []
    for t in sorted(state["topics"], key=lambda t: num(t["id"])):
        s = topic_status(state, t)
        if s == "done" or not (t.get("decision") or t.get("plan")):
            continue
        stamp = (
            "  [✎ ALREADY PUSHED — reply pending; do NOT re-implement, "
            "go to the reply step]"
            if s == "reply_pending"
            else ""
        )
        out.append(f"{tref(t['id'])} — {t.get('summary') or ''}{stamp}")
        if t.get("decision"):
            out.append(f"  decision: {t['decision']}")
        if t.get("plan"):
            out.append(f"  plan: {t['plan']}")
        change = _fenced_diff(t.get("agreed_change"))
        if change:
            out += ["  change agreed:", *(f"    {ln}" for ln in change.strip("\n").splitlines())]
        if t.get("diff_url"):
            out.append(f"  pushed: {t['diff_url']}")
        for d in t.get("dropped") or []:
            if d.get("key"):
                ref = f"{fix_refs.prefix(SKILL, state.get('iid'))}{d['key']}"
                out.append(f"  dropped fix kept: `git diff {ref}/before {ref}/after` "
                           f"(dropped because: {d.get('why')})")
        out.append("")
    return "\n".join(out).strip() or "(no recorded plans yet — grill first)"


def render_bodies(state):
    """First + last note of each open thread — enough to write a summary AND
    judge status (did the author actually address it, or just acknowledge?).

    Mirrors review-mr's findings.py's `render_bodies` in structure, but the skip
    condition (`done` here vs. its acked/wontfix/draft) and its "resolved by"
    annotation are specific to each skill's own status vocabulary. Stays duplicated:
    see CLAUDE.md's "Sharing vs. duplication"."""
    out = []
    for t in state["topics"]:
        if topic_status(state, t) == "done":
            continue
        for th in t["thread_ids"]:
            x = state["threads"].get(th, {})
            loc = (
                f"{os.path.basename(x['file'])}:{x.get('line') or ''}"
                if x.get("file")
                else MR_LEVEL
            )
            out.append(
                f"[{tref(t['id'])}] {loc}"
                f"  (last spoke: {first_name(x.get('last_author'))})"
                f"  {x.get('url')}"
            )
            out.append(
                f"  {first_name(x.get('author'))}: {(x.get('body') or '').strip()}"
            )
            if x.get("note_count", 1) > 1:
                out.append(
                    f"  {first_name(x.get('last_author'))} (last of {x['note_count']}): "
                    f"{(x.get('last_body') or '').strip()}"
                )
        out.append("")
    return "\n".join(out).strip() or "(no open threads)"


# The commands that only read the state: they run alongside any other, since a write is
# whole or nothing (lib/mr_common.py's `save`). Mirrors findings.py's READ_ONLY.
READ_ONLY = ("state", "code", "path", "bodies", "plans", "judge-brief")
# Runs that take minutes (checks, a push with its hooks) hold the state only to store
# their result, so a press or an update check is not kept waiting behind them.
UNHELD = ("push",)
OUTCOMES = ("fix", "reply", "push-back", "question")


# ---------------------------------------------------------------- rounds
#
# Where the review-ui mod draws, the rework runs in ROUNDS, as review-mr's review does, and
# keeps this skill's two passes inside them: first a plan for every open topic (the PLAN
# pass), then one topic at a time fixed, pushed and replied to (the FIX pass). Between
# rounds the update check only takes notes: a reviewer's new thread or reply marks its topic
# in the sidebar and waits in its inbox, and the mod's background agent prepares a plan for
# it. Opening any topic starts a round; each decision moves on to the next topic of the pass
# that runs, in list order from the one just left, round to the top again, the ones put off
# for later last. A pass runs until nothing is left in it: a thread arriving during the fix
# pass waits until that pass is through, and its plan comes up after.
#
# Without the mod the topics come up one after another as they always did (`next_topic`).

PASSES = ("plan", "fix")


def reviewer_mark(state, t):
    """What the reviewers have said on `t`, as a fingerprint: each thread's comments by
    others and whether it is resolved. Your own replies leave it as it is, so a plan agreed
    on it, a Later and a "seen" hold until a reviewer writes again."""
    marks = []
    for x in t["thread_ids"]:
        th = state["threads"].get(x) or {}
        theirs = [n.get("at") for n in th.get("notes") or [] if not n.get("mine")]
        marks.append((x, len(theirs), max([a for a in theirs if a] or [""]),
                      bool(th.get("resolved"))))
    return hashlib.sha1(json.dumps(marks).encode()).hexdigest()[:12]


def start_fix_round(t):
    """A fix agreed on a topic whose earlier fix is pushed: the reviewers asked for more, so
    the topic goes back to making a fix, and what described the earlier one (its link, its
    checks, the reply about it) is dropped. Its pushes stay (`t["pushes"]`): the next push
    is recorded beside them, and the card lists them all."""
    if (t.get("outcome") or "fix") == "fix" and t.get("diff_url"):
        for k in ("diff_url", "fix", "checked", "push_token", "push_from", "handed_from",
                  "reply", "reply_at", "reply_early", "post_differs", "early_posted",
                  "reply_posted", "fix_lost"):
            t.pop(k, None)


def plan_holds(t, mark):
    """Whether `t`'s agreed plan is about what the reviewers said last. A plan agreed before
    plans carried their mark holds."""
    return bool(t.get("decision")) and t.get("plan_on") in (None, mark)


_WORKING = {}


def working_diff():
    """`git diff HEAD` of the checkout, once per run: the fix in progress, if any."""
    if "diff" not in _WORKING:
        _WORKING["diff"] = _git("diff", "--no-color", "HEAD") or ""
    return _WORKING["diff"]


def is_fix(t):
    """Whether `t`'s plan changes code. Any plan that does is a fix, a push-back included,
    so its card offers Fix it, the push and a reply after it; the others are replies."""
    return (t.get("outcome") or "fix") == "fix"


def early_reply(t):
    """Whether `t`'s draft is a reply ahead of its fix: one only a fix can have. On a
    reply-only plan the reply is the topic's whole answer, and posted it waits on the
    reviewer; taken as one ahead of a fix it left the topic "to reply" with nothing to come."""
    return bool(t.get("reply_early")) and is_fix(t)


def step_of(state, t, status=None):
    """Where `t` stands in the rework: `plan` (no plan agreed on what the reviewers said
    last), `fix` (a fix is agreed and not made yet), `fixed` (the fix is in the working tree,
    shown, not pushed), `reply` (pushed, or a plan that needs no code: the reply is left), or
    `none` (nothing of yours to do)."""
    s = status or topic_status(state, t)
    if s not in NEEDS_YOU:
        return "none"
    if s == "reply_pending":
        return "reply"
    if not plan_holds(t, reviewer_mark(state, t)):
        return "plan"
    if not is_fix(t) or t.get("diff_url"):
        return "reply"
    return "fixed" if t.get("fix") and working_diff().strip() else "fix"


STEP_WORDS = {
    "plan": "to plan: no plan agreed on what the reviewers said last",
    "fix": "to fix: the plan is agreed, the fix not made",
    "fixed": "fix made, to push",
}


def step_report(state, t):
    """Where `t` stands now, in one line, after a command the agent ran to move it: so a
    move that changes nothing on the card says so instead of passing unnoticed."""
    st = step_of(state, t)
    if st in STEP_WORDS:
        where = STEP_WORDS[st]
    elif st == "reply":
        where = ("to reply: its fix is pushed, the reply is left" if is_fix(t) else
                 f"to reply: the plan agreed is \"{OUTCOME_WORD.get(t.get('outcome'), t.get('outcome'))}\"")
    else:
        where = {"waiting": "waiting on the reviewer", "done": "settled: its threads are resolved",
                 "reply_pending": "nothing to do"}.get(topic_status(state, t), "nothing of yours to do")
    return (f"{tref(t['id'])} is now {where}. Show it (`quote {t['id']} --focus`) so its card "
            "matches.")


def drop_fix(state, iid, t, why, plan, outcome, new=()):
    """The fix for `t` not wanted after all, on the user's word: what is in the working tree
    is kept in git under the topic (`<t>/dropped<N>`, beside its pushes; dropped with the
    rest once the MR is merged or closed), the working tree goes back to HEAD, and the plan
    becomes a reply saying what was found. A fix already pushed is not dropped here: taking
    it back is a fix of its own, or a reply."""
    root = (_git("rev-parse", "--show-toplevel") or "").strip() or die("not in a git checkout")
    new = [p for p in dict.fromkeys([*new, *((t.get("checked") or {}).get("new") or [])])
           if os.path.exists(os.path.join(root, p))]
    diff = working_diff()
    if is_pushed_fix(t) and is_fix(t) and not (diff.strip() or new):
        die(f"{tref(t['id'])}'s fix is pushed, and nothing of it is in the working tree: "
            "taking it back is a new fix (agree one), or a reply (`set --outcome`).")
    kept = None
    if diff.strip() or new:
        head = (_git("rev-parse", "HEAD") or "").strip()
        tree = checks.snapshot(root, new) or die("could not read the fix's files from the working tree")
        commit = (_git("commit-tree", tree, "-p", head, "-m", f"rework-mr: dropped fix for {t['id']}")
                  or "").strip() or die("could not keep the fix in git; nothing was changed")
        kept = f"{t['id']}/dropped{len(t.get('dropped') or []) + 1}"
        fix_refs.keep(root, SKILL, iid, kept, head, commit) or die(
            "could not keep the fix in git; nothing was changed")
        if _git("restore", "--source=HEAD", "--staged", "--worktree", "--", ":/") is None:
            die(f"the fix is kept ({kept}), but the working tree could not be put back to "
                "HEAD: `git status` says what is left")
        for p in new:
            os.remove(os.path.join(root, p))
        _WORKING.clear()
    for k in ("fix", "checked", "push_token", "push_from", "handed_from", "reply_early",
              "agreed_change", "change", "kept", "answer_now"):
        t.pop(k, None)
    t.update(outcome=outcome, decision=why, plan=plan, plan_on=reviewer_mark(state, t))
    t.pop("state", None)
    t.setdefault("dropped", []).append({"key": kept, "why": why, "at": int(time.time())})


def pass_of(state):
    return state.get("pass") if state.get("pass") in PASSES else "plan"


def in_pass(state, t, p):
    """Whether `t` is the user's to work on in pass `p`."""
    st = step_of(state, t)
    return st != "none" and (st == "plan") == (p == "plan")


def focus_status(state, t):
    """The status the current topic is kept by (lib/focus.py). Without the mod, the topic's
    own. With it, `need` while the topic is the user's in the pass that runs, so agreeing a
    plan moves the plan pass on, while a fix, its push and its reply keep their topic
    current to the end; `unplanned`/`planned` for a topic of the other pass, which holds
    while unchanged once the user opened it."""
    s = topic_status(state, t)
    if not ui.drawn() or step_of(state, t, s) == "none":
        return s
    p = pass_of(state)
    if in_pass(state, t, p):
        return "need"
    return "unplanned" if p == "fix" else "planned"


def proposal_of(state, t):
    """The plan proposed for `t` on what the reviewers said last (the background agent's or
    the chat's), or None."""
    p = t.get("proposal") or {}
    return p if p and p.get("on") == reviewer_mark(state, t) else None


def proposal_note(state, tid):
    """Where the mod draws, what to do about a topic shown in the plan step with no
    proposal: its card can only offer to make one, so the agent stores its own first. The
    opener's no-mod route (`change-view`, then a question) reads as just as right, and a
    model following it leaves the user a card without Agree plan."""
    t = topic_for(state, tid) if tid else None
    if not (t and ui.drawn() and step_of(state, t) == "plan" and not proposal_of(state, t)):
        return None
    return (f"note: {tid} has no proposal yet, so its card has nothing to agree to. Store "
            f"yours now, then show it again: `propose {tid} --says needs --why \"…\" "
            f"--outcome fix|reply|push-back|question --plan \"…\"` (for a small fix, the "
            f"exact diff with `--change -`), then `quote {tid} --focus`. The proposal's change "
            f"takes the place of `change-view`.")


def _rotated(state, after):
    ids = [t["id"] for t in state["topics"]]
    start = ids.index(after) + 1 if after in ids else 0
    return state["topics"][start:] + state["topics"][:start]


def pass_now(state):
    """The pass that runs: the one stored while it has anything left, else the other."""
    p = pass_of(state)
    if any(in_pass(state, t, p) for t in state["topics"]):
        return p
    q = PASSES[1 - PASSES.index(p)]
    return q if any(in_pass(state, t, q) for t in state["topics"]) else p


def round_pick(state, after=None, p=None):
    """The next topic of the round after `after`, in pass `p` (the one that runs): the first
    in list order from just past it, round to the top again; never `after` itself. Mirrors
    findings.py's `round_pick`."""
    p = p or pass_now(state)
    order = _rotated(state, after)
    todo = [t for t in order if t["id"] != after and in_pass(state, t, p)]
    return todo[0] if todo else None


def round_order(state):
    """The topics a round goes through, in the order it visits them: the current one first,
    then the pass that runs, then the other pass's. The sidebar lists them so."""
    cur = state.get("focus")
    order = _rotated(state, None)
    p = pass_now(state)
    q = PASSES[1 - PASSES.index(p)]
    out = [cur] if cur and topic_for(state, cur) and step_of(state, topic_for(state, cur)) != "none" else []
    for ps in (p, q):
        out += [t["id"] for t in order if t["id"] not in out and in_pass(state, t, ps)]
    return out


def next_up(state):
    """The topic the current one hands over to (lib/focus.py's `pick_next`)."""
    if not ui.drawn():
        return next_topic(state)
    if not state.get("in_round"):
        return None                   # between rounds
    was, state["pass"] = pass_of(state), pass_now(state)
    # A new pass starts at the top, the topic just left included: it is one of this pass's.
    t = round_pick(state, state.get("focus") if state["pass"] == was else None, state["pass"])
    if t is None:
        state["in_round"] = False     # nothing left: the round is over
    return t


def start_round(state, tid=None):
    """Open `tid`, or the round's first topic: a round runs from here."""
    state["in_round"] = True
    state["mr_news"] = []
    state["pass"] = pass_now(state)
    t = topic_for(state, tid) if tid else round_pick(state, None, state["pass"])
    if t:
        CURRENT.jump(state, t["id"])


def news_of(state, t):
    """What arrived on `t` that the user has not opened, and when it last did: `(kinds,
    at)`, the kinds among `thread` (a thread new to the rework) and `reply` (a reviewer
    wrote again)."""
    if inbox_of(state, t) not in ("new", "fyi"):
        return [], None
    thr = [x for x in (state["threads"].get(i) for i in t["thread_ids"]) if x]
    at = [n.get("at") for x in thr for n in x.get("notes") or [] if not n.get("mine") and n.get("at")]
    replied = t.get("seen") or any(n.get("mine") for x in thr for n in x.get("notes") or [])
    return ["reply" if replied else "thread"], max(at) if at else None


def inbox_of(state, t):
    """Where `t` stands in the inbox: `new` (news the user has not opened), `yours`
    (opened, not decided), `fyi` (a reviewer's comment the agent found
    asks nothing of you), or None for a topic that does not need the user. Mirrors
    findings.py's `inbox_of`."""
    if topic_status(state, t) not in NEEDS_YOU:
        return None
    mark = reviewer_mark(state, t)
    p = proposal_of(state, t)
    if p and p.get("says") == "fyi" and step_of(state, t) == "plan":
        return None if t.get("seen") == mark else "fyi"
    return "yours" if t.get("seen") == mark else "new"


def inbox_json(state, topics):
    """The sidebar's inbox, in the shape findings.py's `inbox_json` gives: replies and new
    threads, how many wait on the reviewers. No pushes: the branch is
    yours, so a push is news to nobody."""
    fresh = [x for x in topics if x["inbox"] in ("new", "fyi")]
    first = round_pick(state, None, pass_now(state))
    return {
        "since": min([x["news_at"] for x in fresh if x["news_at"]] or [None]) if fresh else None,
        "replies": [x["id"] for x in fresh if "reply" in x["news"]],
        "resolved": [],
        "threads": [{"id": x["id"], "by": first_name(_thread_by(state, x["id"])) or None,
                     "fyi": x["inbox"] == "fyi"}
                    for x in fresh if "thread" in x["news"]],
        "pushes": [], "own": [], "matching": 0,
        "mr": state.get("mr_news") or [],
        "waiting": sum(1 for x in topics if x["status"] == "waiting"),
        "first": first["id"] if first else None,
        "newer_push": False,
    }


def _thread_by(state, tid):
    t = topic_for(state, tid) or {}
    th = state["threads"].get((t.get("thread_ids") or [None])[0]) or {}
    return th.get("author")


MOD_ENV = "REVIEW_UI_MOD"     # set on the mod's own runs of this script


def chat_opens(state, tid):
    """Note that the model opened `tid` (the opener, or `quote --focus`), so the background
    agent leaves it to the chat. The mod opens topics too, after a press: those stay the
    background agent's, since no turn of the model's follows."""
    if os.environ.get(MOD_ENV) != "1":
        state["chat_on"] = tid


def judge_list(state):
    """What the mod's background agent has to prepare: each topic whose plan is still to be
    agreed and has no proposal on what the reviewers said last. Not the topic the chat is
    on: the current one, where the model opened it."""
    cur = state.get("focus")
    chat = cur if cur and cur == state.get("chat_on") else None
    return {"topics": [t["id"] for t in state["topics"]
                       if t["id"] != chat and step_of(state, t) == "plan"
                       and not proposal_of(state, t)], "own": []}


def judge_brief(state, iid, skip=()):
    """The task the mod hands its background agent: each topic with what to read and what to
    store, but those in `skip` (what an agent failed on before, which the chat takes
    instead). The agent stores and shows nothing; the user finds its proposal on the topic's
    card when they get to it."""
    todo = [x for x in judge_list(state)["topics"] if x not in skip]
    if not todo:
        return ""
    run_as = (f"cd {shlex.quote(os.getcwd())} && python3 {shlex.quote(os.path.abspath(__file__))}"
              f" <command> --iid {iid}")
    out = [f"Reviewers wrote on GitLab MR !{iid}, which the user authored and is reworking. "
           "Prepare each topic below in the background, so its card carries your proposal by "
           "the time the user opens it. Nobody reads your replies: only what you store counts.",
           "", f"Run every command as `{run_as}`. Store with `propose` (or `keep-plan`, below) and "
           "nothing else: never "
           "edit code, commit, push, post, resolve, or show anything (no `set`, `agree`, "
           "`post`, `open`, `later`, `quote --focus`, `show_topic`).", "",
           "Read first: `bodies` (every open thread, first and last comment), then `quote <t>` "
           "for each topic (the code the comment is on and the whole thread), then the code "
           "itself where the point needs it.", "",
           "For each, store one proposal:",
           "`propose <t> --says needs|fyi --why \"<1–3 English sentences: what the reviewer "
           "asks, what the code does, and the trade-off; end on a **Recommendation:** "
           "sentence>\" [--summary \"<English one-liner>\"] [--outcome fix|reply|push-back|"
           "question --plan \"<one sentence: what to do>\"] [--answer-now] [--change - <<'CHANGE_EOF' … "
           "CHANGE_EOF]`.",
           "`fyi` where the comment asks nothing of the user (a thank-you, an OK on what they "
           "did): no outcome then. `needs` takes `--outcome` and `--plan`; `fix` for every "
           "plan that changes code, a push-back that also adds a comment included, the others "
           "for a plan that only replies. For a small fix "
           "add `--change -` with the change on stdin as `@@ <path>:<first>-<last>` blocks, "
           "each followed by the new lines that replace those lines (the diff is built from the "
           "file; never write one by hand): the idea and its main change at its origin (the "
           "rename where the name is defined, the new parameter in the signature), never the "
           "call sites it adapts. `--answer-now` on a fix whose plan answers the reviewer ahead "
           "of it: they asked a question, or offered options and the plan picks one. In `--why` and `--plan` alike, every identifier, path and value "
           "in backticks: the card draws them as code.", ""]
    if any((topic_for(state, tid) or {}).get("decision") for tid in todo):
        out += ["A topic marked *plan agreed* had its plan agreed before the reviewer wrote "
                "again (`plans` shows it). Where the new comment only acknowledges it (a "
                "thank-you, \"sounds good\") and changes nothing about that plan, keep it: "
                "`keep-plan <t> --why \"<one English sentence: what they said>\"`. Where it "
                "asks for anything else, propose anew as above.", ""]
    for tid in todo:
        t = topic_for(state, tid)
        kinds, _ = news_of(state, t)
        title = "" if t.get("summary") else " (no summary yet: give it `--summary`)"
        what = "wrote again in its thread" if kinds == ["reply"] else "opened it"
        agreed = " (plan agreed)" if t.get("decision") else ""
        out.append(f"- {tid}{title}{agreed}: {first_name(_thread_by(state, tid)) or 'a reviewer'} {what}.")
    out += ["", "End with one line saying what you stored."]
    return "\n".join(out)


def _fenced_diff(text):
    """The diff inside an agreed change: its ```diff block, or the text itself where it
    reads as one; None for a change shown as a before/after snippet."""
    m = re.search(r"```diff[^\n]*\n(.*?)```", text or "", re.S)
    body = m.group(1) if m else text or ""
    return body if looks_like_diff(body) else None


def render_fix(state, tid):
    """The card of a fix in the working tree, where the mod draws: what the reviewer asked
    and what you did, how big it is, whether it is the change agreed, and the commit it goes
    into. The diff itself is the sidebar's."""
    t = topic_for(state, tid) or die(f"no topic {tid}")
    f = t.get("fix") or {}
    x = state["threads"].get((t["thread_ids"] or [None])[0]) or {}
    path = f"{x['file']}:{x.get('line') or ''}" if x.get("file") else ""
    title = title_md(state, t, t.get("summary") or "")
    out = [f"{title} · {loc_md(path)}" if path else title, ""]
    if t.get("plan"):
        out += [ui.heading_md(ui.ASKED_ICON, "Agreed plan"), "", t["plan"].strip(), ""]
    # What changed, in short: the plan is above it and the thread in the sidebar, so neither
    # is told again; the checks only where they did not pass, as passing is what a shown
    # fix means.
    c = f.get("compare") or {}
    if c.get("differs"):
        # Above the change: it is what the user reviews in the diff viewer before pushing.
        out += [ui.heading_md(ui.DIFFERS_ICON, "Differs from the plan you agreed"), "",
                c["differs"].strip(), ""]
    # The heading carries the size and, dim, how many commits it rewrites or adds; the
    # commits themselves follow the summary.
    fixups, new = f.get("fixup") or [], f.get("new") or []
    counts = [f"{len(new)} new commit{'s' if len(new) != 1 else ''}"] if new else []
    if fixups:
        counts.append(f"{len(fixups)} commit{'s' if len(fixups) != 1 else ''} rewritten")
    out += [f"{ui.heading_md(ui.DID_ICON, 'Changes')}  {diffsize.of_stat(f.get('stat') or [])}"
            + (f"  ({', '.join(counts)})" if counts else ""), ""]
    summary = (f.get("did") or "").strip() or ("Changes applied as planned." if c.get("as_agreed") else "")
    if summary:
        out += [summary, ""]
    for target in fixups:
        sha, _, subject = target.partition(" ")
        out.append(f"{ui.REWRITTEN_ICON} `{sha}` {subject}".rstrip())
    for subject in new:
        out.append(f"{ui.NEW_COMMIT_ICON} {subject}")
    if checks_of(t) != "passed":
        out += ([""] if out[-1] else []) + checks_line(t)
    return "\n".join(out).rstrip()


def mark_seen(state):
    """Before every save, where the mod draws: the topic the round opened marked as seen by
    the user. Mirrors findings.py's `settle`."""
    if not ui.drawn():
        return
    cur = CURRENT.get(state)
    if cur and state.get("seen_focus") != cur:
        t = topic_for(state, cur)
        t["seen"] = reviewer_mark(state, t)
    state["seen_focus"] = cur


def save(path, state):
    if isinstance(state, dict) and "topics" in state:
        mark_seen(state)
    _save(path, state)


def post_reply(state, ctx, iid, tid, expect, fixed=False, resolve=False):
    """Post `tid`'s drafted reply into its thread (or `Fixed: <diff url>` with `fixed`), and
    resolve its threads with `resolve`. The user's press alone: the mod sets the variable
    a press runs under. Returns the posted comment's URL."""
    if os.environ.get(PRESS_ENV) != "1":
        die("posting is the user's press, on the Post button under a reply's card; nothing "
            "else posts. Offer the draft and let them press it.")
    t = topic_for(state, tid) or die(f"no topic {tid}")
    if not t["thread_ids"]:
        die(f"{tref(tid)} has no thread to reply in")
    if fixed:
        t.get("diff_url") or die(f"{tref(tid)} has no diff URL yet: push the fix first")
        body = fixed_body(state, t)
    else:
        body = t.get("reply") or die(f"{tref(tid)} has no draft reply yet")
        if expect and reply_version(body) != expect:
            die(f"the draft of {tref(tid)} changed after the card you pressed Post under was "
                "drawn; nothing was posted. Press Post under the newest card.")
    t.pop("post_differs", None)          # posted from here: no hand-posted reply to correct
    hits = find_handles(body)
    if hits:
        die(f"the draft of {tref(tid)} names internal topic handles ({', '.join(hits)}), "
            "which mean nothing on GitLab; nothing was posted. Reword it first.")
    early = not fixed and early_reply(t)
    if not early:
        t.pop("reply_early", None)
    if early and resolve:
        die(f"the reply drafted for {tref(tid)} comes before its fix: post it without resolving")
    if resolve and not fixed and t.get("diff_url"):
        # A longer reply after the fix explains something: the reviewer reads it, and
        # resolves the thread once they agree.
        die(f"the reply drafted for {tref(tid)} explains its fix: post it without resolving")
    disc = f"projects/{ctx['enc']}/merge_requests/{iid}/discussions"
    note = api_post(f"{disc}/{t['thread_ids'][0]}/notes", {"body": body})
    url = f"{ctx['web']}/-/merge_requests/{iid}#note_{note.get('id')}"
    if early:
        # Said ahead of the fix (which option, what follows): the plan stands, and the fix
        # still comes, with a short `Fixed: <link>` once pushed.
        t.pop("reply")
        t.pop("reply_early")
        t["early_posted"] = url
        return url
    if resolve:
        for x in t["thread_ids"]:
            if not (state["threads"].get(x) or {}).get("resolved"):
                api_post(f"{disc}/{x}?resolved=true", {}, method="PUT")
    t["state"] = "waiting"
    t.pop("waiting_on", None)
    if t.get("diff_url"):
        t["reply_posted"] = url          # the pushed fix's card links it
    return url


PRESS_ENV = "REVIEW_UI_PRESS"
# Where this skill keeps what is its own in a repository (`lib/fix_refs.py`).
SKILL = "rework-mr"


def reply_version(body):
    """A draft's version: what a Post press names, so it posts the text its card showed.
    Mirrors findings.py's `draft_version`."""
    return hashlib.sha1((body or "").encode()).hexdigest()[:12]


def repo_root():
    return (_git("rev-parse", "--show-toplevel") or "").strip() or die("not in a git repository")


def checks_of(t):
    """Where the checks of `t`'s fix stand: `passed` (on exactly the files there now, under
    the repository's checks as they are), `stale` (files or checks changed since), `failed`,
    or None (not run)."""
    c = t.get("checked")
    if not c:
        return None
    if not c.get("ok"):
        return "failed"
    key = ("checks", t["id"])
    if key not in _WORKING:
        root = (_git("rev-parse", "--show-toplevel") or "").strip()
        rec, why = checks.status(root) if root else (None, "no repository")
        _WORKING[key] = "passed" if (
            not why and checks.digest(rec) == c.get("record")
            and checks.snapshot(root, c.get("new") or []) == c.get("tree")) else "stale"
    return _WORKING[key]


def checks_line(t):
    """The fix card's line on its checks, and the failing output under it."""
    c = t.get("checked") or {}
    st = checks_of(t)
    if st == "passed":
        return [f"Checks passed: {', '.join(c.get('ran') or []) or 'there are none here'}."]
    if st == "stale":
        return ["Files or checks changed since the checks ran."]
    if st == "failed":
        tail = "\n".join((c.get("output") or "").splitlines()[-12:])
        return [f"Checks failed: {c.get('failed')}.", "", "```text", tail, "```"] if tail else \
            [f"Checks failed: {c.get('failed')}."]
    return ["Checks not run yet."]


class Handoff(Exception):
    """A push the script cannot finish: what the agent is asked to do instead."""


def refuse_leaving_fix(state, to=None):
    """Die when a fix is made and not pushed on a topic other than `to`: its files are in
    the working tree, and the next topic's fix would be made, checked and pushed on top of
    them. The mod hides every way out of such a topic; this holds the paths around it."""
    held = [x["id"] for x in state.get("topics") or []
            if x["id"] != to and x.get("fix") and step_of(state, x) == "fixed"]
    if held:
        die(f"the fix for {', '.join(map(tref, held))} is made and not pushed: push it, or "
            "take it out of the working tree, before leaving it.")


def run_checks(root, tid, new, iid):
    """`verify`: the repository's checks on `tid`'s fix, the stamp stored on the topic.
    Run unheld (they take minutes); the state is read again and held to store it."""
    rec, why = checks.status(root)
    if why:
        die(checks.brief(root, why))
    _, state = local_state(iid)
    others = [x["id"] for x in (state or {}).get("topics") or []
              if x["id"] != tid and x.get("fix") and step_of(state, x) == "fixed"]
    if others:
        # Its files are in the checkout too, and would be checked, committed and pushed as
        # this topic's.
        die(f"the fix for {', '.join(map(tref, others))} is in the working tree and not "
            f"pushed: push it first, or take it out, before checking {tref(tid)}'s.")
    try:
        stamp = checks.run(root, rec, new)
    except checks.Stop as e:
        die(str(e))
    path, state = local_state(iid)
    t = topic_for(state, tid) if state else None
    if t:
        hold(path)
        path, state = local_state(iid)
        t = topic_for(state, tid)
        t["checked"] = {**stamp, "at": int(time.time())}
        t.pop("push_token", None)
        t.pop("handed_from", None)
        save(path, state)
    strays = [p for p in checks.untracked(root) if p not in new]
    if not stamp["ok"]:
        print(f"Checks failed on the fix for {tref(tid)}: {stamp['failed']}.\n")
        print(stamp.get("output") or "")
        print("\nFix what fails and run verify again: a fix is shown once its checks pass.")
        sys.exit(1)
    print(f"Checks passed on the fix for {tref(tid)}: "
          f"{', '.join(stamp['ran']) or 'there are none here'}.")
    if strays:
        print("Not part of the fix, so neither checked nor committed (pass --new for a file "
              "that is): " + ", ".join(strays))


def keep_pushed_fixes(state, ctx, iid):
    """The update check's care of the pushed fixes kept in git (`lib/fix_refs.py`): once the
    MR is merged or closed, every one of them goes. While it is open:

    - a fix pushed before pushes were recorded one by one becomes one push, from its saved
      diff link (its start, and the head of the version it names), kept in git where git
      still has both commits. Its link spans every push since the topic's first, so it is
      clean only where GitLab has no version between the two;
    - a push whose link GitLab had not made yet when it was pushed gets it now."""
    root = (_git("rev-parse", "--show-toplevel") or "").strip()
    if not root:
        return
    if (state.get("mr_status") or {}).get("state") in ("merged", "closed"):
        fix_refs.drop(root, SKILL, iid)
        return
    old = [(t, ends) for t in state["topics"] if is_pushed_fix(t) and not t.get("pushes")
           and not t.get("fix_lost") and (ends := fix_refs.from_diff_url(t["diff_url"]))]
    unlinked = [p for t in state["topics"] for p in t.get("pushes") or [] if not p.get("url")]
    if not (old or unlinked):
        return
    vs = versions(ctx, iid) or []
    at = {v.get("id"): k for k, v in enumerate(vs)}
    for t, (start, diff_id) in old:
        k = at.get(diff_id)
        head = vs[k].get("head_commit_sha") if k is not None else None
        if not head or not fix_refs.keep(root, SKILL, iid, push_key(t["id"], 1), start, head):
            t["fix_lost"] = True          # asked once: git no longer has it, the link stays
            continue
        older = vs[k + 1].get("head_commit_sha") if k + 1 < len(vs) else None
        t["pushes"] = [{"before": start, "start": start, "after": head, "url": t["diff_url"],
                        "clean": older == start}]
    web = ctx.get("web")
    for p in unlinked:
        k = next((k for k, v in enumerate(vs) if v.get("head_commit_sha") == p["after"]), None)
        if web and k is not None and p.get("start"):
            p["url"] = f"{web}/-/merge_requests/{iid}/diffs?diff_id={vs[k]['id']}&start_sha={p['start']}"


def diff_url_for(ctx, iid, start, head):
    """The diff link of the topic's pushes: GitLab makes the new version a moment after the
    push, so it is waited for."""
    for _ in range(20):
        vs = versions(ctx, iid)
        if vs and vs[0].get("head_commit_sha") == head:
            return f"{ctx['web']}/-/merge_requests/{iid}/diffs?diff_id={vs[0]['id']}&start_sha={start}"
        time.sleep(1)
    return None


def handoff_brief(tid, fix, token, skip_hooks):
    """What a press hands the agent when its fix is not one fixup: the commits to make, new
    ones first, each from the files that belong to it."""
    flag = "--no-verify " if skip_hooks else ""
    steps = [f'`git commit {flag}-m "{x}"` for the new commit "{x}"' for x in fix.get("new") or []]
    steps += [f"`git commit {flag}--fixup={x.split()[0]}` for {x}" for x in fix.get("fixup") or []]
    hooks = ("" if skip_hooks else " Let git run the repository's hooks on each commit: they "
             "have not run for real on this repository's checks yet.")
    # The checks left the whole fix staged; a commit made from that index takes it all.
    return (f"The user pressed Ack and push for {tid}, and its fix goes into more than one "
            "fixup commit, so the press hands you the commits. The checks left the whole fix "
            "staged: start from an empty index (`git reset -q`), then, in this order, `git add` "
            "the files that belong to each commit and make it: " + "; ".join(steps or [
                "one `git commit --fixup=<sha>` per commit the fix belongs to"]) + "."
            + hooks + f" Then finish the press: run `threads.py push {tid} --committed --token "
            f"{token}` yourself, in this turn. The token is the press's, good for this one push.")


def push_fix(ctx, iid, path, tid, committed=False):
    """`push`: commit `tid`'s checked fix as a fixup of its commit, squash it in, and
    force-push, with no check run again where the files are the ones checked. Stores the
    diff link. Raises Handoff for what needs the agent."""
    state = load(path)
    t = topic_for(state, tid) or die(f"no topic {tid}")
    root = repo_root()
    c = t.get("checked") or {}
    if not c.get("ok"):
        raise Handoff(f"The checks have not passed on the fix for {tid}: run `threads.py "
                      f"verify {tid}`, fix what fails, and show the fix again.")
    rec, why = checks.status(root)
    if why or checks.digest(rec) != c.get("record"):
        raise Handoff(f"The repository's checks changed since they ran on {tid}'s fix "
                      f"({why or 'the record was written again'}): run `threads.py verify "
                      f"{tid}` and show the fix again.")
    sid = hunk.installed() and hunk.find_session(root, hunk.owner_id())
    left = hunk.user_notes(sid) if sid else []
    if left is None:
        # Unread is not none: a note the user left could be the reason not to push.
        raise Handoff(f"The diff viewer's notes on {tid}'s fix could not be read: run "
                      "`threads.py hunk-notes`, and push only once it reads them.")
    if left:
        raise Handoff(f"I left notes on {tid}'s fix in the diff viewer: run `threads.py "
                      "hunk-notes`, answer them, and show the fix again.")
    fix = t.get("fix") or {}
    targets = [f.split()[0] for f in fix.get("fixup") or []]
    new = fix.get("new") or []
    # Commits the agent made, from a press that handed them over: what they started from.
    handed = t.get("handed_from")
    # A push cut short after its commit (a timeout, a rejected push) goes on from there.
    committed = committed or checks._out(root, "rev-parse", "HEAD^{tree}") == c.get("tree")
    if committed:
        if checks._out(root, "rev-parse", "HEAD^{tree}") != c.get("tree"):
            raise Handoff(f"The commits are not the files checked for {tid}: run `threads.py "
                          f"verify {tid}` before pushing.")
    else:
        if checks.snapshot(root, c.get("new") or []) != c.get("tree"):
            raise Handoff(f"Files changed since the checks ran on {tid}'s fix: run "
                          f"`threads.py verify {tid}` and show the fix again.")
        if len(targets) != 1 or new:
            # The push goes on from the agent's commits as this press's: a token of it, good
            # for one push, and dropped by a new fix or new checks.
            token = secrets.token_hex(4)
            hold(path)
            state = load(path)
            topic_for(state, tid).update(push_token=token,
                                         handed_from=checks._out(root, "rev-parse", "HEAD"))
            save(path, state)
            raise Handoff(handoff_brief(tid, fix, token, checks.skips_commit_hooks(rec)))
    # The branch as it was before this push rewrote it, and the MR's head on GitLab then,
    # both kept across a push cut short: what the remote holds must be in the first, and the
    # second is where this push's own diff starts.
    before = t.get("push_from") or checks._out(root, "rev-parse", "HEAD")
    start = t.get("push_start") or (versions(ctx, iid) or [{}])[0].get("head_commit_sha")
    if not t.get("push_from"):
        hold(path)
        state = load(path)
        topic_for(state, tid).update(push_from=before, push_start=start)
        save(path, state)
    try:
        if handed:
            # The agent's commits skipped no check the record does not stand for, but their
            # messages are checked here, and the new commits the card named must be among them.
            checks.check_messages(root, handed)
            made = (checks._out(root, "log", "--format=%s", f"{handed}..HEAD") or "").splitlines()
            missing = [x for x in new if x not in made]
            if missing:
                raise checks.Stop("the fix's card names new commits that are not on the branch: "
                                  + ", ".join(f'"{x}"' for x in missing))
        elif not committed:
            checks.commit_fixup(root, rec, c, targets[0])
        if targets:
            oldest = targets[0] if len(targets) == 1 else checks._out(
                root, "merge-base", "--octopus", *targets) or targets[0]
            checks.autosquash(root, oldest, c["tree"])
        checks.push(root, rec, before)
    except checks.Stop as e:
        raise Handoff(f"Pushing {tid}'s fix stopped: {e}") from e
    if not handed:
        # Only a push whose commit ran the real hooks here tells whether the record's early
        # runs stand for them; the agent's commits may have skipped them.
        checks.trust(root)
    head = checks._out(root, "rev-parse", "HEAD")
    url = diff_url_for(ctx, iid, start, head) if start else None
    # This push's change alone: from the branch before its fix was committed (the agent's
    # commits start from where the press handed them over) to the head it made. Where the
    # branch held more than GitLab's head (a rebase, commits not pushed yet), the link from
    # that head shows those too: the push is not clean, and joins no other into one fix.
    frm = handed or before

    def tree(sha):
        return checks._out(root, "rev-parse", f"{sha}^{{tree}}") if sha else None
    if tree(frm) == tree(head) and start:
        frm = start                        # committed before the press: the change is GitLab's
    clean = bool(start) and tree(frm) == tree(start)
    hold(path)
    state = load(path)
    t = topic_for(state, tid)
    t.setdefault("pushes", []).append({"before": frm, "start": start, "after": head, "url": url,
                                       "clean": clean,
                                       "at": datetime.datetime.now(datetime.timezone.utc).isoformat()})
    n = len(t["pushes"])
    for k in ("push_from", "push_start", "handed_from", "start_sha"):
        t.pop(k, None)
    if url:
        t["diff_url"] = url
    save(path, state)
    # The fix as pushed, for the diff viewer after the push, held in git against the force
    # push that leaves both commits on no branch.
    fix_refs.keep(root, SKILL, iid, push_key(tid, n), frm, head)
    if sid:
        say, close = hunk_close_decision(hunk.user_notes(sid))
        if close:
            hunk.close_session(sid, hunk.owner_id())
    return state, url


CURRENT = focus.Current(focus_status, NEEDS_YOU + ("need",), next_up,
                        claims_idle=lambda state: not ui.drawn())


def local_state(iid=None):
    """This MR's state file and state, read without glab or the network, for the views that
    are stateless otherwise (`change-view`, `diff-view`): the MR `iid` where given, else this
    checkout's (`own_mr`). `(None, None)` where there is none."""
    try:
        if not (_git("remote", "get-url", "origin") or "").strip():
            return None, None
        slug = project_slug()
        if iid is None:
            iid = own_mr(slug)
            if iid is None:
                return None, None
        path = state_file(STATE_ROOT, slug, iid, "topics.json")
        return path, load(path)
    except (Exception, SystemExit):  # noqa: BLE001 — a view must render without it
        return None, None


def resolve_state(args):
    ctx = context()
    mr = None
    iid = args.iid
    if iid is None:
        mr = mr_view()
        iid = mr["iid"]
    path = state_file(STATE_ROOT, ctx["slug"], iid, "topics.json")
    if args.cmd not in READ_ONLY + UNHELD:
        hold(path)  # mirrors findings.py: runs that write take turns
    state = load(path)
    if state is None:
        # By number where one was given: `glab mr view` asks about the checked-out branch,
        # which a detached HEAD (mid-rebase) does not have.
        mr = mr or mr_object(ctx, iid)
        state = new_state(ctx, mr)
    elif mr:
        state.update(mr_web_url=mr.get("web_url"), title=mr.get("title"),
                     source_branch=mr.get("source_branch"))
    # Single override point: the MR's own web_url beats anything reconstructed from
    # the remote (scheme, port, install path). Doing it here means every consumer of
    # ctx["web"] — thread URLs, diff URLs — inherits it for free.
    ctx["web"] = web_base(state.get("mr_web_url")) or ctx["web"]
    return ctx, iid, path, state


def settable_state(v):
    # done/reply_pending are derived (GitLab resolution / a stored diff_url), never
    # stored — naming one here gets the reason and the fix, not just the two choices.
    if v in ("done", "reply_pending", "reply-pending"):
        raise argparse.ArgumentTypeError(
            f"{v} is derived, not settable — done = reviewer resolved the thread, "
            "reply-pending = a diff_url is stored; run sync to see it. "
            "set --state accepts only: open, waiting"
        )
    return v


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd")
    for name in ("sync", "todo", "present", "bodies", "plans", "path", "cleanup-worktree",
                 "state", "leave"):
        sub.add_parser(name).add_argument("--iid", type=int)
    pk = sub.add_parser("code", help="a topic's file as JSON for the mod's sidebar: the "
                        "working tree, the thread's line carried through your changes since")
    pk.add_argument("topic")
    pk.add_argument("--iid", type=int)
    pk.add_argument("--fix", action="store_true",
                    help="the fix in the working tree: its changes against HEAD marked")
    pk.add_argument("--file", help="one of the fix's files to show (the sidebar's pick), not "
                    "the comment's")
    pk.add_argument("--push", type=int, help="which of a pushed fix's pushes (1-based, as the "
                    "card counts them), not the latest")
    sub.add_parser(
        "check-handles",
        help="print any internal topic handles "
        "found in stdin (used by guard-reply.sh; no MR context needed)",
    )
    # The two view renderers are stateless (no glab, no state file) — see their
    # docstrings. change-preview.sh / diff-view.sh are their entry points; the rendering
    # lives here so the fence handling has one implementation instead of one per script.
    pc = sub.add_parser(
        "change-view",
        help="render a change illustration (used by "
        "change-preview.sh; reads the change from FILE or stdin)",
    )
    pc.add_argument("topic")
    pc.add_argument("file", nargs="?", help="the change; omit to read stdin")
    pc.add_argument("--iid", type=int)
    pc.add_argument(
        "--for",
        dest="for_path",
        help="path the change applies to — supplies the fence language "
        "for a snippet that isn't a diff",
    )
    pdv = sub.add_parser(
        "diff-view",
        help="render a working diff (used by diff-view.sh; reads the diff from stdin)",
    )
    pdv.add_argument("topic")
    pdv.add_argument("--iid", type=int)
    pdv.add_argument("--did", help="where the review-ui mod draws: one short sentence "
                     "summarising the change (\"Changes applied as planned.\", or what more)")
    pdv.add_argument("--fixup", action="append", default=[], metavar="SHA SUBJECT",
                     help="a commit the fix goes into, which the push "
                     "squashes it into (repeatable)")
    pdv.add_argument("--new-commit", action="append", default=[], metavar="SUBJECT",
                     help="a new commit the fix makes, by its "
                     "subject, made before the fixups (repeatable)")
    pdv.add_argument("--as-agreed", action="store_true",
                     help="where the review-ui mod draws: the fix carries out the plan agreed")
    pdv.add_argument("--differs", metavar="TEXT",
                     help="where the review-ui mod draws: one sentence, how the fix differs "
                     "from the plan agreed (a surprise, a change of direction, significantly "
                     "more or less)")
    pdv.add_argument(
        "--plain",
        action="store_true",
        help="always render the diff inline, never in a viewer window "
        "(diff-view.sh passes this when it narrowed the diff itself)",
    )
    pdv.add_argument(
        "--note",
        action="append",
        default=[],
        metavar="FILE:LINE:TEXT",
        help=f"anchor one short note in the viewer, at most "
        f"{hunk.MAX_NOTES} per topic; only where the change deviates "
        f"from the agreed plan, does more than was asked, or is not "
        f"obvious from the diff (repeatable)",
    )
    sub.add_parser(
        "hunk-notes",
        help="print the notes the USER left in the viewer, and "
        "take them out of it (nothing if there are none — read this before "
        "acting on an ACK, and answer everything it prints)",
    )
    sub.add_parser(
        "hunk-close",
        help="close this topic's diff viewer window once the "
        "push has landed; refuses while unread notes are in it",
    )
    sub.choices["sync"].add_argument(
        "--all", action="store_true", help="include resolved topics"
    )
    pq = sub.add_parser("quote")
    pq.add_argument("topic")
    pq.add_argument("--iid", type=int)
    pq.add_argument(
        "--focus",
        action="store_true",
        help="make this the current topic — the user asked to move to it, "
        "or the current one is settled. Without it, a topic that is not "
        "current is shown for your research only",
    )
    pu = sub.add_parser("url")
    pu.add_argument("topic")
    pu.add_argument("--iid", type=int)
    prv = sub.add_parser("reply-view")
    prv.add_argument("topic")
    prv.add_argument("--iid", type=int)
    prv.add_argument(
        "--refine",
        action="store_true",
        help="re-showing a reworded draft on the SAME topic: omit the "
        "topic's context — its header, the code and the thread, all "
        "on screen already and unchanged — and print only the draft, "
        "its thread URL and the prompt. Safe whenever you are "
        "re-showing: a context that moved since, or was never shown, "
        "comes back in full anyway.",
    )
    pr = sub.add_parser(
        "reply", help="the drafted reply BODY only — the paste/post payload"
    )
    pr.add_argument("topic")
    pr.add_argument("--iid", type=int)
    ps = sub.add_parser("set")
    ps.add_argument("topic")
    ps.add_argument("--iid", type=int)
    ps.add_argument(
        "--state",
        type=settable_state,
        choices=("open", "waiting"),
        help="open (your turn) or waiting (you fully addressed it); "
        "done/reply-pending are derived — run sync",
    )
    for f in ("summary", "decision", "plan", "start-sha", "diff-url"):
        ps.add_argument(f"--{f}")
    ps.add_argument("--outcome", choices=OUTCOMES,
                    help="with --decision: what the plan does (default fix)")
    ps.add_argument(
        "--reply",
        help="the drafted reply body; `-` reads stdin (use a quoted "
        "heredoc for anything multi-line)",
    )
    ps.add_argument("--early", action="store_true",
                    help="with --reply: a reply ahead of the fix (posted without resolving; "
                    "the plan stands)")
    # Rounds, where the review-ui mod draws (see "rounds" above).
    pdf = sub.add_parser("drop-fix", help="on the user's word: <t>'s fix is not wanted after "
                         "all; it is kept in git, the working tree cleaned, the plan a reply")
    pdf.add_argument("topic")
    pdf.add_argument("--why", required=True, help="what was found: why the fix is dropped")
    pdf.add_argument("--plan", required=True, help="one sentence: what the reply says")
    pdf.add_argument("--outcome", choices=[o for o in OUTCOMES if o != "fix"], default="reply")
    pdf.add_argument("--new", action="append", default=[],
                     help="a file the fix added (untracked); repeat for each")
    pdf.add_argument("--iid", type=int)
    for name, hlp in (("open", "start a round, at <t> or the inbox's first"),
                      ("agree", "on a person's press: store <t>'s proposed plan as agreed"),
                      ("wait", "on a person's press: <t> needs nothing more of you until a "
                               "reviewer writes again"),
                      ("hunk-show", "on a person's press: <t>'s fix in the diff viewer, "
                                    "switched to")):
        pp = sub.add_parser(name, help=hlp)
        pp.add_argument("topic", nargs="?" if name == "open" else None)
        pp.add_argument("--iid", type=int)
        if name == "hunk-show":
            pp.add_argument("--pushed", action="store_true",
                            help="the fix as pushed, from the commits kept in git, not the "
                            "working tree's")
            pp.add_argument("--push", type=int, help="with --pushed: which of its pushes "
                            "(1-based, as the card counts them), not the latest")
    sub.add_parser("refresh").add_argument("--iid", type=int)
    pjb = sub.add_parser("judge-brief")
    pjb.add_argument("--iid", type=int)
    pjb.add_argument("--skip", action="append", default=[], help="a topic an agent failed on")
    ppd = sub.add_parser("posted", help="the user posted <t>'s reply by hand: the draft and "
                         "what went up, to compare; `--ok` once it holds")
    ppd.add_argument("topic")
    ppd.add_argument("--iid", type=int)
    ppd.add_argument("--ok", action="store_true", help="what they posted holds: record it")
    ppd.add_argument("--differs", metavar="VERDICT",
                     help="what they posted does not hold: your verdict, in a line or two, "
                     "with the corrected version of the posted text on --fix; shows its card")
    ppd.add_argument("--fix", help="with --differs: `-` reads the corrected text from stdin")
    ppd.add_argument("--apply", action="store_true",
                     help="on the user's press only: edit the posted reply on GitLab to the "
                     "corrected version, and record it")
    pkp = sub.add_parser("keep-plan", help="the background agent's: <t>'s agreed plan holds "
                         "after the reviewer's new comment, which only acknowledged it")
    pkp.add_argument("topic")
    pkp.add_argument("--iid", type=int)
    pkp.add_argument("--why", required=True, help="one sentence: what the reviewer said")
    pq2 = sub.add_parser("propose", help="store a proposed plan for <t> (the background "
                         "agent's, or yours before showing the topic)")
    pq2.add_argument("topic")
    pq2.add_argument("--iid", type=int)
    pq2.add_argument("--says", choices=("needs", "fyi"), required=True)
    pq2.add_argument("--why", required=True)
    pq2.add_argument("--summary")
    pq2.add_argument("--outcome", choices=OUTCOMES)
    pq2.add_argument("--plan")
    pq2.add_argument("--change", help="`-`: the change from stdin, as `@@ <path>:<first>-<last>` "
                     "blocks of the new lines (the diff is built from the file)")
    pq2.add_argument("--answer-now", action="store_true",
                     help="with a fix: the plan answers the reviewer now, ahead of the fix "
                     "(they asked a question or offered options)")
    pq2.add_argument("--unless-judged", action="store_true",
                     help="keep a proposal stored since on the same comments")
    ppo = sub.add_parser("post", help="on a person's press only: post <t>'s reply")
    ppo.add_argument("topic")
    ppo.add_argument("--iid", type=int)
    ppo.add_argument("--expect", help="the draft version the card showed")
    ppo.add_argument("--fixed", action="store_true", help="post `Fixed: <diff url>`")
    ppo.add_argument("--resolve", action="store_true", help="and resolve the thread")
    pck = sub.add_parser("checks", help="whether this repository's checks are worked out; "
                         "what to read to work them out where not")
    pck.add_argument("--set", metavar="-", help="`-`: store the reading, JSON from stdin")
    pv = sub.add_parser("verify", help="run the repository's checks on <t>'s fix: its "
                        "hooks ahead of time, then the checks of the parts it touches")
    pv.add_argument("topic")
    pv.add_argument("--iid", type=int)
    pv.add_argument("--new", action="append", default=[], metavar="PATH",
                    help="a new file that is part of the fix (repeatable)")
    pph = sub.add_parser("push", help="commit <t>'s checked fix as a fixup, squash and "
                         "force-push, without running the checks again")
    pph.add_argument("topic")
    pph.add_argument("--iid", type=int)
    pph.add_argument("--committed", action="store_true",
                     help="the fixup commits are made already")
    pph.add_argument("--token", help="with --committed: the one a press's hand-off named")
    pm = sub.add_parser("merge")
    pm.add_argument("into")
    pm.add_argument("others", nargs="+")
    pm.add_argument("--iid", type=int)
    args = ap.parse_args()
    cmd = args.cmd or "sync"

    if cmd == "check-handles":
        # pure text check, no MR/state needed — keep it independent of glab/network
        print(" ".join(find_handles(sys.stdin.read())))
        return
    if cmd == "change-view":
        if args.file:
            with open(args.file, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        else:
            text = sys.stdin.read()
        # Stateless, so it names its topic but not the current one: the hook then enforces
        # it as before, which is right — a change illustration is never research.
        print(
            critical_manifest.with_manifest(
                render_change_view(args.topic, text, args.for_path), topic=args.topic
            )
        )
        # Kept with the topic: a plan agreed on it is agreed on this change, which the fix
        # is judged against once it is made (`plans` shows it).
        path, state = local_state(args.iid)
        if state and topic_for(state, args.topic):
            # Read again once held: a background judgment or a refresh may have written since.
            hold(path)
            state = load(path)
            topic_for(state, args.topic)["change"] = text
            save(path, state)
        return
    if cmd == "diff-view":
        notes = []
        for raw in args.note:
            n = parse_note(raw)
            if n is None:
                die(f"--note must be FILE:LINE:TEXT, got {raw!r}")
            notes.append(n)
        # Both checks BEFORE anything is shown or touched. The cap used to be enforced deep
        # inside, after the viewer had been spawned, stripped of last round's notes and
        # reloaded — so a fourth note aborted the command with the window already rebuilt
        # and no block printed at all. And `--plain` with notes is a contradiction the
        # caller has to hear about: the diff is not going to a viewer, so there is nothing
        # to anchor them to, and staying quiet let the model report notes it never left.
        if len(notes) > hunk.MAX_NOTES:
            die(
                f"{len(notes)} notes for one topic, at most {hunk.MAX_NOTES} are allowed. "
                "A note belongs only where the change deviates from the agreed plan, does "
                "more than was asked, or is not obvious from the diff; everything else is "
                "already visible in the diff itself."
            )
        if notes and args.plain:
            die(
                "--note cannot be used with git arguments: narrowing the diff forces the "
                "inline shape, and an inline diff has no lines to anchor a note to. Put "
                "the point in your prose instead, or show the whole diff."
            )
        diff = sys.stdin.read()
        drawn = ui.drawn()
        # The fix is the topic's however it is shown: the push commits it by what is stored
        # here (its commits above all), card or no card.
        path, state = local_state(args.iid)
        if state and topic_for(state, args.topic) and (diff.strip() or drawn):
            if not diff.strip():
                diff_view_block(args.topic, diff)          # dies with the reason
            if args.as_agreed and args.differs:
                die("--as-agreed and --differs say opposite things: pass one")
            # Read again once held: a background judgment or a refresh may have written since.
            hold(path)
            state = load(path)
            t = topic_for(state, args.topic)
            # Whether the fix still carries out the plan agreed is the agent's judgment, said
            # here: the idea kept, nothing surprising, no change of direction, not
            # significantly more or less. A line-by-line count could not tell a new
            # parameter's adapted call sites from a change nobody agreed.
            if drawn and t.get("decision") and not (args.as_agreed or args.differs):
                die(f"say whether the fix carries out the plan agreed for {tref(args.topic)} "
                    "(`plans`): `--as-agreed`, or `--differs \"<what differs: a surprise, a "
                    "change of direction, significantly more or less>\"`")
            t.pop("push_token", None)
            t.pop("handed_from", None)
            # Code changed on a plan that was to reply only: it is a fix now, which is pushed
            # and answered as one. Left a reply, its card had no push and `push` refused it.
            t["outcome"] = "fix"
            t["fix"] = {"did": args.did, "fixup": args.fixup, "new": args.new_commit, "stat": diff_stat(diff),
                        "notes": notes,
                        "compare": {"as_agreed": bool(args.as_agreed), "differs": args.differs}}
            if drawn:
                # Where the mod draws, the fix is a card and its diff the sidebar's: the diff
                # viewer opens only on the user's press (`hunk-show`), with these notes.
                CURRENT.jump(state, args.topic)
                reload_open_viewer(notes)
                emit(state, render_fix(state, args.topic), args.topic)
                save(path, state)
                return
            save(path, state)
        print(
            critical_manifest.with_manifest(
                diff_view_block(args.topic, diff, args.plain, notes),
                topic=args.topic,
            )
        )
        return
    if cmd == "hunk-notes":
        # Stateless like the views above, and deliberately silent when there is nothing:
        # "no output" is the common answer and the one that means "go ahead and push".
        # Which is exactly why every OTHER outcome has to say something — see below.
        root = (_git("rev-parse", "--show-toplevel") or "").strip()
        sid = root and hunk.installed() and hunk.find_session(root, hunk.owner_id())
        if not sid:
            return
        notes = hunk.user_notes(sid)
        if notes is None:
            die(
                "could not read the viewer's notes — the daemon did not answer. This is "
                "NOT the same as there being none: do not push. Retry, and if it keeps "
                "failing ask the user whether they left anything on the diff."
            )
        for line in hunk_note_lines(notes):
            print(line)
        # Printed first, dropped second: the note is the user's only copy of the request
        # until this output reaches the model, and a delete that ran before the print
        # would lose it for good on any failure in between.
        for _p, _s, _ln, _t, nid in notes:
            hunk.drop_note(sid, nid)
        return

    if cmd == "hunk-close":
        # Silent like `hunk-notes`, and for the same reason: the common outcome — the
        # window closed, or there was never one — is nothing the user needs telling about.
        root = (_git("rev-parse", "--show-toplevel") or "").strip()
        owner = hunk.owner_id()
        sid = root and hunk.installed() and hunk.find_session(root, owner)
        if not sid:
            return
        # Read WITHOUT deleting. A note that appeared since the last read is a request the
        # user made and nobody has answered, and closing the window would take both the
        # note and the diff it is anchored to. `hunk-notes` is the one door notes leave by,
        # so this only refuses and says so.
        say, close = hunk_close_decision(hunk.user_notes(sid))
        if say:
            print(say)
        if close:
            hunk.close_session(sid, owner)
        return

    if cmd == "checks":
        root = repo_root()
        if args.set:
            try:
                rec = checks.store(root, json.loads(sys.stdin.read()))
            except ValueError as e:
                die(f"not stored: {e}")
            print("stored: " + "; ".join(
                [f"{h['event']}: {'run ahead' if h.get('early') else 'runs at its moment'}"
                 for h in rec["hooks"]] + [f"tests: {', '.join(x['name'] for x in rec['tests']) or 'none'}"]))
            return
        rec, why = checks.status(root)
        print(checks.brief(root, why) if why else json.dumps(
            {k: rec[k] for k in ("hooks", "tests", "trusted")}, indent=2))
        return
    if cmd == "verify":
        run_checks(repo_root(), args.topic, args.new, args.iid)
        return

    ctx, iid, path, state = resolve_state(args)

    if cmd == "path":
        print(path)
    elif cmd == "state":                  # offline, and asked for after every view
        print(json.dumps(state_json(state)))
    elif cmd == "leave":                  # offline: the sidebar's close (findings.py's too)
        refuse_leaving_fix(state)
        CURRENT.leave(state)
        save(path, state)
        print(json.dumps(state_json(state)))
    elif cmd == "refresh":                # the mod's update check: fetch, reconcile, state
        me = resync(state, ctx, iid)
        if not me:
            print(json.dumps(state_json(state)))   # the state as it was: the next check tries again
            return
        state["diff_base"] = mr_base(ctx, iid) or state.get("diff_base")
        # The MR as a whole: approved, auto-merge set, merged. Mirrors findings.py's.
        mr = mr_object(ctx, iid) or {}
        # Kept on each check, so a state written before it was kept learns it too.
        state["source_branch"] = mr.get("source_branch") or state.get("source_branch")
        status = mr_status(ctx, iid, me, mr)
        state.setdefault("mr_news", []).extend(mr_news(state.get("mr_status"), status,
                                                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
        state["mr_status"] = status
        keep_pushed_fixes(state, ctx, iid)
        save(path, state)
        print(json.dumps(state_json(state)))
    elif cmd == "open":
        refuse_leaving_fix(state, args.topic)
        start_round(state, args.topic)
        save(path, state)
        print(json.dumps(state_json(state)))
    elif cmd == "agree":
        t = topic_for(state, args.topic) or die(f"no topic {args.topic}")
        p = proposal_of(state, t) or die(f"{tref(args.topic)} has no proposed plan to agree")
        if p.get("says") == "fyi":
            die(f"{tref(args.topic)} asks nothing of you: `wait {args.topic}` closes it")
        t.update(decision=p.get("why"), plan=p.get("plan"), outcome=p.get("outcome") or "fix",
                 agreed_change=p.get("change"), plan_on=reviewer_mark(state, t),
                 answer_now=bool(p.get("answer_now")))
        start_fix_round(t)
        save(path, state)
        print(f"agreed the plan for {tref(args.topic)}")
    elif cmd == "wait":
        t = topic_for(state, args.topic) or die(f"no topic {args.topic}")
        t["state"] = "waiting"
        t["waiting_on"] = reviewer_mark(state, t)
        save(path, state)
        print(step_report(state, t))
    elif cmd == "posted":
        # Mirrors findings.py's `posted`: the user posted the reply themselves, often
        # reworded; the agent compares it with the draft before it counts as posted.
        t = topic_for(state, args.topic) or die(f"no topic {args.topic}")
        if args.apply:
            # Edits the user's own comment on GitLab: their press, never the agent's move.
            if os.environ.get(PRESS_ENV) != "1":
                die("editing a posted reply is the user's press, on its card; nothing else "
                    "edits it. Show the card and let them press it.")
            try:
                print(f"edited the posted reply of {tref(args.topic)}: "
                      f"{posted.apply(api_post, ctx, iid, t)}")
            except ValueError as e:
                die(str(e))
            args.ok = True
        if args.differs:
            fix = sys.stdin.read() if args.fix == "-" else args.fix
            try:
                posted.store_differs(state, t, args.differs, fix, last=True, since=t.get("reply_at"))
            except ValueError as e:
                die(str(e))
            save(path, state)
            emit(state, f"{title_md(state, t, t.get('summary') or '')}\n\n"
                 + posted.card_md(t, "reply"), args.topic)
        elif args.ok:
            t.pop("post_differs", None)
            if early_reply(t) and t.pop("reply_early"):
                # Ahead of the fix: the plan stands, as after a Post press.
                t["early_posted"] = True
            else:
                t.pop("reply_early", None)
                t["state"] = "waiting"
                t.pop("waiting_on", None)
            t.pop("reply", None)
            if current_topic(state) == args.topic:
                CURRENT.leave(state)
            save(path, state)
            print(f"{tref(args.topic)} is posted")
        else:
            resync(state, ctx, iid)
            save(path, state)
            _disc, mine = posted.own_note(state, t, last=True, since=t.get("reply_at"))
            print("Drafted:\n\n" + (t.get("reply") or "(no draft kept)").strip())
            print("\nPosted:\n\n" + (mine.get("body") if mine else "(not on GitLab yet: nothing of the "
                                        "user's came after the draft)").strip())
    elif cmd == "keep-plan":
        t = topic_for(state, args.topic) or die(f"no topic {args.topic}")
        t.get("decision") or die(f"{tref(args.topic)} has no agreed plan to keep: `propose` one")
        # What the reviewers said is the plan's again: it holds until they write once more.
        t["plan_on"] = reviewer_mark(state, t)
        t["kept"] = {"why": args.why, "on": t["plan_on"]}
        save(path, state)
        print(f"kept the plan for {tref(args.topic)}")
    elif cmd == "propose":
        t = topic_for(state, args.topic) or die(f"no topic {args.topic}")
        if args.unless_judged and proposal_of(state, t):
            print(f"{tref(args.topic)} has a proposal on these comments already; kept it")
            return
        if args.says == "needs" and not (args.outcome and args.plan):
            die("a proposal that needs the user takes --outcome and --plan")
        change = sys.stdin.read() if args.change == "-" else args.change
        # The new lines for the ranges they replace, or a clean diff: either way stored as a
        # diff that parses, built from the file where it can be (lib/change.py). The fix is
        # compared with it once made, and the card draws it.
        if change:
            try:
                change = change_of.of(repo_root(), change)
            except ValueError as e:
                die(f"the change was not stored: {e}")
        if args.summary:
            t["summary"] = args.summary
        t["proposal"] = {"says": args.says, "why": args.why, "outcome": args.outcome,
                         "plan": args.plan, "change": change, "on": reviewer_mark(state, t)}
        if args.answer_now:
            t["proposal"]["answer_now"] = True
        save(path, state)
        print(f"stored the proposal for {tref(args.topic)}")
    elif cmd == "judge-brief":
        print(judge_brief(state, iid, args.skip))
    elif cmd == "post":
        early = early_reply(topic_for(state, args.topic) or {}) and not args.fixed
        url = post_reply(state, ctx, iid, args.topic, args.expect, args.fixed, args.resolve)
        if early and current_topic(state) == args.topic:
            CURRENT.leave(state)               # said; the round goes on, the fix comes later
        resync(state, ctx, iid)
        save(path, state)
        print(f"posted {tref(args.topic)}: {url}")
    elif cmd == "push":
        pressed = os.environ.get(PRESS_ENV) == "1"
        if ui.drawn() and not pressed:
            # The one push that is not a press itself: the agent's commits, going on from a
            # press that handed them over, by the token that press stored.
            t = topic_for(state, args.topic) or die(f"no topic {args.topic}")
            if not (args.committed and args.token and args.token == t.get("push_token")):
                die("pushing is the user's press, on the fix's card; nothing else pushes.")
            t.pop("push_token")
            pressed = True
            save(path, state)
        try:
            state, url = push_fix(ctx, iid, path, args.topic, args.committed)
        except Handoff as e:
            print(e)
            sys.exit(3)
        print(f"pushed the fix for {tref(args.topic)}" + (f": {url}" if url else
              " (GitLab showed no new version yet: run `diff-url.py url` for the link)"))
        if pressed and os.environ.get(PRESS_ENV) != "1":
            # A press the model finished: what comes after the push (a "Fixed" reply, or a
            # longer one) is the user's to choose on the topic's card.
            print(f"{tref(args.topic)} is pushed, and its next card is the user's: end the "
                  "turn here, and never post or resolve for them.")
    elif cmd == "hunk-show" and args.pushed:
        # The pushed fix, from the two commits kept in git: written as a patch, opened in
        # this session's diff viewer window.
        t = topic_for(state, args.topic) or die(f"no topic {args.topic}")
        root = (_git("rev-parse", "--show-toplevel") or "").strip()
        ends = kept_fix(state, t, args.push) or die(f"{tref(args.topic)} has no pushed fix kept "
                                                    "in git to show: its diff link is all there is")
        shown = fix_refs.show(root, ends, state_file(STATE_ROOT, ctx["slug"], iid, f"fix-{args.topic}.patch"),
                              window_label())
        if not shown:
            die(hunk.not_shown())
        hunk.focus_window(shown[0])
        print(f"the pushed fix for {tref(args.topic)} is in the diff viewer, window {shown[1]}")
    elif cmd == "hunk-show":
        t = topic_for(state, args.topic) or die(f"no topic {args.topic}")
        root = (_git("rev-parse", "--show-toplevel") or "").strip()
        diff = working_diff()
        if not diff.strip():
            die("nothing in the working tree to show")
        shown = hunk.show_working_diff(root, window_label(), diff_stat(diff)) if root else None
        if not shown:
            die("the diff viewer did not open with this fix: it needs hunk inside tmux, and "
                "its window must hold exactly the fix's files")
        notes = [n for n in (t.get("fix") or {}).get("notes") or [] if isinstance(n, dict)]
        if notes:
            try:
                hunk.add_notes(shown[0], notes)
            except ValueError:
                pass
        hunk.focus_window(shown[0])
        print(f"the fix for {tref(args.topic)} is in the diff viewer, window {shown[1]}")
    elif cmd == "code":                   # offline: the sidebar asks on every move
        # The topic's file for the sidebar. A fix in the working tree (`--fix`): the file the
        # comment is on (else the fix's first, or the one picked), its changes against HEAD.
        # A pushed fix kept in git: the same from the commit before it to the one it made.
        # Otherwise the file as the MR changes it, the thread's line carried through the
        # changes since. Mirrors findings.py's `code`.
        t = topic_for(state, args.topic) or die(f"no topic {args.topic}")
        x = (state["threads"].get(t["thread_ids"][0]) if t["thread_ids"] else None) or {}
        root = (_git("rev-parse", "--show-toplevel") or "").strip()
        if args.fix:
            files = [[f, a, r] for f, a, r in diff_stat(working_diff())]
            v = code_view.fix_view(root, x, files, args.file, "HEAD")
            if v:
                v["mode"] = "fix"
        else:
            ends = kept_fix(state, t, args.push)
            files = [[f, a, r] for f, a, r in fix_refs.stat(root, ends)] if ends else []
            names = [f for f, _a, _r in files]
            if files and (args.file in names or code_view.commented(root, x) in names):
                v = code_view.fix_view(root, x, files, args.file, *ends)
                if v:
                    v["mode"] = "pushed"      # the rows are the pushed fix's change
            else:
                v = code_view.view(root, x.get("file"), x.get("line_end") or x.get("line"),
                                   x.get("head_sha"), start=x.get("line_start"),
                                   side=x.get("side") or "new", base=state.get("diff_base"))
                if v and files:
                    v.update(fix_files=files, commented=code_view.commented(root, x))
        print(json.dumps(v))
    elif cmd == "quote":
        topic_for(state, args.topic) or die(f"no topic {args.topic}")
        if args.focus:
            refuse_leaving_fix(state, args.topic)
        if args.focus and ui.drawn():
            state["in_round"] = True      # opening a topic starts a round from it
            chat_opens(state, args.topic)
        note = CURRENT.view(
            state, args.topic, args.focus, f"threads.py quote {args.topic} --focus"
        )
        if note:
            print(note, file=sys.stderr)
        emit(state, render_quote(state, args.topic), args.topic)
        if args.topic == current_topic(state) and (note := proposal_note(state, args.topic)):
            print(note, file=sys.stderr)
        # `render_quote` recorded what it showed; persist it so a later `--refine` can
        # tell whether the context still matches — and the current topic with it. Every
        # command that renders a topic in full does this; it is the only reason these
        # read-only views write at all.
        save(path, state)
    elif cmd == "url":
        print(render_url(state, args.topic))
    elif cmd == "reply-view":
        body = reply_body(state, args.topic, os.path.dirname(path))
        # Drafting a reply is asking the user to approve it, so its topic is current.
        CURRENT.jump(state, args.topic)
        emit(state, render_reply_view(state, args.topic, body, args.refine), args.topic)
        save(path, state)
    elif cmd == "reply":
        # body only — the payload for the clipboard or `glab api -F body=@-`
        print(reply_body(state, args.topic, os.path.dirname(path)), end="")
    elif cmd == "present":
        if ui.drawn() and not state.get("in_round"):
            start_round(state)            # the opener carries on where the rework stands
        cleanup = render_cleanup(cleanup_facts(ctx, iid))
        emit(state, render_present(state, cleanup), current_topic(state))
        if ui.drawn():
            chat_opens(state, current_topic(state))
        if note := proposal_note(state, current_topic(state)):
            print(note, file=sys.stderr)
        save(path, state)
    elif cmd == "bodies":
        print(render_bodies(state))
    elif cmd == "cleanup-worktree":
        print(cleanup_worktree(ctx, iid))
    elif cmd == "plans":
        print(render_plans(state))
    elif cmd in ("sync", "todo"):
        resync(state, ctx, iid)
        # The MR's diff base, so the offline `code` command can show the MR's changes.
        state["diff_base"] = mr_base(ctx, iid) or state.get("diff_base")
        save(path, state)
        # `sync` isn't gated (see paste-gates.json's note), so the manifest it carries
        # is simply never read for that command — harmless, and keeping one code path
        # for both is simpler than branching just to omit it.
        table = render_table(
            state,
            "mine" if cmd == "todo" else "all",
            show_done=getattr(args, "all", False),
        )
        cleanup = render_cleanup(cleanup_facts(ctx, iid))
        if ui.drawn():
            table = ""                     # the sidebar is the table; the payload carries it
        emit(state, "\n\n".join(x for x in (table, cleanup) if x))
    elif cmd == "set":
        t = topic_for(state, args.topic) or die(f"no topic {args.topic}")
        fld = {"start-sha": "start_sha", "diff-url": "diff_url"}
        for f in (
            "summary",
            "state",
            "decision",
            "plan",
            "start-sha",
            "diff-url",
            "reply",
        ):
            v = getattr(args, f.replace("-", "_"))
            if v is None:
                continue
            if f == "reply":
                # `-` means stdin, which is how a multi-line body should arrive: a quoted
                # heredoc passes backticks, `$` and quotes through untouched, where the
                # same text in a double-quoted shell argument would be mangled by
                # expansion (or worse, executed).
                if v == "-":
                    v = sys.stdin.read()
                hits = find_handles(v)
                if hits:
                    die(
                        f"draft has internal topic handle(s): {' '.join(hits)} — reword "
                        f"(link the other thread via `url <other-t>`), then re-run"
                    )
                v = v.rstrip("\n") + "\n"
                # A reply after the fix ends on the line that links it, as `Fixed: <link>`
                # alone would: the reviewer reads it there and resolves the thread.
                if is_pushed_fix(t) and is_fix(t) and not args.early and t["diff_url"] not in v:
                    v += "\n" + fixed_body(state, t)
                t.pop("post_differs", None)   # a new draft: the old one's check is over
                # When it was written: a reply the user posts by hand is one made after this.
                t["reply_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                if args.early:
                    t["reply_early"] = True
                else:
                    t.pop("reply_early", None)
            t[fld.get(f, f)] = v
            if f == "diff-url" and t.get("pushes") and not t["pushes"][-1].get("url"):
                t["pushes"][-1]["url"] = v      # the link the push could not wait for
        if args.outcome and not is_fix({"outcome": args.outcome}) and step_of(state, t) == "fixed":
            die(f"{tref(args.topic)}'s fix is in the working tree: a plan that only replies "
                f"would leave it there, to be pushed with the next topic's. Drop it first "
                f"(`drop-fix {args.topic} --why \"…\" --plan \"…\"`).")
        if args.decision is not None:
            # A plan agreed in the chat: on what the reviewers said last, and on the change
            # shown for it, if one was (`change-view`).
            t["outcome"] = args.outcome or t.get("outcome") or "fix"
            t["plan_on"] = reviewer_mark(state, t)
            t["agreed_change"] = t.get("change")
            start_fix_round(t)
        elif args.outcome:
            # The plan's kind changed in the chat, the plan itself kept.
            t["outcome"] = args.outcome
            start_fix_round(t)
        save(path, state)
        print(step_report(state, t))
    elif cmd == "drop-fix":
        t = topic_for(state, args.topic) or die(f"no topic {args.topic}")
        drop_fix(state, iid, t, args.why, args.plan, args.outcome, args.new)
        save(path, state)
        print(step_report(state, t))
    elif cmd == "merge":
        into = topic_for(state, args.into) or die(f"no topic {args.into}")
        for oid in args.others:
            o = topic_for(state, oid)
            if not o:
                continue
            into["thread_ids"] += [
                x for x in o["thread_ids"] if x not in into["thread_ids"]
            ]
            state["topics"] = [t for t in state["topics"] if t["id"] != oid]
        save(path, state)


if __name__ == "__main__":
    main()
