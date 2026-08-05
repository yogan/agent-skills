#!/usr/bin/env python3
"""Stop hook for the review-mr skill: enforce that script output is actually pasted
into the visible message, not paraphrased.

Same failure, same cure as rework-mr's hook (see that file for the full rationale):
Claude Code collapses tool output, so the user's only window is the chat message. The
skill runs `findings.py resume`/`present`/`quote`/... and is told to paste the result
verbatim; the model instead summarises it — "picking up where we left off, topic t2 needs
you" — leaving the user with a topic id and no table, no location, no code.

Documentation could not fix this. It was tried three times: a "paste verbatim" rule at
the top of SKILL.md, a single `resume` command so there was no multi-step sequence to
skip, and finally an explicit rule 0 with the failure spelled out. The model still ran
the command and paraphrased its output. So it is enforced here instead.

Gated commands (GATES below): `findings.py resume` (the session opener), `present`,
`todo`, `quote` (a topic's thread/draft + code), `updates` (pushes since baseline) and
`diff` (what the author changed for one topic). `sync` is deliberately NOT gated — it is
run internally before several other commands, so gating it would block turns where it
was never meant to be the answer.

Contract (Claude Code Stop hook):
  stdin  — JSON with `transcript_path` and `stop_hook_active`.
  stdout — `{"decision":"block","reason":"…"}` to force a retry, or nothing to allow.
It fails OPEN: any error → allow the stop (never wedge a session).
"""
import json
import re
import sys
import time

GATES = [
    {
        "key": "resume",
        "cmd_re": re.compile(r"findings\.py\s+resume\b"),
        "signature": ("**MR !", "| State |"),
        "reason": (
            "You ran `findings.py resume` (the session opener) but your message did not "
            "include its output. Tool output is COLLAPSED — the user cannot see it, so a "
            "reply that names a topic like `t2` without the table above it tells them "
            "nothing. Re-send your message STARTING with the full `resume` output pasted "
            "verbatim: the pushes-since-baseline line, the MR header, the overview table, "
            "and the topic that needs them. Your own prose comes after it, not instead."
        ),
    },
    {
        "key": "present",
        "cmd_re": re.compile(r"findings\.py\s+present\b"),
        "signature": ("**MR !", "| State |"),
        "reason": (
            "You ran `findings.py present` but your message did not include its output. "
            "Re-send your message starting with the FULL output pasted verbatim — the MR "
            "header, the overview table, and the first topic that needs the user."
        ),
    },
    {
        "key": "todo",
        "cmd_re": re.compile(r"findings\.py\s+todo\b"),
        "signature": ("**MR !",),
        "reason": (
            "You ran `findings.py todo` but your message did not include its output. "
            "Re-send it with the FULL table pasted verbatim, not a paraphrase."
        ),
    },
    {
        "key": "quote",
        # `set <t> --draft` echoes the refreshed render_quote output, so it produces the
        # same kind of block. Same key on purpose: only the NEWEST such block has to be
        # pasted (see the per-key reduction in main()), and after a draft edit that is
        # set's echo, not the now-stale earlier `quote`.
        "cmd_re": re.compile(r"findings\.py\s+quote\b"
                             r"|findings\.py\s+set\b[^\n]*--draft"),
        "signature": ("\u25c8",),
        "reason": (
            "You ran `findings.py quote` but your message did not include its output. "
            "Re-send your message starting with the FULL quote block pasted verbatim: the "
            "topic header with its `file:line`, the code in question, and the draft or "
            "thread notes. Do not lead with your own verdict — the user cannot tell which "
            "finding you mean or see the code you are asserting things about."
        ),
    },
    {
        "key": "updates",
        "cmd_re": re.compile(r"findings\.py\s+updates\b"),
        "signature": ("since your last review",),
        "reason": (
            "You ran `findings.py updates` but your message did not include its output. "
            "Re-send it with the FULL block pasted verbatim — every `- **push N:**` line "
            "with its nested detail — then add your one-line summary per push as a further "
            "sub-bullet. Do not collapse the pushes into prose."
        ),
    },
    {
        "key": "diff",
        "cmd_re": re.compile(r"findings\.py\s+diff\b"),
        "signature": ("\u25c8",),
        "reason": (
            "You ran `findings.py diff` to show what the author changed for this topic, "
            "but your message did not include its output. Re-send it with the FULL block "
            "pasted verbatim — the compare URL and the fenced diff — so the user can see "
            "the change instead of taking your word that it was addressed."
        ),
    },
]


