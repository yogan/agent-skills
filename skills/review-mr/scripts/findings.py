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

Subcommands (read-only against GitLab, but for post, resolve, approve-mr and posted --apply,
each only on a person's press):
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
  view <t> <from>..<to> [-- <file>…]   any commits of the review's worktree in the viewer
                 window, for <t>, when the user asks to see a change `diff` does not cover
  point <t> <file> <line> <text> [--old]   on the user's request only: your note on that
                 line of <t>'s diff in the viewer window, brought into view
  notes          the user's notes in the viewer window, numbered, and those from diffs it
                 has moved on from
  reply <n> <text>   answer the user's note <n> under it in the window
  import <file>  bulk-add findings from a JSON array (review-branch seed)
  add [--thread <id>]  add one finding; --thread links an already-posted thread
  set <t> …      update a topic's fields / state
  drop <t>       mark a topic dropped: still listed, closed, and `set <t> --state reset` undoes it
  merge <into> <o…>  fold other topics into <into>
  link <t> [<discussion_id>]  attach a posted GitLab thread to a topic (✎→○);
                 omit the id to auto-pick your one unlinked thread on that file
  candidates     your posted threads not yet linked to any topic (for matching)
  state          the review as JSON, for the review-ui mod's buttons (offline)
  post <t>       post <t>'s draft: a new thread at its line, or a reply into its
                 thread. Only on a person's press (the review-ui mod's Post button)
  own-review done|matched   the user's own review before the topics (mod only)
  propose <discussion_id> --into <t> --summary … --why … [--recommend separate]
                 offer merging their comment into <t>, with the reason the user decides on
  proposal [<discussion_id>]   a proposed match as its card (`own-review matched` shows the first)
  settle <discussion_id> merge|separate|link   on the press that answers a proposal
  verdict <t> settled|more|wait --text …   the agent's judgment of the author's answer (its card)
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

from lib import code_view, critical_manifest, diffsize, fix_refs, focus, hunk, judge, posted, press, tab, ui  # noqa: E402
from lib.fences import (LINK_ICON, SUGGESTION_INFO, YOU, block_lang, fence, suggestion_reach, suggestion_repeat,  # noqa: E402
                        diff_info, gitlab_note_body, lang_for, note_md, quoted_fence, segments, upload_links, uploads_at,
                        suggestion_diff, when_of)
from lib.cli import iid_option  # noqa: E402
from lib.gitlab import (after_posting, api, api_post, approve, context, current_user, die, file_at, mr_base,  # noqa: E402
                        remember_user, mr_head, mr_object, mr_status, mr_view, posted_but, versions, web_base)
from lib.mr_common import (DEFAULT_LANG, LANG_GIVEAWAYS, MR_LEVEL, first_name, instant, people,  # noqa: E402
                           hold, load, mr_json, mr_news, topic_handles, loc_md, num, plain_text, reads_as,
                           short_summary, state_file, topic_for, tref)
from lib.mr_common import save as _save                          # noqa: E402
from lib.snippet import MAX_BACKTRACK, open_construct            # noqa: E402

from lib.mr_common import state_root  # noqa: E402

STATE_ROOT = state_root("review-mr")
# Where this skill keeps what is its own in a repository (`lib/fix_refs.py`).
SKILL = "review-mr"
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


_ts = instant


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
    carries fields (`author`, `seq`, ...) that reflect reviewing someone
    else's MR, which threads.py's own state has no use for. Stays duplicated rather
    than shared: see CLAUDE.md's "Sharing vs. duplication"."""
    return {
        "project": ctx["path"], "slug": ctx["slug"], "iid": mr["iid"],
        "mr_web_url": mr.get("web_url"), "title": mr.get("title"),
        "author": mr_author(mr), "author_username": mr_author_username(mr),
        "lang": get_lang(ctx["slug"]),
        "last_reviewed_head": None, "seq": 0,
        "threads": {}, "topics": [],
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
        by = next((n for n in resolvable if n.get("resolved_by")), None) if resolved else None
        resolved_by = ((by or {}).get("resolved_by") or {}).get("name") \
            or ((by or {}).get("resolved_by") or {}).get("username")
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
                       **gitlab_note_body(n.get("body"), uploads_at(ctx, n)), "at": n.get("created_at"),
                       "mine": bool(me) and (n.get("author") or {}).get("username") == me,
                       "url": f"{ctx['web']}/-/merge_requests/{iid}#note_{n.get('id')}"}
                      for n in notes],
            # The side and the exact blobs the position names — what a ```suggestion's
            # line offsets count from (see `_comment_source`).
            "side": "new" if pos.get("new_line") is not None else "old",
            "head_sha": pos.get("head_sha"),
            "base_sha": pos.get("start_sha") or pos.get("base_sha"),
            "body": upload_links(first.get("body"), uploads_at(ctx, first)),
            "resolved": resolved,
            "resolved_by": resolved_by,
            # Whether it was the user, and when: what a closed topic's card ends on.
            "resolved_username": ((by or {}).get("resolved_by") or {}).get("username"),
            "resolved_mine": bool(me and by) and (by.get("resolved_by") or {}).get("username") == me,
            "resolved_at": (by or {}).get("resolved_at"),
            "url": f"{ctx['web']}/-/merge_requests/{iid}#note_{first.get('id')}",
            "note_count": len(notes),
            "last_author": (last.get("author") or {}).get("name") or last_user,
            "last_username": last_user,
            "last_body": upload_links(last.get("body"), uploads_at(ctx, last)),
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


BY_USER_HELP = ("with --kind/--severity: what the user stated themselves, so each one passed "
                "is theirs and your own re-judging leaves it")


def user_owns(t):
    """Which of `t`'s kind and severity the user stated themselves (`by_user`), each on its
    own: a severity they gave says nothing of the kind, which stays the agent's to judge. A
    state written while one mark covered both (`kind_by_user`) owns both, as it meant then."""
    own = set(t.get("by_user") or ())
    if t.get("kind_by_user"):
        own |= {"kind", "severity"}
    return own


def own_by_user(t, fields):
    """Mark `fields` (of kind and severity) of `t` the user's, beside those already theirs."""
    own = user_owns(t) | set(fields)
    t.pop("kind_by_user", None)
    if own:
        t["by_user"] = sorted(own)
    else:
        t.pop("by_user", None)


def refuse_rejudge(t, kind, severity, by_user):
    """Why the agent may not set this kind/severity on `t`, or None. One the user stated
    themselves (`--by-user`: in their comment, or asked for in the chat) stands against the
    agent's own judgment: only a change the user asks for replaces it. A topic of the
    user's own review is not theirs by that alone: its kind is a default or the agent's
    reading of their comment, the very misreads a re-judge corrects. Filling in a severity
    nobody gave yet changes nothing of theirs."""
    if by_user:
        return None
    own = user_owns(t)
    asked = [(f, v) for f, v in (("kind", kind), ("severity", severity)) if v is not None]
    changed = [f for f, v in asked if f in own and t.get(f) not in (None, v)]
    if not changed:
        return None
    both = len(changed) > 1
    now = ", ".join(str(t[f]) for f in changed)
    rest = [f for f, _v in asked if f not in changed]
    return (f"{t['id']}'s {' and '.join(changed)} {'are' if both else 'is'} the user's ({now}): "
            f"left as {'they are' if both else 'it is'}; nothing was set."
            + (f" Set the {rest[0]} alone." if rest else "")
            + " Say in one line how you would judge it instead; `--by-user` only when the user "
            f"asks you to change {'them' if both else 'it'}.")


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
    if peer_resolved(state, t, thr):
        return "acked"
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


def _watched(state, t, thr):
    """`t`'s threads where `t` is another reviewer's thread, or the MR author's own, that the
    user has said nothing in, where the mod draws; else None."""
    if t.get("source") not in ("peer", "author") or not ui.drawn():
        return None
    thr = thr if thr is not None else [x for x in (state["threads"].get(i) for i in t["thread_ids"]) if x]
    return thr if thr and not any(x.get("you_spoke") for x in thr) else None


def peer_waiting(state, t, thr=None):
    """Whether another reviewer's thread, or the MR author's own, is one the user watches:
    they have said nothing in it and it is still open. Where the mod draws, the agent judges
    whether it needs them (`peer_view_of`); without the mod it stays the author's, as before."""
    thr = _watched(state, t, thr)
    return bool(thr) and not all(x.get("resolved") for x in thr)


def peer_resolved(state, t, thr=None):
    """Whether a thread the user watches (`peer_waiting`) is resolved on GitLab: it closes by
    itself, whoever resolved it. Nothing in it was the user's, so there is no answer to judge
    or ack; a verdict stored on it before is no longer read. Reopened, it is watched again."""
    thr = _watched(state, t, thr)
    return bool(thr) and all(x.get("resolved") for x in thr)


def peer_closed(t, thr):
    """Whether the user closed another reviewer's thread (Next or Close on its card, Seen or
    Leave to them before) and nobody wrote in it since."""
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
        return "draft" if draft_text(t) else "new"
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
#
# `gone` is not one: it is set again on every sync for each thread the fetch no longer
# lists, and a thread listed again is not deleted. Kept across a fetch, one fetch that came
# back empty left every thread gone for good, out of adoption and skipped by `resolve`.
LOCAL_THREAD_FIELDS = ()


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
        if tid in linked or x.get("gone"):
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
    name = f if full else os.path.basename(f)
    return f"{name}:{line}" if line else name


def loc_link_md(state, loc):
    """A card's `file:line`, as the link to that line on the MR's Changes page in GitLab,
    where the user opens it to comment there by hand. The line's anchor needs both of its
    numbers (`code_view.line_anchor`), from the MR's diff in the review worktree; where it
    cannot be told, the link opens the Changes page. No link where the mod does not draw:
    a pasted heading keeps its plain location, nor for a topic on the MR itself."""
    if not (loc and ui.drawn() and state.get("mr_web_url")):
        return loc_md(loc)
    path, _, line = loc.rpartition(":")
    wt = get_worktree(state.get("slug") or "", state.get("iid"))
    rows = (code_view.rows_of(wt, path, state.get("diff_base"))
            if wt and os.path.isdir(wt) and line.isdigit() else None)
    anchor = code_view.line_anchor(rows, path, int(line)) if rows else None
    return f"[{loc_md(loc)}]({state['mr_web_url']}/diffs{f'#{anchor}' if anchor else ''})"


def _place(state, x):
    """`path:line` of a thread for a card's heading: where the mod draws, under the file's
    name now in the review worktree (`code_view.place_now`), as the sidebar beside the card
    follows a rename the author pushed since; elsewhere, and with no worktree, as posted.
    "" for a thread on the MR itself."""
    if not x.get("file"):
        return ""
    wt = get_worktree(state.get("slug") or "", state.get("iid")) if ui.drawn() else None
    if wt and os.path.isdir(wt):
        return code_view.place_now(wt, x)
    return (f"{x['file']}:{x['line']}" if x.get('line') else x['file'])


def _thread_loc(x):
    """`basename:line` for a thread's diff position, or "" when it has none — a
    discussion on the merge request itself carries no position, and every caller has
    to say so in its own words rather than print a stray separator."""
    return (f"{os.path.basename(x['file'])}:{x['line']}" if x.get('line') else os.path.basename(x['file'])) if x.get("file") else ""


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
            + " / ".join(f"`set {x} --summary '...'`" for x in untitled)
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
            f"not translated/summarized). Run `set {t['id']} --summary '<short "
            f"English title>'` before presenting this topic further.")


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
            f"`set <t> --summary '<short English line>'`, then re-render.")


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


def suggestion_repeat_refusal(state, t, body):
    """Why a draft is not stored, where a ```suggestion's replacement ends on the very line
    just past its range: applied, that line would stand twice. Said where the draft is
    stored, on stderr, so it reaches the agent even through a `| tail` that cuts a warning,
    and the draft is fixed before anyone reads it. The lines around it go with it."""
    path, anchor = _draft_target(state, t, bool(t["thread_ids"]))
    source = _suggestion_source(state, body, path)
    for kind, info, seg in segments((body or "").splitlines()):
        if kind == "text":
            continue
        line = suggestion_repeat("\n".join(seg), info, anchor, source)
        if not line:
            continue
        shown = range(max(1, int(anchor) - 2), min(len(source), line + 2) + 1)
        return (f"draft not stored: the ```{info} block ends on line {line} as it stands, "
                f"which its range (counted from line {anchor}) leaves in place, so it would "
                f"stand twice once applied. Widen the range to take line {line} in "
                f"(```suggestion:-{SUGGESTION_INFO.match(info).group(1) or 0}+{line - int(anchor)}), "
                "or end the replacement before it, and store the "
                "draft again:\n" + "\n".join(f"{i:>5} | {source[i - 1]}" for i in shown))
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
# There every comment is looked through for a suggestion; here only the first and the last,
# as a thread's view here shows no other.
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


def posted_code(state, t):
    """How a corrected comment's code blocks are shown on its card: a suggestion as the diff
    it makes of the lines it replaces, as its draft showed it, highlighted; other code in its
    language."""
    path, anchor = _draft_target(state, t, bool(t["thread_ids"]))

    def draw(content, info):
        if SUGGESTION_INFO.match(info or ""):
            diff = suggestion_diff(content, info, anchor, _suggestion_source(state, "```suggestion", path))
            return fence(diff, diff_info(path)) if diff else fence(content, lang_for(path))
        return fence(content, block_lang(content, info, path))
    return draw


def render_quote(state, tid, refine=False):
    """A topic as the user reads it (`_render_quote`), and where the user's own posting of its
    draft no longer holds, the correction under it: its card is the correction's then."""
    body = _render_quote(state, tid, refine)
    t = topic_for(state, tid)
    if t and t.get("post_check") and t.get("post_differs"):
        body += "\n\n" + posted.card_md(t, "comment", posted_code(state, t))
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
    if refine and not draft_text(t):
        die(f"--refine re-shows a reworded DRAFT and {tref(tid)} has none — everything it "
            f"renders is context. Use plain `quote {tid}`")
    # Where the mod draws, every view of a topic is its whole card: the older cards of the
    # topic are folded away, so the newest is all that is left to read, and what the sidebar
    # shows (the code, the thread) the card leaves out by itself.
    refine = refine and not ui.drawn()
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
        out.append((f"{title} · {loc_link_md(state, loc)}" if loc else title) if ui.drawn()
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
        if draft_text(t):
            out += _draft_block(state, t, draft_text(t))
        elif topic_status(state, t) != "dropped":     # nothing left to draft for it
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
                      f"it; store the agreed draft with `set {t['id']} --draft '…'`, "
                      f"written in {state.get('lang') or DEFAULT_LANG}",
                      file=sys.stderr)
        closing = closing_line(state, t)
        if closing:
            # How it was closed, the card's last line, as a posted topic's story ends.
            out += ["", closing if ui.drawn() else f"_({closing})_"]
        return "\n".join(out).strip()
    v = verdict_of(state, t)
    if ui.drawn() and v and not refine and not peer_waiting(state, t):
        return render_answered(state, t, summ, v)
    # A topic closed on a judged answer, opened again to look at: its whole story, the
    # verdict it was closed on and what closed it.
    v = t.get("verdict")
    if (ui.drawn() and v and v.get("says") != "wait" and not refine and t.get("closed")
            and topic_status(state, t) == "acked"):
        return render_answered(state, t, summ, v)
    # Answered, judged, and followed up on: it waits on the author again, and its card still
    # says what was asked, what they did and the verdict on it, with the follow-up in the
    # story under them. Drawn again after the reply went up, it had lost all three.
    if (ui.drawn() and v and v.get("says") != "wait" and not refine and not peer_waiting(state, t)
            and topic_status(state, t) == "open" and (state["threads"].get(t["thread_ids"][0]) or {}).get("mine")):
        return render_answered(state, t, summ, v)
    shown = []                            # the notes, as rendered — see `context_digest`
    # Where the mod draws, a comment of the user's own already posted ends its card on that:
    # when it went up, and, where it is, who resolved its thread. The advice that led to it
    # is followed, so it is not given again.
    first = state["threads"].get((t["thread_ids"] or [None])[0]) or {}
    is_own_posted = ui.drawn() and bool(first.get("mine"))
    for i, th in enumerate(t["thread_ids"]):
        x = state["threads"].get(th, {})
        # An MR-level thread carries no diff position at all, so there is no path to
        # print — `None:` is what an unguarded f-string produced there.
        path = _place(state, x)
        if i == 0:
            title = title_md(state, t, summ)
            # Where the mod draws, the draft's own label carries the language.
            out.append(f"{title} · {loc_link_md(state, path)}" if ui.drawn()
                       else f"{title} · {loc_md(path)} · {lang_hint(state)}")
            for warn in (needs_title_warning(state, t),
                         summary_language_warning(state, [t])):
                if warn:
                    out.append(warn)
        else:
            out.append(loc_md(path))
        if x.get("url") and not ui.drawn():       # drawn: each comment links itself
            out.append(x["url"])
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
    # Another reviewer's thread keeps its summary while the user watches it: one for their
    # information is acked from the start, and without it its card said nothing at all.
    if t.get("why") and (topic_status(state, t) not in ("acked", "dropped") or peer_waiting(state, t)):
        # What the agent makes of it: its own finding, now on the user's merged comment, or
        # another reviewer's thread, judged when it came in.
        out += (["", ui.analysis_heading(), "", ui.without_recommendation(t["why"])]
                if is_own_posted else analysis_md(t["why"]))
    closing = closing_line(state, t)
    v = verdict_of(state, t)
    if v:
        # The agent's judgment of the author's answer, stored when the answer came in: a
        # card a press brings up has no model turn beside it.
        out += ["", ui.heading_md(ui.VERDICT_ICON, f"Verdict: {verdict_word(state, t, v)}"), "",
                v["text"].strip()]
    # A card's order, the same in both skills: what is decided on (the analysis, the
    # verdict), the draft that answers it, then the story, its latest event last.
    out += _draft_block(state, t, t.get("draft"), follow_up=True)
    if is_own_posted:
        # The topic's whole story, as rework-mr's card tells its own: every comment, every
        # push of the author's and what closed it, each at its time, the latest event last;
        # then, while it is open, whom it waits on.
        out += ["", *story_steps(state, t, None if close_in_story(state, t) else closing_event(state, t))]
        if not closing and topic_status(state, t) == "open":
            out.append(ui.step_md(ui.STEP_WAITS_ICON, f"Waits on {first_name(state.get('author')) or 'the author'}"))
    elif closing:
        # The latest thing that happened to the topic, the card's last line.
        out += ["", closing if ui.drawn() else f"_({closing})_"]
    if peer_waiting(state, t):
        # Another reviewer's thread, closed here or not, stays open on GitLab: whose it is to
        # settle, as an own comment's card ends on whom it waits on.
        # A step of its story: under the closing line where there is one.
        out += ([] if closing else [""]) + [
            ui.step_md(ui.STEP_WAITS_ICON, f"{t.get('by') or 'Its reviewer'}'s to settle")]
    return "\n".join(out).strip()


