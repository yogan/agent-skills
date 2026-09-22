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

Two tiers of rule:
  Per-spec, data-driven (what `violation()` checks; needs a spec loaded) — three kinds:
    gates     — a command ran and produced real output → that output must appear in the
                visible message. The main mechanism.
    forbidden — a pattern that must never appear in a visible message, whatever ran.
                Catches the model COMPOSING a block itself, for which no command runs
                at all, so no gate can see it.
    required  — a pattern that may only appear once a given gate has actually fired
                this turn. The inverse check: the ritual phrase is there, but the
                command that was supposed to produce it never ran.
  Engine-level, unconditional (`_leaked_manifest`, `_garbled_backtick_escape`) — always
  checked, spec or no spec, because neither is about either skill's vocabulary: one
  guards the manifest mechanism this engine itself relies on, the other guards plain
  Markdown hygiene. New engine-level checks belong here, not as one more per-spec
  `forbidden` entry duplicated into every skill's JSON.

Contract (Claude Code Stop hook):
  stdin  — JSON with `transcript_path` and `stop_hook_active`.
  stdout — `{"decision":"block","reason":"…"}` to force a retry, or nothing to allow.
It fails OPEN throughout: any error → allow the stop (never wedge a session).

Loop guard: `stop_hook_active` is true when we're already inside a hook-forced
continuation, so we block at most once per reply and never spin — with ONE deliberate
exception. Both engine-level checks run regardless of `stop_hook_active`: each is
narrow, deterministic, and trivial for the model to fix, unlike the broader
verbatim-paste checks, and a retry forced by some OTHER violation can introduce either
as a side effect ("just paste everything to be safe") on the very turn where the
general loop guard would otherwise hide it. The manifest case was observed in
production on a real MR review, not theoretical. Because they keep looking, those two
judge only what a retry can still change (`_retractable_text`) — a message already shown
to the user cannot be taken back, and blocking on it again would wedge the turn.

Scope: only acts on a turn where a gated command actually ran AND produced real output
(or where a forbidden/required pattern hits) — every other turn, and every session that
never touches these skills, passes straight through.

Detection: a turn qualifies when a gated Bash invocation's OWN paired tool result
(matched by tool_use id) carries that command's output signature — success, not an
error. Requiring the literal subcommand/script name in the command text (not just a bare
keyword anywhere) and then the actual, run-specific output text to reappear verbatim in
the model's message is what keeps this from false-passing on a message that only talks
about the markers without actually pasting the block.

Reading a skill's own source must not trip any of that, and the signature check alone is
not enough to guarantee it: SKILL.md, REFERENCE.md, this file and the specs all quote
these command names and phrases, and a spec DEFINES its signatures, so `paste-gates.json`
contains every one of its own by construction. `_invocation_text` is the part that
actually separates the two — a command segment that merely reads a file cannot gate.

The pasted block itself is meant to stay clean (no marker the user would see) — but the
producer's OWN trailing manifest (see `_split_manifest`) is a deliberate exception: each
gated command appends a machine-readable "my block starts here, and these of its lines
are critical" payload after a `<!-- paste-gate:critical -->` marker, which this file
strips before comparing anything and which must never survive into the model's visible
reply (enforced unconditionally, independent of any spec). That first half is what keeps
a gated command from being judged on output it did not print: it is routinely not alone
in its Bash call — a formatter or a linter chained ahead of it, stderr folded in by
`2>&1` — and those lines are nobody's to paste.

