"""A comment the user posted by their own hand, checked against the draft it came from.

Both review skills keep a draft the user may post themselves, and often reword. The agent
compares what went up with the draft; where it no longer says what it should, it stores its
verdict and a corrected version of the posted text. The card then shows both, the changes
marked word by word, and the user's press edits the posted note on GitLab to the corrected
version. That edit is the user's press alone, never the agent's move.

What differs between the skills is a parameter: which of the user's notes is the posted one
(review-mr's comment opens its thread, rework-mr's reply is the newest) and the word the card
uses for it.
"""
import datetime
import re

from lib import ui, worddiff

NOTE_ID = re.compile(r"#note_(\d+)$")

# The proposed edit's heading and the press that applies it: md-text_box_edit_outline.
EDIT_ICON = "\U000f1a7d"


def _time(at):
    try:
        return datetime.datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    except ValueError:
        return None


def is_after(at, since):
    """Whether a note made `at` came after `since` (both ISO times); any note counts where
    no time was kept, and none whose time cannot be read."""
    if not since:
        return True
    a, b = _time(at), _time(since)
    return bool(a and b and a > b)


def own_note(state, t, last, since=None):
    """(discussion id, note) of the user's own comment on `t`'s threads, posted after
    `since` (when the draft was written): the newest with `last`, else the first. (None,
    None) where there is none on GitLab yet. An older comment of theirs is never the one: a
    check pressed before posting must not compare, nor correct, what they said before."""
    hits = [(th, n) for th in t["thread_ids"]
            for n in (state["threads"].get(th) or {}).get("notes") or []
            if n.get("mine") and is_after(n.get("at"), since)]
    if not hits:
        return None, None
    return hits[-1] if last else hits[0]


def store_differs(state, t, verdict, fix, last, since=None):
    """Store the agent's verdict on the posted comment and its corrected version. Raises
    ValueError where there is nothing posted to correct."""
    disc, note = own_note(state, t, last, since)
    nid = NOTE_ID.search((note or {}).get("url") or "")
    if not (disc and nid):
        raise ValueError("nothing of yours is posted on this topic yet")
    if not (fix or "").strip():
        raise ValueError("--differs takes the corrected text on --fix")
    t["post_differs"] = {"verdict": verdict.strip(), "fix": fix.strip() + "\n",
                         "discussion": disc, "note": nid.group(1), "url": note.get("url"),
                         "posted": (note.get("body") or "").strip()}


def card_md(t, word):
    """The card's body under the topic's title: the verdict, then the proposed edit."""
    d = t["post_differs"]
    return "\n".join([
        ui.heading_md(ui.VERDICT_ICON, f"Your posted {word} differs"), "", d["verdict"], "",
        ui.heading_md(EDIT_ICON, "Proposed edit"), "", worddiff.block(d["posted"], d["fix"]),
    ])


def apply(api_post, ctx, iid, t):
    """Edit the posted note on GitLab to the corrected version; returns the note's link.
    Raises ValueError where no correction is stored."""
    d = t.get("post_differs")
    if not d:
        raise ValueError("no corrected version is stored for this topic")
    api_post(f"projects/{ctx['enc']}/merge_requests/{iid}/discussions/{d['discussion']}"
             f"/notes/{d['note']}", {"body": d["fix"]}, method="PUT")
    return d.get("url")
