#!/usr/bin/env python3
"""Track your review findings for someone ELSE's GitLab MR, reconcile them
against the live discussions, and render them as markdown (which the agent chat
styles).

This is the REVIEWER side — the mirror of rework-mr's threads.py (the author
side). **Nothing reaches GitLab but what you post**: in the UI, or with the Post
button of the review-ui mod, which runs `post` for you. Everything else here only
*reads* discussions + the branch tip, and keeps a local overlay of your findings and
their lifecycle.

State lives at ~/.claude/review-mr/<slug>--mr<iid>/findings.json, keyed by
project + MR iid, so it survives across sessions (a review spans days). The
review worktree path is remembered per MR too, at
~/.claude/review-mr/<slug>--mr<iid>/worktree — one worktree per MR, so reviewing
several MRs of the same repo in parallel no longer means they fight over a single
shared checkout. `prune` removes the worktree (never the findings.json) for any
MR of the current repo that GitLab now reports merged or closed. The draft
language is still repo-wide, at ~/.claude/review-mr/<slug>/lang (default de).

A topic has two orthogonal axes plus a lifecycle state:
  kind    issue (carries a severity) | question | praise
  source  llm 🤖 | human 👤 | both 👥   (both = you found it too → counts as yours)
  state — GitLab is the source of truth for a thread's existence/resolution; the
          local file only overlays YOUR ack:
    ✎ draft     you found it, not posted on GitLab yet            (your turn)
    ○ open      you posted it; the author has not responded        (author's turn)
    ◐ needs-ack author replied and/or resolved the thread          (your turn)
    ● acked     you are satisfied (fixed, or agreed to leave)      (overlay)
  An author resolving a thread does NOT close a topic — only your ack does. So a
  resolved thread sits at ◐ needs-ack until you ack it.

Threads you did not open are surfaced automatically on sync as topics — a peer
reviewer's (💬 peer) or the author's own (🖊️ author) — so they show up in your
lists and can be merged with your findings.

Subcommands (all read-only against GitLab):
  sync           fetch discussions + branch tip, reconcile, render overview  (default)
  todo           render only what needs YOU (✎ drafts + ◐ needs-ack)
  present        overview + the current topic (lib/focus.py)
  resume         the whole resume opener: updates + overview + current topic (one call)
  updates        pushes since your baseline: compare URLs + diffstats + topics touched
  bodies [<t>…]  first + last note of each posted thread (to judge status); with topics,
                 their threads whole
  quote <t>      render a topic's thread notes verbatim (for a draft: display
                 with meta header + where to open the thread). `--refine` leaves the
                 topic's context out — the code, or the notes of the thread a follow-up
                 replies into — for re-showing a reworded draft on a topic already on
                 screen; `set <t> --draft` takes it too, for its echo. `--focus` makes it
                 the current topic; a topic that is not current is shown for research only
  draft <t>      the draft's comment body ONLY — the paste/post payload (no meta)
  diff <t>       the author's change for one topic since you posted it, for YOU to summarise;
                 `--show` when the user asks to see it (viewer window, or inline)
  import <file>  bulk-add findings from a JSON array (review-branch seed)
  add [--thread <id>]  add one finding; --thread links an already-posted thread
  set <t> …      update a topic's fields / state
  drop <t>       remove a (draft) topic
  merge <into> <o…>  fold other topics into <into>
  link <t> [<discussion_id>]  attach a posted GitLab thread to a topic (✎→○);
                 omit the id to auto-pick your one unlinked thread on that file
  candidates     your posted threads not yet linked to any topic (for matching)
  state          the review as JSON, for the review-ui mod's buttons (offline)
  post <t>       post <t>'s draft: a new thread at its line, or a reply into its
                 thread. Only on a person's press (the review-ui mod's Post button)
  own-review done|matched|skip   the user's own review before the topics (mod only)
  propose <discussion_id> --into <t> --summary … --why … [--recommend separate]
                 offer merging their comment into <t>, with the reason the user decides on
  proposal [<discussion_id>]   a proposed match as its card (`own-review matched` shows the first)
  settle <discussion_id> merge|separate|link   on the press that answers a proposal
  verdict <t> settled|more --text …   the agent's judgment of the author's answer (its card)
  resolve <t>    resolve <t>'s threads and ack it. Only on a person's press
  head           one-line push check (branch tip vs last-reviewed head)
  base           GitLab's own merge-base SHA for this MR (feed to review-branch)
  branch         the MR's source branch — for the worktree checkout, which has no
                 other source of truth (a shared worktree sits on the LAST MR's branch)
  set-head       mark the current branch tip as reviewed
  worktree [--set PATH]   get/set this MR's review worktree path
  explainer [--set PATH]  a prepared explainer for this MR: prints its path, or
                  nothing if none applies to the current tip (branch on it — the
                  clickable link goes at the top of `present`/`resume` by itself)
  seed [--set PATH]       a prepared review-branch seed for this MR: prints its path
                  (feed it to `import`), or nothing if none applies to the current tip
  prune [--iid N]  remove OTHER merged/closed MRs' worktrees for this repo
                   (skips --iid's own; never touches findings.json)
  lang [--set XX]         get/set the repo's draft language (default de)
  path           print the state-file path
"""
import argparse
import contextlib
import datetime
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import textwrap
import time

# Repo root, 4 levels up from skills/review-mr/scripts/findings.py — needed so `lib/`,
# which lives outside this skill's own directory, is importable regardless of how this
# script is invoked (direct, or symlinked into ~/.claude/skills/).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.realpath(__file__)))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from lib import code_view, critical_manifest, diffsize, focus, hunk, posted, ui  # noqa: E402
from lib.fences import (LINK_ICON, SUGGESTION_INFO, YOU, block_lang, fence, suggestion_reach,  # noqa: E402
                        diff_info, lang_for, note_md, quoted_fence, segments,
                        suggestion_diff, when_of)
from lib.gitlab import (api, api_post, context, current_user, die, file_at, mr_base,  # noqa: E402
                        mr_head, mr_object, mr_status, mr_view, versions, web_base)
from lib.mr_common import (DEFAULT_LANG, LANG_GIVEAWAYS, MR_LEVEL, TOPIC_ICON, first_name,  # noqa: E402
                           hold, load, mr_json, mr_news, topic_handles, loc_md, num, plain_text, reads_as,
                           short_summary, state_file, topic_for, tref)
from lib.mr_common import save as _save                          # noqa: E402
from lib.snippet import MAX_BACKTRACK, open_construct            # noqa: E402

STATE_ROOT = os.path.expanduser("~/.claude/review-mr")
GLYPH = {"draft": "✎", "open": "○", "needs_ack": "◐", "acked": "●",
         "dropped": "✕"}
WORD = {"draft": "draft", "open": "open", "needs_ack": "needs-ack",
        "acked": "acked", "dropped": "dropped"}
# your turn first (draft + needs-ack), then author's, then terminals
STATUS_ORDER = {"needs_ack": 0, "draft": 1, "open": 2, "acked": 4,
                "dropped": 5}
SEV_ICON = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵"}
KIND_ICON = {"question": "❓", "praise": "💚"}
# llm/human/both = who found it; author/peer = an inbound thread you didn't open
SRC_ICON = {"llm": "🤖", "human": "👤", "both": "👥", "peer": "💬", "author": "🖊️"}
INBOUND = ("peer", "author")


def _ts(value):
    """Parse a GitLab timestamp; None for anything unparseable so comparisons fail
    safe (treated as "no information" rather than "very old")."""
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def lang_hint(state):
    """The standing "write drafts in X" instruction.

    This has to appear in EVERY view the agent reads while drafting, not just the
    overview header. Drafting happens many turns after the last `sync`, so a marker
    that only rides along with the overview scrolls out of context and the agent falls
    back to the documented default — which produced a German draft in an
    all-English project.

    It names what the language applies to, because it was read the other way round
    too: a review came back with all ten summaries in the draft language, the marker
    having been taken as "this whole review is in de". A comment body is the only
    thing that language governs.

    Kept as short and as quiet as those two facts allow, and italic so the chat
    styles it down: it is an instruction to the agent, not something the user asked
    to see, and it cannot be hidden from them — every view is pasted verbatim. When
    the draft language IS English there is nothing left to distinguish, so the second
    half goes away instead of reading "drafts en · rest en".
    """
    lang = state.get("lang") or DEFAULT_LANG
    return f"_drafts {lang}_" if lang.lower().startswith("en") \
        else f"_drafts {lang} · rest en_"


def state_dir(slug):
    d = os.path.join(STATE_ROOT, slug)
    os.makedirs(d, exist_ok=True)
    return d


def mr_author(mr):
    """Short first name of the MR author — 'Doe, Jane - AB12345' -> 'Jane'."""
    a = mr.get("author") or {}
    return first_name(a.get("name") or a.get("username"))


def mr_author_username(mr):
    return (mr.get("author") or {}).get("username")


def new_state(ctx, mr):
    """Mirrors rework-mr's threads.py's `new_state` in shape, not content — this state
    carries fields (`author`, `seq`, `ignored`, ...) that reflect reviewing someone
    else's MR, which threads.py's own state has no use for. Stays duplicated rather
    than shared: see CLAUDE.md's "Sharing vs. duplication"."""
    return {
        "project": ctx["path"], "slug": ctx["slug"], "iid": mr["iid"],
        "mr_web_url": mr.get("web_url"), "title": mr.get("title"),
        "author": mr_author(mr), "author_username": mr_author_username(mr),
        "lang": get_lang(ctx["slug"]),
        "last_reviewed_head": None, "seq": 0,
        "threads": {}, "topics": [], "ignored": [],
    }


# ---------------------------------------------------------------- fetch


def fetch_threads(ctx, iid, me, author=None):
    """All resolvable discussions on the MR, keyed by discussion id. `awaiting` is
    'you' (the reviewers' turn) when the MR author spoke last, else 'author'. This
    is defined around the author so it is correct with several reviewers, not just
    you and the author — falls back to a me-based guess if the author is unknown."""
    disc = api(f"projects/{ctx['enc']}/merge_requests/{iid}/discussions?per_page=100",
               paginate=True)
    out = {}
    for d in disc:
        notes = [n for n in (d.get("notes") or []) if not n.get("system")]
        if not notes:
            continue
        first = notes[0]
        if not first.get("resolvable"):
            continue
        resolvable = [n for n in notes if n.get("resolvable")]
        resolved = bool(resolvable) and all(n.get("resolved") for n in resolvable)
        resolved_by = next((((n.get("resolved_by") or {}).get("name")
                             or (n.get("resolved_by") or {}).get("username"))
                            for n in resolvable if n.get("resolved_by")), None)
        pos = first.get("position") or {}
        last = notes[-1]
        first_user = (first.get("author") or {}).get("username")
        last_user = (last.get("author") or {}).get("username")
        out[d["id"]] = {
            "author": (first.get("author") or {}).get("name") or first_user,
            "first_username": first_user,
            "file": pos.get("new_path") or pos.get("old_path"),
            "line": pos.get("new_line") or pos.get("old_line"),
            # A comment on a range of lines (GitLab's multi-line comment): where it starts;
            # `line` is where it ends, as GitLab anchors it.
            "line_start": ((pos.get("line_range") or {}).get("start") or {}).get("new_line")
                          or ((pos.get("line_range") or {}).get("start") or {}).get("old_line"),
            # Every comment of the thread, for the sidebar, each with its own link.
            "notes": [{"author": (n.get("author") or {}).get("name")
                       or (n.get("author") or {}).get("username"),
                       "body": n.get("body"), "at": n.get("created_at"),
                       "mine": bool(me) and (n.get("author") or {}).get("username") == me,
                       "url": f"{ctx['web']}/-/merge_requests/{iid}#note_{n.get('id')}"}
                      for n in notes],
            # The side and the exact blobs the position names — what a ```suggestion's
            # line offsets count from (see `_comment_source`).
            "side": "new" if pos.get("new_line") is not None else "old",
            "head_sha": pos.get("head_sha"),
            "base_sha": pos.get("start_sha") or pos.get("base_sha"),
            "body": first.get("body"),
            "resolved": resolved,
            "resolved_by": resolved_by,
            "url": f"{ctx['web']}/-/merge_requests/{iid}#note_{first.get('id')}",
            "note_count": len(notes),
            "last_author": (last.get("author") or {}).get("name") or last_user,
            "last_username": last_user,
            "last_body": last.get("body"),
            # When the author last spoke. Needed to tell "the author replied and I have
            # not looked yet" from "the author replied, I read it, and I acked" — see
            # topic_status(). Without it an ack can never stick on an unresolved thread.
            "last_at": last.get("created_at"),
            "mine": bool(me) and first_user == me,      # you opened this thread
            # Whether you said anything in it: another reviewer's thread waits on you until
            # you do (see `peer_waiting`).
            "you_spoke": bool(me) and any((n.get("author") or {}).get("username") == me
                                          for n in notes),
            "by_author": bool(author) and first_user == author,  # author's own thread
            "awaiting": (("you" if last_user == author else "author") if author
                         else ("you" if (me and last_user != me) else "author")),
        }
    return out


# ---------------------------------------------------------------- topics


def next_tid(state):
    """Monotonic, never reused — a persisted counter, so a topic id stays stable
    across the multi-day loop even after drops/merges free lower numbers.

    threads.py's `next_tid` looks similar but isn't: it doesn't persist a `seq`
    (rework-mr's `new_state` has no such field), so it can reuse an id a drop freed up.
    That's a real behavioral gap worth closing there if it's ever seen to bite, not a
    duplication to collapse — see CLAUDE.md's "Sharing vs. duplication"."""
    used = {t["id"] for t in state["topics"]}
    n = state.get("seq", 0) + 1
    while f"t{n}" in used:                       # defensive against legacy state
        n += 1
    state["seq"] = n
    return f"t{n}"


ADD_PROTECTED = {"id", "thread_ids", "seq"}   # never settable from seed/import


def add_topic(state, **fields):
    t = {
        "id": next_tid(state), "kind": "issue", "severity": None,
        "source": "llm", "summary": None, "file": None, "line": None,
        "draft": None, "thread_ids": [], "state": None, "ticket": None,
        "start_sha": None, "note": None, "by": None, "acked_at": None,
        "needs_title": False, "why": None,
    }
    t.update({k: v for k, v in fields.items()
              if v is not None and k not in ADD_PROTECTED})
    state["topics"].append(t)
    return t


def attach_thread(state, t, discussion_id, ctx, iid, start_sha=None):
    """Link a posted GitLab thread to a topic and capture its baseline."""
    first_thread = not t["thread_ids"]
    if discussion_id not in t["thread_ids"]:
        t["thread_ids"].append(discussion_id)
    if first_thread:
        t["draft"] = None
    if start_sha:
        t["start_sha"] = start_sha
    elif not t.get("start_sha"):
        t["start_sha"] = state.get("last_reviewed_head") or mr_head(ctx, iid)
    return t


def topic_status(state, t):
    """draft until posted; then derive from GitLab. The ack overlay wins,
    but a *fresh* author note re-opens it.

    "Fresh" means newer than your ack — compared against `acked_at`, not merely "the
    author holds the last word". Without that comparison an ack could never stick on a
    thread the author had replied to and nobody had resolved on GitLab: the overlay was
    set, and the very reply you were acking kept overriding it, so the topic sat at
    needs-ack forever and the table contradicted the state file.

    threads.py's `topic_status` does the equivalent job for rework-mr, with a genuinely
    different status vocabulary (this skill's ack overlay has no counterpart
    there). It's the root of why so much downstream rendering (_rows, render_table,
    render_bodies, render_present) stays duplicated rather than shared — see CLAUDE.md's
    "Sharing vs. duplication"."""
    if t.get("state") == "dropped":              # not raised after all; kept, so it is listed
        return "dropped"
    thr = [state["threads"].get(x) for x in t["thread_ids"]]
    thr = [x for x in thr if x]
    if not thr:
        return "draft"
    if t.get("follow_up") == "offer":           # your comment merged in: does the agent add?
        return "needs_ack"
    if t.get("kind") == "praise":                # no ack loop: resolved ⇒ done
        return "acked" if all(x.get("resolved") for x in thr) else "open"
    ov = t.get("state")
    if peer_waiting(state, t, thr):
        # Another reviewer's thread is theirs to settle: the user watches it, and it needs
        # them only where the agent judged so (`peer_view_of`), until they mark it seen. One
        # not judged yet is news until it is.
        v = peer_view_of(state, t)
        if v is None:
            # Closed by the user before views were stored, and quiet since: it stays closed.
            return "acked" if peer_closed(t, thr) else "needs_ack"
        return "needs_ack" if v["says"] == "needs" and ov != "acked" else "acked"
    acked_at = _ts(t.get("acked_at"))

    def spoke_after_ack(x):
        if x.get("awaiting") != "you" or x.get("resolved"):
            return False
        if not acked_at:                     # never acked → any author note is fresh
            return True
        said_at = _ts(x.get("last_at"))
        return said_at is None or said_at > acked_at

    author_active = any(spoke_after_ack(x) for x in thr)
    if ov == "acked" and not author_active:
        return ov
    answered = any(x.get("awaiting") == "you" or x.get("resolved") for x in thr)
    # A push changed the lines the comment is on, with no word in the thread: the author
    # may have answered in code, so it is the user's to look at (`check_pushes`).
    if not (answered or pushed_of(state, t)):
        return "open"
    # The answer was judged to leave the next move to the author (they agreed, the fix is
    # still to come): theirs again, until they push or write.
    return "open" if waits_on_author(state, t) else "needs_ack"


def peer_waiting(state, t, thr=None):
    """Whether another reviewer's thread, or the MR author's own, is one the user watches:
    they have said nothing in it and it is still open. Where the mod draws, the agent judges
    whether it needs them (`peer_view_of`); without the mod it stays the author's, as before."""
    if t.get("source") not in ("peer", "author") or not ui.drawn():
        return False
    thr = thr if thr is not None else [x for x in (state["threads"].get(i) for i in t["thread_ids"]) if x]
    return bool(thr) and not any(x.get("you_spoke") for x in thr) \
        and not all(x.get("resolved") for x in thr)


def peer_closed(t, thr):
    """Whether the user closed another reviewer's thread (Seen, or Leave to them before it)
    and nobody wrote in it since."""
    acked = _ts(t.get("acked_at")) if t.get("state") == "acked" else None
    if not acked:
        return False
    said = [_ts(x.get("last_at")) for x in thr]
    return all(s is not None and s <= acked for s in said)


def peer_view_of(state, t):
    """The agent's view of another reviewer's thread, while the thread stands as it was
    judged: `{"says": "fyi" | "needs", "advice": "seen" | "reply" | "merge", "into"}`, or
    None. A new comment in it asks for a new view."""
    v = t.get("peer_view")
    return v if v and v.get("on") == answer_mark(state, t) else None


def ui_state(state, t, status=None):
    """A topic's state in the vocabulary both skills share where the review-ui mod draws
    (lib/ui.py). An unposted topic is `new` until it has words to post, then `draft`.
    Mirrors threads.py's `ui_state`, over this skill's own statuses."""
    s = status or topic_status(state, t)
    if t.get("follow_up") == "offer":           # your move, and nobody answered anything
        return "draft" if t.get("draft") else "new"
    if s == "draft":
        return "draft" if (t.get("draft") or t.get("note")) else "new"
    return {"open": "waiting", "needs_ack": "answered", "acked": "done"}.get(s, s)


def title_md(state, t, summ):
    """A topic's title line, bold: its handle and summary, and where the mod draws, its
    state and kind as icons in front (the icon is the topic marker there)."""
    if ui.drawn():
        # The topic's label as the panel and the band draw it: state, handle, severity.
        return (f"{ui.STATE_ICON[ui_state(state, t)]} **{t['id']}** "
                f"{ui.kind_icon(t.get('kind'), t.get('severity'))}"
                + (f" **{summ}**" if summ else ""))
    return f"**{tref(t['id'])}" + (f" — {summ}**" if summ else "**")


def kind_icon(t):
    if t.get("kind") in KIND_ICON:
        return KIND_ICON[t["kind"]]
    return SEV_ICON.get(t.get("severity")) or "⚪"    # ⚪ = severity not set yet


# ---------------------------------------------------------------- reconcile


# Keys on a thread record that belong to US, not to the fetch — everything else is
# refreshed from GitLab wholesale. Deriving the merge this way instead of listing the
# fetched fields means adding a field to fetch_threads() cannot silently fail to
# propagate onto threads that already exist locally (which is exactly what happened
# when `last_at` was introduced).
LOCAL_THREAD_FIELDS = ("gone",)


def sync(state, live, ctx, iid):
    """`ctx`/`iid` are the current GitLab context and MR iid — every real call site
    has both; only a test that has no live context to give passes `None, None`
    explicitly (see `adopt_inbound`'s docstring for what that trades away)."""
    # A live thread's record is REPLACED, bar the local fields: an `update()` keeps keys the
    # fetch has stopped producing, which is how a conditional field (a line range that the
    # reviewer edited away, say) would linger as stale truth. rework-mr's threads.py had the
    # inverse of this bug — an allowlist of fetched keys, so new fields never reached a
    # thread already in the state file — and this is the shape that cannot rot either way.
    for tid, rec in live.items():
        if tid in state["threads"]:
            local = {k: v for k, v in state["threads"][tid].items()
                     if k in LOCAL_THREAD_FIELDS}
            state["threads"][tid] = {**rec, **local}
        else:
            state["threads"][tid] = rec
    for tid in state["threads"]:
        if tid not in live:                     # discussion deleted upstream
            state["threads"][tid]["resolved"] = True
            state["threads"][tid]["gone"] = True
    adopt_inbound(state, ctx, iid)
    # A topic linked before this fix existed has no baseline of its own either, and
    # `adopt_inbound` above never revisits it (its thread is already in `linked`) — so
    # without this, it would keep deferring to a live read forever, exactly the bug
    # this whole change closes, just for topics adopted before it shipped. Frozen the
    # same way, the first time a baseline is available after that.
    for t in state["topics"]:
        # Before attach_thread consumed an initial draft, old state kept the posted body
        # as a pending follow-up too. Exact equality makes this repair safe for real replies.
        if (t["thread_ids"] and t.get("draft") and
                any(state["threads"].get(tid, {}).get("body") == t["draft"]
                    for tid in t["thread_ids"])):
            t["draft"] = None
        if t["thread_ids"] and not t.get("start_sha"):
            t["start_sha"] = state.get("last_reviewed_head")
            if not t["start_sha"] and ctx is not None and iid is not None:
                t["start_sha"] = mr_head(ctx, iid)


