#!/usr/bin/env python3
"""Paste-enforcement `Stop` hook: make sure gated script output actually reaches the
user's message instead of being paraphrased, described, or silently dropped.

Claude Code collapses tool output — the user never sees it, the chat message is their
only window. Both MR skills exploit that: a command prints a finished block (an overview
table, a quoted topic with its code, a drafted reply, a diff) and the skill instructs the
model to paste it verbatim as its whole message. The model repeatedly drops it instead:
it decides what to paste, makes one more tool call (a Read, some research) before writing
the message, and that intervening call pushes the block out of mind — the reply opens
with the model's own prose, or jumps straight to the trailing question, with the promised
content never actually shown. The user is left with "topic t2 needs you": no table, no
`file:line`, no code.

Documentation could not fix it. In `review-mr` it was tried three times — a "paste
verbatim" rule at the top of SKILL.md, a single `resume` command so there was no
multi-step sequence to skip, and finally an explicit rule 0 naming the failure. The model
still ran the command and paraphrased its output. So it is enforced here instead.

This file is the shared ENGINE. What to enforce is data: each skill ships a gate spec at
`scripts/paste-gates.json` and the hook is registered once with every installed skill's
spec as an argument:

    python3 ~/.claude/hooks/paste-gate.py \
        ~/.claude/skills/review-mr/scripts/paste-gates.json \
        ~/.claude/skills/rework-mr/scripts/paste-gates.json

A spec that is missing or unreadable (skill not installed) is skipped, so the argument
list may name skills you do not have. Previously each skill carried its own 320-line copy
of this engine and every fix had to be ported by hand between them; the ports kept
missing things, which is why it lives in one place now.

Three rule kinds, all optional per spec:
  gates     — a command ran and produced real output → that output must appear in the
              visible message. The main mechanism.
  forbidden — a pattern that must never appear in a visible message, whatever ran. Catches
              the model COMPOSING a block itself, for which no command runs at all, so no
              gate can see it.
  required  — a pattern that may only appear once a given gate has actually fired this
              turn. The inverse check: the ritual phrase is there, but the command that
              was supposed to produce it never ran.

Contract (Claude Code Stop hook):
  stdin  — JSON with `transcript_path` and `stop_hook_active`.
  stdout — `{"decision":"block","reason":"…"}` to force a retry, or nothing to allow.
It fails OPEN throughout: any error → allow the stop (never wedge a session).

Loop guard: `stop_hook_active` is true when we're already inside a hook-forced
continuation, so we block at most once per reply and never spin.

Scope: only acts on a turn where a gated command actually ran AND produced real output
(or where a forbidden/required pattern hits) — every other turn, and every session that
never touches these skills, passes straight through.

Detection is marker-free (nothing extra is added to the pasted block, so the user sees a
clean reply): a turn qualifies when a gated Bash invocation's OWN paired tool result
(matched by tool_use id) carries that command's output signature — success, not an error.
Requiring the literal subcommand/script name in the command text (not just a bare keyword
anywhere) and then the actual, run-specific output text to reappear verbatim in the
model's message is what keeps this from false-firing when a skill's own *source* is merely
read or grepped (SKILL.md, REFERENCE.md, this file and the specs all mention these command
names and phrases too) — and from false-passing on a message that only talks about the
markers without actually pasting the block.
"""
import difflib
import json
import re
import sys
import time

_FLAGS = {"m": re.M, "i": re.I, "s": re.S}


def _allow():
    sys.exit(0)


def _block(reason):
    print(json.dumps({"decision": "block", "reason": reason}))
    sys.exit(0)


def _compile(pattern, flags=""):
    f = 0
    for ch in flags:
        f |= _FLAGS[ch]                    # unknown flag → KeyError → spec dropped
    return re.compile(pattern, f)


