"""The current topic: the one being decided right now, kept in the state file.

Both MR skills work one topic at a time, and the paste gate needs to know which one that
is. A command run for any OTHER topic is research — `diff t7` to write a push summary,
`quote t14` to understand a follow-up that points at it — and the user is not asked to
see it. A gate that cannot tell the two apart demands the research be pasted, which pulls
another topic into the middle of the one being decided.

So the producer states it: every view's block manifest carries the topic it is about and
the current topic (see lib/critical_manifest.py), and hooks/paste-gate.py enforces a
topic's block only while that topic is current. Nothing here reads the agent's prose.

How it moves, deterministically:

- It is claimed by the opener (`present`, `resume`) and by the first topic viewed when
  there is none.
- It moves on when its topic stops needing you — acked, posted, marked waiting — to the
  skill's own next topic.
- A jump (`quote <t> --focus`, or drafting for a topic) moves it to that topic, which
  then holds until its status changes to one that does not need you — so a jump to a
  topic that is only waiting on the other side still holds while you discuss it.

The skills differ only in which statuses need you and which topic comes next, so those
are the parameters; everything else is identical, which is why it lives here.
"""

from lib.mr_common import topic_for


class Current:
    """One skill's rules for the current topic.

    `status(state, t)` is the skill's topic status, `needs_you` the statuses that are the
    user's to act on, and `pick_next(state)` the topic the skill's opener would show.
    """

    def __init__(self, status, needs_you, pick_next):
        self.status, self.needs_you, self.pick_next = status, needs_you, pick_next

    def get(self, state):
        """The current topic's id, or None when nothing needs you — resolved and stored
        back, so it moves on the moment its topic stops needing you."""
        t = topic_for(state, state.get("focus"))
        if t:
            now = self.status(state, t)
            if now in self.needs_you or now == state.get("focus_status"):
                return t["id"]
        self._set(state, self.pick_next(state))
        return state["focus"]

    def jump(self, state, tid):
        """Make `tid` current, whatever its status."""
        self._set(state, topic_for(state, tid))

    def view(self, state, tid, want_focus, jump_cmd):
        """Before a topic view renders `tid`: make it current when asked to (or when
        nothing is), and otherwise return the note that says it is research — for the
        agent's stderr, never the user's screen. None when there is nothing to say."""
        cur = self.get(state)
        if want_focus or cur is None:
            self.jump(state, tid)
            return None
        if cur == tid:
            return None
        return (f"note: {tid} is not the current topic ({cur}) — this view is for your "
                f"research and the user need not see it. To move the discussion to "
                f"{tid} (the user asked to, or {cur} is settled), run `{jump_cmd}`.")

    def _set(self, state, t):
        state["focus"] = t["id"] if t else None
        state["focus_status"] = self.status(state, t) if t else None
