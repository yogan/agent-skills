#!/usr/bin/env python3
"""Track your review findings for someone ELSE's GitLab MR, reconcile them
against the live discussions, and render them as markdown (which the agent chat
styles).

This is the REVIEWER side — the mirror of rework-mr's threads.py (the author
side). **The skill never writes to GitLab**: you post comments and resolve
threads in the UI yourself. This script only *reads* discussions + the branch
tip, and keeps a local overlay of your findings and their lifecycle.

State lives at ~/.claude/review-mr/<slug>--mr<iid>/findings.json, keyed by
project + MR iid, so it survives across sessions (a review spans days). The
review worktree path is remembered repo-wide at ~/.claude/review-mr/<slug>/worktree,
and the draft language at ~/.claude/review-mr/<slug>/lang (default de).

A topic has two orthogonal axes plus a lifecycle state:
  kind    issue (carries a severity) | question | praise
  source  llm 🤖 | human 👤 | both 👥   (both = you found it too → counts as yours)
  state — GitLab is the source of truth for a thread's existence/resolution; the
          local file only overlays YOUR ack/wontfix decision:
    ✎ draft     you found it, not posted on GitLab yet            (your turn)
    ○ open      you posted it; the author has not responded        (author's turn)
    ◐ needs-ack author replied and/or resolved the thread          (your turn)
    ● acked     you are satisfied — the only normal close          (overlay)
    ⊘ wontfix   agreed not to fix / deferred (optional ticket)     (overlay)
  An author resolving a thread does NOT close a topic — only your ack does. So a
  resolved thread sits at ◐ needs-ack until you ack it.

Threads you did not open are surfaced automatically on sync as topics — a peer
reviewer's (💬 peer) or the author's own (🖊️ author) — so they show up in your
lists and can be merged with your findings.

Subcommands (all read-only against GitLab):
  sync           fetch discussions + branch tip, reconcile, render overview  (default)
  todo           render only what needs YOU (✎ drafts + ◐ needs-ack)
  present        overview + the first topic that needs you
  resume         the whole resume opener: updates + overview + first topic (one call)
  updates        pushes since your baseline: compare URLs + diffstats + topics touched
  bodies         first + last note of each posted thread (to judge status)
  quote <t>      render a topic's thread notes verbatim (for a draft: display
                 with meta header + where to open the thread)
  draft <t>      the draft's comment body ONLY — the paste/post payload (no meta)
  diff <t>       the author's change for one topic: compare URL + inline git command
  import <file>  bulk-add findings from a JSON array (review-branch seed)
  add [--thread <id>]  add one finding; --thread links an already-posted thread
  set <t> …      update a topic's fields / state
  drop <t>       remove a (draft) topic
  merge <into> <o…>  fold other topics into <into>
  link <t> [<discussion_id>]  attach a posted GitLab thread to a topic (✎→○);
                 omit the id to auto-pick your one unlinked thread on that file
  candidates     your posted threads not yet linked to any topic (for matching)
  head           one-line push check (branch tip vs last-reviewed head)
  set-head       mark the current branch tip as reviewed
  worktree [--set PATH]   get/set the repo's review worktree path
  lang [--set XX]         get/set the repo's draft language (default de)
  path           print the state-file path
"""
import argparse
import datetime
import json
import os
import re
import sys

from _gl import (api, context, current_user, die, mr_head, mr_object,
                 mr_view, web_base)

STATE_ROOT = os.path.expanduser("~/.claude/review-mr")
TOPIC_ICON = "◈"
GLYPH = {"draft": "✎", "open": "○", "needs_ack": "◐", "acked": "●", "wontfix": "⊘"}
WORD = {"draft": "draft", "open": "open", "needs_ack": "needs-ack",
        "acked": "acked", "wontfix": "wontfix"}
# your turn first (draft + needs-ack), then author's, then terminals
STATUS_ORDER = {"needs_ack": 0, "draft": 1, "open": 2, "wontfix": 3, "acked": 4}
SEV_ICON = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵"}
KIND_ICON = {"question": "❓", "praise": "💚"}
# llm/human/both = who found it; author/peer = an inbound thread you didn't open
SRC_ICON = {"llm": "🤖", "human": "👤", "both": "👥", "peer": "💬", "author": "🖊️"}
INBOUND = ("peer", "author")


def tref(tid):
    """A topic id as the user reads it — always carrying the topic icon, so `t3` never
    turns up bare in rendered output and is never mistaken for a GitLab thread id.

    Deliberately NOT used for: CLI examples (`set t3 --line 9` must stay copy-pasteable),
    `die()` diagnostics about a topic that does not exist, the bare id `add` prints for
    capture, and thread/discussion ids — `candidates` lists those, and they are not topics.
    """
    return f"{TOPIC_ICON} {tid}"


