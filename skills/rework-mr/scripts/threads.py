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
  quote <t>   render a topic's whole thread — original + every reply  (no fetch)
  url <t>     direct URL(s) to the topic's thread (to click & post)  (no fetch)
  reply-view <t>  thread + your drafted reply (from reply-<t>.md) + URL, one paste  (no fetch)
  set <t> …   update a topic's fields (summary, decision, plan, start-sha, diff-url)
  merge <into> <o…>   fold other topics' threads into <into>
  path        print the state-file path
  check-handles   internal — used by guard-reply.sh, no MR context needed
"""
import argparse
import json
import os
import re
import sys

from _gl import api, context, current_user, die, mr_view, web_base

# internal topic handles (t5, t6, t10 …) — must never reach a GitLab comment.
# Single source of truth: guard-reply.sh shells out to `check-handles` below
# instead of reimplementing this regex in bash, so the two can't drift apart.
HANDLE_RE = re.compile(r"\bt[0-9]+\b")


def find_handles(text):
    return sorted(set(HANDLE_RE.findall(text or "")))


STATE_ROOT = os.path.expanduser("~/.claude/rework-mr")
TOPIC_ICON = "◈"
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


def tref(tid):
    """A topic id as the user reads it — always carrying the topic icon, so `t3` never
    turns up bare in rendered output and is never mistaken for a GitLab thread id.
    (Mirrors review-mr's findings.py.)

    Deliberately NOT used for: CLI examples (`set t3 --state waiting` must stay
    copy-pasteable), `die()` diagnostics about a topic that does not exist, and
    thread/discussion ids, which are not topics.
    """
    return f"{TOPIC_ICON} {tid}"


def first_name(name):
    """'Doe, Jane - AB12345' -> 'Jane'; 'Jane Doe' -> 'Jane'."""
    n = (name or "").strip()
    if " - " in n:                      # strip trailing " - <ACCOUNT-ID>"
        n = n.rsplit(" - ", 1)[0].strip()
    if "," in n:                        # "Lastname, Firstname"
        n = n.split(",", 1)[1].strip()
    parts = n.split()
    return parts[0] if parts else (name or "")


def state_file(slug, iid):
    d = os.path.join(STATE_ROOT, f"{slug}--mr{iid}")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "topics.json")


def load(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return None


def save(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def new_state(ctx, mr):
    return {
        "project": ctx["path"], "slug": ctx["slug"], "iid": mr["iid"],
        "mr_web_url": mr.get("web_url"), "title": mr.get("title"),
        "threads": {}, "topics": [],
    }


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
        out[d["id"]] = {
            "author": (first.get("author") or {}).get("name")
            or (first.get("author") or {}).get("username"),
            "file": pos.get("new_path") or pos.get("old_path"),
            "line": pos.get("new_line") or pos.get("old_line"),
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


def topic_for(state, tid):
    return next((t for t in state["topics"] if t["id"] == tid), None)


def next_tid(state):
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
    'waiting' when semantically classified via `set --state waiting`."""
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
    keep = ("author", "file", "line", "body", "resolved", "url",
            "note_count", "last_author", "last_body", "awaiting", "notes")
    for tid, rec in live.items():
        if tid in state["threads"]:
            state["threads"][tid].update({k: rec[k] for k in keep})
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


def short_summary(state, t, width=72):
    text = t.get("summary")
    if not text:
        thr = [state["threads"].get(x, {}) for x in t["thread_ids"]]
        text = thr[0].get("body") if thr else ""
    text = " ".join((text or "").split())
    return text[: width - 1] + "…" if len(text) > width else text


def _num(tid):
    return int("".join(c for c in tid if c.isdigit()) or 0)


def _rows(state, scope, show_done=False):
    topics = state["topics"]
    st = {t["id"]: topic_status(state, t) for t in topics}
    counts = {s: sum(1 for t in topics if st[t["id"]] == s) for s in GLYPH}
    ordered = sorted(topics, key=lambda t: (STATUS_ORDER[st[t["id"]]], _num(t["id"])))
    keep = {"open", "reply_pending"} if scope == "mine" else \
           ({"open", "reply_pending", "waiting"} | ({"done"} if show_done else set()))
    return st, counts, [t for t in ordered if st[t["id"]] in keep]


def _loc(state, t):
    thr = [state["threads"].get(x, {}) for x in t["thread_ids"]]
    loc = next((f"{os.path.basename(x['file'])}:{x.get('line') or ''}"
                for x in thr if x.get("file")), "")
    n = len(t["thread_ids"])
    return loc + (f" (+{n - 1})" if n > 1 else "")


def render_table(state, scope="all", show_done=False):
    st, counts, shown = _rows(state, scope, show_done)
    out = [f"**MR !{state['iid']}** — {state.get('title') or ''}", ""]
    if shown:
        out += ["| Status | Topic | Location | Summary |", "|---|---|---|---|"]
        for t in shown:
            tid = t["id"]
            s = st[tid]
            summ = short_summary(state, t).replace("|", "\\|")
            out.append(f"| {GLYPH[s]} {WORD[s]} | {tref(f'**{tid}**')} "
                       f"| `{_loc(state, t)}` | {summ} |")
    else:
        out.append("_✓ nothing needs you — open threads are waiting on the reviewer_"
                   if scope == "mine" else "_✓ no open threads_")
    footer = [f"{counts['done']} of {len(state['topics'])} topics done"]
    if counts["reply_pending"]:
        footer.append(f"{counts['reply_pending']} pushed, reply pending")
    if counts["waiting"]:
        footer.append(f"{counts['waiting']} waiting for reply")
    return "\n".join(out + ["", "_" + " · ".join(footer) + "._"])


def _note_md(name, body):
    lines = (body or "").strip().splitlines() or [""]
    quoted = "\n".join("> " + ln for ln in lines)
    return f"> **{first_name(name)}**\n>\n{quoted}"


def render_quote(state, tid):
    t = topic_for(state, tid) or die(f"no topic {tid}")
    summ = t.get("summary") or ""
    out = []
    for i, th in enumerate(t["thread_ids"]):
        x = state["threads"].get(th, {})
        path = f"{x.get('file')}:{x.get('line') or ''}"
        if i == 0:
            title = f"**{tref(t['id'])}" + (f" — {summ}**" if summ else "**")
            out.append(f"{title} · `{path}`")
        else:
            out.append(f"`{path}`")
        if x.get("url"):
            out.append(x["url"])
        notes = x.get("notes")
        if notes:                               # whole thread, in order
            for n in notes:
                out += ["", _note_md(n.get("author"), n.get("body"))]
        else:                                   # pre-`notes` state: first + last only
            out += ["", _note_md(x.get("author"), x.get("body"))]
            if x.get("note_count", 1) > 1:
                skipped = x["note_count"] - 2
                if skipped > 0:
                    out += ["", f"_… {skipped} more …_"]
                out += ["", _note_md(x.get("last_author"), x.get("last_body"))]
        out.append("")
    return "\n".join(out).strip()


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


def render_reply_view(state, tid, body):
    """The whole reply block, as ONE paste so none of its parts can be dropped:
    the full thread (original + every reply), then the drafted body as a `> `
    blockquote (matches the thread's rendering), then the thread URL, then the
    c/p/n prompt. `body` is the raw draft (what gets posted); callers guard it
    for internal topic handles first."""
    quoted = "\n".join("> " + ln for ln in (body or "").rstrip("\n").splitlines())
    return "\n".join([
        render_quote(state, tid),
        "", "**Draft reply:**", "",
        quoted,
        "", f"Thread (to post on): {render_url(state, tid)}",
        "", "**`c`** copy to clipboard · **`p`** post on GitLab · "
        "**`n`** next topic (already replied/resolved) · "
        "or just type your thoughts to refine it.",
    ])


def first_open(state):
    opens = [t for t in state["topics"] if topic_status(state, t) == "open"]
    return min(opens, key=lambda t: _num(t["id"])) if opens else None


def render_present(state):
    """The opener the user should see: overview table + the first open topic's
    reviewer comment, ready to reproduce in one reply."""
    parts = [render_table(state, "all")]
    t = first_open(state)
    if t:
        parts += ["\n---\n", render_quote(state, t["id"])]
    return "\n".join(parts)


def render_plans(state):
    """Recorded decisions/plans for the open topics — for resuming after a break
    or a fresh session, so you don't re-grill what's already decided."""
    out = []
    for t in sorted(state["topics"], key=lambda t: _num(t["id"])):
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
    judge status (did the author actually address it, or just acknowledge?)."""
    out = []
    for t in state["topics"]:
        if topic_status(state, t) == "done":
            continue
        for th in t["thread_ids"]:
            x = state["threads"].get(th, {})
            out.append(f"[{tref(t['id'])}] {os.path.basename(x.get('file') or '')}:"
                       f"{x.get('line') or ''}  (last spoke: {first_name(x.get('last_author'))})"
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
    path = state_file(ctx["slug"], iid)
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
    ps = sub.add_parser("set")
    ps.add_argument("topic")
    ps.add_argument("--iid", type=int)
    ps.add_argument("--state", choices=("open", "waiting"))
    for f in ("summary", "decision", "plan", "start-sha", "diff-url"):
        ps.add_argument(f"--{f}")
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

    ctx, iid, path, state = resolve_state(args)

    if cmd == "path":
        print(path)
    elif cmd == "quote":
        print(render_quote(state, args.topic))
    elif cmd == "url":
        print(render_url(state, args.topic))
    elif cmd == "reply-view":
        topic_for(state, args.topic) or die(f"no topic {args.topic}")
        f = os.path.join(os.path.dirname(path), f"reply-{args.topic}.md")
        if not os.path.exists(f):
            die(f"no draft yet — write the reply body to {f} first")
        with open(f) as fh:
            body = fh.read()
        hits = find_handles(body)
        if hits:
            die(f"draft has internal topic handle(s): {' '.join(hits)} — reword "
                f"(link the other thread via `url <other-t>`), then re-run")
        print(render_reply_view(state, args.topic, body))
    elif cmd == "present":
        print(render_present(state))
    elif cmd == "bodies":
        print(render_bodies(state))
    elif cmd == "plans":
        print(render_plans(state))
    elif cmd in ("sync", "todo"):
        sync(state, fetch_threads(ctx, iid, current_user()))
        save(path, state)
        print(render_table(state, "mine" if cmd == "todo" else "all",
                           show_done=getattr(args, "all", False)))
    elif cmd == "set":
        t = topic_for(state, args.topic) or die(f"no topic {args.topic}")
        fld = {"start-sha": "start_sha", "diff-url": "diff_url"}
        for f in ("summary", "state", "decision", "plan", "start-sha", "diff-url"):
            v = getattr(args, f.replace("-", "_"))
            if v is not None:
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