def load_specs(paths):
    """Gate specs from the given JSON files, in argument order.

    A spec that cannot be read or does not parse is skipped silently — that is how "the
    skill is not installed" is expressed, and the hook must not care. A spec that parses
    but has a malformed rule (bad regex, missing key) is dropped WHOLE rather than
    half-loaded: a typo that quietly disables one gate while the others keep firing is
    harder to notice than one that disables the skill's enforcement outright.

    Gate keys are namespaced with the skill name: both skills have a `quote` and a `todo`
    gate, and they must not collide in the per-key reduction below.
    """
    specs = []
    for path in paths:
        try:
            with open(path) as fh:
                raw = json.load(fh)
        except Exception:                  # noqa: BLE001 — unreadable → not enforced
            continue
        name = raw.get("skill") or path
        try:
            spec = {
                "gates": [{
                    "key": f"{name}:{g['key']}",
                    "cmd_re": _compile(g["cmd"], g.get("flags", "")),
                    "signature": tuple(g["signature"]),
                    "reason": g["reason"],
                } for g in raw.get("gates", [])],
                "forbidden": [{
                    "text_re": _compile(f["text"], f.get("flags", "")),
                    "reason": f["reason"],
                } for f in raw.get("forbidden", [])],
                "required": [{
                    "text_re": _compile(r["text"], r.get("flags", "")),
                    "gate": f"{name}:{r['gate']}",
                    "reason": r["reason"],
                } for r in raw.get("required", [])],
            }
        except Exception:                  # noqa: BLE001 — malformed → not enforced
            continue
        specs.append(spec)
    return specs


def _has_text(content):
    """True if this message carries a text block — used to spot the genuine user
    prompt that starts a turn (tool-result user messages have none)."""
    if isinstance(content, str):
        return bool(content)
    if isinstance(content, list):
        return any(isinstance(b, dict) and b.get("type") == "text" for b in content)
    return False


def _result_text(c):
    """Searchable text for a tool_result's `content`, whatever shape it arrives in.

    Nested blocks are joined with NEWLINES, never spaces: `_missing_lines` works per line,
    so flattening several blocks onto one line would collapse the whole result into a
    single "line" that the one-missing-line tolerance then waves through — a silent
    false-allow. An unexpected shape (a dict, say) is walked rather than stringified for
    the same reason: `json.dumps` escapes the newlines away and yields exactly that
    one-line false-allow.
    """
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(_result_text(x) for x in c)
    if isinstance(c, dict):
        if isinstance(c.get("text"), str):
            return c["text"]
        return "\n".join(_result_text(v) for v in c.values())
    return ""


def _fence_flags(result_lines):
    """Per line of `result_lines` (already stripped): was this line inside a fenced code
    block?

    Mirrors CommonMark's own rule (and `rework-mr`'s `fence()`, which exploits it on
    purpose): a fence of N backticks is closed only by a line of AT LEAST N backticks and
    NOTHING ELSE. A naive "toggle on any `` ``` `` line" breaks on exactly the case
    `fence()` widens for — a diff that touches a markdown file, or a quoted ```suggestion,
    carries its own literal ``` mid-block, and `rework-mr` opens with four backticks (or
    more) so that inner run does NOT close it early. Tracking the required close-run-length,
    not just "was a fence line seen", is what keeps this in sync with that.
    """
    flags = []
    in_fence, fence_len = False, 0
    for stripped in result_lines:
        flags.append(in_fence)
        ticks = len(stripped) - len(stripped.lstrip("`"))
        if in_fence:
            if ticks >= fence_len and stripped.lstrip("`").strip() == "":
                in_fence = False           # a bare run of >= fence_len backticks closes it
        elif ticks >= 3:
            in_fence, fence_len = True, ticks
    return flags


