#!/usr/bin/env python3
"""Stop hook for the rework-mr skill: enforce that certain script outputs are
actually pasted into the visible message, not dropped.

Claude Code collapses tool output — the user never sees it. At several points
the skill runs a command that prints something (an overview table, a quoted
reviewer comment, a drafted reply, a change illustration) and instructs the
model to paste it verbatim as its whole message. The model repeatedly drops
it instead: it decides what to paste, makes another tool call (research, a
Read) before writing the message, and that intervening call pushes the
pasted content out of mind — the reply ends up starting with the model's own
prose, or jumping straight to the trailing question, with the promised block
never actually shown. This hook fires when the model tries to end its turn:
for every gated command that ran and produced real output this turn, if that
output is NOT present in the model's visible text, it blocks the stop and
tells the model to paste it.

Gated commands (see GATES below): `threads.py present` (the opener overview +
first topic's comment), `threads.py todo` (the status-only answer),
`threads.py quote` (moving to the next topic), `diff-view.sh` (the working
diff shown before the fixup+push ACK), `threads.py reply-view` (the reply
block), and `change-preview.sh` (a trivial-topic's proposed-change
illustration). One shared mechanism gates all six instead of only the first
one anyone happened to notice. (`threads.py sync` is deliberately excluded —
see the comment above GATES.)

Contract (Claude Code Stop hook):
  stdin  — JSON with `transcript_path` and `stop_hook_active`.
  stdout — `{"decision":"block","reason":"…"}` to force a retry, or nothing to allow.
It fails OPEN: any error → allow the stop (never wedge a session).

Loop guard: `stop_hook_active` is true when we're already inside a hook-forced
continuation, so we block at most once per reply and never spin.

Scope: only acts on a turn where a gated command actually ran AND produced
real output — every other turn (and every non-rework session) passes straight
through.

Detection is marker-free (nothing extra is added to the pasted block, so the
user sees a clean reply): a turn qualifies when a gated Bash invocation's OWN
paired tool result (matched by tool_use id) carries that command's output
signature — success, not an error. Requiring the literal subcommand/script
name in the command text (not just a bare keyword anywhere) and then the
actual, run-specific output text to reappear verbatim in the model's message
is what keeps this from false-firing when the skill's own *source* is merely
read or grepped (this file, SKILL.md, REFERENCE.md all mention these command
names and phrases too) — and from false-passing on a message that only talks
about the markers without actually pasting the block.
"""
import json
import re
import sys

# Every gated command whose output must be pasted verbatim. `cmd_re` matches
# the literal invocation in a Bash command (not a bare keyword — see module
# docstring); `signature` are literal strings a *successful* (non-error) run
# of that command always includes, used only to tell a real block apart from
# an error/empty result — the actual enforcement is the exact-text check
# against the model's visible message, not this signature.
# `sync` is deliberately NOT gated even though the "status-only" doc path pastes
# its output too: the opener also runs `sync` as silent, intentionally-unshown
# prep (identical command text), so gating it would false-block every opener
# turn demanding a redundant paste of that silent prep call. `todo` has no such
# double-duty — it's only ever the user-facing status answer — so it's safe.
GATES = [
    {
        "key": "present",
        "cmd_re": re.compile(r"threads\.py\s+present\b"),
        "signature": ("**MR !",),
        "reason": (
            "You ran `threads.py present` (the opener) but your message did not "
            "include its output. Tool output is COLLAPSED — the user cannot see it. "
            "Re-send your message starting with the FULL `present` output pasted "
            "verbatim: the MR title line, the overview table, and (if there is one) "
            "the first open topic's blockquoted comment. Do not summarise it or lead "
            "with your own research prose."
        ),
    },
    {
        "key": "todo",
        "cmd_re": re.compile(r"threads\.py\s+todo\b"),
        "signature": ("**MR !",),
        "reason": (
            "You ran `threads.py todo` for a status-only request but your message did "
            "not include its output. Re-send your message with the FULL table pasted "
            "verbatim, not a paraphrase."
        ),
    },
    {
        "key": "quote",
        "cmd_re": re.compile(r"threads\.py\s+quote\b"),
        "signature": ("◈",),
        "reason": (
            "You ran `threads.py quote` (moving to the next topic) but your message "
            "did not include its output. Re-send your message starting with the FULL "
            "quoted comment block pasted verbatim, not your own research prose."
        ),
    },
    {
        "key": "diff-view",
        "cmd_re": re.compile(r"diff-view\.sh\b"),
        "signature": ("**Diff (", "```diff"),
        "reason": (
            "You ran `diff-view.sh` to show this topic's working diff but your "
            "message did not include its output. Re-send your message with the FULL "
            "diff-view block pasted verbatim — the fenced diff and the final 'ACK to "
            "fix up and push?' — before asking for that ACK any other way. Never "
            "commit, push, or ask for the ACK without the diff actually shown."
        ),
    },
    {
        "key": "reply-view",
        "cmd_re": re.compile(r"threads\.py\s+reply-view\b"),
        "signature": ("**Draft reply:**", "Thread (to post on):", "**`c`** copy to clipboard"),
        "reason": (
            "You ran `threads.py reply-view` for this reply but your message did not "
            "include its output. Tool output is COLLAPSED — the user cannot see it. "
            "Re-send your message as the FULL reply-view block pasted verbatim: the "
            "thread (reviewer's blockquoted note + every reply), the **Draft reply:** "
            "block, the Thread (to post on): URL, and the final c/p/n prompt line. "
            "Do not summarise it to just the prompt."
        ),
    },
    {
        "key": "change-preview",
        "cmd_re": re.compile(r"change-preview\.sh\b"),
        "signature": ("**Change (", "```"),
        "reason": (
            "You ran `change-preview.sh` to illustrate a trivial topic's fix but your "
            "message did not include its output. Re-send your message with the FULL "
            "change-preview block pasted verbatim — the fenced illustration and the "
            "final 'Agreed?' — not a description of the change."
        ),
    },
]


