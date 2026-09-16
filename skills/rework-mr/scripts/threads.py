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
  present     overview table + the first open topic's comment  (the opener; no fetch)
  bodies      print each open thread's opening note (to summarize from; no fetch)
  plans       print recorded decisions/plans for open topics  (resume; no fetch)
  quote <t>   a topic in full: the code the comment is anchored to (the reviewer's own
              line range when they marked one), then the whole thread — original + every
              reply  (no fetch)
  url <t>     direct URL(s) to the topic's thread (to click & post)  (no fetch)
  reply-view <t>  code + thread + your drafted reply + URL, one paste  (no fetch)
              `--refine` drops the code and thread — for re-showing a reworded draft
              on a topic whose context is already on screen, and unchanged since
  reply <t>   the drafted reply BODY only — the payload for clip.sh / glab
  set <t> --reply -   store a reply body from stdin (quoted heredoc; never a
              scratch file — see reply_body())
  set <t> …   update a topic's fields (summary, decision, plan, start-sha, diff-url, reply)
  merge <into> <o…>   fold other topics' threads into <into>
  path        print the state-file path
  change-view <t> [file]  render a change illustration — reads the change from stdin
              (the documented path: no file, so no protected-path prompt) or from FILE
  diff-view <t>   render a working diff read from stdin (diff-view.sh's body)
  hunk-notes  print the notes the USER left on the diff, and take them out of the viewer
  hunk-close  close the viewer window once the push has landed
  check-handles   internal — used by guard-reply.sh, no MR context needed
"""
import argparse
import glob
import hashlib
import os
import re
import sys

# Repo root, 4 levels up from skills/rework-mr/scripts/threads.py — needed so `lib/`,
# which lives outside this skill's own directory, is importable regardless of how this
# script is invoked (direct, or symlinked into ~/.claude/skills/).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.realpath(__file__)))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from lib import critical_manifest, hunk                         # noqa: E402
from lib.gitlab import (api, context, current_user, die, mr_view,  # noqa: E402
                        project_slug, run, web_base)
from lib.mr_common import (DEFAULT_LANG, MR_LEVEL, TOPIC_ICON, first_name,  # noqa: E402
                           load, loc_md, num, reads_as, save, short_summary,
                           state_file, topic_for, tref)
from lib.snippet import MAX_BACKTRACK, open_construct            # noqa: E402

# internal topic handles (t5, t6, t10 …) — must never reach a GitLab comment.
# Single source of truth: guard-reply.sh shells out to `check-handles` below
# instead of reimplementing this regex in bash, so the two can't drift apart.
HANDLE_RE = re.compile(r"\bt[0-9]+\b")


def find_handles(text):
    return sorted(set(HANDLE_RE.findall(text or "")))


STATE_ROOT = os.path.expanduser("~/.claude/rework-mr")
# Single source for the three per-status lookups below — this diff previously
# needed to touch three separate dicts (plus the `keep` sets in `_rows`) just to
# add one status; keying off one table keeps that to one place.
STATUSES = {
    "reply_pending": (0, "✎", "reply-pending"),
    "open":          (1, "○", "open"),
    "waiting":       (2, "◐", "waiting"),
    "done":          (3, "●", "done"),
}
STATUS_ORDER = {k: v[0] for k, v in STATUSES.items()}
GLYPH = {k: v[1] for k, v in STATUSES.items()}
WORD = {k: v[2] for k, v in STATUSES.items()}


def new_state(ctx, mr):
    return {
        "project": ctx["path"], "slug": ctx["slug"], "iid": mr["iid"],
        "mr_web_url": mr.get("web_url"), "title": mr.get("title"),
        "threads": {}, "topics": [],
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


def fetch_threads(ctx, iid, me):
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
            or pos.get("new_path") or pos.get("old_path"),
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
            "notes": [{"author": (n.get("author") or {}).get("name")
                       or (n.get("author") or {}).get("username"),
                       "body": n.get("body")} for n in notes],
        }
    return out


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
    return t.get("state") or "open"          # you spoke last, no push → LLM decides


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
            local = {k: v for k, v in state["threads"][tid].items()
                     if k in LOCAL_THREAD_FIELDS}
            state["threads"][tid] = {**rec, **local}
        else:
            state["threads"][tid] = rec
            state["topics"].append({
                "id": next_tid(state), "thread_ids": [tid], "summary": None,
                "state": None, "decision": None, "plan": None,
                "start_sha": None, "diff_url": None,
            })
    for tid in state["threads"]:
        if tid not in live:
            state["threads"][tid]["resolved"] = True
    # a reviewer note since your last one (or a reopen) makes any stored
    # "waiting" stale — clear it so the thread re-derives to open.
    for t in state["topics"]:
        thr = [state["threads"].get(x) for x in t["thread_ids"]]
        unresolved = [x for x in thr if x and not x["resolved"]]
        if any(x.get("awaiting") == "you" for x in unresolved):
            t["state"] = None


def _rows(state, scope, show_done=False):
    """Mirrors review-mr's findings.py's `_rows` — same shape (status/counts/ordering for
    render_table), but the `keep` sets below are this skill's own status vocabulary
    (open/reply_pending/...), not findings.py's (draft/needs_ack/...). Stays duplicated:
    see CLAUDE.md's "Sharing vs. duplication"."""
    topics = state["topics"]
    st = {t["id"]: topic_status(state, t) for t in topics}
    counts = {s: sum(1 for t in topics if st[t["id"]] == s) for s in GLYPH}
    ordered = sorted(topics, key=lambda t: (STATUS_ORDER[st[t["id"]]], num(t["id"])))
    keep = {"open", "reply_pending"} if scope == "mine" else \
           ({"open", "reply_pending", "waiting"} | ({"done"} if show_done else set()))
    return st, counts, [t for t in ordered if st[t["id"]] in keep]


def _loc(state, t):
    """Mirrors review-mr's findings.py's `_loc` — same `basename:line`, and the same
    "" when no thread of the topic has a diff position (a discussion on the merge
    request itself has none). Stays duplicated: this table also carries the count of
    the topic's further threads, which review-mr's does not."""
    thr = [state["threads"].get(x, {}) for x in t["thread_ids"]]
    return next((f"{os.path.basename(x['file'])}:{x.get('line') or ''}"
                 for x in thr if x.get("file")), "")


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
    return (f"⚠️ **needs an English summary** — `{t['id']}` has no authored `summary` "
            f"yet. Read the thread and run `set {t['id']} --summary \"<short English "
            f"line>\"` before working this topic.")


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
    ids = [t["id"] for t in topics if t.get("summary")
           and reads_as(t["summary"], DEFAULT_LANG)]
    if not ids:
        return None
    return (f"⚠️ **summaries must be English** — {', '.join(tref(x) for x in ids)} "
            f"read as {DEFAULT_LANG}. The thread's own language governs one thing, the "
            f"body of a reply you post into it; the table and every topic heading are "
            f"English. Reword each with `set <t> --summary \"<short English line>\"`, "
            f"then re-render.")


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
                summ = f"✍️ _needs summary:_ {summ}"   # raw quote, not an authored title
            out.append(critical_manifest.mark(f"| {GLYPH[s]} {WORD[s]} | {tref(f'**{tid}**')} "
                             f"| {_loc_cell(state, t)} | {summ} |"))
    else:
        out.append("_✓ nothing needs you — open threads are waiting on the reviewer_"
                   if scope == "mine" else "_✓ no open threads_")
    warn = summary_language_warning(state, shown)
    if warn:
        out += ["", warn]
    footer = [f"{counts['done']} of {len(state['topics'])} topics done"]
    if counts["reply_pending"]:
        footer.append(f"{counts['reply_pending']} pushed, reply pending")
    if counts["waiting"]:
        footer.append(f"{counts['waiting']} waiting for reply")
    return "\n".join(out + ["", "_" + " · ".join(footer) + "._"])


SUGGESTION_INFO = re.compile(r"^suggestion(?::-(\d+)\+(\d+))?$")
INDENT_CODE = re.compile(r"^(?: {4,}|\t+)\S")   # markdown counts a tab as 4 spaces
DEDENT = re.compile(r"^(?: {4}|\t)")
LIST_ITEM = re.compile(r"^\s*([-*+]|\d+[.)])\s")


def _suggestion_caption(info, anchor):
    """Label for a GitLab ```suggestion block, which loses its marker when re-fenced.

    `suggestion:-A+B` means "replace the A lines above the anchor through the B below", so
    the caption can name the lines the reviewer wants replaced — the one thing the raw
    `:-0+0` never told anybody.
    """
    m = SUGGESTION_INFO.match(info or "")
    if not m:
        return None
    if anchor is None:
        return "_suggested replacement:_"
    try:
        a = max(1, int(anchor) - int(m.group(1) or 0))
        b = max(a, int(anchor) + int(m.group(2) or 0))
    except (TypeError, ValueError):
        return "_suggested replacement:_"
    return f"_suggested replacement for {'line' if a == b else 'lines'} " \
           f"{a if a == b else f'{a}–{b}'}:_"


def _indented_runs(seg):
    """Split a prose run into [(kind, lines)] where kind is "text" or "code".

    A 4-space indented block is markdown's other code block, and reviewers use it as often
    as a fence. A blank line stays inside the block only when code surrounds it, and a run
    hanging under a list item is left as prose — that indentation is list continuation, not
    code.
    """
    def before(i):
        """Index of the nearest non-blank line above `i`, or None."""
        return next((j for j in range(i - 1, -1, -1) if seg[j].strip()), None)

    code = [bool(INDENT_CODE.match(ln)) for ln in seg]
    for i, ln in enumerate(seg):                  # blanks: only inside a block
        if ln.strip():
            continue
        nxt = next((j for j in range(i + 1, len(seg)) if seg[j].strip()), None)
        prev = before(i)
        code[i] = (prev is not None and nxt is not None and code[prev] and code[nxt])
    i = 0
    while i < len(seg):                           # list continuation is not code
        if not code[i]:
            i += 1
            continue
        start = i
        while i < len(seg) and code[i]:
            i += 1
        prev = before(start)
        if prev is not None and LIST_ITEM.match(seg[prev]):
            for j in range(start, i):
                code[j] = False

    runs, buf, kind = [], [], None
    for ln, is_code in zip(seg, code):
        k = "code" if is_code else "text"
        if buf and k != kind:
            runs.append((kind, buf))
            buf = []
        kind = k
        buf.append(ln)
    if buf:
        runs.append((kind, buf))
    return [(k, [DEDENT.sub("", ln) if k == "code" else ln for ln in v])
            for k, v in runs]


def _note_md(name, body, path=None, anchor=None):
    """One thread note: prose blockquoted, its code lifted out of the quote and fenced.

    Reviewers paste code — a ```suggestion block, or an indented snippet. Left
    inside the `> ` quote both render flat: `> ```suggestion:-0+0` is a fence with an info
    string no highlighter knows, and an indented block never carries a language at all. That
    is the reviewer's proposed code rendered as grey text. Lifting it to line start and
    re-fencing with the file's own language is the whole point of showing the note.
    """
    out = [f"> **{first_name(name)}**"]

    def quote(lines):
        while lines and not lines[0].strip():
            lines = lines[1:]
        while lines and not lines[-1].strip():
            lines = lines[:-1]
        if lines:
            # `>` continues the quote we are already in; a blank line separates prose from a
            # fence we just lifted out of it (a `>` there renders as an empty quoted line).
            out.append(">" if out[-1].startswith(">") else "")
            out.extend(f"> {ln}".rstrip() if ln.strip() else ">" for ln in lines)

    def code(content, info):
        if not content.strip():                   # an empty suggestion is not worth a block
            return
        cap = _suggestion_caption(info, anchor)
        if cap:
            out.extend(["", cap])
        lang = info if info and not SUGGESTION_INFO.match(info) else (
            "diff" if looks_like_diff(content) else lang_for(path))
        out.extend(["", fence(content, lang)])

    for kind, info, seg in _segments((body or "").strip().splitlines() or [""]):
        if kind == "code":
            code("\n".join(seg), info)
            continue
        for sub_kind, sub in _indented_runs(seg):
            if sub_kind == "code":
                code("\n".join(sub).strip("\n"), "")
            else:
                quote(sub)
    return "\n".join(out)


# ------------------------------------------------------- fences & code context

FENCE_BY_EXT = {".ts": "ts", ".tsx": "tsx", ".js": "js", ".jsx": "jsx", ".mjs": "js",
                ".py": "python", ".rb": "ruby", ".go": "go", ".java": "java",
                ".kt": "kotlin", ".rs": "rust", ".php": "php", ".cs": "csharp",
                ".sh": "bash", ".env": "bash", ".yml": "yaml", ".yaml": "yaml",
                ".json": "json", ".sql": "sql", ".css": "css", ".scss": "scss",
                ".html": "html", ".vue": "vue", ".svelte": "svelte", ".md": "markdown"}
FENCE_RE = re.compile(r"^(\s*)(`{3,})(.*)$")
#  git's own headers. `index` is matched with its sha shape, not as a bare word: a code
#  snippet starting a line with `index = 0` is not a diff.
DIFF_HINT = re.compile(r"^(@@ |--- |\+\+\+ |diff --git |index [0-9a-f]{4,}\.\.)")


def lang_for(path):
    """Fence language for a path, or "" — an untagged fence renders flat, and the
    highlighting is the single most useful thing in a code block."""
    return FENCE_BY_EXT.get(os.path.splitext(path or "")[1].lower(), "")


def looks_like_diff(text):
    """True when the text is a unified diff, so it can be fenced as ```diff and render
    coloured. Strict on purpose: EVERY non-blank line must carry a diff prefix (space, +,
    -, @, \\) and at least one must be a +/- change. A before/after code snippet — which
    wants the file's own language, not diff — has lines starting with letters and fails
    here, and prose does too."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return False
    if any(DIFF_HINT.match(ln) for ln in lines):
        return True
    if not all(ln[:1] in " +-@\\" for ln in lines):
        return False
    return any(ln[:1] in "+-" for ln in lines)


def fence(text, lang=""):
    """`text` in a fence long enough that nothing inside can close it early.

    CommonMark closes a fence only on a run of at least as many backticks, so a block
    containing ``` needs four. This is what kept biting `change-preview.sh`: it wrapped the
    model's illustration — which itself contained a ```diff block — in a bare ```, so the
    inner fence closed the outer one, the diff rendered as flat text, and everything after
    it leaked out of the block.

    Backtick runs are measured after LEADING WHITESPACE, not at column 0: inside a diff
    every line carries a ` `/`+`/`-` prefix, so a fence in a diffed markdown file arrives
    as "` ```"` — indented, still a valid closer, and invisible to a `^```` scan.

    Every caller in this file routes fenced content through here — the code the comment
    is on, a reviewer's own quoted suggestion, a change illustration, a working diff, a
    drafted reply's code — so marking each content line as critical HERE, once, covers
    all of them instead of needing the same call at five separate sites.
    """
    runs = []
    for ln in (text or "").splitlines():
        s = ln.lstrip()
        if s.startswith("```"):
            runs.append(len(s) - len(s.lstrip("`")))
    bar = "`" * max([3] + [r + 1 for r in runs])
    for ln in (text or "").rstrip(chr(10)).splitlines():
        critical_manifest.mark(ln)
    return f"{bar}{lang}\n{(text or '').rstrip(chr(10))}\n{bar}"


def _segments(lines):
    """Split lines into [(kind, info, body)] with kind in {"text", "code"}.

    An unterminated fence is treated as running to the end — the caller re-emits a closing
    one, which is what keeps a model's forgotten ``` from swallowing the rest of the block
    (the `Agreed?` line included).
    """
    segs, buf, i = [], [], 0
    while i < len(lines):
        m = FENCE_RE.match(lines[i])
        if not m:
            buf.append(lines[i])
            i += 1
            continue
        if buf:
            segs.append(("text", "", buf))
            buf = []
        bars, info = m.group(2), m.group(3)
        body, i = [], i + 1
        close = re.compile(r"^\s*`{%d,}\s*$" % len(bars))
        while i < len(lines) and not close.match(lines[i]):
            body.append(lines[i])
            i += 1
        i += 1                                    # skip the closing fence, if any
        segs.append(("code", info.strip(), body))
    if buf:
        segs.append(("text", "", buf))
    return segs


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
    for kind, info, body in _segments(lines):
        if kind == "text":
            out += body
        else:
            content = "\n".join(body)
            out.append(fence(content,
                             info or ("diff" if looks_like_diff(content)
                                      else lang_for(path))))
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


def render_code_context(x):
    """The code the reviewer's comment hangs on, ready to show ABOVE their note.

    Without it the user is asked to judge a comment about code they cannot see, and the
    agent's research reads as unsourced assertion — the reviewer says "ist äquivalent zu …"
    and the reply is a verdict about lines nobody displayed.

    Read from the exact blob the comment is anchored to (`git show <sha>:<path>` for the
    side the position names), NOT from the working tree: the line number belongs to that
    version, so once the author starts fixing things a working-tree read would silently
    render unrelated lines. When the working tree has since diverged, that is said out
    loud rather than hidden. Falls back to the working tree for threads stored before the
    shas were recorded. None when there is nothing trustworthy to show.

    What gets shown follows the reviewer: their selected range first (marked `┃`), else the
    single anchor line (marked `►`). The marker is never a `►` on one line of a span — it
    claimed a precision the position did not have, and pointed at a closing brace.
    """
    path, line = x.get("file"), x.get("line")
    if not path or not line:
        return None                               # not a diff comment (or no position)
    try:
        n = int(line)
    except (TypeError, ValueError):
        return None
    on_new = (x.get("side") or "new") == "new"
    sha = x.get("head_sha") if on_new else x.get("base_sha")
    text, source = _blob_text(sha, path), f"as reviewed, `{(sha or '')[:8]}`"
    working = _repo_file(path)                     # read once: each call shells out to git
    drift = None
    if text is None:
        text, source = working, "working tree"
    elif working not in (None, text):
        drift = ("_⚠️ the working tree differs from this — you may already have changed "
                 "the file; the lines above are the version the comment is on._")
    if text is None:
        return None
    lines = text.splitlines()
    if n > len(lines):
        return None                               # anchor outside the file → show nothing
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
    body = []
    for i in range(lo, hi + 1):
        if hi - lo + 1 > MAX_BODY and lo + MAX_BODY - 10 < i < hi - 8:
            if body and body[-1] is not None:     # collapse the middle of a huge span once
                body.append(None)
            continue
        body.append(i)
    width = len(str(hi))
    rendered = []
    for i in body:
        if i is None:
            rendered.append(f"  {'…'.rjust(width)} | … {b - a + 1} lines in total …")
            continue
        mark = MARK_SPAN if span and a <= i <= b else (MARK_LINE if not span and i == n
                                                       else " ")
        rendered.append(f"{mark} {str(i).rjust(width)} | {lines[i - 1]}")
    where = f"{path}:{a}–{b}" if span else f"{path}:{n}"
    out = [f"_Code the comment is on — `{where}` ({source}):_", "",
           fence("\n".join(rendered), lang)]
    if drift:
        out += ["", drift]
    return "\n".join(out)


def render_change_view(tid, text, path=None):
    """The whole `change-preview.sh` block: header, the illustration, the `Agreed?`.

    Stateless on purpose — no glab call, no state file — so showing a change can never
    fail on MR resolution. `path` (optional) only supplies the fence language for an
    unfenced snippet that isn't a diff.
    """
    return "\n".join([f"**Change ({tref(tid)}):**", "",
                      render_change(text, path), "", "Agreed?"])


# The closing line of both diff-view shapes, and the paste gate's signature for them — the
# `fixup-ack` rule additionally requires it to be the LAST line of the message, so the
# alternatives to an ACK have to ride on this one line rather than follow it.
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
    return "\n".join([f"**Diff ({tref(tid)}):**", "",
                      fence(diff, "diff")] + _notice(notice) + ["", ACK_ASK + "."])


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


# Mirrors skills/review-mr/scripts/findings.py's `_gl_compare` churn count, and stays
# separate on purpose: that one walks GitLab's already-per-file diff payloads, so it never
# has to find a file boundary and treats every `+++`/`---` as noise. This one parses one raw
# stream from `git diff`, where those same lines are sometimes a header and sometimes
# content. Same arithmetic, different problem — a change to either is worth checking against
# the other.
def diff_stat(diff):
    """[(path, added, removed)] for a unified diff, in the order it lists the files.

    Parsed from the text that was piped in, never re-derived with a second `git` call: the
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
            # this skill's own flow. Without this the stanza is not a file boundary at all:
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
            path = line[4:].strip()
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


def render_diff_pointer(tid, stat, where, notice=""):
    """The `diff-view.sh` block when the diff went to a viewer: what changed and how big,
    where to look at it, and the same ACK question.

    The per-file counts are the point of keeping anything in the message at all. They are
    what lets a small change be approved without switching windows, and they say how much
    there is to read before the user decides to — neither of which a bare "see window 9"
    can do. The fixup target and the summary of what changed are deliberately NOT here: the
    skill has the model state those in its own prose above this block, because only the
    model knows them.

    The rows are `path: +a −r`, fenced as YAML, because a pasted block's only available
    colour is whatever the reader's markdown renderer gives the fence language — the block
    travels through a model message, so terminal escapes cannot survive it. Read as YAML
    each row is a key and a value, which tints the path apart from its counts; the trailing
    colon is the whole cost of that, and it replaces a column separator that cost as much.
    """
    files, adds, dels = len(stat), sum(a for _, a, _ in stat), sum(r for _, _, r in stat)
    # Padded to the longest path, capped so one deeply nested file cannot push every count
    # off the far side of a terminal; a path past the cap simply loses its alignment.
    width = min(max((len(p) for p, _, _ in stat), default=0), 60)
    # Not marked critical here — `fence` marks every line it wraps, and marking twice puts
    # each row in the manifest twice.
    rows = [f"{p + ':':<{width + 2}} +{a} −{r}" for p, a, r in stat]
    head = (f"**Diff ({tref(tid)})** — {files} file{'' if files == 1 else 's'}, "
            f"+{adds} −{dels} · {where}")
    # Marked critical explicitly, unlike the rows, which `fence` marks for us. This is the
    # ONLY line naming where the diff is, so under the hook's one-dropped-line tolerance it
    # was the one line a message could lose and still pass — leaving a list of filenames, a
    # closing question mentioning "that window", and no window named anywhere.
    critical_manifest.mark(head)
    return "\n".join([head, "", fence("\n".join(rows), "yaml")] + _notice(notice)
                     + ["", ACK_ASK + ", here or as notes on the diff in that window."])


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


def window_label(default="diff"):
    """The name to give this agent's viewer window: the MR number where it can be had
    locally, the branch otherwise.

    Read off the state directory's own naming rather than by asking GitLab, and via
    `project_slug()` rather than `context()`, which would shell out to glab for the host's
    API scheme. `diff-view` is deliberately independent of glab and the network (see
    `main`), and a window's NAME is cosmetic enough that it must not be the thing that
    reintroduces a round-trip — or a hang, since that helper takes no timeout — per topic.

    Ranked by each state FILE's mtime, never the directory's. A directory's mtime only
    moves when an entry is added or removed, and `save()` rewrites `topics.json` in place,
    so every reworked MR keeps the directory mtime it was created with: measured on a real
    store, the MR being worked on today ranked BELOW one last touched days earlier, and the
    wrong MR number went onto the very block a force-push is approved from.
    """
    try:
        # Checked before the slug is derived rather than caught after: with no origin
        # remote `remote_url()` calls `die()`, which prints to stderr on the way out.
        # Catching the SystemExit would still leave that stray line in the terminal, on a
        # path where nothing is actually wrong.
        if (_git("remote", "get-url", "origin") or "").strip():
            dirs = glob.glob(os.path.join(STATE_ROOT, f"{project_slug()}--mr*"))
            if dirs:
                # basename first: splitting the full path would read any `--mr` that
                # happens to sit in a directory name above it.
                newest = os.path.basename(max(dirs, key=_state_mtime))
                return TOPIC_ICON + "!" + newest.rsplit("--mr", 1)[1]
    except (Exception, SystemExit):         # noqa: BLE001 — no remote/state → use branch
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
        die("`git diff` is empty — nothing to show and nothing to approve. The change is "
            "probably staged (`git diff` reads unstaged only: pass `-- --cached`), already "
            "committed, or entirely in untracked files. Never ask for the fixup ACK over an "
            "empty diff.")
    root = (_git("rev-parse", "--show-toplevel") or "").strip()
    if plain or not stat or not root:
        return render_diff_view(tid, diff)
    # Whether the notes actually landed is reported, never assumed. The batch is
    # all-or-nothing in the viewer, so one bad anchor drops all of them — and the prose
    # above this block has already told the user the agent flagged something.
    dropped = ("The notes could not be anchored on the diff — read the points in the prose "
               "above instead." if notes else "")
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
        return ("Could not read the viewer's notes, so the window is left open rather than "
                "closed over something unread. Harmless — close it yourself, or it goes on "
                "the next push.", False)
    if notes:
        return ("Notes were left in the viewer since the last read — the window is still "
                "open. Run `threads.py hunk-notes` and answer them; they are a new change "
                "on this topic, on top of the one just pushed.", False)
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
            title = f"**{tref(t['id'])}" + (f" — {summ}**" if summ else "**")
            out.append(f"{title} · {loc_md(path)}")
            for w in (needs_summary_warning(state, t),
                      summary_language_warning(state, [t])):
                if w:
                    out.append(w)
        else:
            out.append(loc_md(path))
        if x.get("url"):
            out.append(x["url"])
        # Code FIRST, comment second: the reviewer's note is about these lines, and a
        # verdict on code the user cannot see is unreviewable.
        code = render_code_context(x)
        if code:
            out += ["", code]
        # The file and anchor let a reviewer's ```suggestion be re-fenced to the file's
        # language and labelled with the lines it replaces.
        f, ln = x.get("file"), x.get("line")
        notes = x.get("notes")
        if notes:                               # whole thread, in order
            for n in notes:
                out += ["", _note_md(n.get("author"), n.get("body"), f, ln)]
        else:                                   # pre-`notes` state: first + last only
            out += ["", _note_md(x.get("author"), x.get("body"), f, ln)]
            if x.get("note_count", 1) > 1:
                skipped = x["note_count"] - 2
                if skipped > 0:
                    out += ["", f"_… {skipped} more …_"]
                out += ["", _note_md(x.get("last_author"), x.get("last_body"), f, ln)]
        out.append("")
    text = "\n".join(out).strip()
    if remember:
        t["shown"] = _digest(text)
    return text



def context_digest(state, tid):
    """A fingerprint of the topic's context as it renders RIGHT NOW.

    Hashing the rendered text, not a list of the fields that feed it: a field list rots —
    `sync`'s own allowlist did, silently, for every field added to the fetch after it —
    while whatever `render_quote` puts on screen is by definition what the user saw.

    Marking is suspended because this render is thrown away. Its code lines would
    otherwise land in the critical-lines manifest and the Stop hook would demand lines
    that appear nowhere in the message.
    """
    with critical_manifest.suspended():
        return _digest(render_quote(state, tid, remember=False))


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
        die(f"no draft for {tid} yet — store one with:\n"
            f"  python3 threads.py set {tid} --reply - <<'REPLY_EOF'\n"
            f"  <the reply body>\n  REPLY_EOF")
    hits = find_handles(body)
    if hits:
        die(f"draft has internal topic handle(s): {' '.join(hits)} — reword "
            f"(link the other thread via `url <other-t>`), then re-run")
    return body


def _quote_draft(body, path=None):
    """The draft as it should be DISPLAYED: prose blockquoted so it reads as the artefact
    being posted, fenced blocks left at line start.

    A fence prefixed with `> ` loses its syntax highlighting in most renderers, and the
    code is usually the point of the reply — the same failure the change illustration had.
    (review-mr's render_draft applies the same rule for the same reason.)
    """
    blocks = []
    for kind, info, seg in _segments((body or "").rstrip("\n").splitlines()):
        if kind == "code":
            content = "\n".join(seg)
            blocks.append(fence(content, info or ("diff" if looks_like_diff(content)
                                                  else lang_for(path))))
            continue
        # Blank lines at a text segment's edges would render as stray `>` markers hugging
        # the fence; the blank line between blocks below does that job properly.
        while seg and not seg[0].strip():
            seg = seg[1:]
        while seg and not seg[-1].strip():
            seg = seg[:-1]
        if seg:
            blocks.append("\n".join(f"> {ln}".rstrip() if ln.strip() else ">"
                                    for ln in seg))
    return "\n\n".join(blocks)


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
    path = next((state["threads"].get(th, {}).get("file") for th in t["thread_ids"]
                 if state["threads"].get(th, {}).get("file")), None)
    out = []
    if refine and t.get("shown") != context_digest(state, tid):
        out.append("_(the code or the thread changed since this was last shown — in full "
                   "again)_" if t.get("shown") else
                   "_(this topic's context has not been shown yet — in full)_")
        refine = False
    if not refine:
        out += [render_quote(state, tid), ""]
    out += [f"**Draft reply — {tref(tid)}:**" if refine else "**Draft reply:**", "",
            _quote_draft(body, path),
            "", f"Thread (to post on): {render_url(state, tid)}",
            "", "**`c`** copy to clipboard · **`p`** post on GitLab · "
            "**`n`** next topic (already replied/resolved) · "
            "or just type your thoughts to refine it."]
    return "\n".join(out)


def first_open(state):
    opens = [t for t in state["topics"] if topic_status(state, t) == "open"]
    return min(opens, key=lambda t: num(t["id"])) if opens else None


def render_present(state):
    """The opener the user should see: overview table + the first open topic's
    reviewer comment, ready to reproduce in one reply.

    Mirrors review-mr's findings.py's `render_present` almost exactly — the only real
    difference is `first_open` vs. its `first_todo`, each picking "the topic that needs
    you" per this skill's own status vocabulary. Stays duplicated: see CLAUDE.md's
    "Sharing vs. duplication"."""
    parts = [render_table(state, "all")]
    t = first_open(state)
    if t:
        parts += ["\n---\n", render_quote(state, t["id"])]
    return "\n".join(parts)


def render_plans(state):
    """Recorded decisions/plans for the open topics — for resuming after a break
    or a fresh session, so you don't re-grill what's already decided."""
    out = []
    for t in sorted(state["topics"], key=lambda t: num(t["id"])):
        s = topic_status(state, t)
        if s == "done" or not (t.get("decision") or t.get("plan")):
            continue
        stamp = ("  [✎ ALREADY PUSHED — reply pending; do NOT re-implement, "
                 "go to the reply step]" if s == "reply_pending" else "")
        out.append(f"{tref(t['id'])} — {t.get('summary') or ''}{stamp}")
        if t.get("decision"):
            out.append(f"  decision: {t['decision']}")
        if t.get("plan"):
            out.append(f"  plan: {t['plan']}")
        if t.get("diff_url"):
            out.append(f"  pushed: {t['diff_url']}")
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
            loc = (f"{os.path.basename(x['file'])}:{x.get('line') or ''}"
                   if x.get("file") else MR_LEVEL)
            out.append(f"[{tref(t['id'])}] {loc}"
                       f"  (last spoke: {first_name(x.get('last_author'))})"
                       f"  {x.get('url')}")
            out.append(f"  {first_name(x.get('author'))}: {(x.get('body') or '').strip()}")
            if x.get("note_count", 1) > 1:
                out.append(f"  {first_name(x.get('last_author'))} (last of {x['note_count']}): "
                           f"{(x.get('last_body') or '').strip()}")
        out.append("")
    return "\n".join(out).strip() or "(no open threads)"


def resolve_state(args):
    ctx = context()
    mr = None
    iid = args.iid
    if iid is None:
        mr = mr_view()
        iid = mr["iid"]
    path = state_file(STATE_ROOT, ctx["slug"], iid, "topics.json")
    state = load(path)
    if state is None:
        mr = mr or mr_view()
        state = new_state(ctx, mr)
    elif mr:
        state.update(mr_web_url=mr.get("web_url"), title=mr.get("title"))
    # Single override point: the MR's own web_url beats anything reconstructed from
    # the remote (scheme, port, install path). Doing it here means every consumer of
    # ctx["web"] — thread URLs, diff URLs — inherits it for free.
    ctx["web"] = web_base(state.get("mr_web_url")) or ctx["web"]
    return ctx, iid, path, state


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    for name in ("sync", "todo", "present", "bodies", "plans", "path"):
        sub.add_parser(name).add_argument("--iid", type=int)
    sub.add_parser("check-handles", help="print any internal topic handles "
                    "found in stdin (used by guard-reply.sh; no MR context needed)")
    # The two view renderers are stateless (no glab, no state file) — see their
    # docstrings. change-preview.sh / diff-view.sh are their entry points; the rendering
    # lives here so the fence handling has one implementation instead of one per script.
    pc = sub.add_parser("change-view", help="render a change illustration (used by "
                        "change-preview.sh; reads the change from FILE or stdin)")
    pc.add_argument("topic")
    pc.add_argument("file", nargs="?", help="the change; omit to read stdin")
    pc.add_argument("--for", dest="for_path",
                    help="path the change applies to — supplies the fence language "
                         "for a snippet that isn't a diff")
    pdv = sub.add_parser("diff-view", help="render a working diff (used by "
                         "diff-view.sh; reads the diff from stdin)")
    pdv.add_argument("topic")
    pdv.add_argument("--plain", action="store_true",
                     help="always render the diff inline, never in a viewer window "
                          "(diff-view.sh passes this when it narrowed the diff itself)")
    pdv.add_argument("--note", action="append", default=[], metavar="FILE:LINE:TEXT",
                     help=f"anchor one short note in the viewer, at most "
                          f"{hunk.MAX_NOTES} per topic; only where the change deviates "
                          f"from the agreed plan, does more than was asked, or is not "
                          f"obvious from the diff (repeatable)")
    sub.add_parser("hunk-notes", help="print the notes the USER left in the viewer, and "
                   "take them out of it (nothing if there are none — read this before "
                   "acting on an ACK, and answer everything it prints)")
    sub.add_parser("hunk-close", help="close this topic's diff viewer window once the "
                   "push has landed; refuses while unread notes are in it")
    sub.choices["sync"].add_argument("--all", action="store_true",
                                     help="include resolved topics")
    pq = sub.add_parser("quote")
    pq.add_argument("topic")
    pq.add_argument("--iid", type=int)
    pu = sub.add_parser("url")
    pu.add_argument("topic")
    pu.add_argument("--iid", type=int)
    prv = sub.add_parser("reply-view")
    prv.add_argument("topic")
    prv.add_argument("--iid", type=int)
    prv.add_argument("--refine", action="store_true",
                     help="re-showing a reworded draft on the SAME topic: omit the "
                          "topic's context — its header, the code and the thread, all "
                          "on screen already and unchanged — and print only the draft, "
                          "its thread URL and the prompt. Safe whenever you are "
                          "re-showing: a context that moved since, or was never shown, "
                          "comes back in full anyway.")
    pr = sub.add_parser("reply", help="the drafted reply BODY only — the paste/post payload")
    pr.add_argument("topic")
    pr.add_argument("--iid", type=int)
    ps = sub.add_parser("set")
    ps.add_argument("topic")
    ps.add_argument("--iid", type=int)
    ps.add_argument("--state", choices=("open", "waiting"))
    for f in ("summary", "decision", "plan", "start-sha", "diff-url"):
        ps.add_argument(f"--{f}")
    ps.add_argument("--reply", help="the drafted reply body; `-` reads stdin (use a quoted "
                                    "heredoc for anything multi-line)")
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
        print(render_change_view(args.topic, text, args.for_path) + critical_manifest.manifest())
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
            die(f"{len(notes)} notes for one topic, at most {hunk.MAX_NOTES} are allowed. "
                "A note belongs only where the change deviates from the agreed plan, does "
                "more than was asked, or is not obvious from the diff; everything else is "
                "already visible in the diff itself.")
        if notes and args.plain:
            die("--note cannot be used with git arguments: narrowing the diff forces the "
                "inline shape, and an inline diff has no lines to anchor a note to. Put "
                "the point in your prose instead, or show the whole diff.")
        print(diff_view_block(args.topic, sys.stdin.read(), args.plain, notes)
              + critical_manifest.manifest())
        return
    if cmd == "hunk-notes":
        # Stateless like the views above, and deliberately silent when there is nothing:
        # "no output" is the common answer and the one that means "go ahead and push".
        # Which is exactly why every OTHER outcome has to say something — see below.
        root = (_git("rev-parse", "--show-toplevel") or "").strip()
        sid = (root and hunk.installed()
               and hunk.find_session(root, hunk.owner_id()))
        if not sid:
            return
        notes = hunk.user_notes(sid)
        if notes is None:
            die("could not read the viewer's notes — the daemon did not answer. This is "
                "NOT the same as there being none: do not push. Retry, and if it keeps "
                "failing ask the user whether they left anything on the diff.")
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

    ctx, iid, path, state = resolve_state(args)

    if cmd == "path":
        print(path)
    elif cmd == "quote":
        print(render_quote(state, args.topic) + critical_manifest.manifest())
        # `render_quote` recorded what it showed; persist it so a later `--refine` can
        # tell whether the context still matches. Every command that renders a topic in
        # full does this — it is the only reason these read-only views write at all.
        save(path, state)
    elif cmd == "url":
        print(render_url(state, args.topic))
    elif cmd == "reply-view":
        body = reply_body(state, args.topic, os.path.dirname(path))
        print(render_reply_view(state, args.topic, body, args.refine)
              + critical_manifest.manifest())
        save(path, state)
    elif cmd == "reply":
        # body only — the payload for the clipboard or `glab api -F body=@-`
        print(reply_body(state, args.topic, os.path.dirname(path)), end="")
    elif cmd == "present":
        print(render_present(state) + critical_manifest.manifest())
        save(path, state)
    elif cmd == "bodies":
        print(render_bodies(state))
    elif cmd == "plans":
        print(render_plans(state))
    elif cmd in ("sync", "todo"):
        sync(state, fetch_threads(ctx, iid, current_user()))
        save(path, state)
        # `sync` isn't gated (see paste-gates.json's note), so the manifest it carries
        # is simply never read for that command — harmless, and keeping one code path
        # for both is simpler than branching just to omit it.
        print(render_table(state, "mine" if cmd == "todo" else "all",
                           show_done=getattr(args, "all", False)) + critical_manifest.manifest())
    elif cmd == "set":
        t = topic_for(state, args.topic) or die(f"no topic {args.topic}")
        fld = {"start-sha": "start_sha", "diff-url": "diff_url"}
        for f in ("summary", "state", "decision", "plan", "start-sha", "diff-url",
                  "reply"):
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
                    die(f"draft has internal topic handle(s): {' '.join(hits)} — reword "
                        f"(link the other thread via `url <other-t>`), then re-run")
                v = v.rstrip("\n") + "\n"
            t[fld.get(f, f)] = v
        save(path, state)
    elif cmd == "merge":
        into = topic_for(state, args.into) or die(f"no topic {args.into}")
        for oid in args.others:
            o = topic_for(state, oid)
            if not o:
                continue
            into["thread_ids"] += [x for x in o["thread_ids"]
                                   if x not in into["thread_ids"]]
            state["topics"] = [t for t in state["topics"] if t["id"] != oid]
        save(path, state)


if __name__ == "__main__":
    main()
