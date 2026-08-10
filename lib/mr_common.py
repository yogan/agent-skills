"""Small rendering/identity/state helpers shared between review-mr's findings.py and
rework-mr's threads.py — genuinely identical apart from a trivial parameter, unlike the
two skills' state machines and rendering logic, which differ for real domain reasons
and stay duplicated (see CLAUDE.md's "Sharing vs. duplication")."""
import json
import os

TOPIC_ICON = "◈"


def load(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return None


def save(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def topic_for(state, tid):
    return next((t for t in state["topics"] if t["id"] == tid), None)


def num(tid):
    """The numeric part of a topic id (`t3` -> 3) — for sorting, and for picking the
    next free id."""
    return int("".join(c for c in tid if c.isdigit()) or 0)


def tref(tid):
    """A topic id as the user reads it — always carrying the topic icon, so `t3` never
    turns up bare in rendered output and is never mistaken for a GitLab thread id.

    Deliberately NOT used for: CLI examples (which must stay copy-pasteable), `die()`
    diagnostics about a topic that does not exist, and thread/discussion ids, which are
    not topics.
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


def short_summary(state, t, width=64):
    text = t.get("summary")
    if not text:
        thr = [state["threads"].get(x, {}) for x in t["thread_ids"]]
        text = thr[0].get("body") if thr else ""
    text = " ".join((text or "").split())
    return text[: width - 1] + "…" if len(text) > width else text


def state_file(root, slug, iid, filename):
    """`root/<slug>--mr<iid>/<filename>`, creating the directory if needed. `root` and
    `filename` are per-skill (different state roots, different file names); the shape
    is what's shared."""
    d = os.path.join(root, f"{slug}--mr{iid}")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, filename)