def close_in_story(state, t):
    """Whether the reply a press last closed `t` with is in its threads, where its story
    tells it as the close (`story_steps`), its link on the words. Mirrors threads.py's."""
    c = t.get("closed") or {}
    return c.get("how") == "replied" and bool(c.get("note")) and any(
        ui.note_key(n.get("url")) == c["note"] for x in t["thread_ids"]
        for n in (state["threads"].get(x) or {}).get("notes") or [])


def closing_line(state, t):
    """`closing_event` as one line, for a card that tells no story and the `closed:` lines a
    command prints."""
    e = closing_event(state, t)
    return ui.step_md(e["icon"], e["text"], e["at"]) if e else None


def ack_topic(state, t):
    """Close `t` here at the user's word (`set --state acked`, `leave --ack`)."""
    if topic_status(state, t) != "acked":
        # How it was closed, for the line its card ends on: on the author's fix where a push
        # changed its lines since the comment, else on what stands.
        t["closed"] = {"how": "fixed" if fixes_of(t) else "acked", "at": now_iso(),
                       "push": len(fixes_of(t))}
    t["state"] = "acked"
    # Stamped so a later author note can be recognised as newer than the ack.
    t["acked_at"] = now_iso()


def drop_topic(t, why=None):
    """Drop `t` here at the user's word (`drop`, `set --state dropped`): when, for the line
    its card ends on, and why where they said. Dropped again, it keeps its first time."""
    if t.get("state") != "dropped" or not t.get("drop"):
        t["drop"] = {"at": now_iso()}
    if why and why.strip():
        t["drop"]["why"] = why.strip()
    t["state"] = "dropped"


def print_acked(state, iid, t):
    """What an ack prints once saved: its `closed:` line, and what the diff viewer did."""
    closing = press_close(state, t)
    if closing:
        print(f"closed: {closing}")
    tidied = tidy_viewer(iid, t["id"])
    if tidied:
        print(tidied)


def press_close(state, t):
    """The closing line a press that closed `t` prints (`closed: …`), which the review-ui mod
    adds to the card as that press's outcome; None where the close was not this press's.

    Only a close made here says what the press did. One resolved on GitLab and not closed
    here (another reviewer's thread the user acked, say) names the resolve, already the last
    line of the card's story: added again as the outcome, it stood on the card twice. Without
    it the mod says what the press did in its own words."""
    e = closing_event(state, t)
    return closing_line(state, t) if e and e.get("last") else None


def closing_event(state, t):
    """What closed a topic, once it is dropped, closed here or its threads are resolved: by
    whom, how and when, as one of its story's events (`story_steps`); None while neither.
    `last`: a close made here, the user's own decision on everything the story tells, which
    ends it whatever times the events before it carry.

    Closed here, it says how: the user's Accept + resolve (the one place "accepted" is said),
    an ack of the author's fix, an ack of a thread somebody resolved. Resolved on GitLab and
    not closed here, it says by whom; the MR author's resolve is no close at all, as authors
    tick threads off while they work and push later: it says whether a fix is pushed yet.
    Dropped, posted or not, it says so, with the reason where the user gave one; a topic
    dropped before drops were recorded says it without a time."""
    if topic_status(state, t) == "dropped":
        d = t.get("drop") or {}
        return {"at": d.get("at"), "icon": ui.DROPPED_ICON, "last": True,
                "text": "You dropped it" + (f": {d['why']}" if d.get("why") else "")}
    thr = [state["threads"].get(x) or {} for x in t["thread_ids"]]
    res = thr[0] if thr and all(x.get("resolved") for x in thr) else None
    author = first_name(state.get("author")) or "the author"
    who = None
    if res:
        who = "you" if res.get("resolved_mine") else first_name(res.get("resolved_by")) or "someone"
    c = t.get("closed") or {}

    def done(text, at, last=False):
        # `ui.closed_md`'s line, as an event.
        return {"at": at, "icon": ui.STEP_DONE_ICON, "text": text, "last": last}

    if topic_status(state, t) == "acked" and t.get("source") not in ("peer", "author"):
        at = c.get("at") or t.get("acked_at")
        if c.get("how") == "accepted":
            # What was accepted: the author's answer in the thread, or only their fix where
            # they pushed and said nothing. Neither (settled in person, say): nothing was.
            replied = any(not n.get("mine") for x in thr for n in x.get("notes") or [])
            what = "answer" if replied else "fix" if fixes_of(t) else None
            return done(f"You accepted {author}'s {what} and resolved it" if what
                        else "You resolved it", at, True)
        if c.get("how") == "replied":
            return done("You replied and resolved it", at, True)
        if c.get("how") == "fixed":
            push = f" (push {c['push']})" if (c.get("push") or 0) > 1 else ""
            return done(f"{author} fixed it{push} · you acked it", at, True)
        if who == "you":
            return done("You resolved and acked it", at, True)
        if who:
            return done(f"{who} resolved it · you acked it", at, True)
        return done("You acked it", at, True)
    if not res:
        return None
    by_author = author_resolved(state, t)
    if by_author:
        how = " and pushed a fix" if by_author == "pushed" else "; nothing pushed for it yet"
        return {"at": res.get("resolved_at"), "icon": ui.RESOLVED_ICON,
                "text": f"{author} resolved it on GitLab{how}"}
    return done(f"{'You' if who == 'you' else who} resolved it", res.get("resolved_at"))


def story_steps(state, t, close=None):
    """`t`'s whole story for its card, one line per event in the order they happened
    (`ui.story_md`): every comment in its threads (yours and the others', each the link to
    itself), every push of the author's noted for it (its GitLab link), and `close`, what
    closed it (`closing_event`), at its own time like the rest: a resolve that came with a
    reply goes before the push that followed it. The whole of it
    or none: a card that picked the latest reply out, with the comment, the answers and the
    push before it left out, gave nobody the means to check the verdict above it. What a
    comment says is the sidebar's thread; what a push changed, the sidebar's file. Mirrors
    threads.py's `story_steps`, whose events differ (plans, your own pushes)."""
    author = first_name(state.get("author")) or "the author"
    events = []
    for k, x in enumerate(t["thread_ids"]):
        for i, n in enumerate((state["threads"].get(x) or {}).get("notes") or []):
            mine = bool(n.get("mine"))
            who = "You" if mine else first_name(n.get("author")) or author
            verb = ("posted the initial comment" if mine else "commented") if i == 0 and k == 0 else "replied"
            # A reply of yours a press posted with a resolve: that, whatever came after.
            if mine and (t.get("closed_with") or {}).get(ui.note_key(n.get("url"))) == "replied":
                verb = "replied and resolved it"
            events.append({"at": n.get("at"), "icon": ui.STEP_DONE_ICON if mine else ui.STATE_ICON["answered"],
                           "text": f"{who} {ui.said_md(verb, n.get('url'))}"})
    fixes = kept_fixes(state, t)

    def said(f):
        # Its size is the sidebar's to say, over the fix's files: here it was a second
        # count of the same change.
        also = shared_with(state, t, f)
        return ((f" · {ui.link_md('diff', f['url'])}" if f.get("url") else "")
                + ui.push_aside_md(also, rebase=bool(f.get("rebase")), mixed=bool(f.get("mixed"))))

    for k, f in enumerate(fixes, 1):
        # A push that changed the comment's file elsewhere is a fix once the agent judged it
        # one; until then it is what is known of it, a change to the file.
        did = "pushed a change to its file" if f.get("near") else "pushed a fix"
        nth = f" (push {k})" if len(fixes) > 1 else ""
        # Where it moved the code: the sidebar opens there, and a story that said only "fix"
        # beside a diff of the comment's file losing it all reads as a deletion.
        went = sorted({q["file"] for q in f.get("moved_to") or []})
        moved = ("" if not went else " · moved within its file" if went == [topic_file(state, t)[0]]
                 else " · moved to " + ", ".join(f"`{os.path.basename(q)}`" for q in went))
        events.append({"at": f.get("at"), "icon": ui.PUSHED_ICON,
                       "text": f"{author} {did}{nth}{moved}{said(f)}"})
    last = [close] if close and close.get("last") else []
    return ui.story_md(events + ([close] if close and not last else [])) + [
        ui.step_md(e["icon"], e["text"], e["at"]) for e in last]


def author_resolved(state, t):
    """Whether the MR author resolved `t`'s threads and the user has not closed it here:
    "pushed" where a push of theirs changed its lines since the comment, "unpushed" where
    none has yet, else None. It decides what its card recommends: an ack once the fix is
    there, waiting while it is not."""
    if topic_status(state, t) == "acked" or t.get("source") in ("peer", "author"):
        return None
    thr = [state["threads"].get(x) or {} for x in t["thread_ids"]]
    if not thr or not all(x.get("resolved") for x in thr):
        return None
    name = thr[0].get("resolved_username")
    if not name or name != state.get("author_username") or thr[0].get("resolved_mine"):
        return None
    return "pushed" if fixes_of(t) else "unpushed"


def render_answered(state, t, summ, v):
    """The card of a topic the author answered, where the mod draws: what the comment asked
    for (the agent's recap: the thread itself is in the sidebar), what the author did since
    (the agent's sentence; the change is the sidebar's), and the agent's verdict. The finding's own analysis and recommendation are history by now, and
    read as if the point still stood, so they are left out. A topic judged before recaps
    were has none, and the card goes from its title straight to what the author did."""
    x = state["threads"].get(t["thread_ids"][0], {})
    path = _place(state, x)
    title = title_md(state, t, summ)
    out = [f"{title} · {loc_link_md(state, path)}" if path else title]
    asked = (t.get("asked") or v.get("asked") or "").strip()
    if asked:
        whose = "What you asked" if x.get("mine") else f"What {first_name(x.get('author'))} asked"
        out += ["", ui.heading_md(ui.ASKED_ICON, whose), "", asked]
    author = first_name(state.get("author")) or "the author"
    did = (v.get("did") or "").strip()
    # What they did, in the agent's sentence. The change itself is the sidebar's, every file
    # of it, old and new, with the diff viewer a press away: a second copy of part of it
    # here was the same code twice on one screen.
    if did:
        out += ["", ui.heading_md(ui.DID_ICON, f"What {author} did"), "", did]
    out += ["", ui.heading_md(ui.VERDICT_ICON, f"Verdict: {verdict_word(state, t, v)}"), "",
            v["text"].strip()]
    # The follow-up that answers the verdict, then the topic's whole story and what closed
    # it: one timeline, the latest event last, right above the buttons.
    out += _draft_block(state, t, t.get("draft"), follow_up=True)
    steps = story_steps(state, t, None if close_in_story(state, t) else closing_event(state, t))
    if topic_status(state, t) == "open":
        steps.append(ui.step_md(ui.STEP_WAITS_ICON, f"Waits on {author}"))
    if steps:
        out += ["", *steps]
    t["shown"] = _digest(out)
    return "\n".join(out).strip()


def draft_text(t):
    """The comment drafted for `t`, or None. `note` is the draft's older name, kept by imported
    and seeded topics: it stands for the draft only until the topic is posted, since a posted
    comment spends its draft, and a `note` left behind then was offered and posted again as
    a reply into the topic's own thread."""
    return t.get("draft") or (None if t.get("thread_ids") else t.get("note"))


def draft_body(state, tid):
    """The raw comment text for a draft topic — body only, no meta header (that
    would otherwise land in the posted GitLab comment). This is the payload to
    pipe into clip.sh or post via glab; `quote <t>` is the human-facing display."""
    t = topic_for(state, tid) or die(f"no topic {tid}")
    body = draft_text(t)
    if not body:
        die(f"topic {tid} has no draft yet — set one with "
            f"`set {tid} --draft '…'`")
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
    """Where `t` stands in the inbox: `new` (news that needs the user, not opened yet),
    `fyi` (news only to know of, not opened yet), `yours` (opened, not decided), or None.
    Both kinds of news flag the topic new in the sidebar, until the user opens it: every
    comment of somebody else's and every push on its lines. Neither moves it: an answer only
    to know of leaves it where it waits."""
    status = status or topic_status(state, t)
    v = peer_view_of(state, t) if peer_waiting(state, t) else None
    if v and v["says"] == "fyi" and t.get("state") != "acked":
        # Listed for the user's information until they open it; never part of a round.
        return None if t.get("seen") == answer_mark(state, t) else "fyi"
    if waits_on_author(state, t) and t["thread_ids"]:
        # An answer judged to leave the next move to the author (they agreed, the fix is to
        # come): only to know of, until the user opens it; never part of a round.
        return None if t.get("seen") == answer_mark(state, t) else "fyi"
    if status == "open" and t["thread_ids"] and t.get("seen") != answer_mark(state, t) and _others_last(state, t):
        # Somebody other than the author wrote last (another reviewer joined in): the
        # author's move still, so only to know of.
        return "fyi"
    if status not in NEEDS_YOU:
        return None
    if not t["thread_ids"] or t.get("follow_up") == "offer":
        return None
    return "yours" if t.get("seen") == answer_mark(state, t) else "new"


def _others_last(state, t):
    """Whether one of `t`'s threads has a comment of somebody else's as its newest."""
    return any(((state["threads"].get(x) or {}).get("notes") or [{"mine": True}])[-1].get("mine") is False
               for x in t["thread_ids"])


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
    return own_review_closed(state) and bool(state.get("proposals"))


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


def own_review_closed(state):
    """Whether the user's own review is behind them, so a comment they post from here on is
    matched to the topics. A review imported where the mod did not draw, or saved before
    the own review existed, never had one: once its topics are in, it counts as done, or the
    user's own comments in it would be adopted by nothing (`adopt_inbound` leaves them to the
    matching wherever the mod draws)."""
    how = state.get("own_review")
    if how is None:
        return bool(state.get("imported") or state.get("topics"))
    return how in ("done", "skipped")


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


# The rule `decision_mark` computes by, carried in every mark it makes. A mark is only ever
# compared with one made now, so a change to the rule would make every stored mark differ
# and every waiting push count as answered though the user did nothing. A mark from an
# older rule (one without this prefix: the rule before it counted anybody's note) is
# remade by the next update check instead (`_remark`). Bump it whenever the rule changes.
MARK_RULE = "2:"