def first_name(name):
    """'Doe, Jane - AB12345' -> 'Jane'; 'Jane Doe' -> 'Jane'."""
    n = (name or "").strip()
    if " - " in n:
        n = n.rsplit(" - ", 1)[0].strip()
    if "," in n:
        n = n.split(",", 1)[1].strip()
    parts = n.split()
    return parts[0] if parts else (name or "")


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
    """
    return f"drafts in {state.get('lang') or DEFAULT_LANG}"


def state_dir(slug):
    d = os.path.join(STATE_ROOT, slug)
    os.makedirs(d, exist_ok=True)
    return d


def state_file(slug, iid):
    d = os.path.join(STATE_ROOT, f"{slug}--mr{iid}")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "findings.json")


def load(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return None


def save(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def mr_author(mr):
    """Short first name of the MR author — 'Doe, Jane - AB12345' -> 'Jane'."""
    a = mr.get("author") or {}
    return first_name(a.get("name") or a.get("username"))


def mr_author_username(mr):
    return (mr.get("author") or {}).get("username")


def new_state(ctx, mr):
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
            "by_author": bool(author) and first_user == author,  # author's own thread
            "awaiting": (("you" if last_user == author else "author") if author
                         else ("you" if (me and last_user != me) else "author")),
        }
    return out


# ---------------------------------------------------------------- topics


def topic_for(state, tid):
    return next((t for t in state["topics"] if t["id"] == tid), None)


def next_tid(state):
    """Monotonic, never reused — a persisted counter, so a topic id stays stable
    across the multi-day loop even after drops/merges free lower numbers."""
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
    }
    t.update({k: v for k, v in fields.items()
              if v is not None and k not in ADD_PROTECTED})
    state["topics"].append(t)
    return t


def attach_thread(state, t, discussion_id, ctx, iid, start_sha=None):
    """Link a posted GitLab thread to a topic and capture its baseline."""
    if discussion_id not in t["thread_ids"]:
        t["thread_ids"].append(discussion_id)
    if start_sha:
        t["start_sha"] = start_sha
    elif not t.get("start_sha"):
        t["start_sha"] = state.get("last_reviewed_head") or mr_head(ctx, iid)
    return t


def topic_status(state, t):
    """draft until posted; then derive from GitLab. Overlay (acked/wontfix) wins,
    but a *fresh* author note re-opens it.

    "Fresh" means newer than your ack — compared against `acked_at`, not merely "the
    author holds the last word". Without that comparison an ack could never stick on a
    thread the author had replied to and nobody had resolved on GitLab: the overlay was
    set, and the very reply you were acking kept overriding it, so the topic sat at
    needs-ack forever and the table contradicted the state file."""
    thr = [state["threads"].get(x) for x in t["thread_ids"]]
    thr = [x for x in thr if x]
    if not thr:
        return "draft"
    if t.get("kind") == "praise":                # no ack loop: resolved ⇒ done
        return "acked" if all(x.get("resolved") for x in thr) else "open"
    ov = t.get("state")
    acked_at = _ts(t.get("acked_at"))

    def spoke_after_ack(x):
        if x.get("awaiting") != "you" or x.get("resolved"):
            return False
        if not acked_at:                     # never acked → any author note is fresh
            return True
        said_at = _ts(x.get("last_at"))
        return said_at is None or said_at > acked_at

    author_active = any(spoke_after_ack(x) for x in thr)
    if ov in ("acked", "wontfix") and not author_active:
        return ov
    if any(x.get("awaiting") == "you" or x.get("resolved") for x in thr):
        return "needs_ack"
    return "open"


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


def sync(state, live):
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
    adopt_inbound(state)


def adopt_inbound(state):
    """Surface discussions you didn't open — a peer reviewer's thread (💬, first
    class and mergeable) or the author's own (🖊️, rare) — as topics, so they show
    up in the overview and needs-ack flow. Threads you opened stay out of this
    (they belong to your drafts, matched via `candidates`/`link`)."""
    linked = {th for t in state["topics"] for th in t["thread_ids"]}
    ignored = set(state.get("ignored") or [])       # dropped inbound → stay dropped
    # A thread YOU opened is normally left to candidates/link, because it may be the
    # posted form of a ✎ draft — auto-adopting it would duplicate that topic. But that
    # only applies to a draft about the SAME FILE. Keying on "any draft pending" made
    # this useless in practice: during curation there are essentially always drafts
    # pending, so a comment you wrote straight in the UI would never land in the table.
    draft_files = {t.get("file") for t in state["topics"]
                   if topic_status(state, t) == "draft" and t.get("file")}
    for tid, x in state["threads"].items():
        if tid in linked or tid in ignored or x.get("gone"):
            continue
        if x.get("mine") and x.get("file") in draft_files:
            continue                          # could be that draft, posted — ask instead
        summ = " ".join((x.get("body") or "").split())[:80]
        if x.get("mine"):
            source = "human"                  # 👤 you found it and posted it yourself
        elif x.get("by_author"):
            source = "author"
        else:
            source = "peer"
        t = add_topic(state, source=source, summary=summ or None, file=x.get("file"),
                      line=x.get("line"),
                      by=None if x.get("mine") else first_name(x.get("author")))
        t["thread_ids"].append(tid)


# ---------------------------------------------------------------- render

# Lines this run's render built that the paste-enforcement Stop hook (paste-gate.py)
# must treat as critical — never silently droppable, whatever the block's tolerance for
# a harmless dropped preamble line. Populated at the exact point each such line is
# constructed (a table row, a line inside a fenced code block) instead of being
# re-derived downstream by re-parsing the rendered markdown: this script is the only
# place that unambiguously KNOWS which lines are table/code content, since it built
# them, and re-deriving that fact from text after the fact is exactly the parsing this
# is meant to avoid (see paste-gate.py's own history of fence-width bugs from doing
# just that). One list per process is enough — every CLI invocation is a fresh
# interpreter, so there is no cross-command state to reset between calls.
_critical = []


def _mark(line):
    _critical.append(line.strip())
    return line


def _reset_critical():
    """The CLI never needs this — every invocation is a fresh interpreter, so `_critical`
    starts empty on its own. A test PROCESS calls the render functions many times across
    many cases, though, and would otherwise see critical lines pile up across unrelated
    tests. (Mirrors rework-mr's threads.py, for the same reason.)"""
    _critical.clear()


def _critical_manifest():
    """Trailing, non-visible payload for the gated commands' stdout: paste-gate.py
    splits this off before checking what the model pasted, so it is never something
    the model is asked to reproduce. Empty when nothing this run built was critical
    (a posted-thread `quote`, `updates`, an errored `diff`) — no marker at all beats an
    empty one, since SKILL.md and paste-gates.json treat a bare mention of the marker
    text as something that must never reach a visible reply."""
    if not _critical:
        return ""
    return "\n\n<!-- paste-gate:critical\n" + json.dumps(_critical) + "\n-->"


def short_summary(state, t, width=64):
    text = t.get("summary")
    if not text:
        thr = [state["threads"].get(x, {}) for x in t["thread_ids"]]
        text = thr[0].get("body") if thr else ""
    text = " ".join((text or "").split())
    return text[: width - 1] + "…" if len(text) > width else text


def _num(tid):
    return int("".join(c for c in tid if c.isdigit()) or 0)


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
    """Location `path:line`. Compact (basename) for the overview table; full repo
    path when `full` — used in the per-topic header you read while discussing."""
    f, line = topic_file(state, t)
    if not f:
        return ""
    return f"{f if full else os.path.basename(f)}:{line or ''}"


def _rows(state, scope):
    topics = state["topics"]
    st = {t["id"]: topic_status(state, t) for t in topics}
    counts = {s: sum(1 for t in topics if st[t["id"]] == s) for s in GLYPH}
    ordered = sorted(topics, key=lambda t: (STATUS_ORDER[st[t["id"]]], _num(t["id"])))
    if scope == "mine":                          # only what needs you
        keep = {"draft", "needs_ack"}
    else:
        keep = set(GLYPH)
    return st, counts, [t for t in ordered if st[t["id"]] in keep]


def render_table(state, scope="all"):
    st, counts, shown = _rows(state, scope)
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
            out.append(_mark(
                f"| {GLYPH[s]} {WORD[s]}{mark} | {tref(f'**{tid}**')} "
                f"| {kind_icon(t)} | {SRC_ICON.get(t.get('source'), '🤖')} "
                f"| `{_loc(state, t)}` | {summ} |"))
    else:
        out.append("_✓ nothing needs you right now_" if scope == "mine"
                   else "_no findings yet_")
    total = len(state["topics"])
    done = counts["acked"] + counts["wontfix"]
    prog = [f"{done}/{total} acked/closed"]
    if counts["draft"]:
        prog.append(f"{counts['draft']} to post")
    if counts["needs_ack"]:
        prog.append(f"{counts['needs_ack']} need your ack")
    if counts["open"]:
        prog.append(f"{counts['open']} awaiting author")
    tail = ["", "**Topics:** " + " · ".join(prog)]
    return "\n".join(out + tail)


def _note_md(name, body):
    lines = (body or "").strip().splitlines() or [""]
    quoted = "\n".join("> " + ln for ln in lines)
    return f"> **{first_name(name)}**\n>\n{quoted}"


FENCE_BY_EXT = {".ts": "ts", ".tsx": "tsx", ".js": "js", ".jsx": "jsx",
                ".py": "python", ".rb": "ruby", ".go": "go", ".java": "java",
                ".kt": "kotlin", ".sh": "bash", ".yml": "yaml", ".yaml": "yaml",
                ".json": "json", ".sql": "sql", ".css": "css", ".html": "html"}


SUGGESTION_FENCE = re.compile(r"^```suggestion(?::-(\d+)\+(\d+))?\s*$")


def render_draft(body, lang, anchor=None):
    """Display form of a draft. The paste payload (`draft <t>`) is never touched.

    Three things happen here, each for a reason:

    * ```suggestion is re-fenced to the file's language. GitLab needs the literal
      ```suggestion for one-click-apply, but no terminal or chat renderer knows it as a
      language, so the block the user has to approve would render unhighlighted.
    * Its lines are numbered from the range GitLab's own `suggestion:-A+B` syntax implies
      (A lines above the anchor through B below), so the replacement lines up against the
      source shown above it — and it becomes visible when 3 lines are replaced by 2.
    * PROSE is blockquoted so the draft reads as the artefact being posted rather than as
      commentary. Fenced blocks stay at line start on purpose: indenting or prefixing a
      fence (even with "> ") risks losing the syntax highlighting, which is the single most
      useful thing in this view.
    """
    if not body:
        return body
    out, in_sug, num, width = [], False, None, 2
    for line in body.splitlines():
        m = SUGGESTION_FENCE.match(line)
        if m and not in_sug:
            in_sug = True
            above, below = m.group(1), m.group(2)
            num, width = None, 2
            if anchor:
                try:
                    start_ln = int(anchor) - int(above or 0)
                    end_ln = int(anchor) + int(below or 0)
                    num, width = start_ln, len(str(end_ln))
                except (TypeError, ValueError):
                    num = None
            out.append(f"```{lang}" if lang else "```")
            continue
        if in_sug and line.strip() == "```":
            in_sug, num = False, None
            out.append(line)
            continue
        if in_sug:
            out.append(_mark(f"{str(num).rjust(width)} | {line}" if num is not None else line))
            if num is not None:
                num += 1
            continue
        out.append(f"> {line}".rstrip() if line.strip() else ">")
    return "\n".join(out)


_ML_MAX_BACKTRACK = 30


def _open_construct(lines, before):
    """(marker, start_line) if line `before` (1-based) opens strictly inside an
    unterminated triple-quoted string, backtick string (JS/TS template literal, Go raw
    string), or /* */ block comment carried over from an earlier line, else None.

    `code_snippet` slices a fixed line window with no notion of syntax state. When that
    window happens to start right after a docstring/comment/template-literal opened above
    it, the ONLY delimiter inside the window is the (correct) CLOSING one — but a syntax
    highlighter given just the snippet, with no memory of anything before it, reads that
    as an OPENING one instead. Everything after it then renders as an unterminated
    string, and everything before it (still genuinely inside the construct, just not
    visibly so) renders as if it were top-level code — which is how a docstring sentence
    containing "and"/"any"/"set" ends up with keywords lit up. Scanning from the top of
    the file is the only way to know which reading is right, since that state isn't
    visible from the window alone.
    """
    state = None
    for i in range(before - 1):
        ln = lines[i]
        j = 0
        while j < len(ln):
            if state is None:
                if ln.startswith('"""', j) or ln.startswith("'''", j):
                    state = (ln[j:j + 3], i + 1)
                    j += 3
                    continue
                if ln.startswith("/*", j):
                    state = ("/*", i + 1)
                    j += 2
                    continue
                if ln.startswith("`", j):
                    state = ("`", i + 1)
                    j += 1
                    continue
            else:
                closer = "*/" if state[0] == "/*" else state[0]
                if ln.startswith(closer, j):
                    state = None
                    j += len(closer)
                    continue
            j += 1
    return state


def code_snippet(state, t, context_lines=4):
    """The lines under discussion, read from the review worktree.

    A draft is a comment about specific code, so the code belongs next to it — without
    this the user is asked to approve a comment about a line they cannot see, and the
    agent's research reads as unsourced assertion. Read from the worktree recorded for
    the repo, NOT from cwd: every shell call resets cwd to the repo root, so a relative
    open() would silently read the wrong checkout (or fail).
    """
    file, line = t.get("file"), t.get("line")
    if not file:
        return None
    wt = get_worktree(state.get("slug") or "")
    if not wt:
        return None
    full = os.path.join(wt, file)
    if not os.path.isfile(full):
        return None
    try:
        with open(full, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    try:
        n = int(line)
    except (TypeError, ValueError):
        n = None
    if not n or n > len(lines):
        return None
    lo = max(1, n - context_lines)
    hi = min(len(lines), n + context_lines)
    lang = FENCE_BY_EXT.get(os.path.splitext(file)[1], "")
    opened = _open_construct(lines, lo)
    if opened:
        # Extend back to the real opening delimiter so the highlighter sees the whole
        # construct — unless that is far enough away to blow up the snippet, in which
        # case an unhighlighted (but not actively MIS-highlighted) block beats either a
        # wall of unrelated context or a snippet that reads as broken Python.
        if lo - opened[1] <= _ML_MAX_BACKTRACK:
            lo = opened[1]
        else:
            lang = ""
    width = len(str(hi))
    body = []
    for i in range(lo, hi + 1):
        mark = "►" if i == n else " "
        body.append(_mark(f"{mark} {str(i).rjust(width)} | {lines[i - 1]}"))
    return f"```{lang}\n" + "\n".join(body) + "\n```"


def anchor_warning(state, t, context_lines=4):
    """Flag a topic whose `line` probably points at the wrong code.

    Seeded line numbers come from review-branch's output — a model's estimate — and they
    are wrong often enough to matter: an anchor off by four lines renders unrelated code
    next to the finding, and a comment posted there lands on the wrong line in GitLab.

    Heuristic: the summary almost always names the culprit in backticks. If none of those
    identifiers appear on the marked line but one does appear elsewhere in the file, say so
    and name the closest candidate. Silent when the summary has no usable identifier, or
    when the marked line already matches.
    """
    file, line, summary = t.get("file"), t.get("line"), t.get("summary") or ""
    if not (file and line and summary):
        return None
    wt = get_worktree(state.get("slug") or "")
    if not wt:
        return None
    full = os.path.join(wt, file)
    if not os.path.isfile(full):
        return None
    # Identifiers to look for. Backticks are the reliable signal, but seeded summaries
    # often carry none (they are prose from the reviewer model), so also take
    # code-SHAPED bare words: camelCase or PascalCase/ALLCAPS, which English prose in a
    # summary does not produce. "redirectTo" and "URLSearchParams" qualify; "instead",
    # "created" and "null" do not.
    candidates = [raw.strip().rstrip("()").split("(")[0].split(".")[-1].strip()
                  for raw in re.findall(r"`([^`]+)`", summary)]
    candidates += re.findall(r"\b[A-Za-z_][A-Za-z0-9_]{3,}\b", summary)
    needles = []
    for tok in candidates:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", tok or ""):
            continue
        if len(tok) < 4:
            continue
        if not re.search(r"[a-z][A-Z]|^[A-Z]{2,}", tok):     # must look like code
            continue
        needles.append(tok)
    needles = list(dict.fromkeys(needles))
    if not needles:
        return None
    try:
        with open(full, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
        n = int(line)
    except (OSError, TypeError, ValueError):
        return None
    if not 1 <= n <= len(lines):
        return (f"⚠️ **anchor check**: `{file}` has {len(lines)} lines, so line {n} does not "
                f"exist. Seeded line numbers are the reviewer model's estimate — fix it "
                f"with `set {t['id']} --line <n>` before posting.")
    if any(x in lines[n - 1] for x in needles):
        return None
    hits = [i for i, text in enumerate(lines, 1) if any(x in text for x in needles)]
    if not hits:
        return None
    best = min(hits, key=lambda i: abs(i - n))
    named = ", ".join(f"`{x}`" for x in dict.fromkeys(needles))
    return (f"⚠️ **anchor check**: line {n} does not mention {named}; the closest line that "
            f"does is **{best}**. Seeded line numbers are the reviewer model's estimate, so "
            f"verify before posting — `set {t['id']} --line {best}` if that is the right spot.")


def render_quote(state, tid):
    t = topic_for(state, tid) or die(f"no topic {tid}")
    summ = t.get("summary") or ""
    out = []
    if not t["thread_ids"]:                       # a draft — show its draft text
        title = f"**{tref(t['id'])}" + (f" — {summ}**" if summ else "**")
        # Meta lines (title + where to open the thread) are for the user's eyes
        # only — NOT part of the comment. The paste payload is the body below,
        # which `draft <t>` emits on its own for the clipboard / posting.
        out.append(f"{title}  ({WORD['draft']} — not yet on GitLab)")
        # The code block carries the location, so there is no separate "open a thread
        # at …, then paste" line: it sat directly above the SOURCE snippet and read as if
        # that were the paste payload.
        snip = code_snippet(state, t)
        loc = _loc(state, t, full=True)
        if snip:
            out += ["", f"_Code currently in MR — `{loc}`:_", "", snip]
        warn = anchor_warning(state, t)
        if warn:
            out += ["", warn]
        body = t.get("draft") or t.get("note")
        if body:
            lang = FENCE_BY_EXT.get(os.path.splitext(t.get("file") or "")[1], "")
            # The draft language rides on THIS label. It has to stay somewhere in the
            # drafting view — it is what keeps the agent from falling back to the
            # documented default language — and next to the draft is where it belongs.
            out += ["", f"_Draft of comment to post ({state.get('lang') or DEFAULT_LANG})"
                    f" — thread on `{loc}`:_", "",
                    render_draft(body, lang, t.get("line"))]
        return "\n".join(out).strip()
    for i, th in enumerate(t["thread_ids"]):
        x = state["threads"].get(th, {})
        path = f"{x.get('file')}:{x.get('line') or ''}"
        if i == 0:
            title = f"**{tref(t['id'])}" + (f" — {summ}**" if summ else "**")
            out.append(f"{title} · `{path}` · {lang_hint(state)}")
        else:
            out.append(f"`{path}`")
        if x.get("url"):
            out.append(x["url"])
        if x.get("resolved"):
            who = first_name(x.get("resolved_by")) or "someone"
            out.append(f"_(GitLab thread resolved by {who} — that's their toggle; "
                       "your ack is what closes this topic here)_")
        out += ["", _note_md(x.get("author"), x.get("body"))]
        if x.get("note_count", 1) > 1:
            skipped = x["note_count"] - 2
            if skipped > 0:
                out += ["", f"_… {skipped} more …_"]
            out += ["", _note_md(x.get("last_author"), x.get("last_body"))]
        out.append("")
    return "\n".join(out).strip()


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
    return min(todo, key=lambda t: (STATUS_ORDER[st[t["id"]]], _num(t["id"])))


def render_present(state):
    parts = [render_table(state, "all")]
    t = first_todo(state)
    if t:
        parts += ["\n---\n", render_quote(state, t["id"])]
    return "\n".join(parts)


def render_bodies(state):
    out = []
    for t in state["topics"]:
        if topic_status(state, t) in ("acked", "wontfix", "draft"):
            continue
        for th in t["thread_ids"]:
            x = state["threads"].get(th, {})
            res = (f", resolved by {first_name(x.get('resolved_by')) or '?'}"
                   if x.get("resolved") else "")
            out.append(f"[{tref(t['id'])}] {os.path.basename(x.get('file') or '')}:"
                       f"{x.get('line') or ''}  "
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
        out.append(f"{tid}  `{os.path.basename(x.get('file') or '')}:"
                   f"{x.get('line') or ''}`  {(x.get('body') or '').strip()[:80]}")
    return "\n".join(out).strip() or "(no unlinked threads of yours)"


# ---------------------------------------------------------------- pushes / diffs


def versions(ctx, iid):
    """MR diff versions, newest first — one per push (survives force-push)."""
    return api(f"projects/{ctx['enc']}/merge_requests/{iid}/versions?per_page=100",
               paginate=True)


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
    Returns {'stat': '`+A/−D` · N files', 'paths': [...]} or None if it can't.

    The churn leads and is set in code, so the number the eye wants is first and visually
    separated from the prose that follows; the file count trails it. No colour: the block
    is pasted by the model into a chat message, so ANSI could not survive the round trip
    even where the renderer would honour it."""
    diffs = _compare(ctx, frm, to)
    if diffs is None:
        return None
    add = dele = 0
    paths = []
    for d in diffs:
        paths.append(d.get("new_path") or d.get("old_path"))
        for ln in (d.get("diff") or "").splitlines():
            if ln.startswith("+") and not ln.startswith("+++"):
                add += 1
            elif ln.startswith("-") and not ln.startswith("---"):
                dele += 1
    nf = len(diffs)
    return {"stat": f"`+{add}/−{dele}` · {nf} {'file' if nf == 1 else 'files'}",
            "paths": paths}


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
    labelled **rebase** (branch moved onto a new target base — no reviewable author
    change) or a **diffstat** + topics touched. You add the prose summary per push.

    Rebase vs real-change is read from version metadata: a push whose base_commit_sha
    differs from the previous version's is a rebase; same base + new head is fixups."""
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
        out.append(f"- **push {n}:** {url}")
        if rebased:
            ob = (pred.get("base_commit_sha") or "")[:8]
            nb = (v.get("base_commit_sha") or "")[:8]
            rd = _rebase_kind(ctx, iid, pred.get("id"), v.get("id"))
            if rd is None:
                out.append(f"  - ↻ rebase onto new base `{ob}` → `{nb}` "
                           "(couldn't classify — inspect via the URL)")
            elif rd["changed"] == 0:
                out.append(f"  - ↻ rebase onto new base `{ob}` → `{nb}` — commit "
                           "messages unchanged (likely no author change; a silent "
                           "`--amend` wouldn't show here, so skim the URL if unsure)")
            else:                                # the annoying mixed push
                subs = "; ".join(rd["subjects"][:3])
                more = "…" if len(rd["subjects"]) > 3 else ""
                out.append(f"  - ⚠️ rebase onto new base `{ob}` → `{nb}` **+ "
                           f"{rd['changed']} real change(s) folded in** — inspect "
                           f"carefully. New/edited commits: {subs}{more}")
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


def render_topic_diff(state, ctx, iid, tid, inline_limit=80):
    """What the author changed for one topic since you posted it. Server-side via
    the compare API (force-push-safe): shows the topic file's diff inline when it's
    small (≤ inline_limit lines), else just the compare URL. Always prints the URL."""
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
        if body:
            blocks.append(f"--- {d.get('new_path') or d.get('old_path')}\n{body}")
    text = "\n".join(blocks)
    nlines = text.count("\n") + 1 if text else 0
    if 0 < nlines <= inline_limit:
        for ln in text.splitlines():
            _mark(ln)
        out += ["", "```diff", text, "```"]
    elif nlines:
        out.append(f"_(diff is {nlines} lines — too big to inline; open the URL)_")
    return "\n".join(out)


# ---------------------------------------------------------------- worktree


def worktree_path(slug):
    return os.path.join(state_dir(slug), "worktree")


def get_worktree(slug):
    p = worktree_path(slug)
    if os.path.exists(p):
        with open(p) as f:
            return f.read().strip()
    return None


def set_worktree(slug, path):
    with open(worktree_path(slug), "w") as f:
        f.write(path.strip() + "\n")


# ---------------------------------------------------------------- draft language

DEFAULT_LANG = "de"


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
    path = state_file(ctx["slug"], iid)
    state = load(path)
    if state:
        # Dropped with the approval/merge-readiness footer. Popped so an existing review's
        # state file stops carrying a blob nothing reads (removable once no state predates
        # the removal — it costs one line and saves a confusing read of findings.json).
        state.pop("readiness", None)
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

    for name in ("sync", "todo", "present", "resume", "bodies", "candidates",
                 "head", "set-head", "updates", "path"):
        sub.add_parser(name).add_argument("--iid", type=int)

    sub.add_parser("worktree").add_argument("--set", dest="set_path")
    sub.add_parser("lang").add_argument("--set", dest="set_lang")

    pq = sub.add_parser("quote")
    pq.add_argument("topic")
    pq.add_argument("--iid", type=int)

    pdr = sub.add_parser("draft")
    pdr.add_argument("topic")
    pdr.add_argument("--iid", type=int)

    pdf = sub.add_parser("diff")
    pdf.add_argument("topic")
    pdf.add_argument("--iid", type=int)

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
    for f in ("summary", "file", "line", "draft", "note"):
        pa.add_argument(f"--{f}")

    ps = sub.add_parser("set")
    ps.add_argument("topic")
    ps.add_argument("--iid", type=int)
    ps.add_argument("--kind", choices=("issue", "question", "praise"))
    ps.add_argument("--severity", choices=tuple(SEV_ICON))
    ps.add_argument("--source", choices=tuple(SRC_ICON))
    ps.add_argument("--state", choices=("acked", "wontfix", "reset"))
    for f in ("summary", "file", "line", "draft", "note", "ticket", "start-sha"):
        ps.add_argument(f"--{f}")

    pd = sub.add_parser("drop")
    pd.add_argument("topic")
    pd.add_argument("--iid", type=int)

    pm = sub.add_parser("merge")
    pm.add_argument("into")
    pm.add_argument("others", nargs="+")
    pm.add_argument("--iid", type=int)

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

    # worktree is repo-level (no MR needed)
    if cmd == "worktree":
        ctx = context()
        if args.set_path:
            set_worktree(ctx["slug"], args.set_path)
        print(get_worktree(ctx["slug"]) or "")
        return

    ctx, iid, path, state = resolve_state(args)
    me = current_user()
    author = state.get("author_username")
    if me is None and cmd in ("sync", "todo", "present", "candidates"):
        print("warning: could not determine your glab username — needs-ack and "
              "candidates may be wrong (is `glab auth status` OK?)", file=sys.stderr)

    if cmd == "path":
        print(path)
    elif cmd == "sync":
        sync(state, fetch_threads(ctx, iid, me, author))
        save(path, state)
        print(render_table(state, "all"))
        print("\n_" + head_report(state, ctx, iid) + "_")
    elif cmd == "todo":
        sync(state, fetch_threads(ctx, iid, me, author))
        save(path, state)
        print(render_table(state, "mine") + _critical_manifest())
    elif cmd == "present":
        sync(state, fetch_threads(ctx, iid, me, author))
        save(path, state)
        print(render_present(state) + _critical_manifest())
    elif cmd == "bodies":
        print(render_bodies(state))
    elif cmd == "candidates":
        sync(state, fetch_threads(ctx, iid, me, author))
        save(path, state)
        print(render_candidates(state, me))
    elif cmd == "quote":
        print(render_quote(state, args.topic) + _critical_manifest())
    elif cmd == "draft":
        print(draft_body(state, args.topic))
    elif cmd == "diff":
        print(render_topic_diff(state, ctx, iid, args.topic) + _critical_manifest())
    elif cmd == "updates":
        sync(state, fetch_threads(ctx, iid, me, author))
        save(path, state)
        print(render_updates(state, ctx, iid) + _critical_manifest())
    elif cmd == "resume":
        # The complete opener for an in-progress review, in one call: pushes since your
        # baseline THEN the overview table THEN the first topic needing you.
        # Deliberately one command rather than "run updates, then run present": as two
        # steps the second one gets skipped, and the overview table — the user's only
        # view of where all topics stand — silently goes missing.
        #
        # A single combined print (not three separate ones) so the ONE trailing
        # manifest covers everything this command built — updates has no critical
        # content of its own, but present's table (and first topic's code, if any)
        # does, and paste-gate.py needs it all in one place to check the whole thing.
        sync(state, fetch_threads(ctx, iid, me, author))
        save(path, state)
        u = render_updates(state, ctx, iid)
        p = render_present(state)
        print(f"{u}\n\n---\n\n{p}{_critical_manifest()}")
    elif cmd == "head":
        print(head_report(state, ctx, iid))
    elif cmd == "set-head":
        state["last_reviewed_head"] = mr_head(ctx, iid)
        save(path, state)
        print(f"reviewed baseline set to `{(state['last_reviewed_head'] or '')[:12]}`")
    elif cmd == "import":
        with open(args.file) as f:
            items = json.load(f)
        added = [add_topic(state, **it)["id"] for it in items]
        save(path, state)
        print(f"added {len(added)} findings: "
              f"{', '.join(tref(x) for x in added)}")
    elif cmd == "add":
        t = add_topic(state, kind=args.kind, severity=args.severity,
                      source=args.source, summary=args.summary, file=args.file,
                      line=args.line, draft=args.draft, note=args.note)
        if args.thread:
            # Adopting a comment you posted yourself: creating the topic and linking the
            # thread are one intention, so they are one call. Splitting them produced a
            # pointless "link it too?" confirmation.
            attach_thread(state, t, args.thread, ctx, iid)
        save(path, state)
        print(t["id"])
    elif cmd == "set":
        t = topic_for(state, args.topic) or die(f"no topic {args.topic}")
        if args.state == "reset":
            t["state"] = None
            t.pop("acked_at", None)
        elif args.state:
            t["state"] = args.state
            # Stamped so a later author note can be recognised as newer than the ack.
            t["acked_at"] = now_iso() if args.state in ("acked", "wontfix") else None
        fld = {"start-sha": "start_sha"}
        for f in ("kind", "severity", "source", "summary", "file", "line",
                  "draft", "note", "ticket", "start-sha"):
            v = getattr(args, f.replace("-", "_"))
            if v is not None:
                t[fld.get(f, f)] = v
        save(path, state)
        if args.draft is not None:
            # Echo the refreshed view: after storing a draft the agent needs to show it,
            # and reconstructing the block by hand reintroduces the raw ```suggestion
            # fence (unhighlighted) that render_quote deliberately re-fences for display.
            # Printing it here means the correct block is already in front of it.
            print(render_quote(state, args.topic) + _critical_manifest())
    elif cmd == "drop":
        t = topic_for(state, args.topic)
        if t and t["thread_ids"]:                 # keep dropped inbound threads dropped
            ign = state.setdefault("ignored", [])
            ign += [th for th in t["thread_ids"] if th not in ign]
        state["topics"] = [t for t in state["topics"] if t["id"] != args.topic]
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
    elif cmd == "link":
        t = topic_for(state, args.topic) or die(f"no topic {args.topic}")
        did = args.discussion_id
        if not did:
            # "I posted it by hand" — resolve it instead of asking the user for an id
            # they would have to hunt for. Unambiguous when exactly one unlinked thread
            # of theirs sits on this topic's file, which is the normal case.
            sync(state, fetch_threads(ctx, iid, me, author))
            linked = {th for x in state["topics"] for th in x["thread_ids"]}
            hits = [tid for tid, x in state["threads"].items()
                    if x.get("mine") and not x.get("gone") and tid not in linked
                    and x.get("file") == t.get("file")]
            if len(hits) != 1:
                die(f"cannot auto-link {args.topic}: found {len(hits)} unlinked threads "
                    f"of yours on {t.get('file')} — pass the discussion id "
                    f"(see `candidates`)")
            did = hits[0]
        attach_thread(state, t, did, ctx, iid, args.start_sha)
        save(path, state)
        print(f"{tref(args.topic)} ← {did}")


if __name__ == "__main__":
    main()