def adopt_inbound(state, ctx, iid):
    """Surface open discussions you didn't open — a peer reviewer's thread (💬, first
    class and mergeable) or the author's own (🖊️, rare) — as topics, so they show
    up in the overview and needs-ack flow. Threads you opened stay out of this
    (they belong to your drafts, matched via `candidates`/`link`).

    `ctx`/`iid` may be `None` — tests only, when there is no live GitLab context to
    give; every real call site has both, via `sync`. Without them, a topic adopted
    before any baseline exists gets no fallback and stays `start_sha=None`, deferring
    to a live read at render time exactly like before this function froze anything.
    """
    linked = {th for t in state["topics"] for th in t["thread_ids"]}
    ignored = set(state.get("ignored") or [])       # dropped inbound → stay dropped
    # A thread YOU opened is normally left to candidates/link, because it may be the
    # posted form of a ✎ draft — auto-adopting it would duplicate that topic. But that
    # only applies to a draft about the SAME FILE. Keying on "any draft pending" made
    # this useless in practice: during curation there are essentially always drafts
    # pending, so a comment you wrote straight in the UI would never land in the table.
    draft_files = {t.get("file") for t in state["topics"]
                   if topic_status(state, t) == "draft" and t.get("file")}
    # Frozen once per call, lazily (only once something is actually about to be
    # adopted): `last_reviewed_head` is shared and advances whenever ANY topic is
    # acked, so a topic that deferred to it at RENDER time instead could silently
    # compare from a baseline later than the one in effect when it was adopted. Same
    # freeze `attach_thread` already does for a manually-linked thread.
    baseline = state.get("last_reviewed_head")
    baseline_ready = baseline is not None
    for tid, x in state["threads"].items():
        if tid in linked or tid in ignored or x.get("gone"):
            continue
        # Someone else's thread that is already resolved the first time sync sees it was
        # settled without you — before your review began, or between two syncs. Adopting
        # it costs a summary and an ack per old discussion, and the table refuses to render
        # until every summary is written. A thread adopted while open is `linked` above
        # and stays when it resolves later: that is the resolution you are there to ack.
        # One reopened later is unresolved again and is adopted then.
        if x.get("resolved") and not x.get("mine"):
            continue
        if x.get("mine") and x.get("file") in draft_files:
            continue                          # could be that draft, posted — ask instead
        if x.get("mine") and (own_review_open(state) or ui.drawn()):
            # The agent matches your own comments to the topics by what they say: during
            # your own review, and where the mod draws for any you post later too.
            continue
        if not baseline_ready:
            if ctx is not None and iid is not None:
                baseline = mr_head(ctx, iid)
            baseline_ready = True
        if x.get("mine"):
            source = "human"                  # 👤 you found it and posted it yourself
        elif x.get("by_author"):
            source = "author"
        else:
            source = "peer"
        # No `summary` here on purpose: the only text available is the raw thread body,
        # in whatever language/wording the commenter used — never a title. `needs_title`
        # makes that gap visible in the table/quote instead of silently rendering a
        # truncated, possibly non-English quote as if it were an authored one-liner.
        t = add_topic(state, source=source, file=x.get("file"),
                      line=x.get("line"), needs_title=True, start_sha=baseline,
                      by=None if x.get("mine") else first_name(x.get("author")))
        t["thread_ids"].append(tid)


# ---------------------------------------------------------------- render

def topic_file(state, t):
    """Authoritative (file, line) for a topic. Once it's linked to a GitLab thread,
    the *thread's* position wins — that's where the discussion physically lives, and
    GitLab may have placed the comment on a different line/file than the draft's
    original guess. Only an unposted draft falls back to its stored file/line."""
    for i in t["thread_ids"]:
        x = state["threads"].get(i) or {}
        if x.get("file"):
            return x["file"], x.get("line")
    return t.get("file"), t.get("line")


def _loc(state, t, full=False):
    """Location `path:line`, or "" for a topic with no diff position at all — see
    `loc_md`, which is what turns either into something readable. Compact (basename)
    for the overview table; full repo path when `full` — used in the per-topic header
    you read while discussing. Mirrors rework-mr's threads.py's `_loc`."""
    f, line = topic_file(state, t)
    if not f:
        return ""
    return f"{f if full else os.path.basename(f)}:{line or ''}"


def _thread_loc(x):
    """`basename:line` for a thread's diff position, or "" when it has none — a
    discussion on the merge request itself carries no position, and every caller has
    to say so in its own words rather than print a stray separator."""
    return f"{os.path.basename(x['file'])}:{x.get('line') or ''}" if x.get("file") else ""


def _rows(state, scope):
    """Mirrors rework-mr's threads.py's `_rows` — same shape (status/counts/ordering for
    render_table), but the `keep` sets below are review-mr's own status vocabulary
    (draft/needs_ack/...), not threads.py's (open/reply_pending/...). Stays duplicated:
    see CLAUDE.md's "Sharing vs. duplication"."""
    topics = state["topics"]
    st = {t["id"]: topic_status(state, t) for t in topics}
    counts = {s: sum(1 for t in topics if st[t["id"]] == s) for s in GLYPH}
    ordered = sorted(topics, key=lambda t: (STATUS_ORDER[st[t["id"]]], num(t["id"])))
    if scope == "mine":                          # only what needs you
        keep = {"draft", "needs_ack"}
    else:
        keep = set(GLYPH) - {"dropped"}           # a dropped topic is counted, not listed
    return st, counts, [t for t in ordered if st[t["id"]] in keep]


def render_table(state, scope="all"):
    st, counts, shown = _rows(state, scope)
    untitled = [t["id"] for t in shown if needs_title(state, t)]
    if untitled:
        # Refuse instead of rendering a `✍️ needs summary` row. The marker was pasted
        # through on OpenCode (no Stop hook there to block it), wedged mid-cell between
        # the raiser's name and their words, and read as content — so the dirty row is
        # made unreachable rather than merely discouraged, on every agent. The message
        # itself carries the fix; e2e's title_peer_topic parses the handle from it.
        die(f"needs summary: {', '.join(untitled)} — read the threads (`bodies`), "
            + " / ".join(f'`set {x} --summary "..."`' for x in untitled)
            + ", then re-run")
    head = f"**MR !{state['iid']}** — {state.get('title') or ''}"
    if state.get("author"):
        head += f" · by {state['author']}"
    head += f" · {lang_hint(state)}"
    out = [head, ""]
    if shown:
        out += ["| State | Topic | Kind | Src | Location | Summary |",
                "|---|---|---|---|---|---|"]
        for t in shown:
            tid = t["id"]
            s = st[tid]
            summ = short_summary(state, t).replace("|", "\\|")
            if t.get("source") in INBOUND and t.get("by"):
                summ = f"_{t['by']}:_ {summ}"           # who raised this thread
            resolved = any(state["threads"].get(x, {}).get("resolved")
                           for x in t["thread_ids"])
            mark = " ✓" if resolved and s == "needs_ack" else ""   # GitLab-resolved
            if ui.drawn():
                cells = (ui.state_cell(ui_state(state, t, s)),
                         ui.kind_icon(t.get("kind"), t.get("severity")),
                         ui.SOURCE_ICON.get(t.get("source"), ui.SOURCE_ICON["llm"]))
            else:
                cells = (f"{GLYPH[s]} {WORD[s]}", kind_icon(t),
                         SRC_ICON.get(t.get("source"), "🤖"))
            out.append(critical_manifest.mark(
                f"| {cells[0]}{mark} | {tref(f'**{tid}**')} | {cells[1]} | {cells[2]} "
                f"| {loc_md(_loc(state, t))} | {summ} |"))
    else:
        out.append("_✓ nothing needs you right now_" if scope == "mine"
                   else "_no findings yet_")
    warn = summary_language_warning(state, shown)
    if warn:
        out += ["", warn]
    total = len(state["topics"])
    done = counts["acked"] + counts["dropped"]
    prog = [f"{done}/{total} acked/closed"]
    if ui.drawn():
        # The shared vocabulary's words, in the order a person works through them.
        words = [ui_state(state, t) for t in state["topics"]]
        prog = [f"{done}/{total} closed"] + [
            f"{words.count(w)} {ui.STATE_WORD[w]}"
            for w in ("answered", "new", "draft", "waiting") if words.count(w)]
    else:
        if counts["draft"]:
            prog.append(f"{counts['draft']} to post")
        if counts["needs_ack"]:
            prog.append(f"{counts['needs_ack']} need your ack")
        if counts["open"]:
            prog.append(f"{counts['open']} awaiting author")
    tail = ["", "**Topics:** " + " · ".join(prog)]
    return "\n".join(out + tail)


def _suggestion_source(state, body, path):
    """The file a draft's ```suggestion replaces lines of, as lines: the MR's code as it is
    in the review worktree. Read only when the draft holds one."""
    return _worktree_lines(state, path) if path and "```suggestion" in (body or "") else None


def _numbered(content, info, anchor):
    """A suggestion's lines numbered from the range `suggestion:-A+B` implies — A lines
    above the anchor through B below — or unchanged when there is no anchor to count from."""
    m = SUGGESTION_INFO.match(info)
    try:
        start = int(anchor) - int(m.group(1) or 0)
        width = len(str(int(anchor) + int(m.group(2) or 0)))
    except (TypeError, ValueError):
        return content
    return "\n".join(f"{str(start + k).rjust(width)} | {ln}"
                     for k, ln in enumerate(content.splitlines()))


def render_draft(body, path, anchor=None, source=None):
    """Display form of a draft. The paste payload (`draft <t>`) is never touched.

    Three things happen here, each for a reason:

    * ```suggestion is re-fenced to the file's language. GitLab needs the literal
      ```suggestion for one-click-apply, but no terminal or chat renderer knows it as a
      language, so the block the user has to approve would render unhighlighted.
    * Its lines are numbered from the range GitLab's own `suggestion:-A+B` syntax implies
      (A lines above the anchor through B below), so the replacement lines up against the
      source shown above it — and it becomes visible when 3 lines are replaced by 2.
    * The whole draft is blockquoted, so it reads as the one artefact being posted rather
      than as commentary. Its fenced blocks — a suggestion, a snippet, one indented under
      a list item — sit inside the quote too, where they still render highlighted.
    """
    if not body:
        return body
    out = []
    for kind, info, seg in segments(body.splitlines()):
        if kind == "text":
            out += [f"> {ln}".rstrip() if ln.strip() else ">" for ln in seg]
            continue
        # A suggestion is the exact lines GitLab puts in the file, its indentation part of
        # them; any other block is set flush, as a block nested in a list item comes indented.
        content = "\n".join(seg)
        if not SUGGESTION_INFO.match(info):
            content = textwrap.dedent(content)
        if SUGGESTION_INFO.match(info):
            # A diff against the lines it replaces where the file is known (`source`), as
            # GitLab shows a suggestion; numbered lines where it is not.
            diff = suggestion_diff(content, info, anchor, source)
            out += (quoted_fence(diff, diff_info(path)) if diff
                    else quoted_fence(_numbered(content, info, anchor), lang_for(path)))
        else:
            out += quoted_fence(content, block_lang(content, info, path))
    return "\n".join(out)


# Lines of code shown either side of a topic's anchor. The anchor check counts a name
# inside this window as already in view, so both read the one value.
SNIPPET_CONTEXT = 4

# How far above the window the code a summary names may start and still be shown, every
# line in between included. A finding sits deep inside the function it names often enough
# ("`loadDb` returns null on a read error", anchored on the `return null`), and a snippet
# around the anchor alone then shows code that never says what it belongs to. The whole
# stretch is shown rather than its first line and an elision: the lines between are the
# function's own, and leaving them out is what makes the snippet hard to judge.
NAME_REACH = 40


def _names(summary):
    """The identifiers a summary names: what its snippet should show.

    Backticks are the reliable signal, but seeded summaries often carry none (they are
    prose from the reviewer model), so also take code-SHAPED bare words: camelCase or
    PascalCase/ALLCAPS, which English prose in a summary does not produce. "redirectTo"
    and "URLSearchParams" qualify; "instead", "created" and "null" do not."""
    candidates = [raw.strip().rstrip("()").split("(")[0].split(".")[-1].strip()
                  for raw in re.findall(r"`([^`]+)`", summary or "")]
    candidates += re.findall(r"\b[A-Za-z_][A-Za-z0-9_]{3,}\b", summary or "")
    needles = []
    for tok in candidates:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", tok or ""):
            continue
        if len(tok) < 4:
            continue
        if not re.search(r"[a-z][A-Z]|^[A-Z]{2,}", tok):     # must look like code
            continue
        needles.append(tok)
    return list(dict.fromkeys(needles))


def _anchor_view(lines, n, needles, context_lines=SNIPPET_CONTEXT):
    """Where a topic's snippet starts, and the line it should have been anchored on
    instead, if any: `(lo, None)` when the code it names is in view.

    A name in the window around the anchor is in view. One above it, within
    `NAME_REACH`, is brought into view: the snippet starts there. Otherwise the nearest
    line naming it, if the file has one, is where the anchor probably belongs."""
    lo = max(1, n - context_lines)
    hits = [i for i, text in enumerate(lines, 1) if any(x in text for x in needles)]
    if not hits or any(abs(i - n) <= context_lines for i in hits):
        return lo, None
    above = [i for i in hits if i < lo and lo - i <= NAME_REACH]
    if above:
        return max(above), None
    return lo, min(hits, key=lambda i: abs(i - n))