def decision_mark(state, t):
    """What the user last did on `t`, as a fingerprint: their own newest note in each of its
    threads, the threads they resolved, and the topic's ack. A push noted against one of these
    is answered once it moves. Only the user's own doing: another reviewer's "+1", or the
    author resolving, answers nothing the user was asked to look at."""
    marks = []
    for x in t["thread_ids"]:
        thr = state["threads"].get(x) or {}
        mine = [n.get("at") for n in thr.get("notes") or [] if n.get("mine") and n.get("at")]
        marks.append((x, max(mine) if mine else None,
                      bool(thr.get("resolved") and thr.get("resolved_mine"))))
    return MARK_RULE + hashlib.sha1(json.dumps([marks, t.get("state"), t.get("acked_at")]).encode()
                                    ).hexdigest()[:12]


def _old_rule(mark):
    return bool(mark) and not mark.startswith(MARK_RULE)


def _remark(state, t):
    """Remake the marks on `t` that an older rule made: no answer, just the same moment in
    today's terms, so what the user does from here on is compared like with like."""
    for rec in (t.get("pushed"), t.get("code_seen")):
        if rec and _old_rule(rec.get("mark")):
            rec["mark"] = decision_mark(state, t)


def pushed_of(state, t):
    """The push that changed `t`'s lines and that the user has not answered yet, or None:
    `{"from", "to", "at"}`, the code before and after."""
    p = t.get("pushed")
    # A mark from an older rule still waits: the next check remakes it (`_remark`).
    return (p if p and ui.drawn() and (p.get("mark") == decision_mark(state, t)
                                       or _old_rule(p.get("mark"))) else None)


def watch_of(t, x, origin=None):
    """The lines a push is checked against: where the comment is, in the code it was last
    checked at. Started from where the comment was posted (`origin`, `comment_origin`'s);
    None for a comment that is on no line of the new code (on the MR as a whole, or on a
    line the MR removed)."""
    w = t.get("watch")
    if w:
        return w
    if not (x and x.get("file") and x.get("line") and x.get("side") == "new"):
        return None
    sha, first, last = (origin() if origin else None) or (
        x.get("head_sha") or t.get("start_sha"), x.get("line_start") or x["line"], x["line"])
    if not sha:
        return None
    t["watch"] = {"sha": sha, "first": first, "last": last}
    return t["watch"]


def comment_origin(x, vs, compare):
    """Where the comment `x` was posted: `(sha, first, last)`, the MR's version it was
    written on and its lines there; None where its position says so already.

    GitLab moves a comment's position along with every push that leaves its lines as they
    were, so the position names the newest such version, not the one it was written on: a
    push that changed the code right beside the lines (a `.catch` under the commented one)
    then lies before the position, and was never seen as the answer it was. A comment can
    only be written on a version GitLab has recorded, so a position on a version recorded
    after the comment was moved there; the comment's own is the newest version recorded
    before it, and its lines there are the position's carried back, unchanged since."""
    at = instant(((x.get("notes") or [{}])[0]).get("at"))
    pos = x.get("head_sha")
    if not (at and pos):
        return None
    vs = vs() if callable(vs) else vs
    ats = {v.get("head_commit_sha"): instant(v.get("created_at")) for v in vs or []}
    if not (ats.get(pos) and ats[pos] > at):
        return None
    before = [v for v in vs if instant(v.get("created_at")) and instant(v.get("created_at")) <= at]
    if not before:
        return None
    sha = max(before, key=lambda v: instant(v.get("created_at"))).get("head_commit_sha")
    diffs = compare(pos, sha)
    if not sha or diffs is None:
        return None
    _, first, last = code_view.touched((_file_diff(diffs, x["file"]) or {}).get("diff"),
                                       x.get("line_start") or x["line"], x["line"])
    return sha, first, last


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

    def compare(frm, to):
        return _compare(ctx, frm, to)

    # A rebase brings the target branch's own edits along, and a head-to-head compare cannot
    # tell them from the author's: the file's lines may have moved under the comment with no
    # word of the author's. So after a rebase a file counts only where the MR's OWN change to
    # it differs between the two pushes (each push's base to its head, hunk headers aside),
    # as `_author_touch` does for the `updates` report.
    # The base this check saw last, kept apart from `diff_base`: `sync` and `base` move that
    # one for the sidebar, and a rebase they saw first would be no rebase here. A state from
    # before it was kept starts from `diff_base`.
    old_base = state.get("pushes_base") or state.get("diff_base")
    if base:
        state["pushes_base"] = base
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

    def mr_versions():
        return versions(ctx, iid) or []

    def origin(x):
        return comment_origin(x, mr_versions, compare)

    touched, beside = [], []
    for t in state["topics"]:
        if not t["thread_ids"]:
            continue
        _remark(state, t)
        x = state["threads"].get(t["thread_ids"][0])
        w = watch_of(t, x, lambda x=x: origin(x))
        if not w:
            continue
        p = t.get("pushed")
        sc = t.get("code_seen")
        if sc and sc.get("mark") != decision_mark(state, t):
            # The user acted on the topic after a round showed them this code: whatever a
            # push did to its lines up to there is answered, as a noted push is below. One
            # decision, spent here: kept, it would set the watch back to this code once a
            # later push had moved it on, and an acked push would come back as news.
            t.pop("code_seen")
            if not p and sc.get("sha") not in (None, w["sha"]):
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
        # Nothing since, this push noted already, or a topic closed here. One the author
        # answered in the thread since counts: a reply and the push it speaks of often
        # arrive between two checks, and the push is the topic's all the same.
        if (w["sha"] == head or (p and p.get("to") == head)
                or (not p and topic_status(state, t) not in ("open", "needs_ack"))):
            continue
        # From the push noted already where one waits: its change to the lines is that
        # push's, and a later push that left them alone is no news of the topic.
        since, first, last = w["sha"], w["first"], w["last"]
        if p:
            noted = compare(w["sha"], p["to"])
            if noted is None:
                continue
            _, first, last = code_view.touched((_file_diff(noted, x["file"]) or {}).get("diff"), first, last)
            since = p["to"]
        diffs = compare(since, head)
        if diffs is None:
            continue
        _, a, b = code_view.touched((_file_diff(diffs, x["file"]) or {}).get("diff"), first, last)
        # Where a push moved the code out of the comment's file (`walk_fixes`, which has
        # walked up to here), the code is watched where it went instead.
        fw = t.get("fix_walk")
        places = ([p for p in walk_places(fw) if not p.get("left")]
                  if fw and fw.get("sha") == since and any(p.get("via") for p in walk_places(fw))
                  else [{"file": x["file"], "first": first, "last": last}])
        hit = near = False
        for q in places:
            d = _file_diff(diffs, q["file"])
            on = code_view.touched((d or {}).get("diff"), q["first"], q["last"])[0]
            if on and authored is not None and q["file"] not in authored:
                on = False                # the rebase moved it, not the author
            hit = hit or on
            # The author's push changed the file elsewhere: an answer in code is as often
            # beside the commented lines as on them (a `.catch` two lines down, a guard
            # above), so the topic is the user's to look at too, and the agent judges
            # whether the change concerns it. After a rebase only where the MR's own change
            # to the file is known to differ: what the target branch brought along is
            # nobody's answer. Not for another reviewer's thread: nobody judges a push for
            # one, and a change elsewhere in its file is no news of it.
            near = near or (not on and bool(((d or {}).get("diff") or "").strip())
                            and t.get("source") not in ("peer", "author")
                            and (not rebased or (authored is not None and q["file"] in authored)))
        near = near and not hit
        if hit or near:
            t["pushed"] = {"from": (p or {}).get("from") or w["sha"], "to": head,
                           "at": now_iso()}
            if near:
                t["pushed"]["near"] = True
            t["pushed"]["mark"] = decision_mark(state, t)
            (beside if near else touched).append(t["id"])
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
            # The topics whose lines it changed; those it changed beside their lines are
            # its own once the agent judged it their fix (`push_topics`).
            "topics": touched, "beside": beside, "rebase": rebased,
            # What it was rebased onto, for the line that says so.
            "onto": (mr_object(ctx, iid) or {}).get("target_branch") if rebased else None,
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
        # When it was pushed, not when the check saw it: the box lists its lines by time,
        # and a topic it changed dates its news by it.
        p["at"] = push_time(ctx, vs[i]) or p.get("at")
        for t in state["topics"]:
            if (t.get("pushed") or {}).get("to") == p["head"]:
                t["pushed"]["at"] = p["at"]
        p["url"] = (f"{ctx['web']}/-/merge_requests/{iid}/diffs?diff_id={vs[i]['id']}"
                    + (f"&start_sha={start}" if start else ""))


def waits_on_author(state, t):
    """Whether `t`'s answer was judged to leave the next move to the author, and that
    answer still stands."""
    v = t.get("verdict") or {}
    return v.get("says") == "wait" and v.get("on") == answer_mark(state, t) \
        and not peer_resolved(state, t)


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


def store_verdict(state, t, says, text, did=None, asked=None, case=None, unrelated=False,
                  wrong_move=False):
    """Store the agent's judgment of the answer standing on `t`. `wrong_move`: the code a
    push was found to have moved the commented code to is not it (`settle_moves`)."""
    if asked:
        t["asked"] = asked              # about the comment, so it outlasts every answer
    t["verdict"] = {"says": says, "text": text, "did": did, "on": answer_mark(state, t)}
    if says == "more" and case:
        t["verdict"]["case"] = case
    settle_moves(t, wrong_move)
    settle_near_fixes(t, says, case, unrelated)
    if says == "wait":
        t["draft"] = None               # waiting is choosing not to send it
        t.pop("draft_on", None)


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
    body = t and draft_text(t)
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


def thread_new(state, t):
    """How many comments others wrote on `t` since the user last acted on it (wrote there, or
    closed it here): what is new in its thread. Mirrors threads.py's `thread_new`, whose
    acts differ (a plan, a push)."""
    notes = [n for x in t["thread_ids"] for n in (state["threads"].get(x) or {}).get("notes") or []]
    acts = [n.get("at") for n in notes if n.get("mine")] + [t.get("acked_at"), (t.get("closed") or {}).get("at")]
    last = max([m for m in map(_ts, acts) if m], default=None)
    return sum(1 for n in notes if not n.get("mine")
               and (last is None or ((m := _ts(n.get("at"))) is not None and m > last)))


def card_face(state, t):
    """A fingerprint of what `t`'s card says now beyond its code: its name, where it stands,
    its threads' comments, the verdict on the answer, the author's pushes noted for it, its
    draft and how it was closed. The mod compares the one a card was drawn with (`emit`) to
    the topic's now (`state_json`): a card open while its news arrived went on showing the
    topic as it was, the new comment in the sidebar alone. Mirrors threads.py's."""
    said = [[n.get("url") or n.get("at"), len(n.get("body") or "")]
            for x in t["thread_ids"] for n in (state["threads"].get(x) or {}).get("notes") or []]
    v = t.get("verdict") or {}
    resolved = [bool((state["threads"].get(x) or {}).get("resolved")) for x in t["thread_ids"]]
    return hashlib.sha1(json.dumps([
        t.get("summary"), topic_status(state, t), said, resolved,
        [v.get(k) for k in ("says", "case", "text", "did", "on")], t.get("asked"),
        [[f.get("before"), f.get("after"), bool(f.get("near")), f.get("commits")] for f in fixes_of(t)],
        draft_text(t), t.get("closed"), t.get("why"),
        (peer_view_of(state, t) or {}) if peer_waiting(state, t) else None,
    ], sort_keys=True, default=str).encode()).hexdigest()[:12]


def emit(state, block, topic=None):
    """Print a gated view with its manifest, naming the topic it is about and the current
    one, so the paste gate enforces only what belongs to the topic being decided."""
    extra = view_context(state, topic)
    t = topic_for(state, topic) if topic else None
    if t:
        extra["face"] = card_face(state, t)
        if ui.drawn() and t["thread_ids"] and topic == current_topic(state):
            # The current topic's card, drawn (again): what arrived for it is seen now,
            # also where it arrived while the topic was open already.
            t["seen"] = answer_mark(state, t)
    ui.record({"review": extra["review"], "topic": topic, "focus": current_topic(state),
               "draft": extra.get("draft")})
    print(critical_manifest.with_manifest(block, topic=topic, focus=current_topic(state),
                                          extra=extra))


def news_of(state, t):
    """What arrived on `t` that waits for the user, and when it last did: `(kinds, at)`,
    the kinds among `reply`, `resolved`, `push` and `thread` (another reviewer's). The time
    is that of the thread's newest comment where the thread has news, which is what the News
    box's line about the topic says; a push has a line and a time of its own there, and
    dated by it, a thread from days ago read as opened at the time of the push."""
    if inbox_of(state, t) is None:
        return [], None
    kinds, at = [], []
    thr = [x for x in (state["threads"].get(i) for i in t["thread_ids"]) if x]
    if peer_waiting(state, t, thr):
        kinds.append("thread")
    if "thread" not in kinds and (any(x.get("awaiting") == "you" and not x.get("resolved") for x in thr)
                                  or _others_last(state, t)):
        kinds.append("reply")
    if any(x.get("resolved") for x in thr):
        kinds.append("resolved")
    at += [x.get("last_at") for x in thr if x.get("last_at")] if kinds else []
    push = pushed_of(state, t)
    if push:
        if not kinds:
            at.append(push["at"])
        kinds.append("push")
    return kinds, max(at) if at else None


def push_topics(state, p):
    """The topics push `p` is news of: those whose lines it changed, and those it changed
    beside their lines that the agent judged it a fix of (`settle_near_fixes`). A change
    elsewhere in a comment's file names its topic only then: by file alone, a push named
    every topic on the file it touched."""
    judged = [t["id"] for t in state["topics"] if t["id"] in (p.get("beside") or [])
              and any(f.get("after") == p.get("head") and not (f.get("near") or f.get("off"))
                      for f in t.get("fixes") or [])]
    return [t["id"] for t in state["topics"] if t["id"] in (p.get("topics") or []) + judged]


def inbox_json(state, topics):
    """The sidebar's inbox: what happened to the topics flagged new, by kind, each with its
    topics, the pushes that touched them one line each, and what waits. `topics` are
    `state_json`'s records, which carry each topic's own `news`."""
    # News is what the user has not opened yet: an opened topic is no news, decided or not.
    fresh = [x for x in topics if x["inbox"] in ("new", "fyi")]
    flagged = {x["id"] for x in fresh}

    def having(kind):
        return [x["id"] for x in fresh if kind in x["news"]]
    pushes = [p for p in state.get("pushes") or [] if flagged & set(push_topics(state, p))]
    proposals = [{"thread": p["thread"], "into": p["into"]} for p in state.get("proposals") or []]
    own = unsettled_own(state) if own_review_closed(state) else []
    times = ([x["news_at"] for x in fresh if x["news_at"]] + [p["at"] for p in pushes]
             + [m["at"] for m in state.get("mr_news") or [] if m.get("at")])
    first = round_pick(state)
    return {
        "since": min(times) if times else None,
        # Who wrote last on each topic with a reply: News names them, as every card does.
        "by": {x["id"]: who for x in fresh if (who := _last_other(state, x["id"]))},
        "replies": having("reply"), "resolved": having("resolved"),
        "threads": [{"id": x["id"], "by": (topic_for(state, x["id"]) or {}).get("by"),
                     "fyi": x["inbox"] == "fyi"}
                    for x in fresh if "thread" in x["news"]],
        "pushes": [{**{k: p.get(k) for k in ("at", "stat", "rebase", "url", "same", "own", "onto")},
                    "topics": push_topics(state, p)} for p in pushes],
        "mr": state.get("mr_news") or [],
        "own": proposals, "matching": len(own),
        "waiting": sum(1 for x in topics if x["status"] == "open"),
        "first": first["id"] if first else None,
        # The sidebar's code is the worktree's; a push after the round opened is not in it.
        "newer_push": bool(state.get("worktree_head") and state.get("seen_head")
                           and state["worktree_head"] != state["seen_head"]),
    }


def _last_other(state, tid):
    """The first name of whoever else wrote the newest comment of somebody else's on `tid`."""
    notes = [n for x in (topic_for(state, tid) or {}).get("thread_ids") or []
             for n in (state["threads"].get(x) or {}).get("notes") or [] if n.get("mine") is False]
    return first_name(notes[-1].get("author")) if notes else None


def prune_pushes(state):
    """Forget each push that is no news any more: every topic it touched opened since. One
    not yet linked to its diff stays, for `link_pushes`."""
    flagged = {t["id"] for t in state["topics"] if inbox_of(state, t) in ("new", "fyi")}
    state["pushes"] = [p for p in state.get("pushes") or [] if not p.get("url")
                       or flagged & set((p.get("topics") or []) + (p.get("beside") or []))]


def judge_list(state):
    """What the mod has judged by one model call (`judge_prompt`): the topics whose answer has
    no verdict yet, and the user's own comments not matched to a topic yet."""
    return {"topics": needs_verdict(state),
            "own": unsettled_own(state) if own_review_closed(state) else []}