def _has_text(content):
    """True if this message carries a text block — used to spot the genuine user
    prompt that starts a turn (tool-result user messages have none)."""
    if isinstance(content, str):
        return bool(content)
    if isinstance(content, list):
        return any(isinstance(b, dict) and b.get("type") == "text" for b in content)
    return False


def _result_text(b):
    c = b.get("content", "")
    if isinstance(c, list):
        return " ".join(x.get("text", "") for x in c if isinstance(x, dict))
    if isinstance(c, str):
        return c
    # Unexpected shape (e.g. a dict) — stringify so the signature/exact-text
    # checks still see searchable text instead of silently degrading to a
    # false-allow (an `in` check against a non-string would just always miss).
    return json.dumps(c) if c else ""


def _allow():
    sys.exit(0)


def _block(reason):
    print(json.dumps({"decision": "block", "reason": reason}))
    sys.exit(0)


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        _allow()

    # already inside a hook-forced retry → let it through, never loop
    if data.get("stop_hook_active"):
        _allow()

    path = data.get("transcript_path")
    if not path:
        _allow()

    try:
        with open(path) as f:
            rows = [json.loads(ln) for ln in f if ln.strip()]
    except Exception:
        _allow()

    # Walk the current turn: from the last genuine user prompt to the end.
    # (tool_result messages are role=user too, so a "genuine" prompt is a
    # user message carrying a text block — that's what starts a turn. Synthetic
    # rows Claude Code injects mid-turn, e.g. isMeta skill-load/system-reminder
    # rows, also carry a text block but aren't a real new turn — skip those, or
    # `start` jumps past a gated call that ran earlier in the same turn.)
    # Scanned backward with an early break: only the current turn's tail is
    # ever used, so there's no need to walk the whole (ever-growing) transcript.
    start = 0
    for i in range(len(rows) - 1, -1, -1):
        r = rows[i]
        if (r.get("type") == "user" and not r.get("isMeta")
                and _has_text(r.get("message", {}).get("content"))):
            start = i
            break

    # Pair each gated Bash invocation with ITS OWN result (matched by tool_use
    # id): a real block ran iff some gated command's paired tool result carries
    # that command's signature. Pairing the command to its own output is what
    # keeps this from false-firing when the skill's *source* is merely read or
    # grepped — only an actual invocation that printed a real block counts.
    matched = {}      # tool_use_id -> gate dict, for Bash calls matching a gate
    result_by_id = {}  # tool_use_id -> result text
    assistant_text = []
    for r in rows[start:]:
        msg = r.get("message", {}) if isinstance(r, dict) else {}
        role = msg.get("role")
        content = msg.get("content")
        if isinstance(content, str):
            if role == "assistant":
                assistant_text.append(content)
            continue
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            t = b.get("type")
            if t == "tool_use" and b.get("name") == "Bash":
                cmd = (b.get("input") or {}).get("command", "") or ""
                for gate in GATES:
                    if gate["cmd_re"].search(cmd):
                        matched[b.get("id")] = gate
                        break
            elif t == "tool_result":
                result_by_id[b.get("tool_use_id")] = _result_text(b)
            elif t == "text" and role == "assistant":
                assistant_text.append(b.get("text", "") or "")

    shown = "\n".join(assistant_text)

    # For every gated command that actually ran and produced real (non-error)
    # output this turn, require ITS OWN output text to reappear verbatim in the
    # model's visible message — not just the signature phrases, so a message
    # that only *talks about* the markers without actually pasting the block
    # can't satisfy the check. Only enforce a successful run; an errored one
    # (no matching signature) means the model is mid-fix — leave it alone.
    for uid, gate in matched.items():
        result = result_by_id.get(uid, "")
        if not all(s in result for s in gate["signature"]):
            continue
        if result.strip() and result.strip() not in shown:
            _block(gate["reason"])

    _allow()


if __name__ == "__main__":
    main()