def _worktree_lines(state, file):
    wt = get_worktree(state.get("slug") or "", state.get("iid"))
    full = os.path.join(wt, file) if wt and file else None
    if not (full and os.path.isfile(full)):
        return None
    try:
        with open(full, encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()
    except OSError:
        return None


def code_snippet(state, t, context_lines=SNIPPET_CONTEXT):
    """The lines under discussion, read from the review worktree.

    A draft is a comment about specific code, so the code belongs next to it — without
    this the user is asked to approve a comment about a line they cannot see, and the
    agent's research reads as unsourced assertion. Read from the worktree recorded for
    the repo, NOT from cwd: every shell call resets cwd to the repo root, so a relative
    open() would silently read the wrong checkout (or fail).

    The window is a line count around the anchor, reaching back to the code the summary
    names (`_anchor_view`). It has no notion of syntax state — see
    `lib.snippet.open_construct` for what that breaks and how this corrects for it.
    rework-mr's threads.py has the same hazard in `render_code_context`; both route
    through the same shared scanner now.
    """
    file, line = t.get("file"), t.get("line")
    if not file:
        return None
    lines = _worktree_lines(state, file)
    if lines is None:
        return None
    try:
        n = int(line)
    except (TypeError, ValueError):
        n = None
    if not n or n > len(lines):
        return None
    lo, _ = _anchor_view(lines, n, _names(t.get("summary")), context_lines)
    hi = min(len(lines), n + context_lines)
    lang = lang_for(file)
    opened = open_construct(lines, lo)
    if opened:
        # Extend back to the real opening delimiter so the highlighter sees the whole
        # construct — unless that is far enough away to blow up the snippet, in which
        # case an unhighlighted (but not actively MIS-highlighted) block beats either a
        # wall of unrelated context or a snippet that reads as broken Python.
        if lo - opened[1] <= MAX_BACKTRACK:
            lo = opened[1]
        else:
            lang = ""
    diff = _diff_excerpt(state, file, n, lo, hi)
    if diff:
        return diff
    width = len(str(hi))
    body = []
    for i in range(lo, hi + 1):
        mark = "►" if i == n else " "
        body.append(critical_manifest.mark(f"{mark} {str(i).rjust(width)} | {lines[i - 1]}"))
    return f"```{lang}\n" + "\n".join(body) + "\n```"


def _diff_excerpt(state, file, n, lo, hi):
    """Lines `lo`..`hi` of the file as the MR changes them, line `n` the one the topic is
    on — the same diff the sidebar shows (`lib/code_view.py`), so a card and the sidebar
    draw code one way. None when the MR's diff base is not known yet or git cannot read it;
    the plain excerpt stands in then."""
    wt = get_worktree(state.get("slug") or "", state.get("iid"))
    v = code_view.view(wt, file, n, base=state.get("diff_base")) if state.get("diff_base") else None
    a, b = (code_view.row_of(v["rows"], lo), code_view.row_of(v["rows"], hi)) if v else (0, 0)
    if not (v and v["at"] and a and b):
        return None
    lines = code_view.diff_fence(v, a, b)
    return "\n".join([lines[0], *(critical_manifest.mark(x) for x in lines[1:-1]), lines[-1]])


def anchor_warning(state, t, context_lines=SNIPPET_CONTEXT):
    """Flag a topic whose `line` probably points at the wrong code.

    Seeded line numbers come from review-branch's output — a model's estimate — and they
    are wrong often enough to matter: an anchor off by four lines renders unrelated code
    next to the finding, and a comment posted there lands on the wrong line in GitLab.

    Heuristic: the summary almost always names the culprit. If none of the names it gives
    appear in the snippet shown (`_anchor_view`, which reaches back to the code a finding
    sits inside) but one does appear elsewhere in the file, say so and name the closest
    candidate. Silent when the summary has no usable identifier, or when one of them is
    already in view.

    The check is for the agent, which fixes the anchor: the command it names means
    nothing to the person, so where the mod draws it never reaches a card (see
    `anchor_notes`).
    """
    file, line = t.get("file"), t.get("line")
    needles = _names(t.get("summary"))
    if not (file and line and needles):
        return None
    lines = _worktree_lines(state, file)
    try:
        n = int(line)
    except (TypeError, ValueError):
        return None
    if lines is None:
        return None
    if not 1 <= n <= len(lines):
        return (f"⚠️ **anchor check**: `{file}` has {len(lines)} lines, so line {n} does not "
                f"exist. Seeded line numbers are the reviewer model's estimate — fix it "
                f"with `set {t['id']} --line <n>` before posting.")
    _, best = _anchor_view(lines, n, needles, context_lines)
    if best is None:
        return None
    named = ", ".join(f"`{x}`" for x in needles)
    return (f"⚠️ **anchor check**: no line near {n} mentions {named}; the closest line that "
            f"does is **{best}**. Seeded line numbers are the reviewer model's estimate, so "
            f"verify before posting — `set {t['id']} --line {best}` if that is the right spot.")


def anchor_notes(state, topics):
    """The anchor checks of `topics`, for the agent alone: where the mod draws, a card
    shows the code and never a command the person cannot act on, so the agent reads the
    checks on stderr and fixes the anchors itself, at the import (before any card is
    drawn) and whenever it shows a topic."""
    for t in topics:
        warn = anchor_warning(state, t)
        if warn:
            print("note: " + warn.replace("⚠️ ", "").replace("**", ""), file=sys.stderr)


def needs_title(state, t):
    """Whether this topic still has to be given an English one-liner.

    A closed topic (`acked`) is exempt: nothing acts on it any more, and
    demanding a title for finished work is a chore with no reader — rework-mr's
    `needs_summary` draws the same line at its own terminal status.
    """
    return bool(t.get("needs_title")) and topic_status(state, t) != "acked"


def needs_title_warning(state, t):
    """A topic `adopt_inbound` surfaced from a live thread carries no authored
    `summary` — only the raw thread quote (via `short_summary`'s fallback), in
    whatever language/wording the commenter used. Surface that gap explicitly
    wherever the topic is rendered as a heading, so it never passes as a finished,
    English one-liner."""
    if not needs_title(state, t):
        return None
    return (f"⚠️ **needs an English summary** — `{t['id']}` has no authored "
            f"`summary` yet; the title above is the raw thread quote (as posted, "
            f"not translated/summarized). Run `set {t['id']} --summary \"<short "
            f"English title>\"` before presenting this topic further.")


def in_draft_language(state, t):
    """Whether this topic's authored summary reads as the draft language.

    A `needs_title` topic is skipped: it has no authored summary at all, only a raw
    thread quote, which is in the commenter's language by design. The detection itself
    (and why it is deliberately hard to trip) is `lib.mr_common.reads_as`, shared with
    rework-mr, which runs the same check against the thread language.
    """
    if t.get("needs_title") or not t.get("summary"):
        return False
    return reads_as(t["summary"], state.get("lang") or DEFAULT_LANG)


def summary_language_warning(state, topics):
    """Name the topics whose summary was written in the draft language.

    Summaries, topic headings and the overview table are English; the draft language
    governs one thing only, the body of a comment about to be posted. That split is
    stated in every header, and a whole review still came back with every summary in
    the draft language — so it is checked here rather than only documented, and the
    paste gate refuses to show a view still carrying this warning.
    """
    ids = [t["id"] for t in topics if in_draft_language(state, t)]
    if not ids:
        return None
    lang = state.get("lang") or DEFAULT_LANG
    return (f"⚠️ **summaries must be English** — {', '.join(tref(x) for x in ids)} "
            f"read as {lang}. Only a draft comment body uses {lang}; every summary, "
            f"topic heading and table cell is English. Reword each with "
            f"`set <t> --summary \"<short English line>\"`, then re-render.")


def draft_language_warning(state, body):
    """Say so when a draft reads as a language other than the draft language: a drafting
    prompt names it, and a draft in the wrong one still came back."""
    lang = (state.get("lang") or DEFAULT_LANG).lower()
    other = [x for x in LANG_GIVEAWAYS if x != lang and reads_as(body, x)]
    if not other:
        return None
    return (f"⚠️ this draft reads as {other[0]}, but drafts here are written in {lang}: "
            f"rewrite it in {lang} and store it again.")


def _draft_target(state, t, follow_up):
    """(file, line) a draft's suggestion counts its lines from: the line it will sit under.
    A new thread's is the topic's own, a reply's the line of the thread it goes into
    (`post_draft` picks the same thread). The two differ once the user's own comment became
    the topic's thread."""
    path, anchor = t.get("file"), t.get("line")
    if follow_up and t.get("thread_ids"):
        x = state["threads"].get(_reply_thread(state, t)) or {}
        path, anchor = x.get("file") or path, x.get("line") or anchor
    return path, anchor


def suggestion_warning(state, t, body):
    """Say so when a draft's ```suggestion misses lines its replacement rewrites, or
    replaces blank lines only: once applied, the old lines would stand beside the new. The
    lines around it go with the warning, numbered, so the range can be chosen from them."""
    path, anchor = _draft_target(state, t, bool(t["thread_ids"]))
    source = _suggestion_source(state, body, path)
    for kind, info, seg in segments((body or "").splitlines()):
        if kind == "text" or not SUGGESTION_INFO.match(info):
            continue
        reach = suggestion_reach("\n".join(seg), info, anchor, source)
        if not reach:
            continue
        lo, hi, wider = reach
        shown = range(max(1, lo - 2), min(len(source), hi + 3) + 1)
        return (f"warning: the ```{info} block replaces too little. A suggestion replaces "
                f"exactly the lines its range names, counted from line {anchor}, and the "
                f"lines it misses stay: lines {lo}–{hi} at least, which its replacement "
                f"rewrites, belong in it (```{wider}). Check the lines below for the rest of "
                "what it replaces (a paragraph, a section), set the range to cover all of "
                "it, and store the draft again:\n"
                + "\n".join(f"{i:>5} | {source[i - 1]}" for i in shown))
    return None


def _draft_block(state, t, body, follow_up=False):
    """The drafted comment as the user reads it before posting: a label, then the body.

    Both kinds of draft go through here — the comment that opens a thread, and a
    follow-up reply into a thread already posted — because both are the thing the user is
    being asked to approve, and both must be SHOWN, not just stored. A follow-up used to
    be stored and copyable while no view rendered it: the user was asked to approve text
    the skill never displayed, and the agent's only way to show it was to write it out
    itself, which loses the suggestion re-fencing `render_draft` exists for.

    The draft language rides on the label. It has to stay somewhere in the drafting view —
    it is what keeps the agent from falling back to the documented default language — and
    next to the draft is where it belongs.
    """
    if not body:
        return []
    loc = _loc(state, t, full=True)
    # A topic with no file:line is posted on the MR itself, so "thread on …" would name
    # nothing — say where it goes instead.
    if follow_up:
        where = (f"as a reply in the thread on `{loc}`" if loc
                 else f"as a reply in the {MR_LEVEL} thread")
        kind = "follow-up reply"
    else:
        where = (f"as a new thread on `{loc}`" if loc
                 else f"{MR_LEVEL}: post it on the MR, not on a diff line")
        kind = "comment"
    path, anchor = _draft_target(state, t, follow_up)
    # Where the mod draws, the heading names the draft alone, as every draft is headed in
    # both skills, and a Copy sits beside it; the language is the drafting prompt's to say.
    head = (f"Draft {'follow-up' if follow_up else 'comment'}" if ui.drawn()
            else f"Draft of {kind} to post ({state.get('lang') or DEFAULT_LANG})")
    return ["", ui.heading_md(ui.DRAFT_ICON, head),
            f"_{where[:1].upper()}{where[1:]}:_", "", render_draft(body, path, anchor, _suggestion_source(state, body, path))]


def _reply_thread(state, t):
    """The thread a follow-up reply to `t` goes into: the one awaiting the user, else the
    newest."""
    live = [state["threads"].get(x) or {} for x in t["thread_ids"]]
    return next((x for x, th in zip(t["thread_ids"], live) if th.get("awaiting") == "you"),
                t["thread_ids"][-1])


def analysis_md(why):
    """The agent's analysis, as a card shows it: its own label line, then the text, whose
    code the model wrote in backticks, paragraph by paragraph."""
    return ["", ui.analysis_heading(), "", ui.with_recommendation(why.strip())]


def _code_context(state, t):
    """What `--refine` leaves out for an unposted topic: the code the draft is about.

    The code block carries the location, so there is no separate "open a thread at …,
    then paste" line: it sat directly above the SOURCE snippet and read as if that were
    the paste payload.
    """
    out = []
    snip = code_snippet(state, t)
    if snip:
        out += ["", f"_Code currently in MR — `{_loc(state, t, full=True)}`:_", "", snip]
    warn = None if ui.drawn() else anchor_warning(state, t)
    if warn:
        out += ["", warn]
    return out


# Mirrors rework-mr's threads.py's `_comment_source`: the same side-aware pick of the
# blob, read here from the review worktree, there from the repository being reworked.
def _comment_source(state, x):
    """The file as the comment saw it, as lines — what a ```suggestion's offsets count
    from — or None. Read only when the thread holds a suggestion, since it costs a call:
    from the review worktree when it still has that commit, else from GitLab, which keeps
    it after a force-push has dropped it locally."""
    if not any("```suggestion" in (x.get(k) or "") for k in ("body", "last_body")):
        return None
    sha = x.get("head_sha") if (x.get("side") or "new") == "new" else x.get("base_sha")
    if not (sha and x.get("file")):
        return None
    wt = get_worktree(state.get("slug") or "", state.get("iid"))
    r = (subprocess.run(["git", "-C", wt, "show", f"{sha}:{x['file']}"],
                        capture_output=True, text=True, errors="replace") if wt else None)
    text = (r.stdout if r and r.returncode == 0
            else file_at(state.get("project"), sha, x["file"]))
    return text.splitlines() if text is not None else None


def _thread_context(state, x):
    """What `--refine` leaves out for a posted topic: the thread a follow-up replies
    into — first note, last note, and the count of what sits between them.

    A reviewer's ```suggestion is re-fenced here, never shown raw — as a diff against the
    lines it replaces when the file it was made on can be read: the paste gate refuses a
    raw one on screen, so this output must never contain one."""
    f, ln, src = x.get("file"), x.get("line"), _comment_source(state, x)
    # Where the mod draws, each comment is one tight block with its link; the whole thread
    # is in the sidebar.
    tight = ui.drawn()
    notes = x.get("notes") or []
    first_url = x.get("url") if tight else None
    last_url = (notes[-1].get("url") if notes else None) if tight else None
    out = ["", note_md(x.get("author"), x.get("body"), f, ln, src, compact=tight, url=first_url,
                       mine=tight and bool(x.get("mine")),
                       when=when_of(notes[0].get("at")) if notes else None)]
    if x.get("note_count", 1) > 1:
        skipped = x["note_count"] - 2
        if skipped > 0:
            out += ["", f"_… {skipped} more …_"]
        out += ["", note_md(x.get("last_author"), x.get("last_body"), f, ln, src,
                            compact=tight, url=last_url,
                            mine=tight and bool(notes and notes[-1].get("mine")),
                            when=when_of(notes[-1].get("at")) if notes else None)]
    return out


def _digest(lines):
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()[:16]


def context_digest(state, tid):
    """A fingerprint of the topic's context as it renders RIGHT NOW — exactly the lines
    `--refine` would leave out, so the two cannot drift apart: both come from the same
    two builders above.

    Marking is suspended because this render is thrown away. Its code lines would
    otherwise land in the block manifest's critical lines, and the Stop hook would
    demand lines that appear nowhere in the message.
    """
    t = topic_for(state, tid) or die(f"no topic {tid}")
    with critical_manifest.suspended():
        if not t["thread_ids"]:
            return _digest(_code_context(state, t))
        return _digest([ln for th in t["thread_ids"]
                        for ln in _thread_context(state, state["threads"].get(th, {}))])


def render_quote(state, tid, refine=False):
    """A topic as the user reads it (`_render_quote`), and where the user's own posting of its
    draft no longer holds, the correction under it: its card is the correction's then."""
    body = _render_quote(state, tid, refine)
    t = topic_for(state, tid)
    if t and t.get("post_check") and t.get("post_differs"):
        body += "\n\n" + posted.card_md(t, "comment")
    return body


def _render_quote(state, tid, refine=False):
    """A topic as the user reads it. `refine=True` is the render for re-showing a REWORDED
    draft on the topic they are already looking at: it drops the topic's context — the code
    the comment is about, or the notes of the thread it replies into — and keeps the title,
    the draft and its label.

    The context was pasted when the topic came up and has not changed since; repeating it
    every time a wording is adjusted buries the draft, which is the one thing that did
    change. The title line stays because it is a single line and says which topic this is
    and whether it is posted yet; the label stays because it carries the draft language
    and where the comment goes. A topic with no draft at all has nothing but context, so
    `refine` is refused there rather than rendering an empty answer.

    It is **checked, not trusted**: an anchor fix moves the code out from under a draft and
    a `sync` brings author notes, and leaving out a context that has moved would ask the
    user about a comment they are not looking at. When the digest of the context does not
    match what was last shown — or nothing was ever shown — this renders in full anyway
    and says why. So `refine` is always safe to pass when re-showing.
    """
    t = topic_for(state, tid) or die(f"no topic {tid}")
    if refine and not (t.get("draft") or t.get("note")):
        die(f"--refine re-shows a reworded DRAFT and {tref(tid)} has none — everything it "
            f"renders is context. Use plain `quote {tid}`")
    # A card wraps a long title to its own width, so where the mod draws the summary goes
    # in whole; a pasted heading is cut to a line.
    summ = (t.get("summary") if ui.drawn() else None) or short_summary(state, t)
    out = []
    if refine and t.get("shown") != context_digest(state, tid):
        out.append("_(the code or the thread changed since this was last shown — in full "
                   "again)_" if t.get("shown") else
                   "_(this topic's context has not been shown yet — in full)_")
        refine = False
    if not t["thread_ids"]:                       # a draft — show its draft text
        title = title_md(state, t, summ)
        # Meta lines (title + where to open the thread) are for the user's eyes
        # only — NOT part of the comment. The paste payload is the body below,
        # which `draft <t>` emits on its own for the clipboard / posting.
        # Where the mod draws, the state icon in the title already says it is unposted, and
        # the location follows it, as a posted topic's does.
        loc = _loc(state, t, full=True)
        out.append((f"{title} · {loc_md(loc)}" if loc else title) if ui.drawn()
                   else f"{title}  ({WORD['draft']} — not yet on GitLab)")
        warn = summary_language_warning(state, [t])
        if warn:
            out.append(warn)
        if not refine:
            ctx = _code_context(state, t)
            t["shown"] = _digest(ctx)
            out += ctx
            # The agent's analysis, stored with the finding: what is wrong, why it
            # matters, and whether to raise it. It is what a Draft or a Drop is decided
            # on, and a card a press brings up has no model turn to write it in.
            if t.get("why"):
                out += analysis_md(t["why"])
        if t.get("draft") or t.get("note"):
            out += _draft_block(state, t, t.get("draft") or t.get("note"))
        else:
            # Header + code and silence read as a complete presentation — reported
            # live as title, code fragment, "nothing else", with no sign a comment
            # was missing. The gap is the AGENT's to fill (SKILL: analysis, then ask
            # how to handle it), so the reminder goes to stderr: everything in the
            # returned block is pasted to the user verbatim, and a "write the draft"
            # instruction shown to THEM is the same leak the ✍️ marker was. The
            # follow-up branch deliberately gets no note: a posted topic without a
            # pending reply has nothing missing.
            # It also names the draft language: an unposted topic's header carries no
            # marker (the draft label does), so without a draft there is none on screen
            # at exactly the moment one gets written.
            if ui.drawn() and t.get("why"):
                # The card carries the stored analysis, and its buttons ask the question.
                # "Add only what the card does not say" was read as leave to retell it:
                # the card is the reply, and the user asks for more with a press.
                print(f"note: {t['id']} has no draft yet; its analysis is on the card and "
                      f"its buttons ask the question, so the card is the whole reply. A draft "
                      f"is written in "
                      f"{state.get('lang') or DEFAULT_LANG}", file=sys.stderr)
            else:
                print(f"note: {t['id']} has no draft yet — your reply is the block above "
                      f"+ your analysis of the topic, then ask how they want to handle "
                      f'it; store the agreed draft with `set {t["id"]} --draft "…"`, '
                      f"written in {state.get('lang') or DEFAULT_LANG}",
                      file=sys.stderr)
        return "\n".join(out).strip()
    v = verdict_of(state, t)
    if ui.drawn() and v and not refine and not peer_waiting(state, t):
        return render_answered(state, t, summ, v)
    shown = []                            # the notes, as rendered — see `context_digest`
    # Where the mod draws, a comment of the user's own already posted ends its card on that:
    # when it went up, and, where it is, who resolved its thread. The advice that led to it
    # is followed, so it is not given again.
    first = state["threads"].get((t["thread_ids"] or [None])[0]) or {}
    is_own_posted = ui.drawn() and bool(first.get("mine"))
    tail = []
    for i, th in enumerate(t["thread_ids"]):
        x = state["threads"].get(th, {})
        # An MR-level thread carries no diff position at all, so there is no path to
        # print — `None:` is what an unguarded f-string produced there.
        path = f"{x['file']}:{x.get('line') or ''}" if x.get("file") else ""
        if i == 0:
            title = title_md(state, t, summ)
            # Where the mod draws, the draft's own label carries the language.
            out.append(f"{title} · {loc_md(path)}" if ui.drawn()
                       else f"{title} · {loc_md(path)} · {lang_hint(state)}")
            for warn in (needs_title_warning(state, t),
                         summary_language_warning(state, [t])):
                if warn:
                    out.append(warn)
        else:
            out.append(loc_md(path))
        if x.get("url") and not ui.drawn():       # drawn: each comment links itself
            out.append(x["url"])
        if x.get("resolved"):
            who = first_name(x.get("resolved_by")) or "someone"
            # Where the mod draws, a part of the card of its own, headed as the analysis is,
            # at its end: it changes what the person's next press means.
            if ui.drawn():
                tail.append(f"{ui.heading_md(ui.RESOLVED_ICON, f'Resolved on GitLab by {who}')}\n\n"
                            "That is their toggle: your ack is what closes this topic here.")
            else:
                out.append(f"_(GitLab thread resolved by {who} — that's their toggle; "
                           "your ack is what closes this topic here)_")
        if not refine:
            ctx = _thread_context(state, x)
            shown += ctx
            out += ctx
        out.append("")
    if not refine:
        t["shown"] = _digest(shown)
    while out and not out[-1]:            # the loop's trailing spacer, before the label
        out.pop()
    # A follow-up reply drafted for a thread that is already posted — the author pushed
    # back, or answered without changing anything, and the reworked point needs to reach
    # them. It is the thing being decided, so it goes last, exactly as an unposted
    # topic's draft does. `note` is NOT a fallback here: that is review-branch's seed
    # text for the original comment, long since posted.
    if t.get("why") and topic_status(state, t) not in ("acked", "dropped"):
        # What the agent makes of it: its own finding, now on the user's merged comment, or
        # another reviewer's thread, judged when it came in.
        out += (["", ui.analysis_heading(), "", ui.without_recommendation(t["why"])]
                if is_own_posted else analysis_md(t["why"]))
    if is_own_posted:
        mine = next((n for n in first.get("notes") or [] if n.get("mine")), None)
        when = when_of((mine or {}).get("at"))
        link = f" · [its thread ↗]({mine['url']})" if (mine or {}).get("url") else ""
        out += ["", ui.heading_md(ui.STATE_ICON["waiting"], "Your comment is posted"), "",
                f"{when or 'On GitLab'}{link}"]
    for part in tail:
        out += ["", part]
    v = verdict_of(state, t)
    if v:
        # The agent's judgment of the author's answer, stored when the answer came in: a
        # card a press brings up has no model turn beside it.
        out += ["", ui.heading_md(ui.VERDICT_ICON, f"Verdict: {verdict_word(state, t, v)}"), "",
                v["text"].strip()]
    out += _draft_block(state, t, t.get("draft"), follow_up=True)
    return "\n".join(out).strip()


def render_answered(state, t, summ, v):
    """The card of a topic the author answered, where the mod draws: what the comment asked
    for (the agent's recap: the thread itself is in the sidebar), what the author did since
    (the agent's sentence, and the code that changed on the comment's lines), and the
    agent's verdict. The finding's own analysis and recommendation are history by now, and
    read as if the point still stood, so they are left out. A topic judged before recaps
    were has none, and the card goes from its title straight to what the author did."""
    x = state["threads"].get(t["thread_ids"][0], {})
    path = f"{x['file']}:{x.get('line') or ''}" if x.get("file") else ""
    title = title_md(state, t, summ)
    out = [f"{title} · {loc_md(path)}" if path else title]
    asked = (t.get("asked") or v.get("asked") or "").strip()
    if asked:
        whose = "What you asked" if x.get("mine") else f"What {first_name(x.get('author'))} asked"
        out += ["", ui.heading_md(ui.ASKED_ICON, whose), "", asked]
    author = first_name(state.get("author")) or "the author"
    did = (v.get("did") or "").strip()
    part = ["", ui.heading_md(ui.DID_ICON, f"What {author} did")] + (["", did] if did else [])
    # The code on the comment's lines since it was posted, once the worktree holds the push.
    wt = get_worktree(state.get("slug") or "", state.get("iid"))
    if wt and x.get("head_sha") and x.get("file") and x.get("line") and x.get("side") == "new":
        first = x.get("line_start") or x["line"]
        fence = code_view.since_fence(wt, x["head_sha"], x["file"], int(first), int(x["line"]))
        if fence:
            part += ["", *fence]
    if did or len(part) > 2:
        out += part
    out += ["", ui.heading_md(ui.VERDICT_ICON, f"Verdict: {verdict_word(state, t, v)}"), "",
            v["text"].strip()]
    out += _draft_block(state, t, t.get("draft"), follow_up=True)
    t["shown"] = _digest(out)
    return "\n".join(out).strip()


def render_verdict_change(state, t, before):
    """The card of a verdict the chat changed: the topic's title, the new verdict, what it
    was. The rest of the topic's card is unchanged and up already."""
    v = verdict_of(state, t)
    x = state["threads"].get(t["thread_ids"][0], {})
    path = f"{x['file']}:{x.get('line') or ''}" if x.get("file") else ""
    title = title_md(state, t, t.get("summary") or short_summary(state, t))
    was = verdict_word(state, t, before)
    out = [f"{title} · {loc_md(path)}" if path else title, "",
           ui.heading_md(ui.VERDICT_ICON, f"Verdict: {verdict_word(state, t, v)}"),
           *([f"_was: {was}_"] if was != verdict_word(state, t, v) else []), "", v["text"].strip()]
    return "\n".join(out)


def draft_body(state, tid):
    """The raw comment text for a draft topic — body only, no meta header (that
    would otherwise land in the posted GitLab comment). This is the payload to
    pipe into clip.sh or post via glab; `quote <t>` is the human-facing display."""
    t = topic_for(state, tid) or die(f"no topic {tid}")
    body = t.get("draft") or t.get("note")
    if not body:
        die(f"topic {tid} has no draft yet — set one with "
            f"`set {tid} --draft \"…\"`")
    return body


def first_todo(state):
    """The first topic that needs you: needs-ack before drafts, low tid first."""
    st = {t["id"]: topic_status(state, t) for t in state["topics"]}
    todo = [t for t in state["topics"] if st[t["id"]] in ("needs_ack", "draft")]
    if not todo:
        return None
    return min(todo, key=lambda t: (STATUS_ORDER[st[t["id"]]], num(t["id"])))


# Statuses that are the user's to act on — what keeps a topic current (lib/focus.py).
NEEDS_YOU = ("needs_ack", "draft")


# ---------------------------------------------------------------- rounds
#
# Where the review-ui mod draws, the user works through what needs them in ROUNDS. Between
# rounds the update check only takes notes: news (an answer, a push on a comment's lines,
# another reviewer's thread, a comment of the user's own) marks its topic in the sidebar
# and waits in its inbox; it never becomes the current topic by itself. Opening any topic
# starts a round (`in_round`), and each decision moves on to the next topic that needs the
# user, in list order from the one just left, round to the top again. Moving on from a topic
# leaves it as it is: it comes up again once the round gets back to it. With nothing left, the
# round ends and the inbox is back.
#
# Without the mod the topics come up one after another as they always did (`first_todo`).


def inbox_of(state, t, status=None):
    """Where `t` stands in the inbox: `new` (news the user has not opened), `yours` (opened,
    not decided), or None for a topic that does not need the user (or an
    unposted one in the first pass, which has no news to speak of)."""
    status = status or topic_status(state, t)
    v = peer_view_of(state, t) if peer_waiting(state, t) else None
    if v and v["says"] == "fyi" and t.get("state") != "acked":
        # Listed for the user's information until they open it; never part of a round.
        return None if t.get("seen") == answer_mark(state, t) else "fyi"
    if status not in NEEDS_YOU:
        return None
    if not t["thread_ids"] or t.get("follow_up") == "offer":
        return None
    return "yours" if t.get("seen") == answer_mark(state, t) else "new"


def round_pick(state, after=None):
    """The next topic of the round after `after`: the first that needs the user in list
    order from just past it, round to the top again; never `after` itself."""
    ids = [t["id"] for t in state["topics"]]
    start = ids.index(after) + 1 if after in ids else 0
    order = state["topics"][start:] + state["topics"][:start]
    todo = [t for t in order if t["id"] != after
            and focus_status(state, t) in NEEDS_YOU + ("check", "left")]
    return todo[0] if todo else None


def round_order(state):
    """The topics a round goes through, in the order it visits them: the current one first,
    then on in list order from just past it, wrapping; between rounds,
    the next round's from the top. The sidebar lists them so, as what comes next."""
    cur = current_topic(state)
    ids = [t["id"] for t in state["topics"]]
    start = ids.index(cur) if cur in ids else 0
    order = state["topics"][start:] + state["topics"][:start]
    todo = [t for t in order if focus_status(state, t) in NEEDS_YOU + ("check", "left")]
    return [t["id"] for t in sorted(todo, key=lambda t: (t["id"] != cur, order.index(t)))]


def later_proposals(state):
    """Comments the user posted once the topics ran, matched by the agent and waiting for
    their answer: a step of the round, between two topics."""
    return state.get("own_review") in ("done", "skipped") and bool(state.get("proposals"))


def next_up(state):
    """The topic the current one hands over to (lib/focus.py's `pick_next`)."""
    if not ui.drawn():
        return first_todo(state)
    if not state.get("in_round") or later_proposals(state):
        return None                   # between rounds, or a comment's match is the step
    t = round_pick(state, state.get("focus"))
    if t is None:
        state["in_round"] = False     # nothing left: the round is over
    return t


def focus_status(state, t):
    """`check` while the user's own posting of a draft is being checked: the topic stays
    current until the agent found it sound, or the user keeps it as posted. Once the MR is
    merged or closed, `left` for what still matters (`left_after`); an answer the user has
    not accepted yet stays theirs to check and accept, for their own account of what was
    done; `closed:<status>` for the rest: a closed topic the user opened holds while it is as
    they opened it, and lets go once a press changes it (an ack, a drop), as any other does."""
    if t.get("post_check"):
        return "check"
    if is_over(state):
        if t["id"] in left_after(state):
            return "left"
        s = topic_status(state, t)
        return s if s == "needs_ack" else f"closed:{s}"
    return topic_status(state, t)


# ---------------------------------------------------------------- merged or closed
#
# An MR can be merged (or closed) before the review is through: the author is trusted to
# have done what was needed, so what they answered needs nothing more (the user may still
# check and accept it, for their own account of what was done). What still matters is told, so the
# user can raise it elsewhere or drop it: an issue of high or critical severity nobody
# settled, posted or not. The rest closes quietly; drafts never posted are listed, briefly.
IMPORTANT = ("critical", "high")


def is_over(state):
    return (state.get("mr_status") or {}).get("state") in ("merged", "closed")


def _unsettled(state, t):
    s = topic_status(state, t)
    if s in ("acked", "dropped"):
        return False
    return not (s == "needs_ack" and (verdict_of(state, t) or {}).get("says") == "settled")


def left_after(state):
    """The topics a merged or closed MR leaves the user to see to."""
    return [t["id"] for t in state["topics"] if t.get("kind", "issue") == "issue"
            and t.get("severity") in IMPORTANT and _unsettled(state, t)]


def merged_lead(state, tid):
    """The line above a card that a merged or closed MR brings up: why it came up at all."""
    if not (ui.drawn() and is_over(state) and tid in left_after(state)):
        return ""
    word = (state.get("mr_status") or {}).get("state")
    return f"_!{state.get('iid')} was {word} with this unsettled._\n\n"


def merge_left(state):
    """What a merged or closed MR leaves: `left` (left_after), `drafts` (other drafts never
    posted, listed briefly) and how many closed quietly."""
    left = set(left_after(state))
    rest = [t for t in state["topics"] if t["id"] not in left and _unsettled(state, t)]
    drafts = [t["id"] for t in rest if topic_status(state, t) == "draft"]
    return {"left": [t["id"] for t in state["topics"] if t["id"] in left], "drafts": drafts,
            "quiet": len(rest) - len(drafts)}


CURRENT = focus.Current(focus_status, NEEDS_YOU + ("check", "left"), next_up,
                        claims_idle=lambda state: not ui.drawn())


def start_round(state, tid=None):
    """Open `tid`, or the round's first topic: a round runs from here."""
    state["in_round"] = True
    # A comment of the user's own whose match waits comes first: no topic is current then.
    t = topic_for(state, tid) if tid else None if later_proposals(state) else round_pick(state)
    if t:
        CURRENT.jump(state, t["id"])


def focus_on(state, tid):
    """Make `tid` current at the user's word, as a click in the sidebar does: a round runs
    from here. The topic left stays as it is, and comes up again in its turn."""
    if ui.drawn():
        state.pop("left", None)            # the old put-off-on-leave list, gone with it
        state["in_round"] = True
    CURRENT.jump(state, tid)


def settle(state):
    """Before every save: the current topic as it stands now, kept, so a round that ended
    stays ended (news arriving later must not pick a topic up again), and the topic it
    opened marked as seen by the user."""
    if not ui.drawn() or state.get("own_review") in ("pending", "matching"):
        return
    cur = CURRENT.get(state)
    if cur and state.get("seen_focus") != cur:
        t = topic_for(state, cur)
        t["seen"] = answer_mark(state, t)
        if state.get("worktree_head"):
            # The code the round showed, and what the user had done by then: a decision
            # after this answers the pushes up to it (`check_pushes`).
            t["code_seen"] = {"sha": state["worktree_head"], "mark": decision_mark(state, t)}
    state["seen_focus"] = cur


def drop_stale_drafts(state):
    """Forget each follow-up draft written on an answer that no longer stands: a reply, a
    push or a resolve since makes it a reply to something the author has moved past, and its
    Post would contradict the verdict on the newer answer. A draft from before drafts carried
    their answer is judged by the verdict instead: one that says settled or wait leaves
    nothing for a reply to ask."""
    for t in state.get("topics") or []:
        if not (t.get("thread_ids") and t.get("draft")):
            continue
        mark = answer_mark(state, t)
        v = t.get("verdict") or {}
        if (t["draft_on"] != mark if t.get("draft_on")
                else v.get("on") == mark and v.get("says") in ("settled", "wait")):
            t["draft"] = None
            t.pop("draft_on", None)


def save(path, data):
    """Write a state file. The review's own state is settled first; the same save writes the
    small records beside it too (a prepared seed or explainer, the review language), which
    have no topics and are written as they are."""
    if isinstance(data, dict) and "topics" in data:
        drop_stale_drafts(data)
        settle(data)
    _save(path, data)


def review_begun(state):
    """Whether this MR's review got past its first pass: the agent's findings imported (an
    empty list too), or, from before that mark existed, topics, threads, or the user's own
    review begun. A state file alone says
    nothing: the explainer's choice and the baseline are written before the findings, so a
    first pass stopped there (a press taken back, the session quit) left a file holding no
    review, and a later run that took it for one resumed into nothing at all."""
    return bool(state and (state.get("imported") or state.get("topics") or state.get("threads")
                           or state.get("own_review")))


def own_review_open(state):
    """Whether the topics wait for the user's own review (`own-review`): seeded, and not
    shown yet so they do not steer it, or the user's own comments still being matched to
    them (pending proposals). Set only where the review-ui mod draws: without its buttons
    there is no press to end the wait, so the fallback keeps today's flow."""
    return state.get("own_review") in ("pending", "matching") or bool(state.get("proposals"))


def render_proposal(state, thread=None):
    """One of the user's own comments that the agent matched to a topic, as its card: the
    comment, the topic, the agent's reason and its recommendation. The card's buttons
    answer it. `thread` picks which proposal; the first by default."""
    props = state.get("proposals") or []
    p = next((x for x in props if x["thread"] == thread), None) if thread else (props or [None])[0]
    if not p:
        return "_No comment of yours waits to be matched._"
    x = state["threads"].get(p["thread"]) or {}
    t = topic_for(state, p["into"]) or {}
    loc = _thread_loc(x) or MR_LEVEL
    into = p["into"]
    advice = {"separate": f"keep it apart from {into}, as a topic of its own.",
              "link": f"link it to {into}: it is {into}'s draft, posted by hand."}.get(
        p.get("recommend"), f"merge it into {into}.")
    # The card is about the user's own comment, so it is signed only where someone else
    # answered it and the two must be told apart.
    notes = x.get("notes") or []
    if any(not n.get("mine") for n in notes):
        comment = [note_md(n.get("author"), n.get("body"), x.get("file"), x.get("line"),
                           compact=True, url=n.get("url"), mine=bool(n.get("mine")),
                           when=when_of(n.get("at")))
                   for n in notes]
        comment = [ln for c in comment for ln in (c, "")][:-1]
    else:
        link = f"[{LINK_ICON}]({x['url']}) " if x.get("url") else ""
        body = (x.get("body") or "").strip().splitlines() or [""]
        comment = [f"> {link}{body[0]}".rstrip()] + [f"> {ln}".rstrip() for ln in body[1:]]
    # The topic it may be: its handle and its summary in quotes, so where the title starts
    # and ends is plain; neither a merge nor a split is implied until the recommendation.
    match = f"**Possible match:** {into} — “{t.get('summary') or ''}”" if t else ""
    out = [f"**Your comment** · {loc_md(loc)}", "", *comment, "", match, ""]
    if p.get("why"):
        out += [p["why"].strip(), ""]
    out.append(ui.recommendation_md(advice))
    more = len(props) - 1
    if more:
        out += ["", f"_{more} more of your comments to match after this one._"]
    return "\n".join(out)


def own_threads(state):
    """The user's own posted threads, in the order GitLab lists them, each with what became
    of it: the topic it is on, or the topic the agent proposed merging it into. What the
    sidebar lists while the topics wait for the user's own review."""
    linked = {th: t["id"] for t in state["topics"] for th in t["thread_ids"]}
    proposed = {p["thread"]: p["into"] for p in state.get("proposals") or []}
    return [{"id": tid, "file": x.get("file"), "line": x.get("line"),
             "text": (plain_text(x.get("body")) or "")[:200],
             "topic": linked.get(tid), "proposal": proposed.get(tid)}
            for tid, x in state["threads"].items() if x.get("mine") and not x.get("gone")]


def close_own_review(state, how):
    """End the user's own review (`how`: done; `skipped` is read in older states) and start
    the first round; what
    to print: the first match as its card, else the round's first topic as its card. The
    script shows the topic so the agent does not: shown by the agent too, it came up twice.

    With nothing of theirs to match (`own-review done` finding no comment, a typical case),
    the topic's card says so on a line above it, which is the whole message: an agent left
    to say it writes it under the card and retells the topic with it."""
    state["own_review"] = how
    if not ui.drawn():
        return f"own review {how}"
    start_round(state)        # the first pass is the first round
    if state.get("proposals"):
        return capture_emit(state, render_proposal(state))
    tid = state.get("focus")
    if not topic_for(state, tid):
        return f"own review {how}; no topic needs the user"
    anchor_notes(state, [topic_for(state, tid)])
    block = render_quote(state, tid, None)
    lead = (f"_No comments of yours on !{state.get('iid')} to match: "
            "on to the agent's topics._" if not own_threads(state)
            else "_Every comment of yours is on a topic already: on to the topics._")
    return capture_emit(state, lead + "\n\n" + block, tid)


def capture_emit(state, block, topic=None):
    """`emit`'s output as text, for a caller that prints it after saving."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        emit(state, block, topic)
    return buf.getvalue().rstrip("\n")


def unsettled_own(state):
    """The user's own threads on no topic and in no proposal: what the matching has still to
    settle, or, once the topics run, the comments they posted since."""
    return [x["id"] for x in own_threads(state) if not (x["topic"] or x["proposal"])]


def answer_mark(state, t):
    """What the author's answer on `t` is, as a fingerprint: each thread's newest note and
    whether it is resolved. A verdict is about one answer, so a new note or a resolve makes
    it stale."""
    marks = [(x, (state["threads"].get(x) or {}).get("last_at"),
              bool((state["threads"].get(x) or {}).get("resolved"))) for x in t["thread_ids"]]
    push = pushed_of(state, t)
    if push:
        marks.append(push["to"])          # an answer in code, by the push it came with
    return hashlib.sha1(json.dumps(marks).encode()).hexdigest()[:12]


# ---------------------------------------------------------------- answers in code
#
# An author often answers a comment by pushing the fix and saying nothing. Where the mod
# draws, the update check compares each waiting topic's lines with the MR's new head
# (GitLab's compare, so the review worktree is never touched while the user works) and a
# push that changed them makes the topic the user's to look at, like a reply would.


def decision_mark(state, t):
    """What the user last did on `t`, as a fingerprint: its threads' newest notes and
    resolutions, and its ack. A push noted against one of these is answered once it moves."""
    marks = [(x, (state["threads"].get(x) or {}).get("last_at"),
              bool((state["threads"].get(x) or {}).get("resolved"))) for x in t["thread_ids"]]
    return hashlib.sha1(json.dumps([marks, t.get("state"), t.get("acked_at")]).encode()
                        ).hexdigest()[:12]


def pushed_of(state, t):
    """The push that changed `t`'s lines and that the user has not answered yet, or None:
    `{"from", "to", "at"}`, the code before and after."""
    p = t.get("pushed")
    return p if p and ui.drawn() and p.get("mark") == decision_mark(state, t) else None


def watch_of(t, x):
    """The lines a push is checked against: where the comment is, in the code it was last
    checked at. Started from the thread's own position; None for a comment that is on no
    line of the new code (on the MR as a whole, or on a line the MR removed)."""
    w = t.get("watch")
    if w:
        return w
    if not (x and x.get("file") and x.get("line") and x.get("side") == "new"):
        return None
    sha = x.get("head_sha") or t.get("start_sha")
    if not sha:
        return None
    t["watch"] = {"sha": sha, "first": x.get("line_start") or x["line"], "last": x["line"]}
    return t["watch"]


def _file_diff(diffs, path):
    return next((d for d in diffs or [] if path in (d.get("new_path"), d.get("old_path"))), None)


def check_pushes(state, ctx, iid, base):
    """Note what the MR's pushes since the last check did to the waiting topics: each
    topic whose lines a push changed gets `pushed`, and the push a line of the inbox's
    (`state["pushes"]`). `base` is the MR's merge base now: a new one makes it a rebase."""
    head = mr_head(ctx, iid)
    if not head:
        return
    # A review the check never saw before compares from where the user last reviewed, so the
    # pushes made while it waited reach the inbox as the ones made during a round do.
    seen = state.get("seen_head") or state.get("last_reviewed_head")
    state["seen_head"] = head
    cache = {}

    def compare(frm, to):
        if (frm, to) not in cache:
            cache[(frm, to)] = _compare(ctx, frm, to)
        return cache[(frm, to)]

    # A rebase brings the target branch's own edits along, and a head-to-head compare cannot
    # tell them from the author's: the file's lines may have moved under the comment with no
    # word of the author's. So after a rebase a file counts only where the MR's OWN change to
    # it differs between the two pushes (each push's base to its head, hunk headers aside),
    # as `_author_touch` does for the `updates` report.
    old_base = state.get("diff_base")
    rebased = bool(seen and seen != head and base and old_base and base != old_base)
    authored = None
    if rebased:
        before, after = compare(old_base, seen), compare(base, head)
        if before is not None and after is not None:
            def own(diffs):
                return {(d.get("new_path") or d.get("old_path")):
                        _HUNK_HEADER_RE.sub("", d.get("diff") or "") for d in diffs}
            b, a = own(before), own(after)
            authored = {p for p in set(b) | set(a) if b.get(p) != a.get(p)}

    touched = []
    for t in state["topics"]:
        if not t["thread_ids"]:
            continue
        x = state["threads"].get(t["thread_ids"][0])
        w = watch_of(t, x)
        if not w:
            continue
        p = t.get("pushed")
        sc = t.get("code_seen")
        if (not p and sc and sc.get("sha") not in (None, w["sha"])
                and sc.get("mark") != decision_mark(state, t)):
            # The user acted on the topic after a round showed them this code: whatever a
            # push did to its lines up to there is answered, as a noted push is below.
            p = {"to": sc["sha"]}
        if p and not pushed_of(state, t):
            # Answered since (an ack, a reply): from here on the code is checked from the
            # push the user saw, the comment's lines carried through it.
            seen_diffs = compare(w["sha"], p["to"])
            if seen_diffs is not None:
                d = _file_diff(seen_diffs, x["file"])
                _, a, b = code_view.touched((d or {}).get("diff"), w["first"], w["last"])
                w.update(sha=p["to"], first=a, last=b)
            t.pop("pushed", None)
            p = None
        if w["sha"] == head or (p and p.get("to") == head) or (not p and topic_status(state, t) != "open"):
            continue                      # nothing since, or this push is noted already
        diffs = compare(w["sha"], head)
        if diffs is None:
            continue
        d = _file_diff(diffs, x["file"])
        hit, a, b = code_view.touched((d or {}).get("diff"), w["first"], w["last"])
        if hit and authored is not None and x["file"] not in authored:
            hit = False                   # the rebase moved it, not the author
        if hit:
            t["pushed"] = {"from": (p or {}).get("from") or w["sha"], "to": head,
                           "at": now_iso()}
            t["pushed"]["mark"] = decision_mark(state, t)
            touched.append(t["id"])
        elif not p:
            w.update(sha=head, first=a, last=b)
    if seen and seen != head:
        stat = _gl_compare(ctx, seen, head)
        # Every push is said, dim where it asks nothing: one that left the code as it was
        # (its commits squashed, reworded or reordered; GitLab's own diff of it is empty), and
        # a rebase, by how many of the MR's OWN files it changed (`authored`): a head-to-head
        # size would count the target branch's commits it brought along.
        state.setdefault("pushes", []).append({
            "at": now_iso(), "head": head, "from": seen, "stat": (stat or {}).get("stat"),
            "topics": touched, "rebase": rebased,
            "same": (stat or {}).get("paths") == [],
            "own": len(authored) if rebased and authored is not None else None})


def link_pushes(state, ctx, iid):
    """Each push in the inbox with the link to what it changed: GitLab's diff between the
    MR's version it made and the head the check saw before it."""
    pending = [p for p in state.get("pushes") or [] if not p.get("url") and p.get("head")]
    if not pending:
        return
    vs = versions(ctx, iid) or []
    for p in pending:
        i = next((k for k, v in enumerate(vs) if v.get("head_commit_sha") == p["head"]), None)
        if i is None:
            continue
        start = p.get("from") or (vs[i + 1]["head_commit_sha"] if i + 1 < len(vs) else None)
        p["url"] = (f"{ctx['web']}/-/merge_requests/{iid}/diffs?diff_id={vs[i]['id']}"
                    + (f"&start_sha={start}" if start else ""))


def waits_on_author(state, t):
    """Whether `t`'s answer was judged to leave the next move to the author, and that
    answer still stands."""
    v = t.get("verdict") or {}
    return v.get("says") == "wait" and v.get("on") == answer_mark(state, t)


# What is wrong with an answer that does not settle its topic, as the verdict's heading
# names it: the buttons are the same for each, the heading says which one it is.
VERDICT_CASES = {"not-fixed": "Not fixed", "partly-fixed": "Partly fixed",
                 "fix-wrong": "Fix is wrong", "comment-wrong": "Your comment was wrong"}


def verdict_word(state, t, v):
    if v["says"] == "settled":
        return "Settled"
    word = VERDICT_CASES.get(v.get("case") or "", "Not settled")
    mine = (state["threads"].get(t["thread_ids"][0]) or {}).get("mine")
    return word if mine else word.replace("Your comment", "The comment")


def store_verdict(state, t, says, text, did=None, asked=None, unless_judged=False, case=None):
    """Store the agent's judgment of the answer standing on `t`. With `unless_judged` (the
    background agent's way), nothing is stored where that answer already has a verdict: the
    agent judges what was unjudged when it started, and one stored since, by the user's
    Analyse more in the chat, is the newer word. Returns whether it stored."""
    if unless_judged and verdict_of(state, t):
        return False
    if asked:
        t["asked"] = asked              # about the comment, so it outlasts every answer
    t["verdict"] = {"says": says, "text": text, "did": did, "on": answer_mark(state, t)}
    if says == "more" and case:
        t["verdict"]["case"] = case
    if says == "wait":
        t["draft"] = None               # waiting is choosing not to send it
        t.pop("draft_on", None)
    return True


def verdict_of(state, t):
    """The agent's verdict on the author's answer to `t`, while that answer is the one
    standing: `{"says": "settled" | "more", "text": …}`, or None."""
    v = t.get("verdict")
    if not (v and t["thread_ids"] and topic_status(state, t) == "needs_ack"):
        return None
    return v if v.get("on") == answer_mark(state, t) else None


def needs_verdict(state):
    """The answered topics the agent has not judged yet, where the mod draws. Each card a
    press brings up shows the stored verdict, as a new topic's shows its analysis, so they
    are judged together before the first is shown."""
    if not ui.drawn():
        return []
    return [t["id"] for t in state["topics"]
            if t["thread_ids"] and t.get("follow_up") != "offer"
            and (not (peer_view_of(state, t) or peer_closed(
                t, [x for x in (state["threads"].get(i) for i in t["thread_ids"]) if x]))
                 if peer_waiting(state, t)
                 else topic_status(state, t) == "needs_ack" and not verdict_of(state, t))]


def current_topic(state):
    """The current topic — a jump that still holds, else the first that needs you. None
    while the topics wait for the user's own review, so no view of one is shown."""
    if state.get("own_review") in ("pending", "matching"):
        return None
    if own_review_open(state) and not ui.drawn():
        return None
    # Where the mod draws, a comment of the user's own that waits for its match is a step
    # of the round: the topic being decided stays current, and the match comes up next.
    return CURRENT.get(state)


def _jump_cmd(tid, iid):
    return f"findings.py quote {tid} --focus --iid {iid}"


def draft_version(body):
    """A draft's version: what a Post press names, so it posts the text its card showed."""
    return hashlib.sha1((body or "").encode()).hexdigest()[:12]


def view_context(state, topic=None):
    """Which review a view belongs to, for the review-ui mod: the script and the folder to
    run it in, the MR, and the version of the draft the view shows, if any. A button the
    person presses under the card acts through exactly these."""
    extra = {"review": {"skill": "review-mr", "script": os.path.abspath(__file__),
                        "cwd": os.getcwd(), "iid": state.get("iid")}}
    t = topic_for(state, topic) if topic else None
    body = t and (t.get("draft") or t.get("note"))
    if body:
        extra["draft"] = draft_version(body)
    # Where the mod draws, the opener leaves the table to the panel; the model still needs
    # the overview, so it reads one here, in the payload the person never sees. Not while
    # the user's own review is pending: a model that knows the topics mentions them ("its 5
    # topics are on hold"), and the topics must not steer that review.
    if state.get("own_review") != "pending":
        extra["topics"] = [f"{x['id']} {ui_state(state, x)}: {x.get('summary') or ''}"
                           for x in state["topics"]]
    return extra


def emit(state, block, topic=None):
    """Print a gated view with its manifest, naming the topic it is about and the current
    one, so the paste gate enforces only what belongs to the topic being decided."""
    extra = view_context(state, topic)
    ui.record({"review": extra["review"], "topic": topic, "focus": current_topic(state),
               "draft": extra.get("draft")})
    print(critical_manifest.with_manifest(block, topic=topic, focus=current_topic(state),
                                          extra=extra))


def news_of(state, t):
    """What arrived on `t` that waits for the user, and when it last did: `(kinds, at)`,
    the kinds among `reply`, `resolved`, `push` and `thread` (another reviewer's)."""
    if inbox_of(state, t) is None:
        return [], None
    kinds, at = [], []
    thr = [x for x in (state["threads"].get(i) for i in t["thread_ids"]) if x]
    if peer_waiting(state, t, thr):
        kinds.append("thread")
    if any(x.get("awaiting") == "you" and not x.get("resolved") for x in thr) and "thread" not in kinds:
        kinds.append("reply")
    if any(x.get("resolved") for x in thr):
        kinds.append("resolved")
    at += [x.get("last_at") for x in thr if x.get("last_at")]
    push = pushed_of(state, t)
    if push:
        kinds.append("push")
        at.append(push["at"])
    return kinds, max(at) if at else None


def inbox_json(state, topics):
    """The sidebar's inbox: what arrived since the user last worked through it, by kind,
    each with its topics, the pushes one line each, and what waits. `topics` are
    `state_json`'s records, which carry each topic's own `news`."""
    # News is what the user has not put off: a topic put off is listed as such, once.
    fresh = [x for x in topics if x["inbox"] in ("new", "yours", "fyi")]

    def having(kind):
        return [x["id"] for x in fresh if kind in x["news"]]
    pushes = state.get("pushes") or []
    proposals = [{"thread": p["thread"], "into": p["into"]} for p in state.get("proposals") or []]
    own = unsettled_own(state) if state.get("own_review") in ("done", "skipped") else []
    times = ([x["news_at"] for x in fresh if x["news_at"]] + [p["at"] for p in pushes]
             + [m["at"] for m in state.get("mr_news") or [] if m.get("at")])
    first = round_pick(state)
    return {
        "since": min(times) if times else None,
        "replies": having("reply"), "resolved": having("resolved"),
        "threads": [{"id": x["id"], "by": (topic_for(state, x["id"]) or {}).get("by"),
                     "fyi": x["inbox"] == "fyi"}
                    for x in fresh if "thread" in x["news"]],
        "pushes": [{k: p.get(k) for k in ("at", "stat", "topics", "rebase", "url", "same", "own")} for p in pushes],
        "mr": state.get("mr_news") or [],
        "own": proposals, "matching": len(own),
        "waiting": sum(1 for x in topics if x["status"] == "open"),
        "first": first["id"] if first else None,
        # The sidebar's code is the worktree's; a push after the round opened is not in it.
        "newer_push": bool(state.get("worktree_head") and state.get("seen_head")
                           and state["worktree_head"] != state["seen_head"]),
    }


def judge_list(state):
    """What the mod's background agent has to judge: the topics whose answer has no verdict
    yet, and the user's own comments not matched to a topic yet."""
    return {"topics": needs_verdict(state),
            "own": unsettled_own(state) if state.get("own_review") in ("done", "skipped") else []}


def judge_brief(state, iid, skip=()):
    """The task the mod hands its background agent when the update check found news: each
    item with what to read and what to store, but those in `skip` (what an agent failed on
    before, which the chat takes instead). The agent stores and shows nothing; the user
    finds its judgment on the topic's card when they get to it."""
    j = {k: [x for x in v if x not in skip] for k, v in judge_list(state).items()}
    if not (j["topics"] or j["own"]):
        return ""
    run = (f"cd {shlex.quote(os.getcwd())} && python3 {shlex.quote(os.path.abspath(__file__))}"
           f" <command> --iid {iid}")
    out = [f"New activity arrived on GitLab MR !{iid} while the user reviews it. Judge it in "
           "the background, so that each topic's card carries your judgment by the time the "
           "user opens it. Nobody reads your replies: only what you store counts.",
           "", f"Run every command as `{run}`. Store with the commands below and nothing "
           "else: never post, resolve, open or show anything (no `post`, `resolve`, `open`, "
           "`later`, `quote --focus`, `show_topic`).", ""]
    if j["topics"]:
        out += [f"Read first, once for all of them: `bodies {' '.join(j['topics'])}` (their "
                "threads whole, every comment in order: what was agreed in the middle of a "
                "thread counts as much as its last word), then `diff <t>` for each topic below "
                "that says so: what the author changed since the comment is what a verdict is "
                "about.", ""]
    for tid in j["topics"]:
        t = topic_for(state, tid)
        if peer_waiting(state, t):
            who = t.get("by") or "someone else"
            title = "" if t.get("summary") and not t.get("needs_title") else \
                " --summary \"<English one-liner>\""
            out += [f"- {tid}: a thread by {who}, new or with new comments. It is {who}'s to "
                    f"settle; the user watches it and steps in only when they have something "
                    f"to add. Read it (in `bodies`) and store your view: `peer {tid} fyi|needs "
                    f"--unless-judged{title} --why \"<1–2 English sentences: what it says>\"`. "
                    f"`needs` only when it is the user's to answer: a question to them, a point "
                    f"that repeats or contradicts one of their topics (`--advice merge --into "
                    f"<t>`), or one they would want to clarify (`--advice reply`); its --why then "
                    f"ends on a `**Recommendation:**` sentence saying why. Anything else is `fyi`."]
            continue
        kinds, _ = news_of(state, t)
        # The recap is about the comment, so a topic keeps the one it was first given.
        asked = "" if t.get("asked") else (
            " --asked \"<one sentence: the problem the comment raised and what it asked for, "
            "e.g. '`save()` drops its promise, so a failed write goes unnoticed; asked for an "
            "`await`.'>\"")
        what = ("the author pushed a change to the lines the comment is on, and said nothing"
                if kinds == ["push"] else "the author answered")
        out += [f"- {tid} ({t.get('summary') or 'no summary'}): {what}. Read the thread "
                f"(in `bodies`) and the change since the comment (`diff {tid}`), then store "
                f"one verdict: `verdict {tid} settled|more|wait --unless-judged{asked} --did \"<one sentence: what "
                f"the author did, e.g. 'Pushed the `await` on the commented line; no reply.'>\" "
                f"--text \"<1–3 sentences: whether it holds and why>\"`. Code in backticks (`persistDb`, "
                f"`await persistDb('comment')`), as a card shows it as code. `settled` when the "
                f"comment is answered; `more` takes `--case not-fixed|partly-fixed|fix-wrong|"
                f"comment-wrong` (the last when the author did as asked and the comment itself "
                f"was mistaken) and ends its text on one next step (a reply asking for X, "
                f"accepting a follow-up MR, …). `wait` when the author agreed and the change is still to "
                f"come: the topic goes back to waiting on them, until they push or write."]
    if j["own"]:
        out += ["", "Comments the user posted on GitLab themselves since the topics started. "
                "Compare each with the topics by what it says (file and line are a hint, never "
                "the test), and settle every one:",
                "`propose <discussion_id> --into <t> --summary \"<English one-liner>\" --why "
                "\"<what both say, and where they differ>\" [--recommend separate|link]` for the "
                "same point as a topic (merge is the default; `link` when it is that topic's own "
                "draft, posted by hand), or `add --thread <discussion_id> --source human --file "
                "<f> --line <n> --summary \"<English one-liner>\"` for a point of its own.", ""]
        for tid in j["own"]:
            x = state["threads"].get(tid) or {}
            out.append(f"- {tid} at {_thread_loc(x) or MR_LEVEL}: "
                       f"{(plain_text(x.get('body')) or '').strip()[:400]}")
        out += ["", "The topics:"] + [f"- {t['id']} {topic_status(state, t)}: "
                                      f"{t.get('summary') or ''}" for t in state["topics"]
                                      if topic_status(state, t) != "dropped"]
    out += ["", "End with one line saying what you stored."]
    return "\n".join(out)


def state_json(state):
    """The review as the review-ui mod reads it: one record per topic, the current one,
    and each draft's version. Derived the same way as every table, so the mod's buttons
    never disagree with what the views say."""
    topics = []
    left = set(left_after(state)) if is_over(state) else set()
    for t in state["topics"]:
        body = t.get("draft") or t.get("note")
        kinds, news_at = news_of(state, t)
        topics.append({
            "inbox": inbox_of(state, t), "news": kinds, "news_at": news_at,
            "id": t["id"], "status": topic_status(state, t), "state": ui_state(state, t),
            "kind": t.get("kind"),
            "severity": t.get("severity"), "source": t.get("source"),
            "summary": t.get("summary"), "file": t.get("file"), "line": t.get("line"),
            "posted": bool(t["thread_ids"]), "draft": draft_version(body) if body else None,
            "follow_up": t.get("follow_up"),
            # Posted by the user's own hand: being checked against the draft.
            "post_check": bool(t.get("post_check")),
            "post_differs": bool(t.get("post_check") and t.get("post_differs")),
            # Merged or closed, and not settled: the user's to raise elsewhere or drop.
            "left": t["id"] in left,
            "verdict": (verdict_of(state, t) or {}).get("says"),
            # What a button that acts on the verdict hands the chat, which did not judge it.
            "verdict_text": (verdict_of(state, t) or {}).get("text"),
            "peer": peer_waiting(state, t),
            "peer_view": ({k: v for k, v in peer_view_of(state, t).items() if k != "on"}
                          if peer_waiting(state, t) and peer_view_of(state, t) else None),
            # Another reviewer's thread the user watches and has not closed for themselves.
            "watching": bool(peer_waiting(state, t) and (peer_view_of(state, t) or {}).get("says")
                             == "fyi" and t.get("state") != "acked"),
            "resolved": bool(t["thread_ids"]) and all(
                (state["threads"].get(x) or {}).get("resolved") for x in t["thread_ids"]),
            # Its thread on GitLab, where the box's line about it links.
            "thread_url": next((u for x in t["thread_ids"] if (u := (state["threads"].get(x) or {}).get("url"))), None),
        })
    proposals = []
    for p in state.get("proposals") or []:
        x = state["threads"].get(p["thread"]) or {}
        proposals.append({**p, "where": _thread_loc(x) or MR_LEVEL,
                          "comment": (plain_text(x.get("body")) or "")[:300]})
    def thread_of(x):
        # A thread as the sidebar draws it, in the shape rework-mr's `state_json` gives.
        return {"url": x.get("url"), "file": x.get("file"), "line": x.get("line"),
                "resolved": x.get("resolved"),
                "notes": [{**n, "author": YOU if n.get("mine") else first_name(n.get("author"))}
                          for n in x.get("notes") or [{"author": x.get("author"),
                                                       "body": x.get("body"), "url": x.get("url")}]]}
    own = own_threads(state)
    for x in own:
        x["where"] = _thread_loc(state["threads"][x["id"]]) or MR_LEVEL
        x["thread"] = [thread_of(state["threads"][x["id"]])]
    cur = topic_for(state, current_topic(state)) if current_topic(state) else None
    thread = [thread_of(state["threads"][th]) for th in (cur or {}).get("thread_ids") or []
              if th in state["threads"]]
    return {"skill": "review-mr", "iid": state.get("iid"), "title": state.get("title"),
            "url": state.get("mr_web_url"), "lang": state.get("lang") or DEFAULT_LANG,
            "mr": mr_json(state.get("mr_status")),
            "merged": merge_left(state) if is_over(state) else None,
            "author": first_name(state.get("author")) or None,
            # An explainer recommendation the user has not answered yet: the band asks it.
            "explainer": ({k: state["explainer_choice"][k] for k in ("recommend", "why")}
                          if (state.get("explainer_choice") or {}).get("chosen") is None
                          and state.get("explainer_choice") else None),
            "round": round_order(state) if ui.drawn() else [],
            "current": current_topic(state), "topics": topics,
            "own_review": state.get("own_review"), "proposals": proposals,
            "own_threads": own, "thread": thread,
            "in_round": bool(state.get("in_round")),
            "inbox": inbox_json(state, topics), "judge": judge_list(state)}


def diff_position(ctx, iid, path, line):
    """GitLab's position for a comment on `line` of the new `path`, or None when the MR does
    not change that file, where GitLab cannot anchor one.

    A line the MR added takes `new_line` alone; an unchanged line takes both `old_line` and
    `new_line`, which is what the hunk walk is for. An unchanged line outside every hunk is
    one GitLab's own page lets a reviewer comment on once the lines around it are expanded:
    its old number is the new one less what the hunks before it added.
    """
    refs = (mr_object(ctx, iid).get("diff_refs") or {})
    base = {"position_type": "text", "base_sha": refs.get("base_sha"),
            "start_sha": refs.get("start_sha"), "head_sha": refs.get("head_sha"),
            "old_path": path, "new_path": path}
    for d in api(f"projects/{ctx['enc']}/merge_requests/{iid}/diffs?per_page=100",
                 paginate=True):
        if d.get("new_path") != path:
            continue
        base["old_path"] = d.get("old_path") or path
        old = new = None
        for ln in (d.get("diff") or "").splitlines():
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)", ln)
            if m:
                old, new = int(m[1]), int(m[2])
                continue
            if new is None or ln.startswith("\\"):
                continue
            if ln.startswith("+"):
                if new == line:
                    return {**base, "new_line": line}
                new += 1
            elif ln.startswith("-"):
                old += 1
            else:
                if new == line:
                    return {**base, "old_line": old, "new_line": line}
                old, new = old + 1, new + 1
        # Outside every hunk: the shift of the last hunk that ends above it.
        shift = 0
        for m in re.finditer(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", d.get("diff") or "",
                             re.M):
            a, b = int(m[1]), int(m[2] if m[2] is not None else 1)
            c, n = int(m[3]), int(m[4] if m[4] is not None else 1)
            if c > line:
                break
            shift = (c + n) - (a + b)
        return {**base, "old_line": line - shift, "new_line": line}
    return None


