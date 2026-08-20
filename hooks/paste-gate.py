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
continuation, so we block at most once per reply and never spin — with ONE deliberate
exception. A leaked critical-lines manifest (see `_leaked_manifest`) is checked
regardless of `stop_hook_active`: it is narrow, deterministic, and trivial for the model
to fix, unlike the broader verbatim-paste checks, and a retry forced by some OTHER
violation can introduce exactly this leak as a side effect ("just paste everything to be
safe") on the very turn where the general loop guard would otherwise hide it. Observed in
production on a real MR review, not theoretical.

Scope: only acts on a turn where a gated command actually ran AND produced real output
(or where a forbidden/required pattern hits) — every other turn, and every session that
never touches these skills, passes straight through.

Detection: a turn qualifies when a gated Bash invocation's OWN paired tool result
(matched by tool_use id) carries that command's output signature — success, not an
error. Requiring the literal subcommand/script name in the command text (not just a bare
keyword anywhere) and then the actual, run-specific output text to reappear verbatim in
the model's message is what keeps this from false-firing when a skill's own *source* is
merely read or grepped (SKILL.md, REFERENCE.md, this file and the specs all mention these
command names and phrases too) — and from false-passing on a message that only talks
about the markers without actually pasting the block.

The pasted block itself is meant to stay clean (no marker the user would see) — but the
producer's OWN trailing manifest (see `_split_manifest`) is a deliberate exception: each
gated command appends a machine-readable "these lines are critical" payload after a
`<!-- paste-gate:critical -->` marker, which this file strips before comparing anything
and which must never survive into the model's visible reply (enforced unconditionally,
independent of any spec). Table rows and fenced-code content used to be re-derived here
by parsing the rendered Markdown (fence-run-length tracking, `startswith("|")`) — the
producer (findings.py / threads.py) is the only place that unambiguously KNOWS which
lines are which, since it built them, and re-deriving that fact downstream from text is
exactly the kind of context-sensitive parsing that kept finding new edge cases (this
file's own history has two examples: a naive fence toggle, then one that didn't account
for a WIDENED fence). The manifest replaces that heuristic outright — there is no
fallback if a producer doesn't emit one, by design; see `_split_manifest`.
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


# Bash tool plumbing, not part of any producer's own output: appended whenever a gated
# command's own `cd` changes the shell's directory. The model was never going to paste
# this back, but `_missing_lines` doesn't know that — it just sees one more line of
# `result` absent from `shown`, and that alone can burn the one-line tolerance a
# genuinely harmless edit (e.g. a reworded header) would otherwise get.
_CWD_RESET_RE = re.compile(r"\nShell cwd was reset to [^\n]*\Z")


def _strip_tool_noise(result):
    return _CWD_RESET_RE.sub("", result)


MANIFEST_MARKER = "<!-- paste-gate:critical"
_MANIFEST_RE = re.compile(re.escape(MANIFEST_MARKER) + r"\n(.*?)\n-->", re.S)


def _split_manifest(result):
    """(visible, critical) — the human-visible block a gated command printed, and the
    SET of lines its own producer (findings.py / threads.py) declared critical.

    The producer is the single source of truth for this, not a heuristic re-derived here
    from the rendered text: it is the only place that unambiguously KNOWS which lines are
    table rows or fenced code content, since it built them — this hook used to re-parse
    that fact from Markdown syntax (fence-run-length tracking, `startswith("|")`), which
    is exactly the kind of context-sensitive parsing regex/string heuristics keep getting
    subtly wrong at the margins (this file's own history has two examples). The producer
    appends its manifest as a trailing, non-visible payload; `visible` is everything
    before it — what a gate's signature check and the verbatim comparison both operate on.

    A missing or malformed manifest yields an EMPTY critical set, not a fallback guess:
    there is no heuristic left to fall back to, by design (see the commit that removed
    it) — a producer that doesn't emit one is a producer that hasn't been updated yet,
    which should be visible as "nothing here is protected", not silently patched over.
    """
    i = result.find(MANIFEST_MARKER)
    if i == -1:
        return result, set()
    m = _MANIFEST_RE.search(result, i)
    if not m:
        return result, set()
    try:
        lines = json.loads(m.group(1))
        critical = {str(x).strip() for x in lines}
    except Exception:                      # noqa: BLE001 — malformed → no critical lines
        critical = set()
    return result[:i].rstrip("\n"), critical