def _missing_lines(result, shown):
    """(missing, corrupted, critical) — three distinct ways a line of `result` can fail
    to show up intact in the visible message.

    Alignment is `difflib.SequenceMatcher` over the two LINE sequences, not a
    contiguous-substring match over the whole blob and not a plain "is this line present
    anywhere" set check. The skills ask the model to interleave its own text with pasted
    output — `review-mr`'s `updates` block is pasted and then annotated with a summary
    sub-bullet per push — so requiring one unbroken block flagged correct, fully-pasted
    messages: every original line was present, just not consecutively. `get_opcodes()`
    gives that for free: an untouched original line surfaces as `equal` regardless of how
    much unrelated `insert`ed commentary surrounds it, so interleaving never has to be
    special-cased.

    `corrupted` — a `replace` opcode whose original line is a PREFIX or SUFFIX of one of
    its replacement lines: the model's own transition sentence spliced onto a fenced code
    line with no newline in between, say, leaving the code line's text glued onto (not
    replacing) something else. Checked as `startswith`/`endswith` against only the lines
    difflib itself aligned to this position — not a global scan — which is also what keeps
    two independently-pasted, legitimate blocks that coincidentally share an 8+ char run
    in the middle of some unrelated, DISTANT line from being misread as gluing: if nothing
    aligns there, difflib reports a plain `delete`, not a `replace`.

    `critical` — genuinely absent (`delete`, or a `replace` that isn't a boundary match),
    from a part of the block where a silent drop is exactly the failure this hook exists to
    prevent: a markdown table row (starts with `|` — an entire finding disappearing from
    the overview) or a line inside a fenced code block (the source the user is meant to
    judge a finding against, per `_fence_flags`). These get no tolerance, unlike a dropped
    prose line.

    `missing` — genuinely absent, and not `critical`: a dropped preamble sentence, a
    swapped-out lead-in. Can be an honest, harmless edit — see `violation()`, which
    tolerates exactly one of these, but none of `corrupted` or `critical`.
    """
    result_lines = [ln.strip() for ln in result.strip().splitlines()]
    shown_lines = [ln.strip() for ln in shown.splitlines()]
    in_fence = _fence_flags(result_lines)

    missing, corrupted, critical = [], [], []
    matcher = difflib.SequenceMatcher(None, result_lines, shown_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("equal", "insert"):
            continue
        counterparts = shown_lines[j1:j2]
        for k in range(i1, i2):
            stripped = result_lines[k]
            if len(stripped) < 8 or set(stripped) <= set("-|` "):
                continue
            if tag == "replace" and any(
                    c != stripped and (c.startswith(stripped) or c.endswith(stripped))
                    for c in counterparts):
                corrupted.append(stripped)
            elif in_fence[k] or stripped.startswith("|"):
                critical.append(stripped)
            else:
                missing.append(stripped)
    return missing, corrupted, critical


def _load(path):
    try:
        with open(path) as f:
            return [json.loads(ln) for ln in f if ln.strip()]
    except Exception:                      # noqa: BLE001 — unreadable → fail open
        return None


def _scan_turn(rows, gates):
    """(matched, result_by_id, shown) for the current turn.

    Walk from the last genuine user prompt to the end. (tool_result messages are
    role=user too, so a "genuine" prompt is a user message carrying a text block — that's
    what starts a turn. Synthetic rows Claude Code injects mid-turn, e.g. isMeta
    skill-load/system-reminder rows, also carry a text block but aren't a real new turn —
    skip those, or `start` jumps past a gated call that ran earlier in the same turn.)
    Scanned backward with an early break: only the current turn's tail is ever used, so
    there's no need to walk the whole (ever-growing) transcript.
    """
    start = 0
    for i in range(len(rows) - 1, -1, -1):
        r = rows[i]
        if (r.get("type") == "user" and not r.get("isMeta")
                and _has_text(r.get("message", {}).get("content"))):
            start = i
            break

    # Pair each gated Bash invocation with ITS OWN result (matched by tool_use id): a real
    # block ran iff some gated command's paired tool result carries that command's
    # signature. Pairing the command to its own output is what keeps this from false-firing
    # when a skill's *source* is merely read or grepped — only an actual invocation that
    # printed a real block counts.
    matched = {}       # tool_use_id -> gate dict, for Bash calls matching a gate
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
                for gate in gates:
                    if gate["cmd_re"].search(cmd):
                        matched[b.get("id")] = gate
                        break
            elif t == "tool_result":
                result_by_id[b.get("tool_use_id")] = _result_text(b.get("content", ""))
            elif t == "text" and role == "assistant":
                assistant_text.append(b.get("text", "") or "")

    return matched, result_by_id, "\n".join(assistant_text)


def violation(path, specs):
    """Reason to block, or None. Re-readable so it can be retried — see main()."""
    rows = _load(path)
    if rows is None:
        return None

    gates = [g for s in specs for g in s["gates"]]
    matched, result_by_id, shown = _scan_turn(rows, gates)

    if not shown.strip():
        # No assistant text in this turn yet. Either there is genuinely nothing to
        # check, or the message has not reached the transcript file — the hook can fire
        # within ~300 ms of the message being written. Judging now would report every
        # line as missing.
        return None

    # For every gated command that actually ran and produced real (non-error) output this
    # turn, require ITS OWN output text to reappear verbatim in the model's visible
    # message — not just the signature phrases, so a message that only *talks about* the
    # markers without actually pasting the block can't satisfy the check. Only enforce a
    # successful run; an errored one (no matching signature) means the model is mid-fix —
    # leave it alone.
    # Keep only the LAST invocation per gate key. A topic can legitimately be re-rendered
    # several times in one turn — quote, then an anchor fix, then `set --draft` — and each
    # rendering supersedes the previous one. Demanding every one of them be pasted made a
    # correct message (which pasted the newest block) get blocked for omitting a stale one.
    # dict preserves transcript order, so the last write per key wins.
    last_per_key = {}
    for uid, gate in matched.items():
        last_per_key[gate["key"]] = uid

    fired = set()      # gate keys that ran successfully — for the `required` rules
    reason = None
    for gate_key, uid in last_per_key.items():
        gate = matched[uid]
        result = result_by_id.get(uid, "")
        if not all(s in result for s in gate["signature"]):
            continue
        fired.add(gate_key)
        # Tolerate ONE dropped PROSE line. Observed: a message that pasted the whole
        # overview — table, counts, footer — but swapped the leading status line for its
        # own preamble. Blocking that is noise. Two or more missing lines still means a
        # section went astray (a paraphrase misses nearly all of them). A CORRUPTED line
        # (glued onto another line instead of dropped outright) or a CRITICAL one (a table
        # row or fenced-code line dropped outright) gets no such tolerance — one is already
        # a finding the user never saw, or a fence that broke and let prose bleed into code.
        if reason is None and result.strip():
            missing, corrupted, critical = _missing_lines(result, shown)
            if corrupted or critical or len(missing) > 1:
                reason = gate["reason"]
    if reason:
        return reason

    # Command-independent checks. A block the model wrote out itself needs no command to
    # run, so no gate above can see it.
    for spec in specs:
        for bad in spec["forbidden"]:
            if bad["text_re"].search(shown):
                return bad["reason"]
        for req in spec["required"]:
            if req["gate"] not in fired and req["text_re"].search(shown):
                return req["reason"]

    return None


def main(argv):
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

    specs = load_specs(argv)
    if not specs:                          # nothing installed to enforce
        _allow()

    # Re-read before blocking. The transcript is written asynchronously: a block was
    # observed 348 ms after a 2384-character message was produced, and replaying that
    # same transcript afterwards allowed it — the hook had simply read the file before
    # the message landed. Retry with a widening delay and bail out as soon as it clears;
    # only a turn that is genuinely about to be blocked ever pays the wait.
    reason = violation(path, specs)
    for delay in (0.25, 0.5, 1.0):
        if not reason:
            break
        time.sleep(delay)
        reason = violation(path, specs)
    if reason:
        _block(reason)
    _allow()


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except SystemExit:
        raise
    except Exception:                      # noqa: BLE001 — a bug here must never wedge a session
        _allow()