# Set by the review-ui mod for the commands it runs on a person's press. A drafted comment
# is posted only that way: the model has no such press, and the mod refuses its Bash calls
# of `post`, so what reaches GitLab is always text the person saw and chose to send.
PRESS_ENV = "REVIEW_UI_PRESS"


def post_draft(state, ctx, iid, tid, expect):
    """Post `tid`'s draft: a new thread at its line (or on the MR itself), or, for a topic
    already posted, a reply into its thread. Returns the posted note's URL."""
    if os.environ.get(PRESS_ENV) != "1":
        die("posting is the user's press, on the Post button under a draft's card; "
            "nothing else posts. Offer the draft and let them press it.")
    t = topic_for(state, tid) or die(f"no topic {tid}")
    body = draft_body(state, tid)
    if expect and draft_version(body) != expect:
        die(f"the draft of {tref(tid)} changed after the card you pressed Post under was "
            "drawn; nothing was posted. Press Post under the newest card.")
    hits = topic_handles(body)
    if hits:
        die(f"the draft of {tref(tid)} names internal topic handles ({', '.join(hits)}), "
            "which mean nothing on GitLab; nothing was posted. Reword it first.")
    disc = f"projects/{ctx['enc']}/merge_requests/{iid}/discussions"
    if t["thread_ids"]:
        pick = _reply_thread(state, t)
        note = api_post(f"{disc}/{pick}/notes", {"body": body})
        # A follow-up into a thread its author resolved reopens it: the reply asks something
        # of them still, and a resolved thread hides it from their list of open ones.
        th = state["threads"].get(pick) or {}
        if th.get("resolved"):
            api_post(f"{disc}/{pick}?resolved=false", {}, method="PUT")
            th["resolved"] = False
        t["draft"] = None
        t.pop("follow_up", None)
        return f"{ctx['web']}/-/merge_requests/{iid}#note_{note.get('id')}"
    fields = {"body": body}
    if t.get("file") and t.get("line"):
        pos = diff_position(ctx, iid, t["file"], int(t["line"]))
        if not pos:
            die(f"`{t['file']}:{t['line']}` is not part of the MR's diff, so GitLab cannot "
                "anchor a comment there; nothing was posted. Copy it and post it by hand, "
                "or move the topic to a changed line.")
        fields["position"] = pos
    d = api_post(disc, fields)
    attach_thread(state, t, d["id"], ctx, iid)
    first = (d.get("notes") or [{}])[0]
    return f"{ctx['web']}/-/merge_requests/{iid}#note_{first.get('id')}"


