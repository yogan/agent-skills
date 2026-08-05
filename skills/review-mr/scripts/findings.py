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
  status         approval + merge-readiness (detailed_merge_status) + approve/revoke nudge
  updates        pushes since your baseline: compare URLs + diffstats + topics touched
  bodies         first + last note of each posted thread (to judge status)
  quote <t>      render a topic's thread notes verbatim (for a draft: display
                 with meta header + where to open the thread)
  draft <t>      the draft's comment body ONLY — the paste/post payload (no meta)
  diff <t>       the author's change for one topic: compare URL + inline git command
  import <file>  bulk-add findings from a JSON array (review-branch seed)
  add            add one finding
  set <t> …      update a topic's fields / state
  drop <t>       remove a (draft) topic
  merge <into> <o…>  fold other topics into <into>
  link <t> <discussion_id>   attach a posted GitLab thread to a topic (✎→○)
  candidates     your posted threads not yet linked to any topic (for matching)
  head           one-line push check (branch tip vs last-reviewed head)
  set-head       mark the current branch tip as reviewed
  worktree [--set PATH]   get/set the repo's review worktree path
  lang [--set XX]         get/set the repo's draft language (default de)
  path           print the state-file path
"""
import argparse
import json
import os
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

# detailed_merge_status → (icon, wording, whose turn). Unknown values fall through.
MERGE_FRIENDLY = {
    "mergeable": ("✅", "ready to merge", None),
    "not_approved": ("🔸", "needs approval", None),
    "discussions_not_resolved": ("⛔", "threads unresolved on GitLab", "resolve"),
    "need_rebase": ("⚠️", "needs rebase", "author"),
    "ci_must_pass": ("❌", "CI failing", "author"),
    "ci_still_running": ("⏳", "CI running", None),
    "conflict": ("⛔", "merge conflicts", "author"),
    "draft_status": ("📝", "marked draft", "author"),
    "requested_changes": ("🔻", "changes requested", None),
    "blocked_status": ("⛔", "blocked by another MR", None),
    "checking": ("…", "merge status still checking", None),
    "unchecked": ("…", "merge status not checked yet", None),
    "not_open": ("—", "not open", None),
}
TURN_TXT = {"author": " — author's turn", "resolve": " — resolve them on GitLab"}


def first_name(name):
    """'Doe, Jane - AB12345' -> 'Jane'; 'Jane Doe' -> 'Jane'."""
    n = (name or "").strip()
    if " - " in n:
        n = n.rsplit(" - ", 1)[0].strip()
    if "," in n:
        n = n.split(",", 1)[1].strip()
    parts = n.split()
    return parts[0] if parts else (name or "")


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
        "start_sha": None, "note": None, "by": None,
    }
    t.update({k: v for k, v in fields.items()
              if v is not None and k not in ADD_PROTECTED})
    state["topics"].append(t)
    return t


def topic_status(state, t):
    """draft until posted; then derive from GitLab. Overlay (acked/wontfix) wins,
    but a fresh author note re-opens it: if a linked thread is unresolved and the
    author spoke last, it is needs-ack regardless of a stale ack."""
    thr = [state["threads"].get(x) for x in t["thread_ids"]]
    thr = [x for x in thr if x]
    if not thr:
        return "draft"
    if t.get("kind") == "praise":                # no ack loop: resolved ⇒ done
        return "acked" if all(x.get("resolved") for x in thr) else "open"
    author_active = any(
        x.get("awaiting") == "you" and not x.get("resolved") for x in thr)
    ov = t.get("state")
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


def sync(state, live):
    keep = ("author", "first_username", "file", "line", "body", "resolved",
            "resolved_by", "url", "note_count", "last_author", "last_username",
            "last_body", "mine", "by_author", "awaiting")
    for tid, rec in live.items():
        if tid in state["threads"]:
            state["threads"][tid].update({k: rec[k] for k in keep})
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
    for tid, x in state["threads"].items():
        if tid in linked or tid in ignored or x.get("mine") or x.get("gone"):
            continue
        summ = " ".join((x.get("body") or "").split())[:80]
        t = add_topic(state, source="author" if x.get("by_author") else "peer",
                      summary=summ or None, file=x.get("file"), line=x.get("line"),
                      by=first_name(x.get("author")))
        t["thread_ids"].append(tid)


# ---------------------------------------------------------------- render


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
            s = st[t["id"]]
            summ = short_summary(state, t).replace("|", "\\|")
            if t.get("source") in INBOUND and t.get("by"):
                summ = f"_{t['by']}:_ {summ}"           # who raised this thread
            resolved = any(state["threads"].get(x, {}).get("resolved")
                           for x in t["thread_ids"])
            mark = " ✓" if resolved and s == "needs_ack" else ""   # GitLab-resolved
            out.append(
                f"| {GLYPH[s]} {WORD[s]}{mark} | {TOPIC_ICON} **{t['id']}** "
                f"| {kind_icon(t)} | {SRC_ICON.get(t.get('source'), '🤖')} "
                f"| `{_loc(state, t)}` | {summ} |")
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
    r = state.get("readiness")
    if r:
        tail += ["", f"**Approvals:** {_approval_line(r)}  |  "
                 f"**Merge:** {_merge_line(r)}"]
        adv = _advice(state, st)
        if adv:
            tail += ["", adv]
    return "\n".join(out + tail)


def _note_md(name, body):
    lines = (body or "").strip().splitlines() or [""]
    quoted = "\n".join("> " + ln for ln in lines)
    return f"> **{first_name(name)}**\n>\n{quoted}"


def render_quote(state, tid):
    t = topic_for(state, tid) or die(f"no topic {tid}")
    summ = t.get("summary") or ""
    out = []
    if not t["thread_ids"]:                       # a draft — show its draft text
        title = f"**{TOPIC_ICON} {t['id']}" + (f" — {summ}**" if summ else "**")
        # Meta lines (title + where to open the thread) are for the user's eyes
        # only — NOT part of the comment. The paste payload is the body below,
        # which `draft <t>` emits on its own for the clipboard / posting.
        out.append(f"{title}  ({WORD['draft']} — not yet on GitLab) · {lang_hint(state)}")
        out.append(f"↳ open a new thread at `{_loc(state, t, full=True)}`, then paste:")
        body = t.get("draft") or t.get("note")
        if body:
            out += ["", body]
        return "\n".join(out).strip()
    for i, th in enumerate(t["thread_ids"]):
        x = state["threads"].get(th, {})
        path = f"{x.get('file')}:{x.get('line') or ''}"
        if i == 0:
            title = f"**{TOPIC_ICON} {t['id']}" + (f" — {summ}**" if summ else "**")
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
            out.append(f"[{t['id']}] {os.path.basename(x.get('file') or '')}:"
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
    Returns {'stat': 'N files, +A −D', 'paths': [...]} or None if it can't."""
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
    return {"stat": f"{nf} {'file' if nf == 1 else 'files'}, +{add} −{dele}",
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


# ---------------------------------------------------------------- readiness


def _approvals(ctx, iid):
    try:
        return api(f"projects/{ctx['enc']}/merge_requests/{iid}/approvals") or {}
    except SystemExit:
        return {}


def refresh_readiness(state, ctx, iid, me):
    """Fetch approval + merge status into state["readiness"] so the renderers (all
    offline) can show it. Read-only. Called by the fetching commands."""
    mr = mr_object(ctx, iid)
    ap = _approvals(ctx, iid)
    who = ap.get("approved_by") or []
    users = [(u.get("user") or {}) for u in who]
    state["readiness"] = {
        "merge": mr.get("detailed_merge_status") or mr.get("merge_status"),
        "resolved": mr.get("blocking_discussions_resolved"),
        "draft": bool(mr.get("draft") or mr.get("work_in_progress")),
        "req": ap.get("approvals_required"),
        "you_approved": bool(me) and any(u.get("username") == me for u in users),
        "approvers": [first_name(u.get("name") or u.get("username")) for u in users],
    }
    return state["readiness"]


def _merge_line(r):
    ms = r.get("merge")
    icon, text, turn = MERGE_FRIENDLY.get(ms, ("•", (ms or "unknown").replace("_", " "), None))
    return f"{icon} {text}{TURN_TXT.get(turn, '')}"


def _approval_line(r):
    apr = r.get("approvers") or []
    req = r.get("req")
    base = str(len(apr)) + (f"/{req}" if req else "")
    who = f" ({', '.join(apr)})" if apr else ""
    return f"{base} · you {'✓' if r.get('you_approved') else '✗'}{who}"


def _advice(state, st):
    """The approve/revoke nudge. Nudge to approve only when your review is fully
    done AND GitLab agrees all threads are resolved (else name the gap). Offer to
    revoke when a topic needs you again after you'd approved."""
    r = state.get("readiness")
    if not r or not state["topics"]:
        return None
    open_work = sum(1 for t in state["topics"]
                    if st[t["id"]] in ("draft", "open", "needs_ack"))
    if r.get("you_approved"):
        if open_work:
            return ("⚠️ you've already approved, but a topic needs you again — "
                    "**revoke approval?** (I can run `glab mr revoke` on your OK)")
        return None                              # approved & done; merge line says the rest
    if open_work:
        return None                              # review not finished → no approve nudge
    if r.get("resolved"):
        return ("✅ all topics closed and GitLab shows every thread resolved — "
                "**approve?** (I'll run `glab mr approve` on your OK)")
    return ("you've closed every topic on your side, but GitLab still shows "
            "unresolved threads — resolve them there first, then approve")


def render_status(state):
    """Compact merge-readiness block for the `status` command."""
    r = state.get("readiness")
    if not r:
        return "(no merge status fetched yet — run `sync`)"
    st = {t["id"]: topic_status(state, t) for t in state["topics"]}
    out = [f"**MR !{state['iid']}** — merge readiness",
           f"**Approvals:** {_approval_line(r)}",
           f"**Merge:** {_merge_line(r)}"]
    adv = _advice(state, st)
    if adv:
        out += ["", adv]
    return "\n".join(out)


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
                    line += f" · touches {', '.join(touch)}"
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
        return f"{tid} isn't posted on GitLab yet — nothing the author could change"
    start = t.get("start_sha") or state.get("last_reviewed_head")
    if not start:
        return f"{tid}: no baseline captured — nothing to compare yet"
    cur = mr_head(ctx, iid)
    file = _loc(state, t, full=True).rsplit(":", 1)[0]
    vs = versions(ctx, iid)
    diff_id = vs[0].get("id") if vs else None
    web = _mr_web(state, ctx, iid)
    url = f"{web}/diffs?diff_id={diff_id}&start_sha={start}"
    out = [f"**{TOPIC_ICON} {tid}** — author changes since you posted "
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

    for name in ("sync", "todo", "present", "bodies", "candidates",
                 "head", "set-head", "updates", "status", "path"):
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
    pl.add_argument("discussion_id")
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
        refresh_readiness(state, ctx, iid, me)
        save(path, state)
        print(render_table(state, "all"))
        print("\n_" + head_report(state, ctx, iid) + "_")
    elif cmd == "todo":
        sync(state, fetch_threads(ctx, iid, me, author))
        refresh_readiness(state, ctx, iid, me)
        save(path, state)
        print(render_table(state, "mine"))
    elif cmd == "present":
        sync(state, fetch_threads(ctx, iid, me, author))
        refresh_readiness(state, ctx, iid, me)
        save(path, state)
        print(render_present(state))
    elif cmd == "status":
        refresh_readiness(state, ctx, iid, me)
        save(path, state)
        print(render_status(state))
    elif cmd == "bodies":
        print(render_bodies(state))
    elif cmd == "candidates":
        sync(state, fetch_threads(ctx, iid, me, author))
        save(path, state)
        print(render_candidates(state, me))
    elif cmd == "quote":
        print(render_quote(state, args.topic))
    elif cmd == "draft":
        print(draft_body(state, args.topic))
    elif cmd == "diff":
        print(render_topic_diff(state, ctx, iid, args.topic))
    elif cmd == "updates":
        sync(state, fetch_threads(ctx, iid, me, author))
        refresh_readiness(state, ctx, iid, me)
        save(path, state)
        print(render_updates(state, ctx, iid))
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
        print(f"added {len(added)} findings: {', '.join(added)}")
    elif cmd == "add":
        t = add_topic(state, kind=args.kind, severity=args.severity,
                      source=args.source, summary=args.summary, file=args.file,
                      line=args.line, draft=args.draft, note=args.note)
        save(path, state)
        print(t["id"])
    elif cmd == "set":
        t = topic_for(state, args.topic) or die(f"no topic {args.topic}")
        if args.state == "reset":
            t["state"] = None
        elif args.state:
            t["state"] = args.state
        fld = {"start-sha": "start_sha"}
        for f in ("kind", "severity", "source", "summary", "file", "line",
                  "draft", "note", "ticket", "start-sha"):
            v = getattr(args, f.replace("-", "_"))
            if v is not None:
                t[fld.get(f, f)] = v
        save(path, state)
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
        if args.discussion_id not in t["thread_ids"]:
            t["thread_ids"].append(args.discussion_id)
        if args.start_sha:
            t["start_sha"] = args.start_sha
        elif not t.get("start_sha"):
            t["start_sha"] = state.get("last_reviewed_head") or mr_head(ctx, iid)
        save(path, state)
        print(f"{args.topic} ← {args.discussion_id}")


if __name__ == "__main__":
    main()