def _said_by(state, n):
    """Who wrote a comment, as the judging prompt names them: the user, the MR's author, or
    another reviewer by name."""
    if n.get("mine"):
        return "The user (reviewer)"
    name = first_name(n.get("author")) or "Someone"
    return f"{name} (the MR's author)" if name == first_name(state.get("author")) else name


def _whole_threads(state, t):
    """`t`'s threads whole, every comment in order with who wrote it: what was agreed halfway
    through a thread counts as much as its last word."""
    out = []
    for th in t["thread_ids"]:
        x = state["threads"].get(th) or {}
        notes = x.get("notes") or [{"author": x.get("author"), "body": x.get("body"),
                                    "mine": x.get("mine")}]
        out.append(f"Thread on {_thread_loc(x) or MR_LEVEL}"
                   + (" (resolved)" if x.get("resolved") else "") + ":")
        out += [f"{_said_by(state, n)}: {(n.get('body') or '').strip()}" for n in notes]
        out.append("")
    return "\n".join(out).strip()


# What each kind of item is answered with, as the prompt says it once for all of its kind.
_ANSWER_FORMS = {
    "answer": (
        'An author\'s answer: {"verdict": "settled" | "more" | "wait", "did": "<one sentence: '
        'what the author did, e.g. Pushed the `await` on the commented line; no reply.>", "text": '
        '"<1-3 sentences: whether it holds and why>"}. `settled` when the comment is answered. '
        '`more` adds "case": "not-fixed" | "partly-fixed" | "fix-wrong" | "comment-wrong" (the '
        "last when the author did as asked and the comment itself was mistaken), and its text "
        "ends on one next step (a reply asking for X, accepting a follow-up MR, ...). `wait` when "
        "the author agreed and the change is still to come: the topic goes back to waiting on "
        "them. An item marked *give asked* also takes \"asked\": \"<one sentence: the problem the "
        "comment raised and what it asked for, e.g. `save()` drops its promise, so a failed write "
        "goes unnoticed; asked for an `await`.>\". An item marked *push beside its lines* may add "
        "\"unrelated_push\": true where the pushed change has nothing to do with the comment (with "
        "`wait` where nothing else answers it). An item marked *code moved* may add "
        "\"wrong_move\": true where the code it names as where the commented code went is not "
        "that code: the topic then stays on the comment's file."),
    "peer": (
        'Another reviewer\'s thread, theirs to settle, which the user watches: {"view": "fyi" | '
        '"needs", "why": "<1-2 sentences: what it says>"}. `needs` only when it is the user\'s '
        "to answer: a question to them (\"advice\": \"reply\"), a point that repeats or "
        "contradicts one of their topics (\"advice\": \"merge\", \"into\": \"<that topic's id>\"), "
        "or one they would want to clarify (\"advice\": \"reply\"); its why then ends on a "
        "`**Recommendation:**` sentence saying why. Anything else is `fyi`. An item marked *give "
        "summary* also takes \"summary\": \"<an English one-liner naming its point>\"."),
    "own": (
        "A comment the user posted on GitLab themselves since the topics started: compare it with "
        "the topics by what it says (file and line are a hint, never the test). The same point "
        'as a topic: {"into": "<topic id>", "summary": "<English one-liner>", "why": "<what both '
        'say, and where they differ>", "recommend": "merge" | "separate" | "link"} (merge is the '
        "usual answer; link when it is that topic's own draft, posted by hand). A point of its "
        'own: {"into": null, "summary": "<English one-liner>"}.'),
}


def judge_prompt(state, ctx, iid, skip=()):
    """The prompt the mod sends to the session's model when the update check found news,
    with the material for every item in it, and what each item was asked on (lib/judge.py):
    None where nothing is to judge. `skip`: the items a call failed on before, which the chat
    takes instead."""
    j = {k: [x for x in v if x not in skip] for k, v in judge_list(state).items()}
    if not (j["topics"] or j["own"]):
        return None
    author = first_name(state.get("author")) or "the author"
    out = [f"News arrived on GitLab merge request !{iid}, which the user is reviewing: {author} "
           "wrote the merge request, the user is its reviewer. Judge each item below, so that "
           "its card carries your judgment when the user opens it.", "", judge.RULES, ""]
    items, kinds = {}, {"answer": [], "peer": [], "own": []}
    for tid in j["topics"]:
        t = topic_for(state, tid)
        items[tid] = answer_mark(state, t)
        name = t.get("summary") if t.get("summary") and not needs_title(state, t) else None
        if peer_waiting(state, t):
            kinds["peer"].append(tid)
            who = t.get("by") or "another reviewer"
            out += [f"## {tid}: a thread by {who}, new or with new comments"
                    + ("" if name else " (*give summary*)"), "",
                    judge.fenced("thread", _whole_threads(state, t)), ""]
            continue
        kinds["answer"].append(tid)
        kinds_now, _ = news_of(state, t)
        near = bool((pushed_of(state, t) or {}).get("near"))
        what = (("the author pushed a change to the file the comment is on, though not to its "
                 "lines, and said nothing: it may answer the comment from beside it, or be "
                 "about something else" if near else
                 "the author pushed a change to the lines the comment is on, and said nothing")
                if kinds_now == ["push"] else
                "the author answered, and pushed a change to the comment's file (not its lines)"
                if near else "the author answered")
        moves = moves_said(t)
        marks = [m for m, on in (("give asked", not t.get("asked")),
                                 ("push beside its lines", near),
                                 ("code moved", unjudged_move(t))) if on]
        out += [f"## {tid}: {name or 'an answered comment'}"
                + (f" (*{'*, *'.join(marks)}*)" if marks else ""), "",
                f"What happened: {what}.", *moves]
        if t.get("asked"):
            out.append(f"What the comment asked, as recapped before: {t['asked']}")
        out += ["", judge.fenced("thread", _whole_threads(state, t)), "",
                "What the author changed since the comment, in the comment's file and wherever "
                "its code moved:" if moves else
                "What the author changed in the comment's file since the comment:",
                judge.fenced("diff", render_topic_diff(state, ctx, iid, tid)), ""]
    for th in j["own"]:
        kinds["own"].append(th)
        items[th] = "own"
        x = state["threads"].get(th) or {}
        out += [f"## {th}: a comment of the user's own at {_thread_loc(x) or MR_LEVEL}", "",
                judge.fenced("comment", plain_text(x.get("body")) or ""), ""]
    if kinds["peer"] or kinds["own"]:
        out += ["## The user's topics, for comparison", ""] + [
            f"- {t['id']} ({topic_status(state, t)}): {t.get('summary') or ''}"
            for t in state["topics"] if topic_status(state, t) != "dropped"] + [""]
    out += ["## How to answer", "",
            "Every text in English, every identifier, path and value in it in backticks: the "
            "card draws them as code. " + judge.CARD_VOICE, ""]
    out += [f"- {', '.join(ids)}: {_ANSWER_FORMS[k]}" for k, ids in kinds.items() if ids]
    out += ["", judge.ANSWER]
    return judge.request("\n".join(out), items, j["topics"], j["own"])


def judge_fresh(state, item, mark):
    """Whether an item `judge_prompt` asked about still waits for that judgment: nothing new
    in its thread since, and nothing stored in the chat meanwhile."""
    j = judge_list(state)
    if item in j["own"]:
        return mark == "own"
    t = topic_for(state, item) if item in j["topics"] else None
    return bool(t) and answer_mark(state, t) == mark


def judge_store(state, ctx, iid, item, a):
    """Store the model's answer `a` for `item`, as the chat's own commands store it (`verdict`,
    `peer`, `propose`, `add --thread`); judge.Malformed where it is not the answer asked for."""
    if item in unsettled_own(state):
        x = state["threads"][item]
        summary = judge.text(a, "summary")
        if a.get("into") is None:
            t = add_topic(state, source="human", summary=summary, file=x.get("file"),
                          line=x.get("line"))
            attach_thread(state, t, item, ctx, iid)
            return
        into = a.get("into")
        if not (isinstance(into, str) and topic_for(state, into)):
            raise judge.Malformed(f"no topic {into!r}")
        propose_match(state, item, into, summary, judge.text(a, "why"),
                      judge.choice(a, "recommend", ("merge", "separate", "link"), required=False)
                      or "merge")
        return
    t = topic_for(state, item)
    if peer_waiting(state, t):
        says = judge.choice(a, "view", ("fyi", "needs"))
        named = bool(t.get("summary")) and not needs_title(state, t)
        summary = judge.text(a, "summary", required=not named)
        advice = "seen" if says == "fyi" else judge.choice(a, "advice", ("reply", "merge", "seen"))
        into = a.get("into") if advice == "merge" else None
        if advice == "merge" and not (isinstance(into, str) and into != item and topic_for(state, into)):
            raise judge.Malformed(f"no topic {into!r} to merge into")
        store_peer_view(state, t, says, judge.text(a, "why"), summary, advice, into)
        return
    says = judge.choice(a, "verdict", ("settled", "more", "wait"))
    did, text = judge.text(a, "did"), judge.text(a, "text")
    asked = judge.text(a, "asked", required=not t.get("asked"))
    case = judge.choice(a, "case", tuple(VERDICT_CASES)) if says == "more" else None
    near = bool((pushed_of(state, t) or {}).get("near"))
    store_verdict(state, t, says, text, did=did, asked=None if t.get("asked") else asked,
                  case=case, unrelated=near and a.get("unrelated_push") is True,
                  wrong_move=unjudged_move(t) and a.get("wrong_move") is True)


def propose_match(state, thread, into, summary, why, recommend):
    """Offer the user to merge their own comment `thread` into topic `into`: the band asks."""
    props = [p for p in state.get("proposals") or [] if p["thread"] != thread]
    state["proposals"] = props + [{"thread": thread, "into": into, "summary": summary,
                                   "why": why, "recommend": recommend}]


def store_peer_view(state, t, says, why, summary, advice, into):
    """The agent's view of another reviewer's thread `t`: for the user's information, or
    needs them, with what to do."""
    if summary and (needs_title(state, t) or not t.get("summary")):
        # Named once: a thread that has a name keeps it, whatever was written in it since
        # (mirrors threads.py's `propose`).
        t["summary"], t["needs_title"] = summary, False
    if why:
        t["why"] = why
    t["peer_view"] = {"says": says, "advice": advice,
                      "into": into if advice == "merge" else None,
                      "on": answer_mark(state, t)}