def resume_round(state):
    """At the opener, where the mod draws: a first pass left unfinished (topics not posted
    yet) carries on as a round. News on posted topics waits in the inbox for the user."""
    if (ui.drawn() and not state.get("in_round")
            and state.get("own_review") not in ("pending", "matching")
            and any(topic_status(state, t) == "draft" for t in state["topics"])):
        start_round(state)


def render_present(state):
    """Mirrors rework-mr's threads.py's `render_present` almost exactly — the table, then
    the current topic. Which topic that is follows each skill's own status vocabulary,
    so it stays duplicated: see CLAUDE.md's "Sharing vs. duplication"."""
    resume_round(state)
    tid = current_topic(state)
    if ui.drawn():
        if state.get("own_review") == "pending":
            # Nothing about the topics yet: the user reviews on their own first.
            return ("**Agent review done.** This is a good time for your own review in "
                    "GitLab; the agent's topics wait until you say you are done.")
        if own_review_open(state):
            return "_Matching your own comments to the topics._"
        # The mod's sidebar lists every topic, so the opener is the current one alone, and
        # with none current there is nothing to show: the model reads the list in the
        # view's payload (`view_context`).
        return render_quote(state, tid) if tid else ""
    parts = [render_table(state, "all")]
    if tid:
        parts += ["\n---\n", render_quote(state, tid)]
    return "\n".join(parts)


def render_bodies(state, whole=()):
    """Mirrors rework-mr's threads.py's `render_bodies` in structure (first + last note
    per open thread), but the skip-list and the "resolved by" annotation below are
    specific to review-mr's status vocabulary and ack flow — threads.py has neither.
    Stays duplicated: see CLAUDE.md's "Sharing vs. duplication".

    The topics named in `whole` instead, each thread with every comment in order: what was
    agreed halfway through a thread is what a verdict on its last comment turns on. Read
    only, unlike `quote`, which records the context it showed and leaves it out next time."""
    if whole:
        out = []
        for tid in whole:
            t = topic_for(state, tid) or die(f"no topic {tid}")
            for th in t["thread_ids"]:
                x = state["threads"].get(th, {})
                out.append(f"[{tref(tid)}] {_thread_loc(x) or MR_LEVEL}  {x.get('url')}")
                notes = x.get("notes") or [{"author": x.get("author"), "body": x.get("body")}]
                out += [f"  {first_name(n.get('author'))}: {(n.get('body') or '').strip()}"
                        for n in notes]
            out.append("")
        return "\n".join(out).strip() or "(no posted threads yet)"
    out = []
    for t in state["topics"]:
        if topic_status(state, t) in ("acked", "draft"):
            continue
        for th in t["thread_ids"]:
            x = state["threads"].get(th, {})
            res = (f", resolved by {first_name(x.get('resolved_by')) or '?'}"
                   if x.get("resolved") else "")
            out.append(f"[{tref(t['id'])}] {_thread_loc(x) or MR_LEVEL}  "
                       f"(last: {first_name(x.get('last_author'))}{res})  {x.get('url')}")
            out.append(f"  {first_name(x.get('author'))}: {(x.get('body') or '').strip()}")
            if x.get("note_count", 1) > 1:
                out.append(f"  {first_name(x.get('last_author'))} "
                           f"(last of {x['note_count']}): "
                           f"{(x.get('last_body') or '').strip()}")
        out.append("")
    return "\n".join(out).strip() or "(no posted threads yet)"


def render_candidates(state, me):
    """Threads YOU authored on GitLab that aren't linked to any topic yet — the
    input to draft↔posted matching (you confirm each link)."""
    linked = {th for t in state["topics"] for th in t["thread_ids"]}
    out = []
    for tid, x in state["threads"].items():
        if tid in linked or not x.get("mine"):
            continue
        if x.get("gone"):
            continue          # deleted upstream — offering it to link would be a trap
        # A one-line preview, so markdown is flattened rather than cut mid-fence: the
        # first 80 raw characters of a comment can open a ```suggestion block on screen.
        preview = plain_text(x.get("body")) or "(no prose — code only)"
        out.append(f"{tid}  {loc_md(_thread_loc(x))}  {preview[:80]}")
    return "\n".join(out).strip() or "(no unlinked threads of yours)"


# ---------------------------------------------------------------- pushes / diffs


def new_versions(state, vs):
    """Versions pushed since your reviewed baseline, oldest→newest. Empty if the
    baseline is the current tip (no push) or unset."""
    last = state.get("last_reviewed_head")
    if not last:
        return []
    out = []
    for v in vs:                                 # newest first
        if v.get("head_commit_sha") == last:
            break
        out.append(v)
    return list(reversed(out))


def _mr_web(state, ctx, iid):
    return state.get("mr_web_url") or f"{ctx['web']}/-/merge_requests/{iid}"


def _compare(ctx, frm, to):
    """Raw `diffs` array of GitLab's compare API (frm→to, straight). Server-side,
    so it works even after a force-push prunes the old sha locally. None on error."""
    if not frm or not to:
        return None
    try:
        r = api(f"projects/{ctx['enc']}/repository/compare"
                f"?from={frm}&to={to}&straight=true")
    except SystemExit:
        return None
    return (r or {}).get("diffs")


def _gl_compare(ctx, frm, to):
    """Server-side diffstat for frm→to via GitLab's compare API. Robust across
    force-push (GitLab keeps every SHA; the local repo prunes old version heads).
    Returns {'stat': <lib/diffsize.py's text>, 'paths': [...]} or None if it can't."""
    diffs = _compare(ctx, frm, to)
    if diffs is None:
        return None
    # Mirrors lib/hunk.py's `diff_stat`. Kept separate: these
    # payloads are already split per file, so a `+++`/`---` here is always noise, while
    # there it can be a header or content depending on position.
    add = dele = 0
    paths = []
    for d in diffs:
        paths.append(d.get("new_path") or d.get("old_path"))
        for ln in (d.get("diff") or "").splitlines():
            if ln.startswith("+") and not ln.startswith("+++"):
                add += 1
            elif ln.startswith("-") and not ln.startswith("---"):
                dele += 1
    return {"stat": diffsize.text(len(diffs), add, dele), "paths": paths}


