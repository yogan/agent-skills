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
review worktree path is remembered repo-wide at ~/.claude/review-mr/<slug>/worktree.

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

Subcommands (all read-only against GitLab):
  sync           fetch discussions + branch tip, reconcile, render overview  (default)
  todo           render only what needs YOU (✎ drafts + ◐ needs-ack)
  present        overview + the first topic that needs you
  bodies         first + last note of each posted thread (to judge status)
  quote <t>      render a topic's thread notes verbatim
  import <file>  bulk-add findings from a JSON array (review-branch seed)
  add            add one finding
  set <t> …      update a topic's fields / state
  drop <t>       remove a (draft) topic
  merge <into> <o…>  fold other topics into <into>
  link <t> <discussion_id>   attach a posted GitLab thread to a topic (✎→○)
  candidates     your posted threads not yet linked to any topic (for matching)
  head           report the branch tip vs the last-reviewed head (author push?)
  set-head       mark the current branch tip as reviewed
  worktree [--set PATH]   get/set the repo's review worktree path
  path           print the state-file path
"""
import argparse
import json
import os
import sys

from _gl import (api, context, current_user, die, mr_head, mr_object,
                 mr_view)

STATE_ROOT = os.path.expanduser("~/.claude/review-mr")
TOPIC_ICON = "◈"
GLYPH = {"draft": "✎", "open": "○", "needs_ack": "◐", "acked": "●", "wontfix": "⊘"}
WORD = {"draft": "draft", "open": "open", "needs_ack": "needs-ack",
        "acked": "acked", "wontfix": "wontfix"}
# your turn first (draft + needs-ack), then author's, then terminals
STATUS_ORDER = {"needs_ack": 0, "draft": 1, "open": 2, "wontfix": 3, "acked": 4}
SEV_ICON = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵"}
KIND_ICON = {"question": "❓", "praise": "💚"}
SRC_ICON = {"llm": "🤖", "human": "👤", "both": "👥"}


def first_name(name):
    """'Doe, Jane - AB12345' -> 'Jane'; 'Jane Doe' -> 'Jane'."""
    n = (name or "").strip()
    if " - " in n:
        n = n.rsplit(" - ", 1)[0].strip()
    if "," in n:
        n = n.split(",", 1)[1].strip()
    parts = n.split()
    return parts[0] if parts else (name or "")


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


def new_state(ctx, mr):
    return {
        "project": ctx["path"], "slug": ctx["slug"], "iid": mr["iid"],
        "mr_web_url": mr.get("web_url"), "title": mr.get("title"),
        "author": mr_author(mr),
        "last_reviewed_head": None, "seq": 0,
        "threads": {}, "topics": [],
    }


# ---------------------------------------------------------------- fetch


def fetch_threads(ctx, iid, me):
    """All resolvable discussions on the MR, keyed by discussion id. `me` is the
    reviewer; `awaiting` is 'you' when the author (anyone but you) spoke last."""
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
        first_user = (first.get("author") or {}).get("username")
        last_user = (last.get("author") or {}).get("username")
        out[d["id"]] = {
            "author": (first.get("author") or {}).get("name") or first_user,
            "first_username": first_user,
            "file": pos.get("new_path") or pos.get("old_path"),
            "line": pos.get("new_line") or pos.get("old_line"),
            "body": first.get("body"),
            "resolved": resolved,
            "url": f"{ctx['web']}/-/merge_requests/{iid}#note_{first.get('id')}",
            "note_count": len(notes),
            "last_author": (last.get("author") or {}).get("name") or last_user,
            "last_username": last_user,
            "last_body": last.get("body"),
            "mine": bool(me) and first_user == me,      # you opened this thread
            "awaiting": "you" if (me and last_user != me) else "author",
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
        "id": next_tid(state), "kind": "issue", "severity": "medium",
        "source": "llm", "summary": None, "file": None, "line": None,
        "draft": None, "thread_ids": [], "state": None, "ticket": None,
        "start_sha": None, "note": None,
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
    return SEV_ICON.get(t.get("severity"), "🟡")


# ---------------------------------------------------------------- reconcile


def sync(state, live):
    keep = ("author", "first_username", "file", "line", "body", "resolved",
            "url", "note_count", "last_author", "last_username", "last_body",
            "mine", "awaiting")
    for tid, rec in live.items():
        if tid in state["threads"]:
            state["threads"][tid].update({k: rec[k] for k in keep})
        else:
            state["threads"][tid] = rec
    for tid in state["threads"]:
        if tid not in live:                     # discussion deleted upstream
            state["threads"][tid]["resolved"] = True
            state["threads"][tid]["gone"] = True


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


def _loc(state, t, full=False):
    """Location `path:line`. Compact (basename) for the overview table; full repo
    path when `full` — used in the per-topic header you read while discussing."""
    def fmt(p, line):
        return f"{p if full else os.path.basename(p)}:{line or ''}"
    if t.get("file"):
        return fmt(t["file"], t.get("line"))
    thr = [state["threads"].get(x, {}) for x in t["thread_ids"]]
    return next((fmt(x["file"], x.get("line")) for x in thr if x.get("file")), "")


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
    out = [head, ""]
    if shown:
        out += ["| State | Topic | Kind | Src | Location | Summary |",
                "|---|---|---|---|---|---|"]
        for t in shown:
            s = st[t["id"]]
            summ = short_summary(state, t).replace("|", "\\|")
            out.append(
                f"| {GLYPH[s]} {WORD[s]} | {TOPIC_ICON} **{t['id']}** "
                f"| {kind_icon(t)} | {SRC_ICON.get(t.get('source'), '🤖')} "
                f"| `{_loc(state, t)}` | {summ} |")
    else:
        out.append("_✓ nothing needs you right now_" if scope == "mine"
                   else "_no findings yet_")
    total = len(state["topics"])
    done = counts["acked"] + counts["wontfix"]
    footer = [f"{done} of {total} closed"]
    if counts["draft"]:
        footer.append(f"{counts['draft']} to post")
    if counts["needs_ack"]:
        footer.append(f"{counts['needs_ack']} need your ack")
    if counts["open"]:
        footer.append(f"{counts['open']} awaiting author")
    return "\n".join(out + ["", "_" + " · ".join(footer) + "._"])


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
        out.append(f"{title} · `{_loc(state, t, full=True)}`  ({WORD['draft']})")
        if t.get("draft"):
            out += ["", t["draft"]]
        elif t.get("note"):
            out += ["", t["note"]]
        return "\n".join(out).strip()
    for i, th in enumerate(t["thread_ids"]):
        x = state["threads"].get(th, {})
        path = f"{x.get('file')}:{x.get('line') or ''}"
        if i == 0:
            title = f"**{TOPIC_ICON} {t['id']}" + (f" — {summ}**" if summ else "**")
            out.append(f"{title} · `{path}`")
        else:
            out.append(f"`{path}`")
        if x.get("url"):
            out.append(x["url"])
        if x.get("resolved"):
            out.append("_(thread resolved by the author — still needs your ack)_")
        out += ["", _note_md(x.get("author"), x.get("body"))]
        if x.get("note_count", 1) > 1:
            skipped = x["note_count"] - 2
            if skipped > 0:
                out += ["", f"_… {skipped} more …_"]
            out += ["", _note_md(x.get("last_author"), x.get("last_body"))]
        out.append("")
    return "\n".join(out).strip()


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
            out.append(f"[{t['id']}] {os.path.basename(x.get('file') or '')}:"
                       f"{x.get('line') or ''}  "
                       f"(last: {first_name(x.get('last_author'))}"
                       f"{', resolved' if x.get('resolved') else ''})  {x.get('url')}")
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


# ---------------------------------------------------------------- head


def head_report(state, ctx, iid):
    cur = mr_head(ctx, iid)
    last = state.get("last_reviewed_head")
    if not last:
        return f"branch tip: `{(cur or '')[:12]}` (no reviewed baseline yet)"
    if cur == last:
        return f"no push since your last review (tip `{(cur or '')[:12]}`)"
    web = state.get("mr_web_url") or ctx["web"] + f"/-/merge_requests/{iid}"
    url = f"{web}/diffs?start_sha={last}"
    return ("**author pushed since your last review** — compare "
            f"`{last[:12]}` → `{(cur or '')[:12]}`\n{url}")


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


# ---------------------------------------------------------------- driver


def resolve_state(args):
    ctx = context()
    mr = None
    iid = getattr(args, "iid", None)
    if iid is None:
        mr = mr_view()
        iid = mr["iid"]
    path = state_file(ctx["slug"], iid)
    state = load(path)
    if state is None:
        mr = mr or mr_view()
        state = new_state(ctx, mr)
    elif mr:
        state.update(mr_web_url=mr.get("web_url"), title=mr.get("title"),
                     author=mr_author(mr))
    if not state.get("author"):                  # self-heal older state files
        state["author"] = mr_author(mr_object(ctx, iid))
    return ctx, iid, path, state


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")

    for name in ("sync", "todo", "present", "bodies", "candidates",
                 "head", "set-head", "path"):
        sub.add_parser(name).add_argument("--iid", type=int)

    sub.add_parser("worktree").add_argument("--set", dest="set_path")

    pq = sub.add_parser("quote")
    pq.add_argument("topic")
    pq.add_argument("--iid", type=int)

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

    # worktree is repo-level (no MR needed)
    if cmd == "worktree":
        ctx = context()
        if args.set_path:
            set_worktree(ctx["slug"], args.set_path)
        print(get_worktree(ctx["slug"]) or "")
        return

    ctx, iid, path, state = resolve_state(args)
    me = current_user()
    if me is None and cmd in ("sync", "todo", "present", "candidates"):
        print("warning: could not determine your glab username — needs-ack and "
              "candidates may be wrong (is `glab auth status` OK?)", file=sys.stderr)

    if cmd == "path":
        print(path)
    elif cmd == "sync":
        sync(state, fetch_threads(ctx, iid, me))
        save(path, state)
        print(render_table(state, "all"))
        print("\n_" + head_report(state, ctx, iid).splitlines()[0] + "_")
    elif cmd == "todo":
        sync(state, fetch_threads(ctx, iid, me))
        save(path, state)
        print(render_table(state, "mine"))
    elif cmd == "present":
        sync(state, fetch_threads(ctx, iid, me))
        save(path, state)
        print(render_present(state))
    elif cmd == "bodies":
        print(render_bodies(state))
    elif cmd == "candidates":
        sync(state, fetch_threads(ctx, iid, me))
        save(path, state)
        print(render_candidates(state, me))
    elif cmd == "quote":
        print(render_quote(state, args.topic))
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