def state_json(state):
    """The review as the review-ui mod reads it: one record per topic, the current one,
    and each draft's version. Derived the same way as every table, so the mod's buttons
    never disagree with what the views say."""
    topics = []
    left = set(left_after(state)) if is_over(state) else set()
    wt = get_worktree(state.get("slug") or "", state.get("iid"))
    kept = fix_refs.kept(wt, SKILL, state.get("iid")) if wt and os.path.isdir(wt) else {}
    for t in state["topics"]:
        body = draft_text(t)
        kinds, news_at = news_of(state, t)
        topics.append({
            "inbox": inbox_of(state, t), "news": kinds, "news_at": news_at,
            "id": t["id"], "status": topic_status(state, t), "state": ui_state(state, t),
            "kind": t.get("kind"),
            "severity": t.get("severity"), "source": t.get("source"),
            "summary": t.get("summary"), "file": t.get("file"), "line": t.get("line"),
            "posted": bool(t["thread_ids"]), "draft": draft_version(body) if body else None,
            # The author's push that changed its lines, kept in git: Show fix in hunk opens it.
            "fix_kept": bool((kept_fixes(state, t, kept) or [{}])[-1].get("ends")),
            # How many fixes of the author's it has, one per push: the sidebar picks between them.
            "pushes": len(fixes_of(t)),
            "follow_up": t.get("follow_up"),
            # Posted by the user's own hand: being checked against the draft.
            "post_check": bool(t.get("post_check")),
            "post_differs": bool(t.get("post_check") and t.get("post_differs")),
            # Merged or closed, and not settled: the user's to raise elsewhere or drop.
            "left": t["id"] in left,
            "verdict": (verdict_of(state, t) or {}).get("says"),
            # What is wrong with an answer that does not settle it (`VERDICT_CASES`): the News
            # box says it in the card's own words.
            "verdict_case": (verdict_of(state, t) or {}).get("case"),
            # What a button that acts on the verdict hands the chat, which did not judge it.
            "verdict_text": (verdict_of(state, t) or {}).get("text"),
            "peer": peer_waiting(state, t),
            # Who opened it, where somebody other than the user did: whose it is to settle.
            "by": t.get("by"),
            "peer_view": ({k: v for k, v in peer_view_of(state, t).items() if k != "on"}
                          if peer_waiting(state, t) and peer_view_of(state, t) else None),
            # Another reviewer's thread the user watches and has not closed for themselves.
            "watching": bool(peer_waiting(state, t) and (peer_view_of(state, t) or {}).get("says")
                             == "fyi" and t.get("state") != "acked"),
            "resolved": bool(t["thread_ids"]) and all(
                (state["threads"].get(x) or {}).get("resolved") for x in t["thread_ids"]),
            # The author resolved it and the user has not closed it: with a fix pushed or not.
            "author_resolved": author_resolved(state, t),
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
    return {"face": card_face(state, cur) if cur else None,
            "thread_new": thread_new(state, cur) if cur else 0,
            # The current topic's draft while it is not on GitLab yet: the sidebar shows it
            # under the lines it will be posted on, as GitLab shows a pending comment.
            "draft_text": draft_text(cur) if cur and not cur["thread_ids"] else None,
            "skill": "review-mr", "iid": state.get("iid"), "title": state.get("title"),
            "url": state.get("mr_web_url"), "lang": state.get("lang") or DEFAULT_LANG,
            "mr": mr_json(state.get("mr_status")),
            "merged": merge_left(state) if is_over(state) else None,
            "author": first_name(state.get("author")) or None,
            "people": people(state, first_name(state.get("author")) or None),
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


# A drafted comment is posted only on the person's press (lib/press.py), so what reaches
# GitLab is always text they saw and chose to send.
def post_draft(state, ctx, iid, tid, expect, resolve=False, failed=None):
    """Post `tid`'s draft: a new thread at its line (or on the MR itself), or, for a topic
    already posted, a reply into its thread. `resolve`: the reply settles the point, so its
    threads are resolved with it and the topic is closed here (GitLab's "Reply and resolve
    thread"); only a reply can, as a new comment has nothing to settle yet. Returns the
    posted note's URL.

    Once the note is on GitLab nothing here dies: the draft is spent at once, and what
    follows it is `after_posting`, which notes a failure in `failed` instead."""
    failed = [] if failed is None else failed
    if not press.claimed():
        die("posting is the user's press, on the Post button under a draft's card (Post + "
            "resolve where the reply settles its thread); nothing else posts. Show the draft "
            "and name the button to press.")
    t = topic_for(state, tid) or die(f"no topic {tid}")
    if resolve and not t["thread_ids"]:
        die(f"{tref(tid)} is not posted yet: a first comment opens its thread, and cannot "
            "resolve it")
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
        t["draft"] = None
        t.pop("follow_up", None)
        # A follow-up into a thread its author resolved reopens it: the reply asks something
        # of them still, and a resolved thread hides it from their list of open ones.
        th = state["threads"].get(pick) or {}
        url = f"{ctx['web']}/-/merge_requests/{iid}#note_{note.get('id')}"
        if resolve:
            before = len(failed)
            for x in t["thread_ids"]:
                other = state["threads"].get(x) or {}
                if not other.get("resolved") and not other.get("gone"):
                    if after_posting(failed, "resolving its thread", api_post,
                                     f"{disc}/{x}?resolved=true", {}, method="PUT") is not None:
                        other["resolved"] = True
            if len(failed) > before:
                return url              # still open on GitLab: not closed here either
            t["state"] = "acked"
            t["acked_at"] = now_iso()
            # How the user closed it, for the line its card ends on (`closing_line`), and
            # with which comment, which its story tells as that for good: a thread reopened
            # later still had this reply resolve it. Mirrors threads.py's `post_reply`.
            t["closed"] = {"how": "replied", "at": t["acked_at"], "note": ui.note_key(url)}
            t.setdefault("closed_with", {})[ui.note_key(url) or url] = "replied"
        elif th.get("resolved"):
            if after_posting(failed, "reopening its thread", api_post,
                             f"{disc}/{pick}?resolved=false", {}, method="PUT") is not None:
                th["resolved"] = False
        return url
    fields = {"body": body}
    if t.get("file") and t.get("line"):
        pos = diff_position(ctx, iid, t["file"], int(t["line"]))
        if not pos:
            die(f"`{t['file']}:{t['line']}` is not part of the MR's diff, so GitLab cannot "
                "anchor a comment there; nothing was posted. Copy it and post it by hand, "
                "or move the topic to a changed line.")
        fields["position"] = pos
    d = api_post(disc, fields)
    # attach_thread links the thread and spends the draft before it asks GitLab for the
    # MR's head, so a failure there leaves the comment posted; the next sync takes the head.
    after_posting(failed, "reading the MR's head", attach_thread, state, t, d["id"], ctx, iid)
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
    baseline is the current tip (no push) or unset, and if it is no version's head
    (set from a brand-new MR's branch tip before GitLab recorded a version of it):
    with nothing to stop at, every version since the MR was opened would count."""
    last = state.get("last_reviewed_head")
    if not last:
        return []
    out = []
    for v in vs:                                 # newest first
        if v.get("head_commit_sha") == last:
            return list(reversed(out))
        out.append(v)
    return []


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


def _push_commits(ctx, frm, to):
    """The commits GitLab's compare API lists from `frm` (left out) to `to`, each with its
    `id` and `parent_ids`; None on error."""
    if not frm or not to:
        return None
    try:
        r = api(f"projects/{ctx['enc']}/repository/compare"
                f"?from={frm}&to={to}&straight=true")
    except SystemExit:
        return None
    got = (r or {}).get("commits") if isinstance(r, dict) else None
    return got if isinstance(got, list) else None


# How long a push may lie before GitLab records the version it made (`push_time`).
PUSH_LAG = 120


def push_time(ctx, version):
    """When the push that made the MR's `version` happened, as near as GitLab tells it.

    The version is recorded some time after the push, by a job of GitLab's own, so its time
    can come after a reply the author wrote right after pushing: a story or the News box
    ordered by it put the reply first. The push's head commit was made before the push, so
    where it was made only a little before the version (`PUSH_LAG`), its time is the
    push's; a commit older than that was pushed later than it was made, and the version's
    time stands. None where GitLab says neither."""
    at = version.get("created_at")
    sha = version.get("head_commit_sha")
    if not (at and sha):
        return at
    try:
        c = api(f"projects/{ctx['enc']}/repository/commits/{sha}")
    except SystemExit:
        return at
    made = (c or {}).get("committed_date") if isinstance(c, dict) else None
    lag = (instant(at) - instant(made)).total_seconds() if instant(at) and instant(made) else None
    return made if lag is not None and 0 <= lag <= PUSH_LAG else at


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
    compare API (force-push-safe): the comment's file, and every file a push moved its code
    to (`topic_files`), said above the diff. Always prints the compare URL.

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
    # The comment's file, and every file its code moved to since: a function moved out of
    # it, fix and all, is in the file it went to, and the comment's file alone shows it
    # removed.
    files = topic_files(state, t)
    vs = versions(ctx, iid)
    diff_id = vs[0].get("id") if vs else None
    web = _mr_web(state, ctx, iid)
    url = f"{web}/diffs?diff_id={diff_id}&start_sha={start}"
    out = [f"**{tref(tid)}** — author changes since you posted "
           f"(`{start[:12]}` → `{(cur or '')[:12]}`)", url]
    out += [f"_{m}_" for m in moves_said(t)]
    diffs = _compare(ctx, start, cur)
    if diffs is None:
        out.append("_(couldn't fetch the diff — open the URL)_")
        return "\n".join(out)

    def which(d):
        return next((k for k, f in enumerate(files) if f in (d.get("new_path"), d.get("old_path"))), None)
    picked = sorted((d for d in diffs if which(d) is not None), key=which) if files else diffs
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
        try:
            viewer = _show_in_viewer(state, ctx, iid, tid, picked, (start, cur))
        except NotesNotCleared as e:
            viewer = None
            out += ["", f"_{e}; the diff is here instead._"]
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
    """The compare API's per-file diffs as one git-style patch: what the viewer reads where
    the review's worktree does not hold them as a commit range (`topic_range`), and what
    such a range must match. GitLab hands back each file's hunks without headers, so they
    are written here — rename lines included, so the viewer shows a renamed file under its
    new name."""
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


def viewer_name(state, tid):
    """The diff viewer window's name while it shows topic `tid` (`hunk.rename`): the
    topic's title, its card's, and its id until the title's short form is known. Mirrors
    threads.py's `viewer_name`."""
    return (topic_for(state, tid) or {}).get("summary"), tid


def topic_range(wt, ends, picked):
    """(viewer args, diff text) of the topic's change as the commit range it is in the
    review's worktree `wt`, or None where it is not one there: no worktree, a commit of
    `ends` it does not have (a force-push pruned the baseline; nothing is fetched for it),
    or a local diff that differs from GitLab's `picked` in any file's line counts (a file
    too big for GitLab to send, say). A renamed file is asked for by both its names, as
    GitLab pairs them; by its new name alone git would show it added."""
    if not (wt and os.path.isdir(wt) and all(fix_refs.has_commit(wt, e) for e in ends)):
        return None
    paths = sorted({p for d in picked for p in (d.get("old_path"), d.get("new_path")) if p})
    text = fix_refs.patch(wt, ends, paths)
    gitlab = sorted(hunk.diff_stat(topic_patch(picked)))
    if not text.strip() or sorted(hunk.diff_stat(text)) != gitlab:
        return None
    return fix_refs.view_args(ends, paths), text


def _show_in_viewer(state, ctx, iid, tid, picked, ends):
    """The pointer lines when the topic's diff (GitLab's `picked`, from `ends`) is now in
    this agent's viewer window, or None to show it inline — no tmux, no viewer, or a window
    that came back holding anything but exactly its files and line counts (see
    `hunk.show_view`). As a commit range of the review's worktree wherever it is one there
    (`topic_range`), else as a patch. Raises `NotesNotCleared` where the window could not be
    cleared for it (`clear_notes_before_load`)."""
    wt = get_worktree(ctx["slug"], iid)
    got = topic_range(wt, ends, picked)
    if got:
        args, text = got
    else:
        text = topic_patch(picked)
        args = ["patch", state_file(STATE_ROOT, ctx["slug"], iid, f"diff-{tid}.patch")]
    return show_load(state, ctx, iid, tid, args, text,
                     wt if wt and os.path.isdir(wt) else os.getcwd())


def show_load(state, ctx, iid, tid, args, text, cwd):
    """Load `args` — a commit range of the worktree `cwd`, or `["patch", path]`, with
    `text` the diff it holds (a patch is written to `path` here) — in this agent's viewer
    window for topic `tid`: the notes in the window kept and cleared first where it moves
    on (`clear_notes_before_load`), the window marked with what it holds (`mark_shown`).
    The pointer lines, or None where the window could not be made to hold exactly `text`."""
    clear_notes_before_load(ctx, iid, args, text)
    if args[0] == "patch":
        with open(args[1], "w") as fh:
            fh.write(text)
    stat = hunk.diff_stat(text)
    shown = hunk.show_view(args, stat, viewer_name(state, tid), cwd)
    if not shown:
        return None
    mark_shown(shown[0], iid, tid, args, text)
    head = f"_Full diff in tmux window {shown[1]}:_ {diffsize.of_stat(stat)}"
    # The only line naming where the diff is, so it must not be the one a paste may drop.
    critical_manifest.mark(head)
    return [head, "", fence("\n".join(hunk.stat_rows(stat)), "yaml")]


def view_range(state, ctx, iid, tid, rng, files):
    """`view`: the commits `rng` (`<from>..<to>`) of the review's worktree, only `files`
    where any are named, in this agent's viewer window for topic `tid` — with every
    guarantee of `diff <t> --show` (`show_load`). The pointer lines; dies with what is
    wrong where nothing was loaded."""
    if "--iid" in files:
        die("`--iid` after `--` is taken for a file: give it before the `--`")
    wt = get_worktree(ctx["slug"], iid)
    if not (wt and os.path.isdir(wt)):
        die(f"!{iid} has no review worktree to show commits from")
    frm, sep, to = rng.partition("..")
    if not (sep and frm and to) or to.startswith("."):
        die(f"`{rng}` is not a range of two commits: give it as <from>..<to>")

    def commit(ref):
        r = subprocess.run(["git", "-C", wt, "rev-parse", "--verify", "--quiet",
                            "--end-of-options", f"{ref}^{{commit}}"],
                           capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None
    ends = tuple(commit(r) or die(f"`{r}` is no commit the review's worktree has (fetch it, "
                                  "or name another)") for r in (frm, to))
    text = fix_refs.patch(wt, ends, files)
    if not text.strip():
        die(f"`{rng}` changes nothing" + (f" in {', '.join(files)}" if files else "")
            + ": there is no diff to show")
    try:
        shown = show_load(state, ctx, iid, tid, fix_refs.view_args(ends, files), text, wt)
    except NotesNotCleared as e:
        die(str(e))
    if not shown:
        die(hunk.not_shown())
    return "\n".join([f"**{tref(tid)}** — `{frm}..{to}` (`{ends[0][:12]}` → `{ends[1][:12]}`)",
                      ""] + shown)


# What this agent's diff viewer window shows, recorded on the window (`hunk.set_mark`) as
# JSON: the MR, the topic, what it loaded (`load`, as `hunk session reload` takes it: a
# commit range of the review's worktree, or a patch file) and a digest of the diff that
# holds; and which of the user's notes in it `notes` has printed (their numbers, in that
# order) and `reply` has answered. On the window because all of it is true only for as long
# as the window is.
VIEWER_MARK = "@review-mr-shown"


class NotesNotCleared(Exception):
    """The diff viewer window could not be asked for the user's notes in it, or could not
    clear them, so it was left holding what it had rather than loaded over them."""


def viewer_window():
    """This agent's diff viewer window, as its session id, or None: no tmux, no `hunk`, or
    no window of this agent's open."""
    if not hunk.installed() or not hunk.in_tmux():
        return None
    found = hunk.find_view(hunk.owner_id())
    return found[0] if found else None


def no_viewer(then):
    """Why there is no diff viewer window to work with, in one line."""
    if not hunk.installed() or not hunk.in_tmux():
        return hunk.not_shown()
    return f"no diff viewer window of yours is open; {then}"


def viewer_shows(sid):
    """The window's `VIEWER_MARK`, or {} where there is none."""
    try:
        m = json.loads(hunk.mark(sid, VIEWER_MARK) or "{}")
    except ValueError:
        return {}
    return m if isinstance(m, dict) else {}


def _set_shows(sid, shows):
    return hunk.set_mark(sid, VIEWER_MARK, json.dumps(shows, separators=(",", ":")))


def _same_load(shows, args, text):
    return shows.get("load") == list(args) and shows.get("digest") == _digest([text])


def kept_notes_path(ctx, iid, make=True):
    """Where the user's notes on a diff the window has since moved on from are kept, until
    `notes` prints them."""
    return state_file(STATE_ROOT, ctx["slug"], iid, "viewer-notes.json", make)


def _kept_notes(path):
    try:
        data = load(path)
    except (OSError, ValueError):
        return []
    return [n for n in data if isinstance(n, dict)] if isinstance(data, list) else []


def clear_notes_before_load(ctx, iid, args, text):
    """Ready this agent's diff viewer window to load `args` (see `VIEWER_MARK`), which holds
    the diff `text`.

    The viewer re-anchors its notes by line number when it loads another diff, so a note
    left on one topic's diff would land on unrelated code in the next: every note goes. The
    user's are kept first (`kept_notes_path`, with the topic they were written on), because
    a load is often a person's press, whose output never reaches the model; the kept copy
    is then the only way their notes still do. A note `notes` printed already has reached
    the model, and is not kept: kept, it came back as unanswered once the window had moved
    on, its answer given long before. Loading the same again moves nothing, and every note
    stays. A load the window cannot take closes it (`hunk.show_view`), which is why the
    notes are kept first whatever comes next.

    Raises `NotesNotCleared` where the viewer cannot say which notes it holds, or cannot clear
    them: the window is then left as it is, and nothing is to be loaded into it."""
    sid = viewer_window()
    if not sid:
        return
    shows = viewer_shows(sid)
    if _same_load(shows, args, text):
        return
    got = hunk.user_notes(sid)
    if got is None:
        raise NotesNotCleared("the diff viewer did not answer, so it was left holding what it "
                         "had, any notes of yours in it included")
    printed = set(shows.get("printed") or [])
    unread = [n for n in got if n[4] not in printed]
    if unread:
        keep_viewer_notes(ctx, shows.get("iid") or iid, shows.get("topic"), unread)
    if not hunk.clear_all_notes(sid):
        raise NotesNotCleared("the notes in the diff viewer could not be cleared, so it was left "
                         "holding what it had")


def keep_viewer_notes(ctx, iid, topic, notes):
    """Add the user's `notes` (`hunk.user_notes`' shape), written on `topic`, to the kept
    copy; a note kept already is not kept twice."""
    path = kept_notes_path(ctx, iid)
    hold(path)
    data = _kept_notes(path)
    have = {n.get("id") for n in data}
    data += [{"topic": topic, "file": f, "side": side, "line": line, "text": text, "id": nid}
             for f, side, line, text, nid in notes if nid not in have]
    _save(path, data)


def mark_shown(sid, iid, tid, args, text):
    """Record on the window that it now shows `args` for topic `tid`, holding the diff
    `text`. The same loaded again keeps the record of which notes were printed and
    answered."""
    prev = viewer_shows(sid)
    shows = {"iid": iid, "topic": tid, "load": list(args), "digest": _digest([text])}
    if _same_load(prev, args, text):
        shows.update({k: prev[k] for k in ("printed", "answered") if k in prev})
    _set_shows(sid, shows)


def point_out(iid, tid, path, line, text, side):
    """A note of the agent's on `path`:`line` of topic `tid`'s diff in the viewer, in view;
    the line saying where. Only on that diff: a note meant for another would go on whatever
    the window holds at that line."""
    sid = viewer_window() or die(no_viewer(f"show the diff first with `diff {tid} --show`"))
    shows = viewer_shows(sid)
    if shows.get("iid") != iid or shows.get("topic") != tid or not shows.get("load"):
        die(f"the diff viewer does not show {tid}'s diff; show it first with "
            f"`diff {tid} --show`")
    if not hunk.point(sid, path, line, text, "agent", side=side, args=shows["load"]):
        die(f"{path}:{line} cannot carry a note: it is not a line {tid}'s diff in the viewer "
            "shows (or it is hidden there as read). Name that place in the chat instead.")
    return f"pointed out {_where(path, side, line)} on {tref(tid)} in window {hunk.window_of(sid)}"


def _where(file, side, line):
    return f"{file}:{line}" + (" (removed line)" if side == "old" else "")


def viewer_notes(ctx, iid):
    """The user's notes for the model, as lines: those in the window not yet answered,
    numbered for `reply`, then those kept from diffs it has moved on from, to answer in the
    chat. Printed before the kept copy is forgotten; a note in the window stays there until
    its topic is acked."""
    sid = viewer_window()
    kept_path = kept_notes_path(ctx, iid, make=False)
    if os.path.exists(kept_path):
        hold(kept_path)
    kept = _kept_notes(kept_path)
    if not sid and not kept:
        die(no_viewer("there are no notes to read"))
    live, shows = [], {}
    if sid:
        live = hunk.user_notes(sid)
        if live is None:
            die("the diff viewer did not answer; nothing can be said about the notes in it")
        shows = viewer_shows(sid)
    printed = list(shows.get("printed") or [])
    answered = set(shows.get("answered") or [])
    # A number, once given, stays the note's for as long as the window shows this diff.
    printed += [n[4] for n in live if n[4] and n[4] not in printed]
    topic = tref(shows["topic"]) if shows.get("topic") else "?"
    lines = []
    for f, side, line, text, nid in live:
        if nid not in answered:
            lines += [f"#{printed.index(nid) + 1}  {topic} {_where(f, side, line)}",
                      f"    {text}"]
    seen = {n[4] for n in live}
    for n in kept:
        if n.get("id") not in seen:
            on = tref(n["topic"]) if n.get("topic") else "an earlier diff"
            lines += [f"written on {on}, {_where(n.get('file'), n.get('side'), n.get('line'))}"
                      ": no longer in the window, answer in the chat", f"    {n.get('text')}"]
    print("\n".join(lines) if lines else "no notes of yours in the diff viewer")
    if sid and printed != list(shows.get("printed") or []):
        _set_shows(sid, {**shows, "printed": printed})
    hunk.forget_notes(kept_path)


def reply_note(number, text):
    """Answer the user's note `number` (as `notes` numbered it) under it in the window."""
    sid = viewer_window() or die(no_viewer("answer the note in the chat"))
    shows = viewer_shows(sid)
    printed = shows.get("printed") or []
    ref = number.lstrip("#")
    if not ref.isdigit() or not 1 <= int(ref) <= len(printed):
        die(f"no note #{ref} in the diff viewer; `notes` numbers them")
    nid = printed[int(ref) - 1]
    live = hunk.user_notes(sid)
    if live is None:
        die("the diff viewer did not answer; nothing was posted")
    note = next((n for n in live if n[4] == nid), None) or die(
        f"note #{ref} is no longer in the diff viewer; answer it in the chat")
    if shows.get("load") and not hunk.show_unread(sid, [note[0]], shows["load"]):
        die(f"#{ref} is on {note[0]}, which the window hides as read and could not show "
            "again: Hunk takes no reply there. Answer it in the chat")
    if not hunk.reply(sid, nid, text):
        die(f"the reply to #{ref} was refused by the viewer")
    _set_shows(sid, {**shows, "answered": (shows.get("answered") or []) + [nid]})
    return f"answered #{ref} under the note on {_where(*note[:3])}"


def tidy_viewer(iid, tid):
    """Topic `tid` is acked: where the diff viewer window shows it, its notes go — the
    agent's, and the user's that `notes` has printed. A user's note never printed stays,
    and so does every note above it in its thread. Never stands in the ack's way: a line to
    print where the viewer did not answer, else None."""
    sid = viewer_window()
    shows = viewer_shows(sid) if sid else {}
    if shows.get("iid") != iid or shows.get("topic") != tid:
        return None
    got = hunk.notes(sid)
    if got is None:
        return "the diff viewer did not answer; its notes stay"
    printed = set(shows.get("printed") or [])
    by_id = {n["id"]: n for n in got}
    stays = set()
    for n in got:
        if n["source"] == "user" and n["id"] not in printed:
            at = n["id"]
            while at in by_id and at not in stays:
                stays.add(at)
                at = by_id[at]["parent"]
    # Newest first, so a reply goes before the note it answers.
    for n in reversed(got):
        if n["id"] not in stays and (n["source"] != "user" or n["id"] in printed):
            hunk.drop_note(sid, n["id"])
    return None


# ---------------------------------------------------------------- worktree


def worktree_path(slug, iid, make=True):
    return state_file(STATE_ROOT, slug, iid, "worktree", make)


def get_worktree(slug, iid):
    p = worktree_path(slug, iid, make=False)
    if os.path.exists(p):
        with open(p) as f:
            return f.read().strip()
    return None


def set_worktree(slug, iid, path):
    # Absolute: a later run from another directory (prune runs before the `cd`) must not
    # read `.` as wherever it happens to be.
    with open(worktree_path(slug, iid), "w") as f:
        f.write(os.path.abspath(path.strip()) + "\n")


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
    keep_author_fixes(state, wt, iid)


def walk_fixes(state, ctx, iid, vs=None):
    """Note each push of the author's that changed a topic's lines since its comment, one
    record per push (`t["fixes"]`), open topics and closed ones alike, the thread resolved or
    not: whatever happens in the thread, the code says whether the author fixed it.

    The MR's versions are GitLab's record of every push (force pushes included), so the walk
    goes from the version the comment was posted on to the newest, one pair of consecutive
    versions at a time, and carries the comment's lines through each (`code_view.touched`).
    A push that only shifted them is no fix; one that changed them is, with its GitLab link
    (that version against the one before). One that removed them and added the same code
    elsewhere — another file, or further down this one — moved it: the record says where
    (`moved_to`), and the walk follows the code there from then on (`walk_places`), until the
    agent rejects the move (`settle_moves`). Where a rebase came with it, the head-to-head
    diff holds the target branch's changes too: the file counts only where the MR's own
    change to it differs between the two versions, as `_author_touch` decides for the
    `updates` report, and the record says it may include other changes. Each topic keeps
    where it got to (`t["fix_walk"]`), so a check walks only the versions new since.

    A push that laid commits on top, as an author who answers each thread with a commit of
    its own pushes, is narrowed to the topic's commits in it (`its_commits`): what the
    sidebar and the viewer show of the fix is theirs, not every other thread's answer that
    went up with them. A push that rewrote the branch (a rebase, a force push) is the fix
    whole, as nobody can say which of its commits did what.

    Where the versions cannot tell (the comment's commit is no version's head), one record
    spans the comment's commit to the head, marked as possibly holding other changes, and
    follows the head until the topic is closed here.

    Another reviewer's thread, or the MR author's own, is walked the same way: what the
    sidebar shows of it is then each push that changed its code, not one span from the
    comment to the head with everything pushed meanwhile. Nobody judges an answer on one
    (it gets a view, `peer_view_of`), so a push that changed only its file elsewhere stays
    listed as a change to its file. A topic noted before it was walked (its one `pushed`
    record) is walked from its comment's commit at the next check, as every topic is."""
    vs = versions(ctx, iid) if vs is None else vs
    chron = list(reversed(vs or []))
    if not chron:
        return
    at = {v.get("head_commit_sha"): k for k, v in enumerate(chron)}
    head = chron[-1].get("head_commit_sha")
    web = _mr_web(state, ctx, iid)

    def compare(frm, to):
        return _compare(ctx, frm, to)

    def own_change(v, path):
        diffs = compare(v.get("base_commit_sha"), v.get("head_commit_sha"))
        if diffs is None:
            return None
        d = _file_diff(diffs, path)
        return _HUNK_HEADER_RE.sub("", (d or {}).get("diff") or "")

    def step(t, places, diffs, after, rebase):
        """What one change (`diffs`, making `after`) did to the places `t`'s code is in:
        (hits, nears, moved, places after it). `rebase`: the two versions where the change
        is a push that came with a rebase, else False."""
        hits, nears, moved, now = [], [], [], []
        for p in places:
            d = _file_diff(diffs, p["file"])
            text = (d or {}).get("diff")
            hit, first, last = code_view.touched(text, p["first"], p["last"])
            there = {**p, "file": (d or {}).get("new_path") or p["file"], "first": first,
                     "last": last}
            if p.get("left"):
                now.append(there)                 # followed, not watched (`walk_places`)
                continue
            # The change touched the place's file: its lines (`hit`), or elsewhere in it
            # (`near`, as `check_pushes` notes it: the agent judges whether it concerns the
            # comment, `settle_near_fixes`). Not for a topic closed (`fixes_closed`): a later
            # change to its file is no answer to it.
            near = not hit and bool((text or "").strip()) and not fixes_closed(state, t)
            if (hit or near) and rebase:
                was, became = own_change(rebase[0], p["file"]), own_change(rebase[1], p["file"])
                if was is not None and was == became:
                    hit = near = False            # the rebase moved it, not the author
                elif was is None or became is None:
                    near = False                  # GitLab could not say whose change it is
            # Lines removed may have gone elsewhere rather than away: a function moved into
            # another file, or down its own. The walk goes on where they went; where every
            # one of them went, this place is only followed (`walk_places`).
            went = code_view.moved_to(text, p["first"], p["last"], [
                (o.get("new_path") or o.get("old_path"), o.get("diff"), o is d)
                for o in diffs]) if hit else []
            if went:
                via = [*p.get("via", []), after]
                now += [{**q, "via": via} for q in went]
                moved += [q for q in went if q not in moved]
                if code_view.all_removed(text, p["first"], p["last"]):
                    there["left"] = after
            now.append(there)
            if hit or near:
                (hits if hit else nears).append(p.get("via") or [])
        return hits, nears, moved, [p for k, p in enumerate(now) if p not in now[:k]]

    def its_commits(t, places, before, after):
        """Which of the commits a push laid on top (`before` an ancestor of `after`, one
        line of commits) are `t`'s: each carried through as a push of its own, from the
        places its code was in before the push, those that changed its lines, else those
        that changed its file. Merged into the push's record: `commits` (those, oldest
        first) with `of` (how many the push held) and `span` (the commit before the first
        of them, and the last), and `paths` (the files they changed, both names of a
        renamed one) where others lie between them; `commits` None where the push is the
        fix whole (no line of commits, one commit, or all of them its); None where GitLab
        could not say, so a later walk asks again (`backfill`)."""
        chain = push_chain(before, after)
        if chain is None:
            return None
        if chain is False or len(chain) < 2:
            return {"commits": None}
        hits, nears, parent, ends = [], [], before, {}
        for c in chain:
            diffs = compare(parent, c)
            if diffs is None:
                return None
            h, n, _moved, places = step(t, places, diffs, c, False)
            (hits if h else nears if n else []).append(c)
            ends[c] = (parent, diffs)
            parent = c
        picked = hits or nears
        if not picked or len(picked) == len(chain):
            return {"commits": None}
        part = {"commits": picked, "of": len(chain), "span": [ends[picked[0]][0], picked[-1]]}
        at = [chain.index(c) for c in picked]
        if at[-1] - at[0] + 1 != len(at):
            # The range from the first to the last holds commits that are not its: only the
            # files its own changed, so the others' files are left out (those that changed
            # a file of its too are in, as they are in the code).
            part["paths"] = sorted({q for c in picked for d in ends[c][1]
                                    for q in (d.get("old_path"), d.get("new_path")) if q})
        return part

    chains = {}

    def push_chain(before, after):
        """The commits from `before` (left out) to `after`, oldest first, where they are one
        line on top of it: [] where `after` is `before`, False where they are not (a force
        push, a merge), None where GitLab could not say."""
        if (before, after) not in chains:
            got = _push_commits(ctx, before, after)
            if got is None:
                return None
            by = {c.get("id"): c for c in got}
            line, at = [], after
            while at != before and len(line) <= len(by):
                c = by.get(at)
                ps = (c or {}).get("parent_ids") or []
                if len(ps) != 1:
                    break
                line.append(at)
                at = ps[0]
            chains[(before, after)] = line[::-1] if at == before and len(line) == len(by) else False
        return chains[(before, after)]

    def backfill(t):
        """A topic whose fixes were recorded before a push's commits were told apart: walked
        again from its comment, on a copy, and each fix given the commits the copy found for
        the same push. Its fixes as they are otherwise (judged, dropped), and a fix the copy
        walked past without finding is the push whole; one it did not get to, GitLab cutting
        its walk short, is left to the next walk."""
        old = [f for f in t.get("fixes") or [] if "commits" not in f]
        if not old:
            return
        if all(f.get("mixed") or f.get("rebase") for f in old):
            for f in old:
                f["commits"] = None
            return
        g = {**json.loads(json.dumps(t)), "fixes": []}
        g.pop("fix_walk", None)
        reached = walk_topic(g, ghost=True)
        found = {(f["before"], f["after"]): f for f in g.get("fixes") or []}
        for f in old:
            new = found.get((f["before"], f["after"]))
            if f.get("mixed") or f.get("rebase"):
                f["commits"] = None
            elif new is None:
                # Not found by a walk that got to the head: the push whole. One GitLab cut
                # short says nothing of the pushes past where it stopped: the next walk asks.
                if reached:
                    f["commits"] = None
            elif "commits" in new:
                f.update({k: new[k] for k in ("commits", "of", "span", "paths") if k in new})

    def walk_topic(t, ghost=False):
        """Walk `t` on from where it got to. Whether the walk got to the head: False where
        GitLab could not say what a version changed, and the walk stopped before it."""
        if not t["thread_ids"] or t.get("state") == "dropped":
            return True
        x = state["threads"].get(t["thread_ids"][0]) or {}
        if not (x.get("file") and x.get("line") and x.get("side") == "new" and x.get("head_sha")):
            return True
        if not ghost:
            backfill(t)
        if "fix_walk" not in t:
            sha, first, last = comment_origin(x, vs, compare) or (
                x["head_sha"], x.get("line_start") or x["line"], x["line"])
            t["fix_walk"] = {"sha": sha, "places": [{"file": x["file"], "first": first, "last": last}]}
        w = t["fix_walk"]
        places = walk_places(w)
        k = at.get(w["sha"])
        if k is None:
            if w["sha"] == head or w.get("lost") and fixes_closed(state, t):
                return True
            diffs = compare(w["sha"], head)
            if diffs is not None and any(
                    code_view.touched((_file_diff(diffs, p["file"]) or {}).get("diff"), p["first"], p["last"])[0]
                    for p in places if not p.get("left")):
                w["lost"] = True
                t["fixes"] = [{"before": w["sha"], "after": head, "at": now_iso(), "mixed": True,
                               "commits": None, "url": f"{ctx['web']}/-/compare/{w['sha']}...{head}"}]
            return diffs is not None
        for prev, cur in zip(chron[k:], chron[k + 1:]):
            diffs = compare(prev.get("head_commit_sha"), cur.get("head_commit_sha"))
            if diffs is None:
                return False                      # GitLab could not say: the next check tries
            rebase = cur.get("base_commit_sha") != prev.get("base_commit_sha")
            after = cur.get("head_commit_sha")
            hits, nears, moved, now = step(t, places, diffs, after, rebase and (prev, cur))
            was, places = places, now
            w["places"] = places
            if hits or nears:
                fix = {"before": prev.get("head_commit_sha"), "after": after,
                       "at": cur.get("created_at") if ghost else push_time(ctx, cur) or now_iso(),
                       "mixed": rebase, "rebase": rebase,
                       "url": f"{web}/diffs?diff_id={cur.get('id')}&start_sha={prev.get('head_commit_sha')}"}
                if not rebase:
                    part = its_commits(t, was, fix["before"], after)
                    if part is not None:
                        fix.update(part)
                else:
                    fix["commits"] = None
                if not hits:
                    fix["near"] = True
                # The moves the places it was found on came through: a move the agent rejects
                # takes the pushes found only on where it led with it (`settle_moves`).
                if any(hits or nears):
                    fix["via"] = hits or nears
                if moved:
                    fix["moved_to"] = moved
                if rebase:
                    # What the viewer and the files list show of it: the push against the
                    # head before it rebased onto the new base (`fix_refs.rebased`), so the
                    # target branch's changes are left out, in a file both changed too.
                    # Where that merge conflicts, the files the MR itself changes, the
                    # target branch's own left out; a file both changed then shows both.
                    fix["base"] = cur.get("base_commit_sha")
                    own = compare(cur.get("base_commit_sha"), cur.get("head_commit_sha"))
                    if own is not None:
                        fix["paths"] = sorted({d.get("new_path") or d.get("old_path") for d in own})
                t.setdefault("fixes", []).append(fix)
            w["sha"] = after
        return True

    for t in state["topics"]:
        walk_topic(t)


def fixes_closed(state, t):
    """Whether `t` is closed for the fix walk: a later change to its file is no answer to it.
    The user's own topic once closed here or dropped; another reviewer's thread the user
    watches once they closed it or it is resolved, and not where the agent's view only made
    it something to know of (`topic_status` reads such a view as done)."""
    if peer_waiting(state, t):
        return t.get("state") in ("acked", "dropped")
    return topic_status(state, t) in ("acked", "dropped")


def walk_places(w):
    """The places a topic's walk (`t["fix_walk"]`) follows its code through, in the code at
    `w["sha"]`: `{"file", "first", "last"}` each, the comment's own first, then every place
    a push moved the code to (`via`: those pushes, in order). One whose code a push moved
    away entirely is `left` (that push): followed but not watched, so its file's later edits
    count for nothing, until the agent judged the move (`settle_moves`). A walk recorded
    before code was followed out of its file is read as its one place."""
    if "places" not in w:
        w["places"] = [{"file": w.pop("file", None), "first": w.pop("first", None),
                        "last": w.pop("last", None)}]
    return w["places"]


def settle_moves(t, wrong=False):
    """Once the agent judged the answer on `t`: each push found to have moved its code that
    the agent had not judged yet stands where it did not call the move `wrong` (the code it
    went to is not the commented code), and the places the code left are let go. A wrong
    one is undone: the place it led to is dropped, with every push found only there, and
    the walk watches where the code was again, from where it is now."""
    w = t.get("fix_walk")
    for f in t.get("fixes") or []:
        if not f.get("moved_to") or f.get("move_seen"):
            continue
        m = f["after"]
        places = walk_places(w) if w else []
        if not wrong:
            f["move_seen"] = True
            if w:
                w["places"] = [p for p in places if p.get("left") != m]
            continue
        f.pop("moved_to")
        for g in t["fixes"]:
            if g.get("via") and all(m in v for v in g["via"]):
                g["off"] = True
        if w:
            w["places"] = [{k: v for k, v in p.items() if not (k == "left" and v == m)}
                           for p in places if m not in p.get("via", [])]


def moved_place(t):
    """Where `t`'s code is, as of the walk's commit (`sha`), where pushes moved it away from
    the comment's place entirely: the first place the walk watches that a move led to. None
    while the comment's own place is watched still."""
    w = t.get("fix_walk")
    places = walk_places(w) if w else []
    if any(not p.get("via") and not p.get("left") for p in places):
        return None
    p = next((p for p in places if p.get("via") and not p.get("left")), None)
    return {**p, "sha": w["sha"]} if p else None


def unjudged_move(t):
    """Whether a push was found to have moved `t`'s code and the agent has not judged that
    yet: its verdict may reject it (`settle_moves`)."""
    return any(f.get("moved_to") and not f.get("move_seen") for f in t.get("fixes") or [])


def topic_files(state, t):
    """The files `t`'s code has been in: the comment's first, then every file a push moved
    it to that the agent did not reject, under its name now. What its diff, the judge and
    the sidebar show of the topic."""
    out = [topic_file(state, t)[0]]
    for f in fixes_of(t):
        out += [q["file"] for q in f.get("moved_to") or []]
    if t.get("fix_walk"):
        out += [p["file"] for p in walk_places(t["fix_walk"]) if p.get("via")]
    return [f for k, f in enumerate(out) if f and f not in out[:k]]


def _place_md(q):
    return f"`{q['file']}`:{q['first']}" + (f"–{q['last']}" if q["last"] != q["first"] else "")


def moves_said(t):
    """Where `t`'s code moved, one sentence per push that moved it, for whoever judges the
    answer. A move is found by matching lines (`code_view.moved_to`), so the judge may find
    it is not the commented code after all."""
    fixes = fixes_of(t)
    return ["The commented code moved to " + ", ".join(_place_md(q) for q in f["moved_to"])
            + (f" in push {n}" if len(fixes) > 1 else "") + "."
            for n, f in enumerate(fixes, 1) if f.get("moved_to")]


def fixes_of(t):
    """The author's fixes to `t` since its comment, oldest first: one per push that changed
    its lines (in the comment's file, or wherever a push moved them), or that file where the
    agent did not find that change to be about something else (`walk_fixes`,
    `settle_near_fixes`), and not one found only where a move the agent rejected led
    (`settle_moves`). A topic from before they were recorded has its
    one noted push, if any (`t["pushed"]`)."""
    if t.get("fixes"):
        return [f for f in t["fixes"] if not f.get("off")]
    p = t.get("pushed") or {}
    return [{"before": p["from"], "after": p["to"], "at": p.get("at"), "mixed": False, "url": None,
             "legacy": True}] if p.get("from") and p.get("to") else []


def settle_near_fixes(t, says, case=None, unrelated=False):
    """Once the agent judged the answer on `t`: each push noted for changing the comment's
    file (or one its code moved to) but not its lines is its fix where the verdict found the
    author did something about
    the comment, and not its push where it did not (the author only agreed, nothing is
    fixed, or the agent said the change is about something else). Until then such a push is
    listed as a change to the file, which is all that is known of it."""
    its = not (unrelated or says == "wait" or case == "not-fixed")
    for f in t.get("fixes") or []:
        if f.get("near") and not f.get("off"):
            if its:
                f.pop("near")
            else:
                f["off"] = True


def fix_key(t, n):
    """Where the topic's `n`th fix (1-based) is kept in git, under the MR's prefix."""
    return f"{t['id']}/push{n}"


def keep_author_fixes(state, wt, iid):
    """Each of the author's fixes to a topic kept in git (`lib/fix_refs.py`), one per push:
    the head before it and the head it made, which a force push would otherwise leave to
    `git gc`. Once the MR is merged or closed they all go. Where git does not have both
    commits yet (the worktree not fetched since), nothing is kept, and the next fetch tries
    again."""
    if not (wt and os.path.isdir(wt)):
        return
    if (state.get("mr_status") or {}).get("state") in ("merged", "closed"):
        fix_refs.drop(wt, SKILL, iid)
        return
    have = fix_refs.kept(wt, SKILL, iid)
    for t in state["topics"]:
        for n, f in enumerate(fixes_of(t), 1):
            key = t["id"] if f.get("legacy") else fix_key(t, n)
            k = have.get(key)
            start, end = fix_ends(f)
            # A push that came with a rebase is kept from the head before it rebased onto
            # its base, a commit of its own: the same push, so not kept again. One kept whole
            # before its commits were told apart is kept again, as its commits.
            if not k or k[1] != end or (k[0] != start and not f.get("base")):
                fix_refs.keep(wt, SKILL, iid, key, start, end, onto=f.get("base"))


def fix_ends(f):
    """The two commits a fix's diff runs between: its commits within the push where it is
    only some of them (`walk_fixes`), else the push's."""
    return tuple(f["span"]) if f.get("span") else (f["before"], f["after"])


def fix_part(f):
    """(how many of its push's commits a fix is, how many the push held) where it is only
    some of them, else None: what the sidebar says was left out."""
    return [len(f["commits"]), f["of"]] if f.get("commits") and f.get("of") else None


def kept_fixes(state, t, kept=None):
    """`fixes_of(t)`, each with `ends`: its two commits as kept in git, or None where they
    are not. `kept`: the MR's kept fixes, where the caller read them for every topic."""
    fixes = fixes_of(t)
    if kept is None:
        wt = get_worktree(state.get("slug") or "", state.get("iid"))
        kept = fix_refs.kept(wt, SKILL, state.get("iid")) if fixes and wt and os.path.isdir(wt) else {}
    out = []
    for n, f in enumerate(fixes, 1):
        ends = kept.get(t["id"] if f.get("legacy") else fix_key(t, n))
        if f.get("span") and ends and tuple(ends) != fix_ends(f):
            # Kept as the push whole still (git did not have its commits when it was kept
            # again): shown whole, and said to be.
            f = {k: v for k, v in f.items() if k not in ("commits", "of", "span", "paths")}
        out.append({**f, "ends": ends})
    return out


def kept_fix(state, t, n=None):
    """The author's `n`th fix to `t` (1-based) as kept in git, else its latest; None where
    it is not kept."""
    f = picked_fix(state, t, n)
    return f["ends"] if f else None


def picked_fix(state, t, n=None):
    """The author's `n`th fix to `t` (1-based), else its latest, with its `ends`; None."""
    fixes = kept_fixes(state, t)
    if not fixes or (n is not None and not 1 <= n <= len(fixes)):
        return None
    return fixes[(n or len(fixes)) - 1]


def shared_with(state, t, f):
    """The other topics the same push is a fix of: one push that changed several topics'
    lines is listed under each, and each says the others are in it too. Not where each is
    only its own commits of the push, and none of them the other's."""
    def meets(g):
        return ((g["before"], g["after"]) == (f["before"], f["after"])
                and (not f.get("commits") or not g.get("commits")
                     or bool(set(f["commits"]) & set(g["commits"]))))
    return [x["id"] for x in state["topics"] if x is not t and any(meets(g) for g in fixes_of(x))]


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

def prepared_path(slug, iid, what, make=True):
    return state_file(STATE_ROOT, slug, iid, f"{what}.json", make)


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
    rec = load(prepared_path(slug, iid, what, make=False))
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

# A test file by where it lives or how it is named: `tests/`, `a.test.ts`, `a_test.go`, and
# the colocated `test_a.py`, `conftest.py` and `ATest.java` / `ATests.cs`.
TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec|e2e)/|[._-](test|spec)\.[^/]+$|_test\.[^/]+$"
                       r"|(^|/)(test_[^/]+|conftest\.py|[^/]+Tests?\.[^/]+)$")
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
        line = (f"📖 **Explainer:** [{os.path.basename(path)} {LINK_ICON}](file://{path})"
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


def _listed_worktree(path):
    """Whether git, from here, lists `path` as one of the repository's working trees."""
    out = subprocess.run(["git", "worktree", "list", "--porcelain"],
                         capture_output=True, text=True).stdout
    real = os.path.realpath(path)
    return any(os.path.realpath(line[len("worktree "):]) == real
               for line in out.splitlines() if line.startswith("worktree "))


def merge_findings(into, others):
    """Fold `others` into `into`: the same point, found more than once, by the user, the agent
    or both. Not on GitLab yet, the user's wording wins: their summary, kind, severity and
    draft. Already posted, what is on GitLab stays as posted, and what the rest adds or
    corrects becomes a follow-up reply on that thread, drafted with the user like any other
    (the topic comes up offering one, as a comment merged in from their own review does); a
    draft of the user's own that was not posted yet is where that reply starts.
    What describes the finding goes together: one finding's severity on another's kind
    describes neither. Only what the user stated of the two stands against that, whichever
    finding they stated it on, and against what was posted too, as it stands against a
    re-judge (`refuse_rejudge`)."""
    if not others:
        return
    group = [into, *others]
    posted = next((x for x in group if x["thread_ids"]), None)
    unposted = [x for x in group if not x["thread_ids"]]
    word_of = posted or next((x for x in group if x.get("source") == "human"), into)
    stated = {}
    for x in group:
        for f in user_owns(x):
            if x.get(f) is not None:
                stated.setdefault(f, x[f])
    if word_of is not into:
        into["summary"], into["severity"] = word_of.get("summary"), word_of.get("severity")
        into["kind"] = word_of.get("kind") or "issue"
    into.update(stated)
    into.pop("by_user", None)
    into.pop("kind_by_user", None)
    own_by_user(into, stated)
    mine = next((x for x in unposted if x.get("source") in ("human", "both") and x.get("draft")),
                None)
    draft_of = (posted if posted.get("draft") or not mine else mine) if posted else word_of
    if draft_of is not into:
        into["draft"] = draft_of.get("draft")
        if draft_of.get("draft_at"):
            into["draft_at"] = draft_of["draft_at"]
    for o in others:
        into["thread_ids"] += [x for x in o["thread_ids"] if x not in into["thread_ids"]]
    into["source"] = "both"
    if posted and unposted:
        into["follow_up"] = "offer"


def keep_to_check(t):
    """A topic's draft the user posted by hand, which they often reword: where cards are
    drawn, kept to check what they posted against (`posted <t>`), however it gets linked."""
    if ui.drawn() and t.get("draft"):
        t["post_check"] = {"draft": t["draft"], "since": t.get("draft_at")}


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
    names = [n for n in sorted(os.listdir(STATE_ROOT))
             if n.startswith(prefix) and n[len(prefix):].isdigit()]
    # Every MR's recorded path, to tell a worktree two MRs were reviewed in (one taken from
    # `git worktree list` for each) from one only the pruned MR used.
    recorded = {int(n[len(prefix):]): get_worktree(ctx["slug"], int(n[len(prefix):])) for n in names}
    recorded = {i: os.path.realpath(p) for i, p in recorded.items() if p}
    for name in names:
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
        # The author's fixes kept for it go too: they live in the repository's common git
        # folder, which removing a worktree leaves, and an MR nobody opens again after its
        # merge never drops them itself (`keep_author_fixes`).
        fix_refs.drop(wt if os.path.isdir(wt) else ".", SKILL, iid)
        if any(p == os.path.realpath(wt) for i, p in recorded.items() if i != iid):
            # Another MR's review reads its code from there: only this MR's record goes.
            os.remove(worktree_path(ctx["slug"], iid))
            recorded.pop(iid, None)
            continue
        r = subprocess.run(["git", "worktree", "remove", "--force", wt],
                           capture_output=True, text=True)
        if r.returncode != 0 and os.path.exists(wt):
            # Git refused: the main checkout, a locked worktree, or a folder that is no
            # worktree at all — none of them ours to delete. Only a linked worktree's
            # leftover (its `.git` file still there, the tree gone from git's list) goes.
            if not os.path.isfile(os.path.join(wt, ".git")) or _listed_worktree(wt):
                continue
            shutil.rmtree(wt, ignore_errors=True)
        os.remove(worktree_path(ctx["slug"], iid))
        recorded.pop(iid, None)
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
READ_ONLY = ("state", "code", "draft", "path", "begun", "proposal", "bodies", "judge-prompt", "hunk-show",
             "view", "point", "notes", "reply")


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
    pi.add_argument("file", help="the findings' JSON; `-` reads it from stdin (a quoted heredoc)")
    pi.add_argument("--seed", action="store_true",
                    help="also keep them as this MR's prepared seed, so a re-run is instant")
    pi.add_argument("--iid", type=int)

    pa = sub.add_parser("add")
    pa.add_argument("--iid", type=int)
    pa.add_argument("--kind", choices=("issue", "question", "praise"))
    pa.add_argument("--severity", choices=tuple(SEV_ICON))
    pa.add_argument("--source", choices=tuple(SRC_ICON))
    pa.add_argument("--thread", dest="thread",
                    help="discussion id of an already-posted thread — links it in the "
                         "same call (use for a comment you posted in the UI yourself)")
    pa.add_argument("--by-user", action="store_true", help=BY_USER_HELP)
    for f in ("summary", "file", "line", "draft", "note", "why"):
        pa.add_argument(f"--{f}")

    ps = sub.add_parser("set")
    ps.add_argument("topic")
    ps.add_argument("--iid", type=int)
    ps.add_argument("--kind", choices=("issue", "question", "praise"))
    ps.add_argument("--severity", choices=tuple(SEV_ICON))
    ps.add_argument("--source", choices=tuple(SRC_ICON))
    ps.add_argument("--state", choices=("acked", "dropped", "reset"))
    ps.add_argument("--by-user", action="store_true", help=BY_USER_HELP)
    for f in ("summary", "file", "line", "draft", "note", "ticket", "start-sha", "why"):
        ps.add_argument(f"--{f}")
    # `set --draft` echoes the refreshed view, so it needs the same switch as `quote`:
    # storing a reworded draft IS the iteration step, and that echo is what the user sees.
    ps.add_argument("--refine", action="store_true", help=REFINE_HELP)

    pd = sub.add_parser("drop")
    pd.add_argument("topic")
    pd.add_argument("--iid", type=int)
    pd.add_argument("--why", help="the user's reason, in a few English words, for the line its card ends on")

    pm = sub.add_parser("merge")
    pm.add_argument("into")
    pm.add_argument("others", nargs="+")
    pm.add_argument("--iid", type=int)

    sub.add_parser("state").add_argument("--iid", type=int)
    pl = sub.add_parser("leave", help="close the topic the user opened to look at: the next one "
                        "that needs them becomes current, or none")
    pl.add_argument("--ack", metavar="TOPIC", help="on a press: close this topic for the user "
                    "first (another reviewer's thread, marked read)")
    pl.add_argument("--to", metavar="TOPIC", help="on a press: make this topic current "
                    "instead (the next of the other reviewers' threads)")
    pl.add_argument("--iid", type=int)
    sub.add_parser("refresh", help="sync quietly and print the state as JSON (the mod's "
                   "update check)").add_argument("--iid", type=int)
    sub.add_parser("approve-mr", help="on a person's press only: approve the MR on GitLab, "
                   "then print the state as JSON").add_argument("--iid", type=int)
    pn = sub.add_parser("open", help="on a press: move the review worktree to the MR's newest "
                        "push and start a round, at <t> or the first topic that needs you")
    pn.add_argument("topic", nargs="?")
    pn.add_argument("--iid", type=int)
    pjb = sub.add_parser("judge-prompt", help="the mod's: the prompt that judges the news (the "
                         "answers, other reviewers' threads, the user's own comments) in one "
                         "model call, as JSON; nothing where nothing waits")
    pjb.add_argument("--iid", type=int)
    pjb.add_argument("--skip", action="append", default=[], help="an item a call failed on")
    sub.add_parser("judge-store", help="the mod's: store that call's answers, the request on "
                   "stdin; prints what was stored and what was left unjudged").add_argument(
        "--iid", type=int)
    ph = sub.add_parser("hunk-show", help="on a person's press: the author's push that changed "
                        "<t>'s lines, kept in git, in the diff viewer")
    ph.add_argument("topic")
    ph.add_argument("--pushed", action="store_true", help="the only kind there is here; "
                    "accepted as rework-mr's `hunk-show` takes it")
    ph.add_argument("--push", type=int, help="which of the author's fixes (1-based); the latest "
                    "where none is given")
    ph.add_argument("--iid", type=int)
    pv = sub.add_parser("view", help="any commits of the review's worktree in your viewer "
                        "window, for <t>: `view <t> <from>..<to> --iid <n> [-- <file>...]`")
    pv.add_argument("topic")
    pv.add_argument("range", help="<from>..<to>: two commits or refs the worktree has")
    pv.add_argument("files", nargs="*", help="after `--`: only these files")
    pv.add_argument("--iid", type=int)
    pt = sub.add_parser("point", help="on the user's request only: your note on a line of "
                        "<t>'s diff in your viewer window, brought into view")
    pt.add_argument("topic")
    pt.add_argument("file", help="the file, as the diff names it")
    pt.add_argument("line", type=int, help="its line number, on the new side unless --old")
    pt.add_argument("text", help="what to say there")
    pt.add_argument("--old", action="store_true", help="a removed line, by its old number")
    pt.add_argument("--iid", type=int)
    sub.add_parser("notes", help="the user said done: their notes in your viewer window not "
                   "yet answered, numbered for `reply`, and those from diffs it has moved on "
                   "from, to answer in the chat").add_argument("--iid", type=int)
    pry = sub.add_parser("reply", help="answer the user's note <number>, as `notes` numbers "
                         "it, under it in the viewer window")
    pry.add_argument("number")
    pry.add_argument("text")
    pry.add_argument("--iid", type=int)
    pc = sub.add_parser("code", help="a topic's file, or one of your threads', as JSON for "
                        "the mod's sidebar")
    pc.add_argument("topic", nargs="?")
    pc.add_argument("--thread")
    pc.add_argument("--since-comment", action="store_true",
                    help="the change since the comment was posted, not the MR's whole change")
    pc.add_argument("--file", help="one of the author's fix's files to show (the sidebar's "
                    "pick), not the comment's")
    pc.add_argument("--push", type=int, help="which of the author's fixes (1-based) to list; "
                    "the latest where none is given")
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
    pv.add_argument("--unrelated-push", action="store_true",
                    help="the push noted for changing the comment's file has nothing to do "
                    "with the comment: it is no longer listed as the topic's")
    pv.add_argument("--wrong-move", action="store_true",
                    help="the code `diff <t>` says the commented code moved to is not that "
                    "code: the topic stays on the comment's file")
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
    pp.add_argument("--resolve", action="store_true",
                    help="a reply that settles the point: resolve its thread and close the topic")

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
    iid_option(ap, sub)

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
    if cmd == "leave":                    # offline: the sidebar's close, and a card's Next
        acked = topic_for(state, args.ack) or die(f"no topic {args.ack}") if args.ack else None
        to = topic_for(state, args.to) or die(f"no topic {args.to}") if args.to else None
        if acked:
            ack_topic(state, acked)
        if to:
            focus_on(state, to["id"])
        else:
            CURRENT.leave(state)
        save(path, state)
        if acked:
            print_acked(state, iid, acked)
        print(json.dumps(state_json(state)))
        return
    if cmd == "hunk-show":                # on a press: the author's fix in the diff viewer
        t = topic_for(state, args.topic) or die(f"no topic {args.topic}")
        wt = get_worktree(state.get("slug") or "", state.get("iid"))
        f = picked_fix(state, t, args.push)
        ends = (f or {}).get("ends") or die(f"{tref(args.topic)} has no push of the author's "
                                            "kept in git to show")
        load = fix_refs.view_args(ends, f.get("paths"))
        text = fix_refs.patch(wt, ends, f.get("paths"))
        try:
            clear_notes_before_load(ctx, iid, load, text)
        except NotesNotCleared as e:
            die(str(e))
        shown = fix_refs.show(wt, ends, viewer_name(state, args.topic), f.get("paths"))
        if not shown:
            die(hunk.not_shown())
        mark_shown(shown[0], iid, args.topic, load, text)
        hunk.focus_window(shown[0])
        print(f"the author's fix for {tref(args.topic)} is in the diff viewer, window {shown[1]}")
        return
    if cmd == "view":
        topic_for(state, args.topic) or die(f"no topic {args.topic}")
        print(view_range(state, ctx, iid, args.topic, args.range, args.files))
        return
    if cmd == "point":
        topic_for(state, args.topic) or die(f"no topic {args.topic}")
        print(point_out(iid, args.topic, args.file, args.line, args.text,
                        "old" if args.old else "new"))
        return
    if cmd == "notes":
        viewer_notes(ctx, iid)
        return
    if cmd == "reply":
        print(reply_note(args.number, args.text))
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
        fix = picked_fix(state, t, args.push) if not args.thread else None
        ends = (fix or {}).get("ends")
        files = [[f, a, r] for f, a, r in fix_refs.stat(wt, ends, fix.get("paths"))] if ends else []
        # Since the comment: what the author did about it, the diff from the code it was
        # posted on. Asked for by the sidebar for a topic whose news is a push, and what a
        # topic with a fix of the author's falls back to where the fix is not kept in git
        # (the worktree not fetched since): never the MR's whole change, in which a file the
        # MR adds is all new and the fix cannot be told from the rest.
        mine = bool((args.since_comment or (not args.thread and fixes_of(t) and not files))
                    and since and (x or {}).get("side") != "old")
        # Code a push moved away from the comment's place is shown where it went, carried on
        # from where the walk last saw it; the diff is still the one since the comment.
        went = moved_place(t) if not args.thread else None
        if went:
            v = code_view.view(wt, went["file"], went["last"], went["sha"], start=went["first"],
                               base=since if mine else state.get("diff_base"))
        else:
            v = code_view.view(wt, file, line, since, start=(x or {}).get("line_start"),
                               side=(x or {}).get("side") or "new",
                               base=since if mine else state.get("diff_base"))
        if files:
            # The author's fix, kept in git: every file of it is shown the same way, as the
            # fix changes it, old and new (the commit before it to the one it made), the
            # comment's own file too. The verdict on the card is about this change, so it
            # is what the sidebar opens on: GitLab's "compare with previous version".
            picked = code_view.fix_view(wt, x or {}, files, args.file, *ends,
                                        moved=fix.get("moved_to") or ())
            if picked:
                v = {**picked, "mode": "pushed"}
            elif v:
                # The fix's files with their sizes, under the file: the viewer's.
                v.update(fix_files=files, commented=code_view.commented(wt, x or {}))
        if v and files and fix_part(fix):
            # Some of the push's commits only: the sidebar says how many were left out.
            v["fix_commits"] = fix_part(fix)
        if v:
            v.setdefault("mode", "since" if mine else "mr")
            push = pushed_of(state, t) if not args.thread else None
            v["since_at"] = (push or {}).get("at")
        if v and v["line"] and v["side"] == "new" and not args.thread:
            # Where the code the topic names begins, as its card's snippet reaches back to.
            at = _anchor_view(code_view.new_lines(v), v["line"], _names(t.get("summary")))[0]
            v["from"] = code_view.row_of(v["rows"], at)
        print(json.dumps(v))
        return
    me = remember_user(state, current_user())
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
        # The session's tab says it reviews this MR, until the session ends.
        tab.take_mr(tab.REVIEW_ICON, iid, state.get("title"))
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
        tab.take_mr(tab.REVIEW_ICON, iid, state.get("title"))
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
            c = state.get("explainer_choice") if cmd == "explainer" else None
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
        # On stdin, through a quoted heredoc in a shell call, the findings stay out of the
        # chat until the user's own review is in: the review-ui mod hides the scripts' quiet
        # calls whole, where a file written for them was shown by the tool that wrote it.
        if args.file != "-" and not os.path.isfile(args.file or ""):
            # `import "$(findings.py seed ...)"` with no seed recorded passes "".
            die(f"no findings file to import at {args.file!r} (did `seed` print nothing?)")
        text = sys.stdin.read() if args.file == "-" else open(args.file).read()
        items = json.loads(text)
        if args.seed:
            seed = state_file(STATE_ROOT, ctx["slug"], iid, "seed-findings.json")
            with open(seed, "w") as f:
                f.write(text)
            set_prepared(ctx["slug"], iid, "seed", seed, mr_head(ctx, iid))
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
        if args.by_user and args.kind is None and args.severity is None:
            die("--by-user goes with --kind or --severity")
        t = add_topic(state, kind=args.kind, severity=args.severity,
                      source=args.source, summary=args.summary, file=args.file,
                      line=args.line, draft=args.draft, note=args.note, why=args.why,
                      by_user=[f for f in ("kind", "severity") if getattr(args, f) is not None]
                      if args.by_user else None)
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
        if args.by_user and args.kind is None and args.severity is None:
            die("--by-user goes with --kind or --severity")
        refused = refuse_rejudge(t, args.kind, args.severity, args.by_user)
        if refused:
            die(refused)
        if args.by_user:
            own_by_user(t, [f for f in ("kind", "severity") if getattr(args, f) is not None])
        if args.state == "reset":
            t["state"] = None
            t.pop("acked_at", None)
            t.pop("drop", None)
        elif args.state == "acked":
            ack_topic(state, t)
        elif args.state == "dropped":
            drop_topic(t)
            t["acked_at"] = None
        # Counted from the anchor the draft is stored with: a `--line` or `--file` in the
        # same call moves it.
        moved = {k: getattr(args, k) for k in ("file", "line") if getattr(args, k) is not None}
        refused = (suggestion_repeat_refusal(state, {**t, **moved}, args.draft)
                   if args.draft is not None else None)
        if refused:
            die(refused)
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
        if args.state == "acked":
            print_acked(state, iid, t)
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
        drop_topic(t, args.why)
        save(path, state)
        # The line its card now ends on, which the review-ui mod adds to the card the Drop
        # press was on as its outcome.
        print(f"closed: {press_close(state, t)}")
    elif cmd == "merge":
        into = topic_for(state, args.into) or die(f"no topic {args.into}")
        # Mirrors threads.py's `merge`: all looked up first, and never the topic merged into.
        others = [topic_for(state, oid) or die(f"no topic {oid}") for oid in args.others]
        others = [o for o in others if o is not into]
        merge_findings(into, others)
        state["topics"] = [t for t in state["topics"] if not any(t is o for o in others)]
        # What still points at a merged-away topic points at the one it went into: a proposal
        # left on it showed no match and could not be settled ("no topic tN"). A peer view
        # that would now advise merging a topic into itself advises nothing.
        gone = {o["id"] for o in others}
        for p in state.get("proposals") or []:
            if p.get("into") in gone:
                p["into"] = into["id"]
        for t in state["topics"]:
            v = t.get("peer_view")
            if v and v.get("into") in gone:
                v["into"] = None if t is into else into["id"]
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
        propose_match(state, args.thread, args.into, args.summary, args.why, args.recommend)
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
            keep_to_check(into)
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
        # The update check's: what GitLab says now, read back as the mod's state. Not while
        # who the user is was never known: their own comments would read as the author's
        # news. The next check tries again.
        if not me:
            print(json.dumps(state_json(state)))
            return
        sync(state, fetch_threads(ctx, iid, me, author), ctx, iid)
        base = mr_base(ctx, iid)
        if ui.drawn():
            check_pushes(state, ctx, iid, base)
            link_pushes(state, ctx, iid)
            walk_fixes(state, ctx, iid)
            prune_pushes(state)
            status = mr_status(ctx, iid, me)
            state.setdefault("mr_news", []).extend(mr_news(state.get("mr_status"), status, now_iso()))
            state["mr_status"] = status
            keep_author_fixes(state, get_worktree(state.get("slug") or ctx["slug"], iid), iid)
        state["diff_base"] = base or state.get("diff_base")
        save(path, state)
        print(json.dumps(state_json(state)))
    elif cmd == "approve-mr":
        # The Done box's Approve MR, once every topic is settled: the person's press alone.
        if not press.claimed():
            die("approving is the user's press, on Approve MR in the sidebar's box; nothing else "
                "approves. Leave it to them.")
        why = approve(ctx, iid)
        if why:
            die(f"GitLab did not take the approval: {why}")
        state["mr_status"] = mr_status(ctx, iid, me)
        save(path, state)
        print(json.dumps(state_json(state)))
    elif cmd == "open":
        # The user opens the inbox (or a topic in it): the review worktree moves to the MR's
        # newest push, so the sidebar shows what the round is about, and the round starts.
        # Here and not in the update check, so the code never changes under a turn.
        # The author's fixes since the comments first: the card about to come up says them,
        # and the worktree keeps them in git as it moves.
        if ui.drawn():
            walk_fixes(state, ctx, iid)
        move_worktree(state, ctx, iid)
        # The pushes stay News while a topic they touched is flagged new (`prune_pushes`).
        state["mr_news"] = []
        if args.topic:
            topic_for(state, args.topic) or die(f"no topic {args.topic}")
            focus_on(state, args.topic)
        else:
            start_round(state)
        save(path, state)
        print(json.dumps(state_json(state)))
    elif cmd == "judge-prompt":
        print(json.dumps(judge_prompt(state, ctx, iid, args.skip) or {}))
    elif cmd == "judge-store":
        done = judge.store_request(sys.stdin.read(), lambda i, m: judge_fresh(state, i, m),
                                   lambda i, a: judge_store(state, ctx, iid, i, a))
        save(path, state)
        print(json.dumps(done))
    elif cmd == "verdict":
        t = topic_for(state, args.topic) or die(f"no topic {args.topic}")
        if not (t["thread_ids"] and topic_status(state, t) == "needs_ack"):
            die(f"{tref(args.topic)} has no answer waiting for you to judge")
        if args.case and args.says != "more":
            die("--case names what is wrong with an answer, so it goes with `more` only")
        before = verdict_of(state, t)
        store_verdict(state, t, args.says, args.text, did=args.did, asked=args.asked,
                      case=args.case, unrelated=args.unrelated_push, wrong_move=args.wrong_move)
        if before and ui.drawn() and args.says != "wait":
            # A verdict the chat changed (Analyse more): the topic's whole card with it, since
            # the card up already is folded away once this one comes.
            save(path, state)
            emit(state, render_quote(state, t["id"]), t["id"])
        else:
            save(path, state)
            left = needs_verdict(state)
            print(f"{tref(args.topic)}: {args.says}" + (f"; still to judge: {', '.join(left)}"
                                                         if left else ""))
    elif cmd == "peer":
        t = topic_for(state, args.topic) or die(f"no topic {args.topic}")
        peer_waiting(state, t) or die(f"{tref(args.topic)} is no other reviewer's thread that "
                                      "waits on the user")
        advice = "seen" if args.says == "fyi" else (args.advice or "reply")
        if advice == "merge" and not topic_for(state, args.into or ""):
            die("--advice merge needs --into <topic>, the topic it repeats")
        store_peer_view(state, t, args.says, args.why, args.summary, advice, args.into)
        save(path, state)
        print(f"{tref(args.topic)}: {args.says}")
    elif cmd == "resolve":
        if not press.claimed():
            die("resolving is the user's press, on the topic's card (Resolve, or Post + resolve "
                "under a drafted reply); nothing else resolves. Show the card and name the "
                "button to press.")
        t = topic_for(state, args.topic) or die(f"no topic {args.topic}")
        disc = f"projects/{ctx['enc']}/merge_requests/{iid}/discussions"
        for x in t["thread_ids"]:
            th = state["threads"].get(x) or {}
            if not th.get("resolved") and not th.get("gone"):
                api_post(f"{disc}/{x}?resolved=true", {}, method="PUT")
                th["resolved"] = True
        t["state"] = "acked"
        t["acked_at"] = now_iso()
        t["closed"] = {"how": "accepted", "at": t["acked_at"]}
        save(path, state)
        print(f"resolved and acked {tref(args.topic)}")
        closing = press_close(state, t)
        if closing:
            print(f"closed: {closing}")
        tidied = tidy_viewer(iid, args.topic)
        if tidied:
            print(tidied)
    elif cmd == "post":
        failed = []
        url = post_draft(state, ctx, iid, args.topic, args.expect, args.resolve, failed)
        # Synced at once, so the topic reads as posted (and the current topic moves on)
        # for whatever asks next, rather than at the next sync.
        after_posting(failed, "reading the threads back",
                      lambda: sync(state, fetch_threads(ctx, iid, me, author), ctx, iid))
        save(path, state)
        print(f"posted {tref(args.topic)}: {url}")
        if args.resolve:
            closing = press_close(state, topic_for(state, args.topic))
            if closing:
                print(f"closed: {closing}")
            tidied = tidy_viewer(iid, args.topic)
            if tidied:
                print(tidied)
        if failed:
            posted_but(f"posted {tref(args.topic)}: {url}", failed)
    elif cmd == "posted":
        t = topic_for(state, args.topic) or die(f"no topic {args.topic}")
        if args.apply:
            # Mirrors threads.py's `posted --apply`: the user's press, never the agent's move.
            if not press.claimed():
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
            # Drawn, the topic's whole card, the correction under it.
            emit(state, render_quote(state, t["id"]) if ui.drawn()
                 else f"{title_md(state, t, t.get('summary') or '')}\n\n"
                 + posted.card_md(t, "comment", posted_code(state, t)), args.topic)
        elif args.ok:
            t.pop("post_differs", None)
            if (t.pop("post_check", None) or {}).get("follow_up"):
                t["draft"] = None            # the follow-up is on GitLab: no draft is left
                t.pop("draft_on", None)
            nxt = CURRENT.get(state)
            if nxt and not press.claimed():
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
            print("\nPosted:\n\n" + ((shown.get("source") or shown.get("body")) if shown else "(not on GitLab yet: nothing of the "
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
        keep_to_check(t)
        attach_thread(state, t, did, ctx, iid, args.start_sha)
        save(path, state)
        print(f"{tref(args.topic)} ← {did}")


if __name__ == "__main__":
    main()