def _version_commits(ctx, iid, vid):
    """Commit list of one MR diff version (id, title, message …). Used to classify
    a rebase push by comparing commit messages before/after — works from GitLab
    alone, so it survives the force-push that prunes the old heads locally."""
    v = api(f"projects/{ctx['enc']}/merge_requests/{iid}/versions/{vid}")
    return (v or {}).get("commits") or []


def _rebase_kind(ctx, iid, pred_vid, cur_vid):
    """Classify a rebase: compare the two versions' commit messages. Returns
    {'changed': N, 'subjects': [...]} — N>0 means real commits were edited/added on
    top of the rebase (the annoying mixed push). None if it can't be determined."""
    try:
        pred = _version_commits(ctx, iid, pred_vid)
        cur = _version_commits(ctx, iid, cur_vid)
    except SystemExit:
        return None
    if not cur:
        return None
    seen = {}                                    # multiset of pred commit messages
    for c in pred:
        k = (c.get("message") or c.get("title") or "").strip()
        seen[k] = seen.get(k, 0) + 1
    extra = []
    for c in cur:
        k = (c.get("message") or c.get("title") or "").strip()
        if seen.get(k, 0) > 0:
            seen[k] -= 1                         # matched an unchanged commit
        else:
            extra.append((c.get("title") or k.splitlines()[0] if k else "").strip())
    return {"changed": len(extra), "subjects": [s for s in extra if s]}


def _topics_touching(state, files):
    fs = set(files)
    hit = []
    for t in state["topics"]:
        tf = topic_file(state, t)[0]
        if tf and tf in fs:
            hit.append(t["id"])
    return hit


_HUNK_HEADER_RE = re.compile(r"^@@[^\n]*@@[^\n]*$", re.M)


def _author_touch(ctx, state, pred, v, start, to):
    """(touched topic ids, checked) for a rebased push: which TRACKED topics' files
    the AUTHOR's own change actually differs on between the two pushes.

    A straight `start`→`to` compare cannot tell the author's own edit from whatever
    the rebase pulled in from the new base: a file the TARGET branch touched between
    the two bases looks identical, content-wise, to one the author touched — a plain
    head-to-head diff has no way to tell them apart. Each version's OWN patch — its
    base to its head — isolates just the author's delta; comparing those two patches
    to each other is what actually answers "did the author's own change to this file
    evolve." Hunk headers are stripped first, so a line-number shift from a nearby,
    unrelated upstream edit does not register as a change on its own.
    """
    before = _compare(ctx, pred.get("base_commit_sha"), start)
    after = _compare(ctx, v.get("base_commit_sha"), to)
    if before is None or after is None:
        return [], False

    def by_path(diffs):
        return {(d.get("new_path") or d.get("old_path")):
                _HUNK_HEADER_RE.sub("", d.get("diff") or "") for d in diffs}

    b, a = by_path(before), by_path(after)
    moved = [p for p in set(b) | set(a) if b.get(p) != a.get(p)]
    return _topics_touching(state, moved), True


def _rebase_folded_in_line(ob, nb, rd, scope_note=""):
    """The `⚠️ ... N real change(s) folded in` line — shared by the two message-based
    branches in `render_updates` (content check unavailable, and content check ran
    clean but a message still changed) so the wording can't drift between them."""
    subs = "; ".join(rd["subjects"][:3])
    more = "…" if len(rd["subjects"]) > 3 else ""
    return (f"  - ⚠️ rebase onto new base `{ob}` → `{nb}` **+ "
            f"{rd['changed']} real change(s) folded in**{scope_note} — inspect "
            f"carefully. New/edited commits: {subs}{more}")


def head_report(state, ctx, iid):
    """One-liner for the sync banner: did the author push since your baseline?"""
    cur = mr_head(ctx, iid)
    last = state.get("last_reviewed_head")
    if not last:
        return f"branch tip `{(cur or '')[:12]}` — no reviewed baseline yet (run set-head)"
    if cur == last:
        return f"no push since your last review (tip `{(cur or '')[:12]}`)"
    n = len(new_versions(state, versions(ctx, iid)))
    return (f"**author pushed since your last review** — {n} new version(s); "
            "run `updates` for the diffs")


def render_updates(state, ctx, iid):
    """The 'any updates?' report: each push since your baseline as a compare URL,
    labelled **rebase** or a **diffstat** + topics touched. You add the prose summary
    per push.

    Rebase vs real-change is read from version metadata: a push whose base_commit_sha
    differs from the previous version's is a rebase; same base + new head is fixups. A
    rebase is NOT automatically "no reviewable author change", though — see the
    content check inside the `rebased` branch below for why a fixup folded into the
    same push can still hide one."""
    cur = mr_head(ctx, iid)
    last = state.get("last_reviewed_head")
    if not last:
        return ("_no reviewed baseline yet — run `set-head` after your first pass, "
                f"then updates are tracked from there (tip `{(cur or '')[:12]}`)._")
    vs = versions(ctx, iid)                       # newest first
    new = new_versions(state, vs)                 # oldest → newest
    if not new:
        return f"_no changes since your last review (tip `{(cur or '')[:12]}`)._"
    web = _mr_web(state, ctx, iid)
    pos = {v.get("id"): i for i, v in enumerate(vs)}
    out = [f"**{len(new)} push(es) since your last review** "
           f"(`{last[:12]}` → `{(cur or '')[:12]}`):", ""]
    for n, v in enumerate(new, 1):
        i = pos.get(v.get("id"))
        pred = vs[i + 1] if (i is not None and i + 1 < len(vs)) else None
        start = (pred or {}).get("head_commit_sha") or v.get("base_commit_sha") or last
        to = v.get("head_commit_sha")
        url = f"{web}/diffs?diff_id={v.get('id')}&start_sha={start}"
        rebased = bool(pred) and v.get("base_commit_sha") != pred.get("base_commit_sha")
        out.append(f"- **[push {n}]({url}):**")
        if rebased:
            ob = (pred.get("base_commit_sha") or "")[:8]
            nb = (v.get("base_commit_sha") or "")[:8]
            # Commit-message comparison (`_rebase_kind`) misses exactly the common
            # case of a folded-in fixup: `git commit --amend --no-edit` (or an
            # interactive-rebase `fixup!`) keeps the target commit's message
            # unchanged BY DESIGN. The reliable signal is content — but NOT a plain
            # `start`→`to` compare (that also contains whatever the rebase pulled in
            # from the new base); see `_author_touch` for why and how it isolates
            # just the author's own delta. Checked first, and skips `_rebase_kind`'s
            # extra API calls entirely when it already has an answer.
            touch, checked = _author_touch(ctx, state, pred, v, start, to)
            if touch:
                topics = ", ".join(tref(x) for x in touch)
                out.append(f"  - ⚠️ rebase onto new base `{ob}` → `{nb}` **+ a real "
                           f"change to a file tracked by {topics}** — inspect "
                           "carefully; check via `quote`/the URL, not a plain "
                           "`diff`, since a baseline already past this push would "
                           "show none there even though this one did.")
            else:
                rd = _rebase_kind(ctx, iid, pred.get("id"), v.get("id"))
                if rd is None:
                    out.append(f"  - ↻ rebase onto new base `{ob}` → `{nb}` "
                               "(couldn't classify — inspect via the URL)")
                elif not checked:
                    # The content check itself failed (e.g. a transient API error) —
                    # `touch` came back empty because there was nothing to check, not
                    # because a check ran clean. Don't claim the stronger guarantee
                    # below when it was never verified; fall back to message-only,
                    # with its original hedge intact.
                    if rd["changed"] == 0:
                        out.append(f"  - ↻ rebase onto new base `{ob}` → `{nb}` — "
                                   "commit messages unchanged (likely no author "
                                   "change; a silent `--amend` wouldn't show here, so "
                                   "skim the URL if unsure — the content check that "
                                   "would normally catch it failed)")
                    else:
                        out.append(_rebase_folded_in_line(ob, nb, rd))
                elif rd["changed"] == 0:
                    out.append(f"  - ↻ rebase onto new base `{ob}` → `{nb}` — no "
                               "tracked topic's file differs, and commit messages "
                               "are unchanged")
                else:                # a real commit, but not on a file you're tracking yet
                    out.append(_rebase_folded_in_line(
                        ob, nb, rd, " (none on a topic you're tracking yet)"))
        else:
            cmp = _gl_compare(ctx, start, to)
            if cmp:
                touch = _topics_touching(state, cmp["paths"])
                line = f"  - {cmp['stat']}"
                if touch:
                    line += f" · touches {', '.join(tref(x) for x in touch)}"
                out.append(line)
            else:
                out.append("  - (diffstat unavailable — inspect via the URL)")
    return "\n".join(out)


def render_topic_diff(state, ctx, iid, tid, show=False, inline_limit=80):
    """What the author changed for one topic since you posted it, server-side via the
    compare API (force-push-safe). Always prints the compare URL.

    Plain, it is the AGENT's input: the whole diff, from which it tells the user in a few
    bullets what changed — the user reads that summary, not a dump. `show=True` is for when
    the user asks to see the diff itself: in the agent's viewer window when there is one
    (`_show_in_viewer`), else inline up to `inline_limit` lines, else just the URL. Only
    that form is a gated view.
    """
    t = topic_for(state, tid) or die(f"no topic {tid}")
    if not t["thread_ids"]:
        return f"{tref(tid)} isn't posted on GitLab yet — nothing the author could change"
    start = t.get("start_sha") or state.get("last_reviewed_head")
    if not start:
        return f"{tref(tid)}: no baseline captured — nothing to compare yet"
    cur = mr_head(ctx, iid)
    file = _loc(state, t, full=True).rsplit(":", 1)[0]
    vs = versions(ctx, iid)
    diff_id = vs[0].get("id") if vs else None
    web = _mr_web(state, ctx, iid)
    url = f"{web}/diffs?diff_id={diff_id}&start_sha={start}"
    out = [f"**{tref(tid)}** — author changes since you posted "
           f"(`{start[:12]}` → `{(cur or '')[:12]}`)", url]
    diffs = _compare(ctx, start, cur)
    if diffs is None:
        out.append("_(couldn't fetch the diff — open the URL)_")
        return "\n".join(out)
    picked = [d for d in diffs if file and (d.get("new_path") == file
              or d.get("old_path") == file)] if file else diffs
    if not picked:
        out.append("_(no change to this topic's file in that range — the author may "
                   "have addressed it elsewhere; open the URL for the whole diff)_")
        return "\n".join(out)
    blocks = []
    for d in picked:
        body = (d.get("diff") or "").rstrip("\n")
        old, new = d.get("old_path"), d.get("new_path")
        if d.get("renamed_file") or (old and new and old != new):
            # A pure rename has no diff lines at all, and printing nothing after the URL
            # read as "no change" — the one answer that is wrong.
            out += ["", f"_renamed `{old}` → `{new}`"
                        f"{'' if body else ' — no content change'}_"]
        if body:
            blocks.append(f"--- {new or old}\n{body}")
    text = "\n".join(blocks)
    nlines = text.count("\n") + 1 if text else 0
    if not nlines:
        return "\n".join(out)
    if show:
        viewer = _show_in_viewer(ctx, iid, tid, picked)
        if viewer:
            return "\n".join(out + [""] + viewer)
    if not show or nlines <= inline_limit:
        # `fence` widens past any backtick run inside: a diff of a markdown file carries
        # its own fences, and a bare ``` would close on the first of them.
        out += ["", fence(text, "diff")]
    else:
        out.append(f"_(diff is {nlines} lines — too big to inline; open the URL)_")
    return "\n".join(out)


def topic_patch(picked):
    """The compare API's per-file diffs as one git-style patch, which is what the viewer
    reads. GitLab hands back each file's hunks without headers, so they are written here —
    rename lines included, so the viewer shows a renamed file under its new name."""
    out = []
    for d in picked:
        old, new = d.get("old_path"), d.get("new_path")
        body = (d.get("diff") or "").rstrip("\n")
        out.append(f"diff --git a/{old} b/{new}")
        if old != new:
            out += [f"rename from {old}", f"rename to {new}"]
        if body:
            out += ["--- /dev/null" if d.get("new_file") else f"--- a/{old}",
                    "+++ /dev/null" if d.get("deleted_file") else f"+++ b/{new}", body]
    return "\n".join(out) + "\n"


def _show_in_viewer(ctx, iid, tid, picked):
    """The pointer lines when the topic's diff is now in this agent's viewer window, or
    None to show it inline — no tmux, no viewer, or a window that came back holding
    anything but exactly this patch's files and line counts (see `hunk.show_patch`)."""
    patch = topic_patch(picked)
    path = state_file(STATE_ROOT, ctx["slug"], iid, f"diff-{tid}.patch")
    with open(path, "w") as fh:
        fh.write(patch)
    shown = hunk.show_patch(path, f"{TOPIC_ICON}!{iid}",
                            get_worktree(ctx["slug"], iid) or os.getcwd())
    if not shown:
        return None
    stat = hunk.diff_stat(patch)
    head = f"_Full diff in tmux window {shown[1]}:_ {diffsize.of_stat(stat)}"
    # The only line naming where the diff is, so it must not be the one a paste may drop.
    critical_manifest.mark(head)
    return [head, "", fence("\n".join(hunk.stat_rows(stat)), "yaml")]


# ---------------------------------------------------------------- worktree


def worktree_path(slug, iid):
    return state_file(STATE_ROOT, slug, iid, "worktree")


def get_worktree(slug, iid):
    p = worktree_path(slug, iid)
    if os.path.exists(p):
        with open(p) as f:
            return f.read().strip()
    return None


def set_worktree(slug, iid, path):
    with open(worktree_path(slug, iid), "w") as f:
        f.write(path.strip() + "\n")


def move_worktree(state, ctx, iid):
    """Put this MR's review worktree on the MR's newest push, as the skill's own checkout
    does (detached, worktree cruft discarded: it is disposable), and note where it is. A
    worktree that is not there, or a fetch that fails, leaves it where it was, said on
    stderr: the round still opens, on the code the sidebar had."""
    wt = get_worktree(state.get("slug") or ctx["slug"], iid)
    if not (wt and os.path.isdir(wt)):
        print("note: no review worktree for this MR; the sidebar's code stays as it was",
              file=sys.stderr)
        return
    branch = mr_object(ctx, iid).get("source_branch")

    def git(*a):
        return subprocess.run(["git", "-C", wt, *a], capture_output=True, text=True, timeout=120)
    ok = branch and git("fetch", "--quiet", "origin",
                        f"+refs/heads/{branch}:refs/remotes/origin/{branch}").returncode == 0
    if ok:
        ok = git("checkout", "--quiet", "-f", "--detach", f"origin/{branch}").returncode == 0
    if not ok:
        print(f"note: could not move the review worktree to origin/{branch}", file=sys.stderr)
    head = git("rev-parse", "HEAD")
    if head.returncode == 0:
        state["worktree_head"] = head.stdout.strip()


# ------------------------------------------------- prepared inputs (explainer, seed)
#
# The two slow steps of a first pass — generating the explainer (a subagent, minutes)
# and seeding from review-branch — each produce something that stays valid as long as
# the branch does, but nothing used to remember either. So re-opening a review paid for
# both again, and neither could be relied on to come out the same way twice.
#
# Both are recorded against the MR tip they were made for. A recorded input whose tip
# has moved is reported stale and regenerated rather than reused, so a reuse can never
# quietly show an explainer for code that has since been pushed over. That check is the
# reason this is a recorded path and not just a naming convention on disk.

def prepared_path(slug, iid, what):
    return state_file(STATE_ROOT, slug, iid, f"{what}.json")


def set_prepared(slug, iid, what, path, head):
    rec = {"path": os.path.abspath(os.path.expanduser(path.strip())), "sha": head}
    save(prepared_path(slug, iid, what), rec)
    return rec


def get_prepared(slug, iid, what, head):
    """The recorded path if it still applies, else None and the reason it doesn't.

    The reason is None when simply nothing was ever recorded — the overwhelmingly
    common case, and not worth a word. A recorded input that has stopped applying is
    the interesting one, because it is about to be regenerated at real cost, so that
    always says why."""
    rec = load(prepared_path(slug, iid, what))
    if not rec:
        return None, None
    path, sha = rec.get("path"), rec.get("sha")
    if not path or not os.path.exists(path):
        return None, f"the {what} recorded for MR !{iid} is gone from disk ({path})"
    if sha and head and sha != head:
        return None, (f"the {what} recorded for MR !{iid} was made at `{sha[:12]}`, "
                      f"but the tip is now `{head[:12]}` — regenerate it")
    return path, None


def wrong_checkout_warning(slug, iid, topics):
    """Reason to suspect the review worktree is on the wrong branch, or None.

    The review worktree is detached and shared between the MRs of a repo, so a run that
    fails to check it out lands on the PREVIOUS MR's code — and nothing fails. Every
    command still works, the table still renders, and the only visible symptom is that
    the findings describe files that aren't there, which reads as a bad seed rather
    than a bad checkout. One session concluded exactly that and offered to throw the
    seed away.

    Only fires when NOT ONE located finding's file exists. A few missing files are
    ordinary — a finding can name a file the MR deletes, or sit on the MR itself with
    no location at all — but a whole seed missing means the checkout, not the seed.
    """
    wt = get_worktree(slug, iid)
    if not wt or not os.path.isdir(wt):
        return None
    located = [t.get("file") for t in topics if t.get("file")]
    if not located or any(os.path.exists(os.path.join(wt, f)) for f in located):
        return None
    return (f"⚠️  none of the {len(located)} files these findings name exist in "
            f"{wt} — that worktree is almost certainly checked out on another MR's "
            f"branch. Re-run the checkout "
            f"(`git -C {wt} checkout -f --detach origin/$(findings.py branch "
            f"--iid {iid})`) before reading any of them; the findings are fine.")


# ------------------------------------------------- whether to generate an explainer
#
# An explainer costs minutes and is worth them only for a change a reader has to build a
# mental model of. Size never decided that well: a refactor that moves code between files
# is large and needs no explanation. So the agent assesses the change, from what it reads
# and from `assess_diff`'s measure of how much of it is code that only moved, and
# recommends; the user decides, with a press where the mod draws.

TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec|e2e)/|[._-](test|spec)\.[^/]+$|_test\.[^/]+$")
_COLOR = re.compile(r"\x1b\[[0-9;]*m")


def assess_diff(wt, base):
    """The MR's change outside tests, as numbers: files, lines added and removed, and how
    many of those lines git sees as moved (the same lines elsewhere in the diff, indentation
    aside), which marks a refactor that only moves code. A renamed file counts as moved
    lines, not as a rename, so moving a file reads as the move it is. None where the diff is
    unreadable."""
    def git(*a):
        return subprocess.run(["git", "-C", wt, *a], capture_output=True, text=True, timeout=120)
    r = git("-c", "color.diff.oldMoved=magenta", "-c", "color.diff.newMoved=cyan",
            "-c", "color.diff.old=red", "-c", "color.diff.new=green", "diff", "--color=always", "--no-renames",
            "--color-moved=plain", "--color-moved-ws=allow-indentation-change", f"{base}..HEAD")
    if r.returncode != 0:
        return None
    files, added, removed, moved, path = set(), 0, 0, 0, None
    for raw in r.stdout.splitlines():
        line = _COLOR.sub("", raw)
        if line.startswith("diff --git "):
            path = line.split(" b/", 1)[-1]
            continue
        if not path or TEST_PATH.search(path) or line.startswith(("+++", "---")):
            continue
        if line[:1] not in "+-":
            continue
        files.add(path)
        added += line[0] == "+"
        removed += line[0] == "-"
        moved += raw.startswith(("\x1b[35m", "\x1b[36m"))
    return {"files": len(files), "added": added, "removed": removed, "moved": moved}


# The share of an MR's changed lines (outside tests) that only moved, in percent, from which
# the explainer card says it: below it, moving is incidental and says nothing about whether
# the change is a refactor that needs no explaining.
MOVED_WORTH_SAYING = 20


def assess_line(a):
    total = a["added"] + a["removed"]
    share = round(100 * a["moved"] / total) if total else 0
    size = f"{diffsize.text(a['files'], a['added'], a['removed'])} outside tests"
    return f"{size}, {share}% of those lines only moved" if share >= MOVED_WORTH_SAYING else size


def render_explainer_choice(state):
    """The card that asks whether to generate an explainer: what the change is, measured,
    and the agent's recommendation."""
    c = state["explainer_choice"]
    word = "Generate one" if c["recommend"] == "generate" else "Skip it"
    out = [f"**Explainer for !{state.get('iid')}?**", ""]
    if c.get("stats"):
        out += [assess_line(c["stats"]) + ".", ""]
    out.append(ui.recommendation_md(f"{word}. {c['why'].strip()}"))
    return "\n".join(out)


def explainer_path(ctx, iid):
    """The prepared explainer for this MR's tip, or None. The recorded path is checked
    before the tip is fetched: almost no MR has a prepared explainer, and `present`/`resume`
    are the commands run most, so the common answer must not cost a round trip to GitLab."""
    if not load(prepared_path(ctx["slug"], iid, "explainer")):
        return None
    p, _ = get_prepared(ctx["slug"], iid, "explainer", mr_head(ctx, iid))
    return p


def explainer_line(path, opened=False):
    """The pasteable link for this MR's prepared explainer, or None.

    It belongs to the OPENER's output rather than to the lookup command, for two
    reasons. The user gets it in one fixed place — above the table, which is what they
    read while deciding what to click — instead of wherever the agent chose to mention
    it. And a lookup printing a line the agent must paste is a second gated block that
    tends to share a shell call with the opener, where its extra lines read to the Stop
    hook as lines the agent dropped from the block it was told to paste whole.

    Where the mod draws, it is a link named after the file, which the terminal opens on a
    click, and it says when the explainer was just opened in the browser; a pasted one
    keeps the bare URL, which every client turns into a link. Marked critical, so an
    explainer that exists is one the user is actually handed a way to open.
    """
    if not path:
        return None
    if ui.drawn():
        line = (f"📖 **Explainer:** [{os.path.basename(path)}](file://{path})"
                + (" · opened in your browser" if opened else ""))
    else:
        line = f"📖 **Explainer:** file://{path}"
    return critical_manifest.mark(line)