The other half — which lines are table rows and fenced-code content — used to be
re-derived here by parsing the rendered Markdown (fence-run-length tracking,
`startswith("|")`) — but the producer (findings.py / threads.py) is the only place that
unambiguously KNOWS which lines are which, since it built them, and re-deriving that fact
downstream from text is exactly the kind of context-sensitive parsing that kept finding
new edge cases (this
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


def _from_line(text, first):
    """`text` from the first line that IS `first` (compared stripped) onward, or `text`
    unchanged when no line matches — never a guess about where else the block might
    start. The first match is the right one even in the pathological case of a command
    run twice in one call: the marker this is paired with is that run's FIRST marker too,
    so both boundaries stay on the same copy of the block."""
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if ln.strip() == first:
            return "\n".join(lines[i:])
    return text


def _split_manifest(result):
    """(visible, critical) — the human-visible block a gated command printed, carved out
    of everything its Bash call produced, and the SET of lines its own producer
    (findings.py / threads.py) declared critical.

    The producer is the single source of truth for both, not a heuristic re-derived here
    from the rendered text. For the critical lines, it is the only place that
    unambiguously KNOWS which are table rows or fenced code content, since it built them —
    this hook used to re-parse that from Markdown syntax (fence-run-length tracking,
    `startswith("|")`), exactly the kind of context-sensitive parsing that keeps getting
    subtly wrong at the margins (this file's own history has two examples).

    For the boundaries, the point is that a gated command is not always alone in its Bash
    call. The model chains a formatter or a linter ahead of it, and `2>&1` folds stderr in
    as well; every such line used to read as a line of the block the model had dropped,
    and two were enough to force a retry on a message that had pasted the block perfectly.
    So the producer says where its block starts (`first`) and ends (the marker itself),
    and everything outside that is not the model's to reproduce.

    A missing or malformed manifest yields an EMPTY critical set and no trimming, not a
    fallback guess: there is no heuristic left to fall back to, by design (see the commit
    that removed it) — a producer that doesn't emit one is a producer that hasn't been
    updated yet, which should be visible as "nothing here is protected", not silently
    patched over. A payload that is a bare JSON LIST is the older shape of the same idea
    and is still read for its critical lines: the install is documented as symlinks into
    ~/.claude but nothing enforces that, and a skill COPIED there once would otherwise go
    from protected to silently unprotected the moment the engine was updated on its own.
    """
    i = result.find(MANIFEST_MARKER)
    if i == -1:
        return result, set()
    m = _MANIFEST_RE.search(result, i)
    if not m:
        return result, set()
    visible, first = result[:i].rstrip("\n"), ""
    try:
        payload = json.loads(m.group(1))
        if isinstance(payload, dict):
            first = str(payload.get("first", "")).strip()
            payload = payload.get("critical", [])
        critical = {str(x).strip() for x in payload}
    except Exception:                      # noqa: BLE001 — malformed → no critical lines
        critical = set()
    return (_from_line(visible, first) if first else visible), critical


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


# A gated command is an INVOCATION; one that merely READS a gated script's source is not.
# `cat …/diff-view.sh`, `head …/threads.py`, `grep -n "findings.py resume" SKILL.md` all put
# a gated name in the command text while printing source, never a rendered block. The
# signature check cannot tell those apart on its own, because a source read can carry the
# signature phrases too — and for one file it always does: a skill's gate spec DEFINES its
# signatures, so `paste-gates.json` contains every one of them by construction (SKILL.md and
# this file quote them as well). Reading a gated script beside its own spec in a single call
# therefore looked exactly like a successful render, and the hook answered by demanding the
# model paste a shell script and a JSON file into chat, verbatim, to end its turn.
#
# So match only the pipeline segments that are not themselves file readers: split on the
# shell's list/pipe separators and drop a segment whose own LEADING word is a reader. Only
# the leading word, so a real invocation piped INTO one (`… | head -40`) stays matched.
_READERS = frozenset(
    "cat bat head tail less more nl od xxd strings wc file stat open "
    "grep rg egrep fgrep sed awk".split())
_SEGMENTS_RE = re.compile(r"\|\||&&|[|;&\n]")
_LEAD_WORD_RE = re.compile(r"^\s*(?:\w+=\S*\s+)*(?:sudo\s+|command\s+|time\s+)*([^\s;|&]+)")
_QUOTES_RE = re.compile(r"[\"']")


def _invocation_text(cmd):
    """`cmd` with its file-reading segments removed — see the note above. A command that
    only reads reduces to the empty string and can match no gate.

    Shell quotes are dropped from what remains. Every gate pattern joins the script to its
    subcommand across plain whitespace (`threads\\.py\\s+quote`), so quoting the path —
    `python3 "$SD/threads.py" quote t7`, the defensive habit for paths with spaces — parks
    a closing quote in the middle and the gate silently cannot fire. That direction is the
    dangerous one: a gate that never fires is invisible, unlike a false block, which
    announces itself. Splitting happens BEFORE this, on the still-quoted text, so removing
    them cannot hand a quoted separator the power to start a new segment.
    """
    kept = []
    for seg in _SEGMENTS_RE.split(cmd):
        lead = _LEAD_WORD_RE.match(seg)
        if lead and lead.group(1).strip("'\"").rsplit("/", 1)[-1] in _READERS:
            continue
        kept.append(seg)
    return _QUOTES_RE.sub("", "\n".join(kept))


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
                cmd = _invocation_text((b.get("input") or {}).get("command", "") or "")
                for gate in gates:
                    if gate["cmd_re"].search(cmd):
                        matched[b.get("id")] = gate
                        break
            elif t == "tool_result":
                result_by_id[b.get("tool_use_id")] = _result_text(b.get("content", ""))
            elif t == "text" and role == "assistant":
                assistant_text.append(b.get("text", "") or "")

    return matched, result_by_id, "\n".join(assistant_text)


def _turn_state(path, gates=()):
    """(matched, result_by_id, shown) for the current turn, or None if there's nothing
    to check yet. Covers both ways "nothing yet" happens: the transcript can't be read
    at all, or it can, but the turn has no assistant text in it — the hook can fire
    within ~300ms of a tool call finishing, well before the assistant's own message
    catches up in the file. Judging now would report every line of a real block as
    missing, so every check below that reasons about the assistant's VISIBLE text
    shares this one early exit rather than each re-deriving it.

    `_turn_could_leak` is the one exception, and does not go through this: it needs to
    see a tool result even when `shown` is still empty (see its own docstring)."""
    rows = _load(path)
    if rows is None:
        return None
    matched, result_by_id, shown = _scan_turn(rows, gates)
    if not shown.strip():
        return None
    return matched, result_by_id, shown


_HOOK_FEEDBACK = "Stop hook feedback:"


def _retractable_text(path):
    """The assistant text this turn that a retry can still change: everything written
    after the most recent block, or the whole turn when there hasn't been one.

    Only `_leaked_manifest` uses this, and only because it is exempt from the loop guard.
    Every other check gets one shot per reply and then stops looking, so it can safely
    reason about the whole turn. This one keeps looking — and a message Claude Code has
    already shown the user cannot be taken back, so judging it again on the next reply
    asks the model to remove text it has no way to reach. That is not a hypothetical: an
    explanation of this very mechanism, quoting the marker with a payload under it, pinned
    the block on for every subsequent reply in the turn and there was no message that
    could have cleared it.

    Wedging a session is the one thing this file must never do, and "block forever on
    something already sent" is that, arrived at the long way round.
    """
    rows = _load(path)
    if rows is None:
        return None
    for i in range(len(rows) - 1, -1, -1):
        r = rows[i]
        if r.get("type") == "system" and r.get("subtype") == "stop_hook_summary":
            rows = rows[i + 1:]
            break
        if (r.get("type") == "user" and r.get("isMeta")
                and _HOOK_FEEDBACK in _result_text(r.get("message", {}).get("content"))):
            rows = rows[i + 1:]
            break
    _, _, shown = _scan_turn(rows, [])
    return shown if shown.strip() else None


def _leaked_manifest(path):
    """Reason to block if the producer's own block manifest leaked into the
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

    Scoped to what a retry can still change (`_retractable_text`), not to the whole turn,
    because unlike every other check here this one keeps looking after it has fired once.
    """
    shown = _retractable_text(path)
    if shown is None:
        return None
    if _MANIFEST_RE.search(shown):
        return ("Your message contains an internal `<!-- paste-gate:critical -->` "
                "manifest block. A gated command appends that payload AFTER the "
                "human-visible block, purely for this hook to read — it must never "
                "reach the user. Re-send your message with everything from that "
                "marker onward removed.")
    return None


def _turn_could_leak(path):
    """True if some tool result THIS turn carries a manifest at all. Unlike `shown`
    (the assistant's text, generated after the tool result and prone to lagging its
    own flush), a tool result exists before the model even starts writing — so this is
    safe to check once, no retry needed, and lets `main()` skip the leak-retry wait on
    every turn (nearly all of them) that has nothing to leak in the first place.

    Does not go through `_turn_state`: that helper treats an empty `shown` as "nothing
    to check yet" and stops there, but a tool result can already be sitting in the
    transcript before the assistant has written a single character of its reply —
    exactly the moment this needs to see past."""
    rows = _load(path)
    if rows is None:
        return False
    _, result_by_id, _ = _scan_turn(rows, [])
    return any(MANIFEST_MARKER in v for v in result_by_id.values())


_BROKEN_BACKTICK_ESCAPE_RE = re.compile(r"(?:\\`){2,}")


def _garbled_backtick_escape(path):
    """Reason to block if the assistant backslash-escaped a run of backticks (e.g.
    trying to show a literal fence) instead of wrapping it in single backticks with the
    run left plain inside — the technique every skill's own docs already use. Markdown
    has no such escape, so this renders as a garbled mess of glued-together words
    instead of the intended literal text. Engine-level and unconditional, like
    `_leaked_manifest`: nothing about it is specific to either skill's vocabulary, so it
    protects any spec built on this engine, not just the two shipped today — and scoped
    the same way, to what a retry can still change, for the same reason."""
    shown = _retractable_text(path)
    if shown is None:
        return None
    if _BROKEN_BACKTICK_ESCAPE_RE.search(shown):
        return ("Your message backslash-escapes a run of backticks to show them "
                "literally — Markdown has no such escape, so it renders as garbled, "
                "illegible text instead. To show a literal fence or backtick run in "
                "prose, wrap it in single backticks with the run left PLAIN inside "
                "(a single backtick, the plain run, a single backtick) — no "
                "backslashes. Re-send your message with that fixed.")
    return None


def _diagnosis(missing, corrupted, critical_out):
    """The evidence behind a block, appended to the gate's own instruction.

    A gate's `reason` says what to do; on its own it never says what was actually wrong,
    so a model whose paste was in fact correct has nothing to act on and re-sends the same
    message — which the loop guard then lets through, since the one block per reply is
    already spent. That happened for real (a linter chained ahead of the gated command,
    before the manifest declared where the block started), and it is unfalsifiable from
    the model's side without this: quoting the lines is what turns "paste it again" into
    something that can be checked, and what makes a future false block diagnosable by
    whoever reads the transcript rather than only by rerunning the comparison by hand.

    Capped and truncated: it rides inside a hook `reason`, so a long block's worth of
    lines would bury the instruction it is attached to.
    """
    def sample(lines):
        out = [ln if len(ln) <= 100 else ln[:99] + "…" for ln in lines[:3]]
        return "; ".join(f"`{ln}`" for ln in out) + (" …" if len(lines) > 3 else "")

    parts = []
    if critical_out or missing:
        parts.append("absent from your message: " + sample(critical_out + missing))
    if corrupted:
        parts.append("run together with other text instead of standing alone: "
                     + sample(corrupted))
    if not parts:
        return ""
    return "\n\nLines of that output " + ", and ".join(parts) + "."


def violation(path, specs):
    """Reason to block, or None. Re-readable so it can be retried — see main(). The
    manifest-leak check lives in `_leaked_manifest`, called separately by `main()` —
    not duplicated here."""
    gates = [g for s in specs for g in s["gates"]]
    state = _turn_state(path, gates)
    if state is None:
        return None
    matched, result_by_id, shown = state

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
                reason = gate["reason"] + _diagnosis(missing, corrupted, crit_out)
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


def _retry_until(check, keep_waiting=lambda result: bool(result), delays=(0.25, 0.5, 1.0)):
    """Call `check()`, re-calling it with a widening delay for as long as
    `keep_waiting(result)` says the current result isn't settled yet, then return the
    last result. Every check in this file reads the SAME asynchronously-written
    transcript — the hook can fire before the assistant's message has caught up in the
    file — so a call right after the triggering event can catch it mid-flush; retrying
    is how each one gives that write a chance to land before committing to an answer.

    `keep_waiting` names which direction is unsettled, and differs per caller: a
    suspected violation may still clear as the message finishes arriving (retry while
    truthy — the default), while a leak that hasn't shown up YET may still be about to
    (retry while falsy). `delays=()` means exactly one attempt, no waiting at all — for
    a check with nothing async to wait on.
    """
    result = check()
    for delay in delays:
        if not keep_waiting(result):
            break
        time.sleep(delay)
        result = check()
    return result


def main(argv):
    try:
        data = json.load(sys.stdin)
    except Exception:
        _allow()

    path = data.get("transcript_path")
    if not path:
        _allow()

    # These two run BEFORE the stop_hook_active gate below, and without requiring any
    # spec to have loaded — they are engine-level conventions, not tied to a specific
    # skill. See `_leaked_manifest`'s docstring for why this must not wait for a fresh
    # (non-retry) turn: a retry forced by some OTHER violation can introduce a leak (or
    # a garbled escape) as a side effect of "just paste everything to be safe", and
    # that is precisely the turn where stop_hook_active is already true.
    #
    # `_turn_could_leak` gates the wait to turns where a manifest actually exists
    # (skipping it everywhere else, see its own docstring) — retried WHILE ABSENT, the
    # opposite of every other check here, since an unflushed leak can still arrive but
    # a confirmed one won't un-happen. The garble check needs no such gate or wait: it's
    # cheap, and nothing async has to land before it means anything either way.
    if _turn_could_leak(path):
        leak = _retry_until(lambda: _leaked_manifest(path), lambda found: not found)
        if leak:
            _block(leak)

    garble = _retry_until(lambda: _garbled_backtick_escape(path), delays=())
    if garble:
        _block(garble)

    # already inside a hook-forced retry → let the REST through, never loop. (The two
    # checks above ran regardless — that is the deliberate exception.)
    if data.get("stop_hook_active"):
        _allow()

    specs = load_specs(argv)
    if not specs:                          # nothing installed to enforce
        _allow()

    # Only a turn that's genuinely about to be blocked ever pays this wait (see
    # `_retry_until`): a block was once observed 348ms behind the message that
    # triggered it, purely from reading the transcript before that message landed.
    reason = _retry_until(lambda: violation(path, specs))
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