# Patterns that must NEVER appear in a visible message, regardless of which command
# ran. Unlike GATES (which need a gated command to have produced output this turn), these
# catch the model COMPOSING a block itself instead of pasting the rendered one — for which
# no command runs at all, so no gate can fire.
FORBIDDEN = [
    {
        "key": "raw-suggestion-fence",
        # A fence at the START of a line. render_quote re-fences suggestion blocks to the
        # file's language for display, so a raw one can only come from the model writing
        # the block out itself. (quote's own trailing note mentions ```suggestion
        # mid-sentence, which is why this is anchored to a line start.)
        # `[>\s]*` not `\s*`: the model wrote "> ```suggestion" inside a blockquote once,
        # and a whitespace-only prefix class let it straight through.
        "text_re": re.compile(r"^[>\s]*```suggestion", re.M),
        "reason": (
            "Your message contains a raw ```suggestion fence, which means you wrote the "
            "draft block out yourself instead of pasting the rendered one. That block is "
            "shown unhighlighted, and the user cannot read the code they are approving. "
            "Re-send your message using the output of `python3 $SD/findings.py quote <t> "
            "--iid <n>` pasted verbatim (run it again if you have since changed the "
            "draft — `set <t> --draft` also prints the refreshed view). The ```suggestion "
            "fence belongs only in the PASTE PAYLOAD from `draft <t>` / the clipboard, "
            "never in what you show."
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


def _missing_lines(result, shown):
    """Lines of `result` that never appear in the visible message.

    Deliberately line-based, not a contiguous-substring match. The skill *asks* the model
    to interleave its own text with pasted output — `updates` is pasted and then annotated
    with a summary sub-bullet per push — so requiring one unbroken block flagged correct,
    fully-pasted messages: every line was present, just not consecutively.

    Blank and very short lines (table rules, fences, `---`) are ignored: they carry no
    information and appear incidentally in unrelated text.
    """
    missing = []
    for line in result.strip().splitlines():
        stripped = line.strip()
        if len(stripped) < 8 or set(stripped) <= set("-|` "):
            continue
        if stripped not in shown:
            missing.append(stripped)
    return missing


def _allow():
    sys.exit(0)


def _block(reason):
    print(json.dumps({"decision": "block", "reason": reason}))
    sys.exit(0)


def _load(path):
    try:
        with open(path) as f:
            return [json.loads(ln) for ln in f if ln.strip()]
    except Exception:                      # noqa: BLE001 — unreadable → fail open
        return None


def _violation(path):
    """Reason to block, or None. Re-readable so it can be retried — see main()."""
    rows = _load(path)
    if rows is None:
        return None

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
    if not shown.strip():
        # No assistant text in this turn yet. Either there is genuinely nothing to
        # check, or the message has not reached the transcript file — the hook can fire
        # within ~300 ms of the message being written. Judging now would report every
        # line as missing.
        return None

    # For every gated command that actually ran and produced real (non-error)
    # output this turn, require ITS OWN output text to reappear verbatim in the
    # model's visible message — not just the signature phrases, so a message
    # that only *talks about* the markers without actually pasting the block
    # can't satisfy the check. Only enforce a successful run; an errored one
    # (no matching signature) means the model is mid-fix — leave it alone.
    # Keep only the LAST invocation per gate key. A topic can legitimately be re-rendered
    # several times in one turn — quote, then an anchor fix, then `set --draft` — and each
    # rendering supersedes the previous one. Demanding every one of them be pasted made a
    # correct message (which pasted the newest block) get blocked for omitting a stale one.
    # dict preserves transcript order, so the last write per key wins.
    last_per_key = {}
    for uid, gate in matched.items():
        last_per_key[gate["key"]] = uid
    for gate_key, uid in last_per_key.items():
        gate = matched[uid]
        result = result_by_id.get(uid, "")
        if not all(s in result for s in gate["signature"]):
            continue
        # Tolerate ONE dropped line. Observed: a message that pasted the whole overview
        # — table, counts, footer — but swapped the leading status line for its own
        # preamble. Blocking that is noise. Two or more missing lines still means a
        # section went astray (a paraphrase misses nearly all of them).
        if result.strip() and len(_missing_lines(result, shown)) > 1:
            return gate["reason"]

    # Command-independent checks: a hand-written block needs no command to run, so no
    # gate above can see it.
    for bad in FORBIDDEN:
        if bad["text_re"].search(shown):
            return bad["reason"]

    return None


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

    # Re-read before blocking. The transcript is written asynchronously: a block was
    # observed 348 ms after a 2384-character message was produced, and replaying that
    # same transcript afterwards allowed it — the hook had simply read the file before
    # the message landed. Retry with a widening delay and bail out as soon as it clears;
    # only a turn that is genuinely about to be blocked ever pays the wait.
    reason = _violation(path)
    for delay in (0.25, 0.5, 1.0):
        if not reason:
            break
        time.sleep(delay)
        reason = _violation(path)
    if reason:
        _block(reason)
    _allow()


if __name__ == "__main__":
    main()