def _missing_lines(result, shown, critical):
    """(missing, corrupted, critical_out) — three distinct ways a line of `result` can
    fail to show up intact in the visible message. `result` is the VISIBLE portion only
    (see `_split_manifest`) and `critical` is the producer-declared set of its lines
    that must never be silently dropped, whatever else about them.

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

    `critical_out` — genuinely absent (`delete`, or a `replace` that isn't a boundary
    match) AND a member of `critical`: a part of the block where a silent drop is exactly
    the failure this hook exists to prevent. These get no tolerance, unlike a dropped
    prose line — checked BEFORE the length/punctuation filter below, so the producer's own
    judgement is never second-guessed by a generic "too short to matter" heuristic.

    `missing` — genuinely absent, and not critical: a dropped preamble sentence, a
    swapped-out lead-in. Can be an honest, harmless edit — see `violation()`, which
    tolerates exactly one of these, but none of `corrupted` or `critical_out`.
    """
    result_lines = [ln.strip() for ln in result.strip().splitlines()]
    shown_lines = [ln.strip() for ln in shown.splitlines()]

    missing, corrupted, critical_out = [], [], []
    matcher = difflib.SequenceMatcher(None, result_lines, shown_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("equal", "insert"):
            continue
        counterparts = shown_lines[j1:j2]
        for k in range(i1, i2):
            stripped = result_lines[k]
            is_critical = stripped in critical
            if not is_critical and (len(stripped) < 8 or set(stripped) <= set("-|` ")):
                continue
            if tag == "replace" and any(
                    c != stripped and (c.startswith(stripped) or c.endswith(stripped))
                    for c in counterparts):
                corrupted.append(stripped)
            elif is_critical:
                critical_out.append(stripped)
            else:
                missing.append(stripped)
    return missing, corrupted, critical_out


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


def _leaked_manifest(path):
    """Reason to block if the producer's own critical-lines manifest leaked into the
    visible message, or None. Deliberately callable independent of `stop_hook_active`
    — see `main()` for why this ONE check is exempt from the usual once-per-reply loop
    guard: it is narrow and deterministic (a fixed structural pattern, not a fuzzy
    heuristic), and the fix — "don't reproduce this exact marker" — is trivial for the
    model to make, unlike the broader verbatim-paste checks below, which legitimately
    need the ceiling to avoid wedging a session on a hard-to-satisfy correction.

    Observed in production, on a real MR review: the model dropped `present`'s output,
    got blocked once (a DIFFERENT violation), and "fixed" it on retry by pasting the
    raw tool result wholesale — introducing this leak as a side effect. Because
    `stop_hook_active` was already true for that retry, a leak check gated the same way
    as everything else would never have seen it: the one violation the loop guard let
    through was a brand new one, not the one it was retrying for.

    Matched with the FULL structural pattern (marker, then a newline, then the JSON
    payload, then a closing "-->"), not a bare `MANIFEST_MARKER in shown` substring
    check: the latter also fires on a mid-sentence, backtick-quoted MENTION of the
    marker phrase — e.g. documentation, or a chat reply explaining this very mechanism —
    which is legitimate prose, not a leaked manifest, and is exactly the false-positive
    class the `forbidden` rules below already guard against for the raw ```suggestion
    fence (see their own `m` / mid-sentence tests). An actual leak has real JSON between
    the marker and the closer; a prose mention almost never reproduces that whole shape
    by accident.
    """
    rows = _load(path)
    if rows is None:
        return None
    _, _, shown = _scan_turn(rows, [])       # gates unused for just computing `shown`
    if not shown.strip():
        return None
    if _MANIFEST_RE.search(shown):
        return ("Your message contains an internal `<!-- paste-gate:critical -->` "
                "manifest block. A gated command appends that payload AFTER the "
                "human-visible block, purely for this hook to read — it must never "
                "reach the user. Re-send your message with everything from that "
                "marker onward removed.")
    return None


def violation(path, specs):
    """Reason to block, or None. Re-readable so it can be retried — see main(). The
    manifest-leak check lives in `_leaked_manifest`, called separately by `main()` —
    not duplicated here."""
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
        result = _strip_tool_noise(result_by_id.get(uid, ""))
        visible, critical = _split_manifest(result)
        if not all(s in visible for s in gate["signature"]):
            continue
        fired.add(gate_key)
        # Tolerate ONE dropped PROSE line. Observed: a message that pasted the whole
        # overview — table, counts, footer — but swapped the leading status line for its
        # own preamble. Blocking that is noise. Two or more missing lines still means a
        # section went astray (a paraphrase misses nearly all of them). A CORRUPTED line
        # (glued onto another line instead of dropped outright) or a CRITICAL one (a table
        # row or fenced-code line dropped outright, per the producer's own manifest) gets
        # no such tolerance — one is already a finding the user never saw, or a fence that
        # broke and let prose bleed into code.
        if reason is None and visible.strip():
            missing, corrupted, crit_out = _missing_lines(visible, shown, critical)
            if corrupted or crit_out or len(missing) > 1:
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

    path = data.get("transcript_path")
    if not path:
        _allow()

    # The manifest-leak check runs BEFORE the stop_hook_active gate below, and without
    # requiring any spec to have loaded — it is an engine-level convention, not tied to
    # a specific skill. See `_leaked_manifest`'s docstring for why this one check must
    # not wait for a fresh (non-retry) turn: a retry forced by some OTHER violation can
    # introduce exactly this leak as a side effect of "just paste everything to be
    # safe", and that is precisely the turn where stop_hook_active is already true.
    leak = _leaked_manifest(path)
    for delay in (0.25, 0.5, 1.0):
        if not leak:
            break
        time.sleep(delay)
        leak = _leaked_manifest(path)
    if leak:
        _block(leak)

    # already inside a hook-forced retry → let the REST through, never loop. (The leak
    # check above ran regardless — that is the one deliberate exception.)
    if data.get("stop_hook_active"):
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
