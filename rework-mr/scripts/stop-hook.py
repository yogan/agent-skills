#!/usr/bin/env python3
"""Stop hook for the rework-mr skill: enforce that a reply-view block is actually
pasted into the visible message.

Claude Code collapses tool output — the user never sees it. During a reply, the
skill runs `threads.py reply-view <t>`, which prints the whole thread + the
drafted reply + the thread URL + the `c`/`p`/`n` prompt. The model must paste
that block verbatim as its message, but repeatedly drops it and echoes only the
prompt line. This hook fires when the model tries to end its turn: if
`reply-view` ran this turn but its output is NOT present in the model's visible
text, it blocks the stop and tells the model to paste the block.

Contract (Claude Code Stop hook):
  stdin  — JSON with `transcript_path` and `stop_hook_active`.
  stdout — `{"decision":"block","reason":"…"}` to force a retry, or nothing to allow.
It fails OPEN: any error → allow the stop (never wedge a session).

Loop guard: `stop_hook_active` is true when we're already inside a hook-forced
continuation, so we block at most once per reply and never spin.

Scope: only acts on a turn where `reply-view` actually ran AND produced a block —
every other turn (and every non-rework session) passes straight through.

Detection is marker-free (nothing extra is added to the pasted block, so the user
sees a clean reply): a turn qualifies when a Bash `reply-view` invocation's OWN
paired tool result (matched by tool_use id) carries the block's structure —
success, not an error. Requiring the literal `threads.py reply-view` subcommand
in the command text (not just the bare word "reply-view" anywhere) and then the
actual, topic-specific output text to reappear verbatim in the model's message
is what keeps this from false-firing when the skill's own *source* is merely
read or grepped (this file's REASON string below, and SKILL.md/REFERENCE.md,
all mention "reply-view" and the block's marker phrases too) — and from
false-passing on a message that only talks about the markers without actually
pasting the block.
"""
import json
import re
import sys

# `threads.py reply-view` invocation in a Bash command, as an actual subcommand
# call — not just the bare word "reply-view" anywhere in the command text. This
# skill's own docs/source mention "reply-view" a lot (including this file's own
# REASON string below), so a loose substring match false-fires when a turn
# merely greps or cats this file (or SKILL.md/REFERENCE.md) with wide context.
REPLYVIEW_CMD_RE = re.compile(r"threads\.py\s+reply-view\b")
# Structural lines every successful reply-view block carries — used both to
# confirm the run succeeded (present in its tool-result output) and to prove the
# model pasted it (present in its visible text). Kept independent of the prompt
# wording so tweaks to the prompt don't break detection.
BLOCK_SIGNATURE = ("**Draft reply:**", "Thread (to post on):", "**`c`** copy to clipboard")

REASON = (
    "You ran `threads.py reply-view` for this reply but your message did not "
    "include its output. Tool output is COLLAPSED — the user cannot see it. "
    "Re-send your message as the FULL reply-view block pasted verbatim: the "
    "thread (reviewer's blockquoted note + every reply), the **Draft reply:** "
    "block, the Thread (to post on): URL, and the final c/p/n prompt line. "
    "Do not summarise it to just the prompt."
)


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
        c = " ".join(x.get("text", "") for x in c if isinstance(x, dict))
    return c or ""


def _allow():
    sys.exit(0)


def _block():
    print(json.dumps({"decision": "block", "reason": REASON}))
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
    # `start` jumps past a reply-view call that ran earlier in the same turn.)
    # Scanned backward with an early break: only the current turn's tail is
    # ever used, so there's no need to walk the whole (ever-growing) transcript.
    start = 0
    for i in range(len(rows) - 1, -1, -1):
        r = rows[i]
        if (r.get("type") == "user" and not r.get("isMeta")
                and _has_text(r.get("message", {}).get("content"))):
            start = i
            break

    # Pair each reply-view Bash invocation with ITS OWN result: a real block ran
    # iff some `reply-view` command's paired tool result contains the block. This
    # avoids false positives from merely *reading* the skill's source (which
    # literally contains these strings) or grepping "reply-view" — only an actual
    # invocation that printed a block counts.
    rv_use_ids = set()               # Bash tool_use ids whose command ran reply-view
    result_by_id = {}                # tool_use_id -> result text
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
                if REPLYVIEW_CMD_RE.search((b.get("input") or {}).get("command", "") or ""):
                    rv_use_ids.add(b.get("id"))
            elif t == "tool_result":
                result_by_id[b.get("tool_use_id")] = _result_text(b)
            elif t == "text" and role == "assistant":
                assistant_text.append(b.get("text", "") or "")

    # The actual reply-view output (not just whether it LOOKS like it ran) —
    # this is what must show up verbatim in the model's message. Requiring the
    # real, topic-specific text (not just the two/three generic marker phrases)
    # also means a message that only *talks about* the markers without actually
    # pasting the block (e.g. "I showed the Draft reply and Thread URL above")
    # can't satisfy the check.
    produced_text = next((result_by_id[uid] for uid in rv_use_ids
                          if all(s in result_by_id.get(uid, "") for s in BLOCK_SIGNATURE)), None)
    # Only enforce a successful reply-view run; an errored one (no block) means
    # the model is mid-fix — leave it alone.
    if produced_text is None:
        _allow()

    shown = "\n".join(assistant_text)
    if produced_text.strip() and produced_text.strip() in shown:
        _allow()                     # block was pasted — good

    _block()                         # ran reply-view, didn't paste it → force redo


if __name__ == "__main__":
    main()