def open_explainer(state, path):
    """Open a prepared explainer in the browser once, when the user is about to review on
    their own: a generated one opens itself when ready, a prepared one has only its link.
    `REVIEW_UI_NO_OPEN=1` keeps it shut (test runs), and so does a machine without `open`.
    Whether it opened now."""
    if not path or state.get("explainer_opened") or os.environ.get("REVIEW_UI_NO_OPEN") == "1":
        return False
    opener = shutil.which("open") or shutil.which("xdg-open")
    if not opener:
        return False
    subprocess.Popen([opener, path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    state["explainer_opened"] = True
    return True


# How long a merged MR's review worktree is kept after its review last changed.
KEEP_WORKTREE_S = 24 * 3600


def prune_worktrees(ctx, skip_iid=None):
    """Remove the git worktree — never the findings/state file — for any MR of
    this repo that has one recorded and that GitLab now reports merged or closed.

    Each MR gets its own worktree (see the module docstring), so parallel reviews
    of different MRs stopped clobbering each other's checkout — but that also
    means each one needs its own cleanup, or they accumulate forever. `skip_iid`
    is the MR the current run is about to use; pruning it out from under the
    session that asked for it would just mean recreating it a moment later.
    Returns one short line per worktree actually removed, for the caller to relay.
    """
    if not os.path.isdir(STATE_ROOT):
        return []
    prefix = f"{ctx['slug']}--mr"
    removed = []
    for name in sorted(os.listdir(STATE_ROOT)):
        if not name.startswith(prefix) or not name[len(prefix):].isdigit():
            continue
        iid = int(name[len(prefix):])
        if iid == skip_iid:
            continue
        wt = get_worktree(ctx["slug"], iid)
        if not wt:
            continue
        try:
            # mr_object() dies (sys.exit) on any API failure — one deleted/
            # inaccessible old MR must not abort the sweep, let alone the
            # actual review this run is here for.
            state = (mr_object(ctx, iid) or {}).get("state")
        except SystemExit:
            continue
        if state not in ("merged", "closed"):
            continue
        # A review worked on lately may still be open in another session, whose sidebar
        # reads its code from this worktree: it goes once the review has lain idle.
        try:
            idle = time.time() - os.path.getmtime(os.path.join(STATE_ROOT, name, "findings.json"))
        except OSError:
            idle = KEEP_WORKTREE_S
        if idle < KEEP_WORKTREE_S:
            continue
        subprocess.run(["git", "worktree", "remove", "--force", wt],
                        capture_output=True, text=True)
        if os.path.isdir(wt):          # already gone from git's list, still on disk
            shutil.rmtree(wt, ignore_errors=True)
        os.remove(worktree_path(ctx["slug"], iid))
        removed.append(f"MR !{iid} ({state}): removed its review worktree at {wt}")
    return removed


# ---------------------------------------------------------------- draft language

def lang_path(slug):
    return os.path.join(state_dir(slug), "lang")


def get_lang(slug):
    """Draft language for this repo. Repo-wide, NOT per-MR, on purpose: an MR being
    reviewed for the first time has no state file yet, so a per-MR field could never
    be configured up front — the setting has to outlive the individual review."""
    p = lang_path(slug)
    if os.path.exists(p):
        with open(p) as f:
            v = f.read().strip()
            if v:
                return v
    return DEFAULT_LANG


def set_lang(slug, lang):
    with open(lang_path(slug), "w") as f:
        f.write(lang.strip() + "\n")


# ---------------------------------------------------------------- driver


# The commands that only read the state: they run alongside any other, since a write is
# whole or nothing (`save`). The mod asks for these after every view and on every move.
READ_ONLY = ("state", "code", "draft", "path", "begun", "proposal", "bodies", "judge-brief")


def resolve_state(args):
    ctx = context()
    mr = None
    iid = getattr(args, "iid", None)
    if iid is None:
        mr = mr_view()
        # Footgun guard: with no --iid we fall back to the MR of the *current
        # branch*. But every shell call tends to reset cwd to the main repo,
        # which is usually checked out on YOUR own branch — so a bare call
        # silently resolves to your MR and writes findings into the wrong
        # state. This skill only ever reviews someone else's MR, so an inferred
        # MR authored by you is always a mistake: refuse loudly.
        me = current_user()
        author = mr_author_username(mr)
        if me and author and me == author:
            die(f"inferred MR !{mr['iid']} from the current branch, but it's "
                f"authored by you ({me}) — this skill reviews someone else's "
                f"MR. You're likely in the wrong checkout (the main repo, not "
                f"the review worktree). Re-run with --iid <n>.")
        iid = mr["iid"]
    path = state_file(STATE_ROOT, ctx["slug"], iid, "findings.json")
    if args.cmd not in READ_ONLY:
        hold(path)
    state = load(path)
    if state:
        # Dropped with the approval/merge-readiness footer. Popped so an existing review's
        # state file stops carrying a blob nothing reads (removable once no state predates
        # the removal — it costs one line and saves a confusing read of findings.json).
        state.pop("readiness", None)
        # "Won't fix" is gone: a topic closed that way is closed, as an accepted one is.
        for t in state.get("topics") or []:
            if t.get("state") == "wontfix":
                t["state"] = "acked"
        drop_stale_drafts(state)
    # Identity (iid/title/url/author) must come from the MR keyed by `iid`,
    # NEVER from the ambient branch. `mr_view()` reads whatever branch cwd is on,
    # and cwd resets to the main repo (usually YOUR own branch) between shell
    # calls — so using it here bakes the wrong MR's identity into the correct
    # per-iid state file (e.g. your MR !547's title landing in mr540's findings).
    # `mr_object(ctx, iid)` fetches by iid and carries the real author name, so
    # it also subsumes the old name-healing step. Fetch only when identity is
    # actually needed — a healthy resume stays offline.
    needs_identity = (
        state is None
        or state.get("iid") != iid
        or not state.get("title")
        or not state.get("author")
        or state.get("author") == state.get("author_username")
    )
    if needs_identity:
        obj = mr_object(ctx, iid)
        if state is None:
            state = new_state(ctx, obj)
        else:
            state.update(iid=iid, mr_web_url=obj.get("web_url"),
                         title=obj.get("title"), author=mr_author(obj),
                         author_username=mr_author_username(obj))
    # Single override point: the MR's own web_url beats anything reconstructed from
    # the remote (scheme, port, install path). Doing it here means every consumer of
    # ctx["web"] — thread URLs, compare URLs, diff URLs — inherits it for free.
    ctx["web"] = web_base(state.get("mr_web_url")) or ctx["web"]
    state.setdefault("lang", get_lang(ctx["slug"]))   # backfill pre-lang state files
    return ctx, iid, path, state


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")

    pb = sub.add_parser("bodies")
    pb.add_argument("--iid", type=int)
    pb.add_argument("topics", nargs="*", help="these topics' threads whole, every comment")
    for name in ("sync", "todo", "present", "resume", "candidates",
                 "head", "base", "branch", "set-head", "updates", "path", "begun"):
        sub.add_parser(name).add_argument("--iid", type=int)

    pw = sub.add_parser("worktree")
    pw.add_argument("--iid", type=int)
    pw.add_argument("--set", dest="set_path")

    for name, what in (("explainer", "explainer HTML"),
                       ("seed", "review-branch seed JSON")):
        pp = sub.add_parser(name)
        pp.add_argument("--iid", type=int)
        pp.add_argument("--set", dest="set_path",
                        help=f"record this {what} as prepared for the MR's current tip")
        if name == "explainer":
            pp.add_argument("--assess", action="store_true",
                            help="measure the change: files, lines outside tests, share moved")
            pp.add_argument("--recommend", choices=("skip", "generate"),
                            help="store your recommendation, shown to the user to decide")
            pp.add_argument("--why", help="with --recommend: one sentence on what the change is")
            pp.add_argument("--choose", choices=("skip", "generate"),
                            help="the user's answer to the recommendation")

    sub.add_parser("prune").add_argument(
        "--iid", type=int, help="this run's own MR — its worktree is never pruned")

    sub.add_parser("lang").add_argument("--set", dest="set_lang")

    pq = sub.add_parser("quote")
    pq.add_argument("topic")
    pq.add_argument("--iid", type=int)
    REFINE_HELP = ("re-showing a REWORDED draft on a topic already on screen: leave the "
                   "topic's context out — the code, or the notes of the thread a "
                   "follow-up replies into — and print the title, the draft and its "
                   "label. Safe whenever you are re-showing: a context that moved since, "
                   "or was never shown, comes back in full anyway.")
    pq.add_argument("--refine", action="store_true", help=REFINE_HELP)
    pq.add_argument("--focus", action="store_true",
                    help="make this the current topic — the user asked to move to it, or "
                         "the current one is settled. Without it, a topic that is not "
                         "current is shown for your research only")

    pdr = sub.add_parser("draft")
    pdr.add_argument("topic")
    pdr.add_argument("--iid", type=int)

    pdf = sub.add_parser("diff")
    pdf.add_argument("topic")
    pdf.add_argument("--iid", type=int)
    pdf.add_argument("--show", action="store_true",
                     help="the user asked to SEE the diff: put it in your viewer window "
                          "(or inline) and paste this output. Without it the diff is your "
                          "input, to summarise in a few bullets")

    pi = sub.add_parser("import")
    pi.add_argument("file")
    pi.add_argument("--iid", type=int)

    pa = sub.add_parser("add")
    pa.add_argument("--iid", type=int)
    pa.add_argument("--kind", choices=("issue", "question", "praise"))
    pa.add_argument("--severity", choices=tuple(SEV_ICON))
    pa.add_argument("--source", choices=tuple(SRC_ICON))
    pa.add_argument("--thread", dest="thread",
                    help="discussion id of an already-posted thread — links it in the "
                         "same call (use for a comment you posted in the UI yourself)")
    for f in ("summary", "file", "line", "draft", "note", "why"):
        pa.add_argument(f"--{f}")

    ps = sub.add_parser("set")
    ps.add_argument("topic")
    ps.add_argument("--iid", type=int)
    ps.add_argument("--kind", choices=("issue", "question", "praise"))
    ps.add_argument("--severity", choices=tuple(SEV_ICON))
    ps.add_argument("--source", choices=tuple(SRC_ICON))
    ps.add_argument("--state", choices=("acked", "dropped", "reset"))
    for f in ("summary", "file", "line", "draft", "note", "ticket", "start-sha", "why"):
        ps.add_argument(f"--{f}")
    # `set --draft` echoes the refreshed view, so it needs the same switch as `quote`:
    # storing a reworded draft IS the iteration step, and that echo is what the user sees.
    ps.add_argument("--refine", action="store_true", help=REFINE_HELP)

    pd = sub.add_parser("drop")
    pd.add_argument("topic")
    pd.add_argument("--iid", type=int)

    pm = sub.add_parser("merge")
    pm.add_argument("into")
    pm.add_argument("others", nargs="+")
    pm.add_argument("--iid", type=int)

    sub.add_parser("state").add_argument("--iid", type=int)
    sub.add_parser("leave", help="close the topic the user opened to look at: the next one "
                   "that needs them becomes current, or none").add_argument("--iid", type=int)
    sub.add_parser("refresh", help="sync quietly and print the state as JSON (the mod's "
                   "update check)").add_argument("--iid", type=int)
    pn = sub.add_parser("open", help="on a press: move the review worktree to the MR's newest "
                        "push and start a round, at <t> or the first topic that needs you")
    pn.add_argument("topic", nargs="?")
    pn.add_argument("--iid", type=int)
    pjb = sub.add_parser("judge-brief", help="the mod's background agent's task: the answers "
                         "to judge and the comments to match")
    pjb.add_argument("--iid", type=int)
    pjb.add_argument("--skip", action="append", default=[], help="an item an agent failed on")
    pc = sub.add_parser("code", help="a topic's file, or one of your threads', as JSON for "
                        "the mod's sidebar")
    pc.add_argument("topic", nargs="?")
    pc.add_argument("--thread")
    pc.add_argument("--since-comment", action="store_true",
                    help="the change since the comment was posted, not the MR's whole change")
    pc.add_argument("--iid", type=int)

    po = sub.add_parser("own-review", help="the user's own review, before the topics: "
                        "done (sync, then match their comments), or matched")
    po.add_argument("--iid", type=int)
    po.add_argument("step", choices=("done", "matched"))

    pr = sub.add_parser("propose", help="one of the user's own comments is the same point "
                        "as a topic: offer the merge to the user, who confirms it with a press")
    pr.add_argument("thread", help="the comment's discussion id (see `candidates`)")
    pr.add_argument("--into", required=True, help="the topic it is the same point as")
    pr.add_argument("--summary", required=True,
                    help="an English one-liner, for the topic it becomes if kept separate")
    pr.add_argument("--why", required=True,
                    help="one or two English sentences the user decides on: what both say, "
                         "and where they differ, if anywhere")
    pr.add_argument("--recommend", choices=("merge", "separate", "link"), default="merge",
                    help="which answer you recommend; the band offers it first. `link`: the "
                         "comment is <t>'s draft, posted by hand")
    pr.add_argument("--iid", type=int)

    pq = sub.add_parser("proposal", help="a comment of yours the agent matched to a topic, as "
                        "its card (the first, or the one of <discussion_id>)")
    pq.add_argument("thread", nargs="?")
    pq.add_argument("--iid", type=int)

    pf = sub.add_parser("follow-up", help="on a press: the agent adds nothing to the user's "
                        "own comment merged into <t>")
    pf.add_argument("topic")
    pf.add_argument("how", choices=("none",))
    pf.add_argument("--iid", type=int)

    pk = sub.add_parser("settle", help="on a press: merge a proposed comment into its "
                        "topic, or keep it as a topic of its own")
    pk.add_argument("thread")
    pk.add_argument("how", choices=("merge", "separate", "link"))
    pk.add_argument("--iid", type=int)

    pp = sub.add_parser("peer", help="the agent's view of another reviewer's thread: for the "
                        "user's information, or needs them")
    pp.add_argument("topic")
    pp.add_argument("says", choices=("fyi", "needs"))
    pp.add_argument("--summary", help="English one-liner, the topic's title")
    pp.add_argument("--why", help="what the thread says; for `needs`, ending on a "
                    "**Recommendation:** sentence")
    pp.add_argument("--advice", choices=("reply", "merge", "seen"),
                    help="with `needs`: what the user should do (the band's first button)")
    pp.add_argument("--into", help="with --advice merge: the topic the thread repeats")
    pp.add_argument("--unless-judged", action="store_true",
                    help="store nothing where the thread already has a view")
    pp.add_argument("--iid", type=int)

    pv = sub.add_parser("verdict", help="the agent's judgment of the author's answer on <t>, "
                        "shown on its card: settled, or more is needed")
    pv.add_argument("topic")
    pv.add_argument("says", choices=("settled", "more", "wait"))
    pv.add_argument("--asked", help="one English sentence: the problem the comment raised and "
                    "what it asked for, shown first, so what the author did reads against it; "
                    "kept with the topic, so given once")
    pv.add_argument("--case", choices=tuple(VERDICT_CASES),
                    help="with `more`: what is wrong, the verdict's heading on the card")
    pv.add_argument("--unless-judged", action="store_true",
                    help="store nothing where the answer already has a verdict")
    pv.add_argument("--did", help="one English sentence: what the author did since the "
                    "comment (replied …, pushed …), shown above the verdict")
    pv.add_argument("--text", required=True,
                    help="1-3 English sentences: whether the answer holds and why, and for "
                         "`more` the next step you recommend")
    pv.add_argument("--iid", type=int)

    pz = sub.add_parser("resolve", help="on a person's press only: resolve <t>'s GitLab "
                        "threads and ack it")
    pz.add_argument("topic")
    pz.add_argument("--iid", type=int)

    pp = sub.add_parser("post", help="on a person's press only: post a topic's draft")
    pp.add_argument("topic")
    pp.add_argument("--iid", type=int)
    pp.add_argument("--expect", help="the draft version the pressed card showed")

    pq3 = sub.add_parser("posted", help="the user's own posting of <t>'s draft, read back "
                         "from GitLab beside the draft, to check it")
    pq3.add_argument("topic")
    pq3.add_argument("--iid", type=int)
    pq3.add_argument("--ok", action="store_true",
                     help="it holds: the check is over, and the next topic's card follows")
    pq3.add_argument("--differs", metavar="VERDICT",
                     help="it does not hold: your verdict, in a line or two, with the "
                     "corrected version of the posted text on --fix; shows its card")
    pq3.add_argument("--fix", help="with --differs: `-` reads the corrected text from stdin")
    pq3.add_argument("--apply", action="store_true",
                     help="on the user's press only: edit the posted comment on GitLab to "
                     "the corrected version, and close the check")

    pl = sub.add_parser("link")
    pl.add_argument("topic")
    pl.add_argument("discussion_id", nargs="?",
                    help="omit to auto-pick the one unlinked thread of yours on this "
                         "topic's file (the 'I just posted it by hand' case)")
    pl.add_argument("--iid", type=int)
    pl.add_argument("--start-sha", dest="start_sha")

    args = ap.parse_args()
    cmd = args.cmd or "sync"

    # lang is repo-level (no MR needed) — it must be settable before any MR in the
    # project has ever been reviewed, which is exactly when no state file exists.
    if cmd == "lang":
        ctx = context()
        if args.set_lang:
            set_lang(ctx["slug"], args.set_lang)
        print(get_lang(ctx["slug"]))
        return

    # worktree is per-MR now (one checkout per MR, not shared repo-wide)
    if cmd == "worktree":
        ctx = context()
        if args.iid is None:
            hint = f" --set {args.set_path}" if args.set_path else ""
            die("worktree is recorded per MR now, not repo-wide — say which one: "
                f"findings.py worktree --iid <n>{hint}  (<n> is the MR's IID, e.g. 2 for MR !2)")
        if args.set_path:
            set_worktree(ctx["slug"], args.iid, args.set_path)
        print(get_worktree(ctx["slug"], args.iid) or "")
        return

    # prune is repo-level (sweeps every MR's worktree for this repo, no single
    # MR's state needs loading)
    if cmd == "prune":
        ctx = context()
        for line in prune_worktrees(ctx, skip_iid=args.iid):
            print(line)
        return

    ctx, iid, path, state = resolve_state(args)
    if cmd == "state":                    # offline, and asked for after every view
        print(json.dumps(state_json(state)))
        return
    if cmd == "leave":                    # offline: the sidebar's close
        CURRENT.leave(state)
        save(path, state)
        print(json.dumps(state_json(state)))
        return
    if cmd == "code":                     # offline: the sidebar asks on every move
        # The file the sidebar shows: the topic's, or one of the user's own threads'.
        if args.thread:
            x = state["threads"].get(args.thread) or die(f"no thread {args.thread}")
            file, line, since = x.get("file"), x.get("line"), x.get("head_sha")
        else:
            t = topic_for(state, args.topic) or die(f"no topic {args.topic}")
            x = state["threads"].get(t["thread_ids"][0]) if t["thread_ids"] else None
            file = (x or {}).get("file") or t.get("file")
            line = (x or {}).get("line") or t.get("line")
            since = (x or {}).get("head_sha")
        wt = get_worktree(state.get("slug") or "", state.get("iid"))
        # Since the comment: what the author did about it, the diff from the code it was
        # posted on (a topic whose lines a push changed opens on it in the sidebar).
        mine = bool(args.since_comment and since and (x or {}).get("side") != "old")
        v = code_view.view(wt, file, line, since, start=(x or {}).get("line_start"),
                           side=(x or {}).get("side") or "new",
                           base=since if mine else state.get("diff_base"))
        if v:
            v["mode"] = "since" if mine else "mr"
            push = pushed_of(state, t) if not args.thread else None
            v["since_at"] = (push or {}).get("at")
        if v and v["line"] and v["side"] == "new" and not args.thread:
            # Where the code the topic names begins, as its card's snippet reaches back to.
            at = _anchor_view(code_view.new_lines(v), v["line"], _names(t.get("summary")))[0]
            v["from"] = code_view.row_of(v["rows"], at)
        print(json.dumps(v))
        return
    me = current_user()
    author = state.get("author_username")
    if me is None and cmd in ("sync", "todo", "present", "candidates"):
        print("warning: could not determine your glab username — needs-ack and "
              "candidates may be wrong (is `glab auth status` OK?)", file=sys.stderr)

    if cmd == "path":
        print(path)
    elif cmd == "begun":
        # FRESH where the first pass never got as far as the findings: it starts over.
        print("RESUME" if review_begun(state) else "FRESH")
    elif cmd == "sync":
        sync(state, fetch_threads(ctx, iid, me, author), ctx, iid)
        state["diff_base"] = mr_base(ctx, iid) or state.get("diff_base")
        save(path, state)
        print(render_table(state, "all"))
        print("\n_" + head_report(state, ctx, iid) + "_")
    elif cmd == "todo":
        sync(state, fetch_threads(ctx, iid, me, author), ctx, iid)
        save(path, state)
        # Where the mod draws, the sidebar is the table: the model reads the list in the
        # payload, and the person gets no card.
        emit(state, "" if ui.drawn() else render_table(state, "mine"))
    elif cmd == "present":
        sync(state, fetch_threads(ctx, iid, me, author), ctx, iid)
        save(path, state)
        exp = explainer_path(ctx, iid)
        opened = (ui.drawn() and state.get("own_review") == "pending"
                  and open_explainer(state, exp))
        ex = explainer_line(exp, opened)
        emit(state, (f"{ex}\n\n" if ex else "") + render_present(state),
             current_topic(state))
        # Again, after the render: `render_quote` records the context it just showed, and
        # `--refine` compares against that. Every command that renders a topic in full
        # saves for this reason, and it is the only reason a view writes at all.
        save(path, state)
    elif cmd == "bodies":
        print(render_bodies(state, args.topics))
    elif cmd == "candidates":
        sync(state, fetch_threads(ctx, iid, me, author), ctx, iid)
        save(path, state)
        print(render_candidates(state, me))
    elif cmd == "quote":
        topic_for(state, args.topic) or die(f"no topic {args.topic}")
        if args.focus:
            focus_on(state, args.topic)
        note = CURRENT.view(state, args.topic, args.focus, _jump_cmd(args.topic, iid))
        if note:
            print(note, file=sys.stderr)
        if ui.drawn():
            anchor_notes(state, [topic_for(state, args.topic)])
        emit(state, merged_lead(state, args.topic) + render_quote(state, args.topic, args.refine), args.topic)
        save(path, state)                 # the context digest, and the current topic
    elif cmd == "draft":
        print(draft_body(state, args.topic))
    elif cmd == "diff":
        note = CURRENT.view(state, args.topic, False, _jump_cmd(args.topic, iid))
        if note:
            print(note, file=sys.stderr)
        emit(state, render_topic_diff(state, ctx, iid, args.topic, args.show), args.topic)
        save(path, state)                 # a current topic it claimed, when none was
    elif cmd == "updates":
        sync(state, fetch_threads(ctx, iid, me, author), ctx, iid)
        save(path, state)
        emit(state, render_updates(state, ctx, iid))
    elif cmd == "resume":
        # The complete opener for an in-progress review, in one call: pushes since your
        # baseline THEN the overview table THEN the current topic.
        # Deliberately one command rather than "run updates, then run present": as two
        # steps the second one gets skipped, and the overview table — the user's only
        # view of where all topics stand — silently goes missing.
        #
        # A single combined print (not three separate ones) so the ONE trailing
        # manifest covers everything this command built — updates has no critical
        # content of its own, but present's table (and the current topic's code, if any)
        # does, and paste-gate.py needs it all in one place to check the whole thing.
        sync(state, fetch_threads(ctx, iid, me, author), ctx, iid)
        save(path, state)
        ex = explainer_line(explainer_path(ctx, iid))
        p = render_present(state)
        if ui.drawn():
            # The pushes, replies and threads since the last review are the sidebar's
            # inbox, as news arriving during a session is; the user opens it.
            block = "\n\n".join(x for x in (ex, p) if x)
            if block:
                emit(state, block, current_topic(state))
            else:
                # No view, but the review is still the mod's to take up: its sidebar is
                # where the inbox this line points the model at lives.
                ui.record({"review": view_context(state)["review"], "topic": None,
                           "focus": current_topic(state), "draft": None})
                print("Resumed. What arrived since the last review is in the sidebar's "
                      "inbox, where the user opens it: nothing to show or offer here.")
        else:
            u = render_updates(state, ctx, iid)
            emit(state, (f"{ex}\n\n" if ex else "") + f"{u}\n\n---\n\n{p}",
                 current_topic(state))
        save(path, state)                 # the context digest present recorded
    elif cmd == "head":
        print(head_report(state, ctx, iid))
    elif cmd == "branch":
        # The MR's own source branch, for the checkout that puts the review worktree on
        # the code being reviewed. It exists because the checkout is the one step with
        # no local source of truth: the worktree is detached and shared between MRs, so
        # whatever it happens to sit on is the PREVIOUS MR's branch, and a run that
        # guesses from it reviews the wrong diff while every command still succeeds.
        obj = mr_object(ctx, iid)
        print(obj.get("source_branch") or die(f"MR !{iid} has no source_branch"))
    elif cmd == "base":
        # GitLab's own merge-base for this MR — feed it to review-branch
        # (REVIEW_BRANCH_BASE) instead of letting it re-derive one locally; see
        # lib/gitlab.py's mr_base for why the local guess can drift.
        base = mr_base(ctx, iid) or die(f"MR !{iid} has no diff_refs.base_sha")
        if state.get("diff_base") != base:
            state["diff_base"] = base      # what the sidebar's file shows the MR's diff against
            save(path, state)
        print(base)
    elif cmd == "explainer" and (args.assess or args.recommend or args.choose):
        state or die("no review state yet: run `set-head` first")
        if args.assess:
            wt = get_worktree(ctx["slug"], iid) or die("no review worktree for this MR")
            a = assess_diff(wt, state.get("diff_base") or mr_base(ctx, iid)) or die(
                "could not read the MR's diff in the review worktree")
            state["explainer_stats"] = a
            save(path, state)
            print(assess_line(a))
        elif args.recommend:
            args.why or die("--recommend needs --why: one sentence on what the change is")
            state["explainer_choice"] = {"recommend": args.recommend, "why": args.why,
                                         "stats": state.get("explainer_stats"), "chosen": None}
            save(path, state)
            emit(state, render_explainer_choice(state))
        else:
            c = state.get("explainer_choice") or die("no explainer recommendation to answer")
            c["chosen"] = args.choose
            save(path, state)
            print(f"explainer: {args.choose}")
    elif cmd in ("explainer", "seed"):
        head = mr_head(ctx, iid)
        if args.set_path:
            c = (state or {}).get("explainer_choice") if cmd == "explainer" else None
            if c and c.get("chosen") is None:
                c["chosen"] = "generate"      # one was made: that was the answer
                save(path, state)
            rec = set_prepared(ctx["slug"], iid, cmd, args.set_path, head)
            print(f"{cmd} recorded for MR !{iid} at "
                  f"`{(rec['sha'] or '')[:12]}`: {rec['path']}")
        else:
            p, why = get_prepared(ctx["slug"], iid, cmd, head)
            # A bare path, or nothing at all. Both lookups exist to be BRANCHED ON —
            # generate, or don't — so the answer is the path and never a sentence. What
            # the user sees is the opener's own explainer line (see `explainer_line`)
            # and the imported topics; neither is this command's job to render.
            if p:
                print(p)
            if why:
                print(why, file=sys.stderr)
    elif cmd == "set-head":
        state["last_reviewed_head"] = mr_head(ctx, iid)
        save(path, state)
        print(f"reviewed baseline set to `{(state['last_reviewed_head'] or '')[:12]}`")
        # Said before the findings are written: their analysis names the author, and with
        # no name in front of it the agent took the one from the skill's own example.
        if state.get("author"):
            print(f"MR author: {state['author']} (call them that)")
    elif cmd == "import":
        with open(args.file) as f:
            items = json.load(f)
        added = [add_topic(state, **it)["id"] for it in items]
        state["imported"] = True              # the first pass is through (`review_begun`)
        c = state.get("explainer_choice")
        if c and c.get("chosen") is None:
            # The review moved past the question: answered in words, and not recorded. Left
            # open, the question's buttons stayed the step's, on every card after it.
            c["chosen"] = "unrecorded"
        if ui.drawn() and "own_review" not in state:
            state["own_review"] = "pending"   # a fresh review: the user reviews first
        save(path, state)
        fresh = [t for t in state["topics"] if t["id"] in added]
        if state.get("own_review") == "pending":
            # The user sees this line under the opener's card: how many topics there are
            # is already a hint, and the topics stay out of sight until their review is in.
            print("imported the agent's findings; they wait for the user's own review, and "
                  "until it is in, none of them is mentioned: not what one says, not how many")
        else:
            print(f"added {len(added)} findings: "
                  f"{', '.join(tref(x) for x in added)}")
        if ui.drawn():
            anchor_notes(state, fresh)
        # Said at the import, which is the first moment the two can be compared — and
        # well before the agent starts reading code that isn't the code under review.
        wrong = wrong_checkout_warning(ctx["slug"], iid,
                                       [t for t in state["topics"] if t["id"] in added])
        if wrong:
            print(wrong)
        # Said here, not just in the next table: the seed is where a whole batch of
        # summaries in the draft language arrives, and fixing them one command later
        # is cheaper than after they have been shown.
        warn = summary_language_warning(state, [t for t in state["topics"]
                                                if t["id"] in added])
        if warn:
            print(warn)
    elif cmd == "add":
        t = add_topic(state, kind=args.kind, severity=args.severity,
                      source=args.source, summary=args.summary, file=args.file,
                      line=args.line, draft=args.draft, note=args.note, why=args.why)
        if args.thread:
            # Adopting a comment you posted yourself: creating the topic and linking the
            # thread are one intention, so they are one call. Splitting them produced a
            # pointless "link it too?" confirmation.
            attach_thread(state, t, args.thread, ctx, iid)
        save(path, state)
        print(t["id"])
        warn = summary_language_warning(state, [t])
        if warn:
            print(warn)
    elif cmd == "set":
        t = topic_for(state, args.topic) or die(f"no topic {args.topic}")
        if args.state == "reset":
            t["state"] = None
            t.pop("acked_at", None)
        elif args.state:
            t["state"] = args.state
            # Stamped so a later author note can be recognised as newer than the ack.
            t["acked_at"] = now_iso() if args.state == "acked" else None
        fld = {"start-sha": "start_sha"}
        for f in ("kind", "severity", "source", "summary", "file", "line",
                  "draft", "note", "ticket", "start-sha", "why"):
            v = getattr(args, f.replace("-", "_"))
            if v is not None:
                t[fld.get(f, f)] = v
        if args.draft is not None:
            # When it was written: a comment the user posts by hand is one made after this.
            t["draft_at"] = now_iso()
        if args.summary is not None:
            t["needs_title"] = False              # now has an authored one-liner
        if args.draft is not None and t["thread_ids"]:
            # A follow-up answers the thread as it stands now (`drop_stale_drafts`).
            t["draft_on"] = answer_mark(state, t)
        save(path, state)
        if args.summary is not None:
            # Answered at the moment the wrong-language summary is stored, rather than
            # two commands later when a whole table has to be re-rendered.
            warn = summary_language_warning(state, [t])
            if warn:
                print(warn)
        if args.draft is not None:
            for warn in (draft_language_warning(state, args.draft),
                         suggestion_warning(state, t, args.draft)):
                if warn:
                    print(warn)
            # Echo the refreshed view: after storing a draft the agent needs to show it,
            # and reconstructing the block by hand reintroduces the raw ```suggestion
            # fence (unhighlighted) that render_quote deliberately re-fences for display.
            # Printing it here means the correct block is already in front of it.
            # Drafting for a topic is asking the user to approve it, so it is current.
            focus_on(state, args.topic)
            emit(state, render_quote(state, args.topic, args.refine), args.topic)
            save(path, state)             # the context digest, and the current topic
    elif cmd == "drop":
        # Kept, marked: a dropped topic is still listed (collapsed, with the other closed
        # ones) so a drop can be seen and undone (`set <t> --state reset`), and its
        # threads stay linked, so `sync` never adopts them again as new.
        t = topic_for(state, args.topic) or die(f"no topic {args.topic}")
        t["state"] = "dropped"
        save(path, state)
    elif cmd == "merge":
        into = topic_for(state, args.into) or die(f"no topic {args.into}")
        for oid in args.others:
            o = topic_for(state, oid)
            if not o:
                continue
            into["thread_ids"] += [x for x in o["thread_ids"]
                                   if x not in into["thread_ids"]]
            if into.get("source") != o.get("source"):
                into["source"] = "both"
            state["topics"] = [t for t in state["topics"] if t["id"] != oid]
        save(path, state)
    elif cmd == "own-review":
        if args.step == "done":
            state["own_review"] = "matching"
            sync(state, fetch_threads(ctx, iid, me, author), ctx, iid)
            save(path, state)
            if unsettled_own(state) or not ui.drawn():
                print(render_candidates(state, me))
                return
        else:
            left = unsettled_own(state) if args.step == "matched" else []
            if left:
                die(f"still to settle: {', '.join(left)} — `propose` each into the topic it "
                    "matches, or `add --thread` it as a topic of its own")
        print(close_own_review(state, "done"))
        save(path, state)
    elif cmd == "propose":
        topic_for(state, args.into) or die(f"no topic {args.into}")
        args.thread in state["threads"] or die(f"no thread {args.thread} (see `candidates`)")
        props = [p for p in state.get("proposals") or [] if p["thread"] != args.thread]
        state["proposals"] = props + [{"thread": args.thread, "into": args.into,
                                       "summary": args.summary, "why": args.why,
                                       "recommend": args.recommend}]
        save(path, state)
        print(f"proposed: {args.thread} → {tref(args.into)}")
    elif cmd == "follow-up":
        t = topic_for(state, args.topic) or die(f"no topic {args.topic}")
        t.pop("follow_up", None)
        t["draft"] = None
        save(path, state)
        print(f"{tref(args.topic)}: nothing to add; waiting on the author")
    elif cmd == "proposal":
        emit(state, render_proposal(state, args.thread))
    elif cmd == "settle":
        props = state.get("proposals") or []
        p = next((x for x in props if x["thread"] == args.thread), None) or \
            die(f"no proposal for {args.thread}")
        state["proposals"] = [x for x in props if x is not p]
        if args.how == "link":
            # The comment is the topic's own draft, posted by hand: it becomes the topic's
            # thread, as `link` makes it, and the draft is spent.
            into = topic_for(state, p["into"]) or die(f"no topic {p['into']}")
            attach_thread(state, into, p["thread"], ctx, iid)
            done = f"linked to {tref(p['into'])} as its posted comment"
        elif args.how == "merge":
            into = topic_for(state, p["into"]) or die(f"no topic {p['into']}")
            attach_thread(state, into, p["thread"], ctx, iid)
            if into.get("source") not in ("human", "both"):
                into["source"] = "both"
            # The user's comment is the topic's thread now; the agent's own findings on it
            # (a better fix, a proven alternative) can still go in as a follow-up reply,
            # so the topic comes up once more to ask.
            into["follow_up"] = "offer"
            done = f"merged into {tref(p['into'])}"
        else:
            x = state["threads"].get(p["thread"]) or {}
            t = add_topic(state, source="human", summary=p["summary"], file=x.get("file"),
                          line=x.get("line"))
            attach_thread(state, t, p["thread"], ctx, iid)
            done = f"kept as {tref(t['id'])}"
        save(path, state)
        print(done)
    elif cmd == "refresh":
        # The update check's: what GitLab says now, read back as the mod's state.
        sync(state, fetch_threads(ctx, iid, me, author), ctx, iid)
        base = mr_base(ctx, iid)
        if ui.drawn():
            check_pushes(state, ctx, iid, base)
            link_pushes(state, ctx, iid)
            status = mr_status(ctx, iid, me)
            state.setdefault("mr_news", []).extend(mr_news(state.get("mr_status"), status, now_iso()))
            state["mr_status"] = status
        state["diff_base"] = base or state.get("diff_base")
        save(path, state)
        print(json.dumps(state_json(state)))
    elif cmd == "open":
        # The user opens the inbox (or a topic in it): the review worktree moves to the MR's
        # newest push, so the sidebar shows what the round is about, and the round starts.
        # Here and not in the update check, so the code never changes under a turn.
        move_worktree(state, ctx, iid)
        state["pushes"] = []
        state["mr_news"] = []
        if args.topic:
            topic_for(state, args.topic) or die(f"no topic {args.topic}")
            focus_on(state, args.topic)
        else:
            start_round(state)
        save(path, state)
        print(json.dumps(state_json(state)))
    elif cmd == "judge-brief":
        print(judge_brief(state, iid, args.skip))
    elif cmd == "verdict":
        t = topic_for(state, args.topic) or die(f"no topic {args.topic}")
        if not (t["thread_ids"] and topic_status(state, t) == "needs_ack"):
            die(f"{tref(args.topic)} has no answer waiting for you to judge")
        if args.case and args.says != "more":
            die("--case names what is wrong with an answer, so it goes with `more` only")
        before = verdict_of(state, t)
        if not store_verdict(state, t, args.says, args.text, did=args.did, asked=args.asked,
                             unless_judged=args.unless_judged, case=args.case):
            die(f"{tref(args.topic)}: already judged since you started; nothing stored")
        if before and ui.drawn() and not args.unless_judged and args.says != "wait":
            # A verdict the chat changed (Analyse more): the topic's card is up already, so
            # this shows what changed and nothing else.
            save(path, state)
            emit(state, render_verdict_change(state, t, before), t["id"])
        else:
            save(path, state)
            left = needs_verdict(state)
            print(f"{tref(args.topic)}: {args.says}" + (f"; still to judge: {', '.join(left)}"
                                                         if left else ""))
    elif cmd == "peer":
        t = topic_for(state, args.topic) or die(f"no topic {args.topic}")
        peer_waiting(state, t) or die(f"{tref(args.topic)} is no other reviewer's thread that "
                                      "waits on the user")
        if args.unless_judged and peer_view_of(state, t):
            die(f"{tref(args.topic)}: already judged since you started; nothing stored")
        advice = "seen" if args.says == "fyi" else (args.advice or "reply")
        if advice == "merge" and not topic_for(state, args.into or ""):
            die("--advice merge needs --into <topic>, the topic it repeats")
        if args.summary:
            t["summary"], t["needs_title"] = args.summary, False
        if args.why:
            t["why"] = args.why
        t["peer_view"] = {"says": args.says, "advice": advice,
                          "into": args.into if advice == "merge" else None,
                          "on": answer_mark(state, t)}
        save(path, state)
        print(f"{tref(args.topic)}: {args.says}")
    elif cmd == "resolve":
        if os.environ.get(PRESS_ENV) != "1":
            die("resolving is the user's press, on the Resolve thread button; nothing else "
                "resolves.")
        t = topic_for(state, args.topic) or die(f"no topic {args.topic}")
        disc = f"projects/{ctx['enc']}/merge_requests/{iid}/discussions"
        for x in t["thread_ids"]:
            th = state["threads"].get(x) or {}
            if not th.get("resolved") and not th.get("gone"):
                api_post(f"{disc}/{x}?resolved=true", {}, method="PUT")
                th["resolved"] = True
        t["state"] = "acked"
        t["acked_at"] = now_iso()
        save(path, state)
        print(f"resolved and acked {tref(args.topic)}")
    elif cmd == "post":
        url = post_draft(state, ctx, iid, args.topic, args.expect)
        # Synced at once, so the topic reads as posted (and the current topic moves on)
        # for whatever asks next, rather than at the next sync.
        sync(state, fetch_threads(ctx, iid, me, author), ctx, iid)
        save(path, state)
        print(f"posted {tref(args.topic)}: {url}")
    elif cmd == "posted":
        t = topic_for(state, args.topic) or die(f"no topic {args.topic}")
        if args.apply:
            # Mirrors threads.py's `posted --apply`: the user's press, never the agent's move.
            if os.environ.get(PRESS_ENV) != "1":
                die("editing a posted comment is the user's press, on its card; nothing else "
                    "edits it. Show the card and let them press it.")
            try:
                print(f"edited the posted comment of {tref(args.topic)}: "
                      f"{posted.apply(api_post, ctx, iid, t)}")
            except ValueError as e:
                die(str(e))
            args.ok = True
        if args.differs:
            fix = sys.stdin.read() if args.fix == "-" else args.fix
            try:
                # A follow-up is the newest of the user's notes; a comment opens its thread.
                check = t.get("post_check") or {}
                posted.store_differs(state, t, args.differs, fix,
                                     last=bool(check.get("follow_up")), since=check.get("since"))
            except ValueError as e:
                die(str(e))
            save(path, state)
            emit(state, f"{title_md(state, t, t.get('summary') or '')}\n\n"
                 + posted.card_md(t, "comment"), args.topic)
        elif args.ok:
            t.pop("post_differs", None)
            if (t.pop("post_check", None) or {}).get("follow_up"):
                t["draft"] = None            # the follow-up is on GitLab: no draft is left
                t.pop("draft_on", None)
            nxt = CURRENT.get(state)
            if nxt and os.environ.get(PRESS_ENV) != "1":
                # On to the next topic, its card as `quote --focus` prints it.
                CURRENT.view(state, nxt, True, _jump_cmd(nxt, iid))
                anchor_notes(state, [topic_for(state, nxt)])
                emit(state, merged_lead(state, nxt) + render_quote(state, nxt), nxt)
            elif not nxt:
                print(f"{tref(args.topic)} is posted; nothing else needs you in this round.")
            save(path, state)
        else:
            if not t.get("post_check") and t.get("draft") and t["thread_ids"]:
                # A follow-up the user posted by hand into a thread already on GitLab: kept
                # to check it against, before the sync, which may drop a follow-up draft
                # once the thread moved on.
                t["post_check"] = {"draft": t["draft"], "follow_up": True, "since": t.get("draft_at")}
            sync(state, fetch_threads(ctx, iid, me, author), ctx, iid)
            save(path, state)
            check = t.get("post_check") or {}
            _disc, shown = posted.own_note(state, t, last=bool(check.get("follow_up")), since=check.get("since"))
            print("Drafted:\n\n" + (check.get("draft") or "(no draft kept)").strip())
            print("\nPosted:\n\n" + (shown.get("body") if shown else "(not on GitLab yet: nothing of the "
                                         "user's came after the draft)").strip())
    elif cmd == "link":
        t = topic_for(state, args.topic) or die(f"no topic {args.topic}")
        did = args.discussion_id
        if not did:
            # "I posted it by hand" — resolve it instead of asking the user for an id
            # they would have to hunt for. Unambiguous when exactly one unlinked thread
            # of theirs sits on this topic's file, which is the normal case.
            sync(state, fetch_threads(ctx, iid, me, author), ctx, iid)
            linked = {th for x in state["topics"] for th in x["thread_ids"]}
            hits = [tid for tid, x in state["threads"].items()
                    if x.get("mine") and not x.get("gone") and tid not in linked
                    and x.get("file") == t.get("file")]
            # The first words of both are what the review-ui mod answers the press by.
            if not hits:
                die(f"no comment of yours on {t.get('file') or 'the MR'} is on GitLab yet")
            if len(hits) > 1:
                die(f"{len(hits)} comments of yours on {t.get('file')} are not linked to a "
                    f"topic — pass the discussion id of the one for {args.topic} "
                    f"(see `candidates`)")
            did = hits[0]
        if ui.drawn() and t.get("draft"):
            # Posted by hand, often reworded: what was drafted is kept to check it against.
            t["post_check"] = {"draft": t["draft"], "since": t.get("draft_at")}
        attach_thread(state, t, did, ctx, iid, args.start_sha)
        save(path, state)
        print(f"{tref(args.topic)} ← {did}")


if __name__ == "__main__":
    main()
