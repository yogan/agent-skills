#!/usr/bin/env python3
"""Tests for the paste-enforcement Stop hook (`paste-gate.py`).

Run: `python3 hooks/test_paste_gate.py` (stdlib only, no deps).

Every case here is either the hook's contract or a bug that was found in production and
fixed — the tests exist so the next engine change cannot quietly bring one back. The hook
is exercised as a SUBPROCESS with a synthetic transcript, because its contract is exactly
that: transcript JSONL + stdin JSON in, `{"decision":"block"}` or silence out.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "paste-gate.py")
REPO = os.path.dirname(HERE)
REVIEW_SPEC = os.path.join(REPO, "skills/review-mr/scripts/paste-gates.json")
REWORK_SPEC = os.path.join(REPO, "skills/rework-mr/scripts/paste-gates.json")

# A realistic `findings.py resume` block: MR header, table, a topic. Its lines are what the
# model has to reproduce.
RESUME_OUT = """**2 push(es) since your last review** (tip `abc123def456`)

- **push 1:** 3 files, +42/-7

---

**MR !123** — Add rate limiting · `feat/rate-limit` → `main`

| State | Topic | Location |
|---|---|---|
| ○ open | ◈ **t1** — unbounded retry loop | `src/client.py:88` |
| ✓ done | ◈ **t2** — missing timeout | `src/client.py:120` |

◈ **t1** — unbounded retry loop
`src/client.py:88`

```python
while True:
    resp = self._send(req)
```
"""

# What findings.py's own render_table/code_snippet would mark critical for RESUME_OUT —
# the table rows and the one code line, not the header/separator/prose around them.
RESUME_CRITICAL = [
    "| ○ open | ◈ **t1** — unbounded retry loop | `src/client.py:88` |",
    "| ✓ done | ◈ **t2** — missing timeout | `src/client.py:120` |",
    "resp = self._send(req)",
]

QUOTE_OUT = """◈ **t4** — swallowed exception hides real failures
`src/worker.py:210`

```python
except Exception:
    pass
```

Draft: Dieser `except` verschluckt jeden Fehler.
"""

REWORK_QUOTE_OUT = """◈ **t7** — Konfiguration wird zweimal geladen
`src/config/loader.py:44`

> Warum wird die Config hier erneut eingelesen? Der Cache oben drüber macht das schon.

```python
cfg = load_config(path)
```
"""

DIFF_VIEW_OUT = """**Diff (t3):**

```diff
--- a/src/client.py
+++ b/src/client.py
@@ -88,3 +88,3 @@
-    while True:
+    for _ in range(MAX_RETRIES):
```

ACK to fix up and push?
"""


def with_manifest(text, critical_lines):
    """`text` with a trailing critical-lines manifest appended, mirroring what
    findings.py/threads.py actually emit for a gated command (see lib/critical_manifest.py's
    `mark`/`manifest`, which both now share). Applied to the RESULT only, and only AFTER
    any `body`/`shown` variant has already been derived from the manifest-free `text` —
    several tests build `shown` by filtering `text.splitlines()`, and if the manifest
    lived inside the shared base constant, those filters could fragment it into `shown`
    too (or leak its opening marker line through untouched), tripping the "marker must
    never be visible" check for a reason that has nothing to do with what the test is
    actually exercising."""
    return text + "\n\n<!-- paste-gate:critical\n" + json.dumps(critical_lines) + "\n-->"


def row(kind, **kw):
    return {"type": kind, **kw}


def user_prompt(text="please continue"):
    return row("user", message={"role": "user", "content": [{"type": "text", "text": text}]})


def bash_call(uid, command):
    return row("assistant", message={"role": "assistant", "content": [
        {"type": "tool_use", "id": uid, "name": "Bash", "input": {"command": command}}]})


def tool_result(uid, text, as_blocks=True):
    content = [{"type": "text", "text": text}] if as_blocks else text
    return row("user", message={"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": uid, "content": content}]})


def assistant_text(text):
    return row("assistant", message={"role": "assistant", "content": [
        {"type": "text", "text": text}]})


class HookCase(unittest.TestCase):
    specs = (REVIEW_SPEC, REWORK_SPEC)

    def run_hook(self, rows, stop_hook_active=False, specs=None, transcript=True):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "transcript.jsonl")
            with open(path, "w") as fh:
                for r in rows:
                    fh.write(json.dumps(r) + "\n")
            payload = {"stop_hook_active": stop_hook_active}
            if transcript:
                payload["transcript_path"] = path
            proc = subprocess.run(
                [sys.executable, HOOK, *(self.specs if specs is None else specs)],
                input=json.dumps(payload), capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, "", proc.stderr)
        return proc.stdout.strip()

    def assertAllowed(self, rows, **kw):
        out = self.run_hook(rows, **kw)
        self.assertEqual(out, "", f"expected allow, got block: {out}")

    def assertBlocked(self, rows, contains=None, **kw):
        out = self.run_hook(rows, **kw)
        self.assertNotEqual(out, "", "expected a block, got allow")
        decision = json.loads(out)
        self.assertEqual(decision["decision"], "block")
        if contains:
            self.assertIn(contains, decision["reason"])
        return decision["reason"]


class TestGates(HookCase):
    def test_pasted_verbatim_allows(self):
        self.assertAllowed([
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py resume --iid 123"),
            tool_result("u1", RESUME_OUT),
            assistant_text(RESUME_OUT + "\n\nt1 is the one that needs you. Verdict?"),
        ])

    def test_leaked_manifest_marker_blocks(self):
        """The producer's trailing critical-lines manifest exists purely for this hook to
        read — it must never reach the user, whatever else is true about the rest of the
        message. Pasting the tool result raw (rather than the intended verbatim-minus-
        manifest block) would leak it."""
        manifest = with_manifest(RESUME_OUT, RESUME_CRITICAL)
        self.assertBlocked([
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py resume --iid 123"),
            tool_result("u1", manifest),
            assistant_text(manifest),
        ], contains="paste-gate:critical")

    def test_mentioning_the_manifest_marker_in_prose_allows(self):
        """A mid-sentence, backtick-quoted MENTION of the marker phrase — documentation,
        or a chat reply explaining this very mechanism — is legitimate prose, not a
        leaked manifest, and must not be confused with one. Observed in production: the
        engine's own explanation of the mechanism, describing the marker inline, tripped
        a bare `MANIFEST_MARKER in shown` substring check. The real leak has an actual
        JSON payload between the marker and its closing `-->`; a prose mention almost
        never reproduces that whole shape by accident — this is the same false-positive
        class the ```suggestion `forbidden` rule already guards against."""
        self.assertAllowed([
            user_prompt(),
            assistant_text("Each gated command appends a trailing "
                           "`<!-- paste-gate:critical -->` JSON manifest to its stdout, "
                           "which this hook strips before comparing anything."),
        ])

    def test_paraphrase_blocks(self):
        self.assertBlocked([
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py resume --iid 123"),
            tool_result("u1", RESUME_OUT),
            assistant_text("Picking up where we left off — the author pushed twice and "
                           "topic t1 still needs you. Shall I show it?"),
        ], contains="findings.py resume")

    def test_interleaved_paste_allows(self):
        """The skills ASK for interleaving (a summary sub-bullet per push), so the check is
        line-based, not one contiguous block. This was a real false positive."""
        lines = RESUME_OUT.strip().splitlines()
        woven = []
        for ln in lines:
            woven.append(ln)
            if ln.startswith("- **push 1:**"):
                woven.append("  - tightened the retry bound, looks right")
        self.assertAllowed([
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py resume --iid 123"),
            tool_result("u1", RESUME_OUT),
            assistant_text("\n".join(woven)),
        ])

    def test_one_dropped_line_tolerated(self):
        """A message that pastes the whole block but swaps the leading status line for its
        own preamble is not the failure this guards against."""
        body = RESUME_OUT.strip().splitlines()[1:]
        self.assertAllowed([
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py resume --iid 123"),
            tool_result("u1", RESUME_OUT),
            assistant_text("Two pushes since your last look.\n" + "\n".join(body)),
        ])

    def test_prose_glued_onto_code_line_blocks(self):
        """A sentence spliced onto the tail of a fenced code line, with no newline in
        between, must not be treated as 'line present' just because the original text is
        still a contiguous substring of the now-longer corrupted line. Observed in
        production: the model's own transition sentence ("I dropped t1 since you're
        accepting that trade-off.") landed mid-fence, glued onto a code line, and the
        gate — checking substring-of-the-whole-blob rather than exact-line membership —
        let the garbled paste through uncaught."""
        corrupted = RESUME_OUT.replace(
            "    resp = self._send(req)",
            "    resp = self._send(req)I dropped t1 since you're accepting that trade-off.")
        self.assertBlocked([
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py resume --iid 123"),
            tool_result("u1", RESUME_OUT),
            assistant_text(corrupted),
        ], contains="findings.py resume")

    def test_one_dropped_table_row_blocks(self):
        """Unlike a dropped preamble line, a dropped TABLE ROW is exactly the failure this
        hook exists to prevent: a whole finding silently disappears from the overview,
        with nothing about the message looking wrong on its own. No tolerance, even though
        it is only one line."""
        body = [ln for ln in RESUME_OUT.strip().splitlines()
                if not ln.startswith("| ○ open |")]
        self.assertBlocked([
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py resume --iid 123"),
            tool_result("u1", with_manifest(RESUME_OUT, RESUME_CRITICAL)),
            assistant_text("\n".join(body)),
        ], contains="findings.py resume")

    def test_one_dropped_code_line_blocks(self):
        """A single line dropped from inside a fenced code block is the source the user is
        meant to judge the finding against — no tolerance, same as a table row."""
        body = [ln for ln in RESUME_OUT.strip().splitlines()
                if "resp = self._send(req)" not in ln]
        self.assertBlocked([
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py resume --iid 123"),
            tool_result("u1", with_manifest(RESUME_OUT, RESUME_CRITICAL)),
            assistant_text("\n".join(body)),
        ], contains="findings.py resume")

    def test_coincidental_substring_is_not_corruption(self):
        """The corrupted-line check must be anchored to the boundary (glued onto the head
        or tail of another line), not a bare substring test: two independently-pasted,
        fully legitimate blocks can coincidentally share an 8+ char run in the MIDDLE of
        an unrelated line. That coincidence is not gluing, and must not force a block
        (dropping one non-critical, non-fenced, non-table prose line is still within the
        one-line tolerance)."""
        dropped = "- **push 1:** 3 files, +42/-7"
        body = [ln for ln in RESUME_OUT.strip().splitlines() if ln != dropped]
        body.append(f"Summary: {dropped} looks like a safe, small change.")
        self.assertAllowed([
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py resume --iid 123"),
            tool_result("u1", RESUME_OUT),
            assistant_text("\n".join(body)),
        ])

    def test_collapsed_duplicate_critical_line_blocks(self):
        """A block can legitimately contain two IDENTICAL critical lines — this is, in
        fact, exactly the original production report that started this round of fixes: a
        reviewed file with `app.include_router(config.router)` registered twice by
        mistake. If the model's paste silently collapses the duplicate down to one
        occurrence — a very plausible "cleanup" for an LLM to make unprompted — a
        set-membership check ("is this text present somewhere") is blind to it: the text
        IS present, just with the wrong multiplicity, so nothing looks missing. Surfaced
        by a differential fuzz between the pre- and post-difflib-refactor
        implementations (dev-time only, not a repo dependency) as a genuine improvement,
        not a behavior change to guard against — difflib's positional alignment catches
        this where the old set-based check could not."""
        dup_out = ("**2 push(es) since your last review** (tip `abc123def456`)\n\n"
                  "**MR !123** — Add rate limiting\n\n"
                  "| State | Topic | Location |\n"
                  "|---|---|---|\n"
                  "| open | duplicate router registration | backend/idp/main.py:44 |\n\n"
                  "```python\n"
                  "app.include_router(health.router)\n"
                  "app.include_router(config.router)\n"
                  "app.include_router(config.router)\n"
                  "```")
        collapsed = dup_out.replace(
            "app.include_router(config.router)\napp.include_router(config.router)",
            "app.include_router(config.router)")
        manifest = with_manifest(dup_out, [
            "| open | duplicate router registration | backend/idp/main.py:44 |",
            "app.include_router(health.router)",
            "app.include_router(config.router)",
        ])
        self.assertBlocked([
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py resume --iid 123"),
            tool_result("u1", manifest),
            assistant_text(collapsed),
        ], contains="findings.py resume")

    def test_two_dropped_lines_block(self):
        kept = [ln for ln in RESUME_OUT.strip().splitlines()
                if "unbounded retry loop" not in ln and "missing timeout" not in ln]
        self.assertBlocked([
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py resume --iid 123"),
            tool_result("u1", RESUME_OUT),
            assistant_text("\n".join(kept)),
        ])

    def test_errored_run_allows(self):
        """No signature in the output → the command failed and the model is mid-fix."""
        self.assertAllowed([
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py resume --iid 999"),
            tool_result("u1", "Traceback (most recent call last):\nSystemExit: no MR 999"),
            assistant_text("That iid does not exist — which MR did you mean?"),
        ])

    def test_grepping_the_source_allows(self):
        """The command name appears in a grep pattern, not as an invocation that printed a
        block. The signature check is what saves this."""
        self.assertAllowed([
            user_prompt(),
            bash_call("u1", 'grep -n "findings.py resume" skills/review-mr/SKILL.md'),
            tool_result("u1", "344:run `findings.py resume` first — its output is gated"),
            assistant_text("`resume` is mentioned once, at SKILL.md:344."),
        ])

    def test_reading_the_source_allows(self):
        """A Read of the spec itself: not a Bash tool_use at all."""
        self.assertAllowed([
            user_prompt(),
            row("assistant", message={"role": "assistant", "content": [
                {"type": "tool_use", "id": "u1", "name": "Read",
                 "input": {"file_path": REVIEW_SPEC}}]}),
            tool_result("u1", RESUME_OUT),
            assistant_text("The spec gates six commands."),
        ])

    def test_stale_rerender_superseded(self):
        """quote, then a fix, then `set --draft` re-renders: only the NEWEST block has to be
        pasted. Demanding every one blocked correct messages."""
        newer = QUOTE_OUT.replace("Dieser `except` verschluckt jeden Fehler.",
                                  "Bitte den Fehler loggen statt zu verschlucken.")
        self.assertAllowed([
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py quote t4 --iid 123"),
            tool_result("u1", QUOTE_OUT),
            bash_call("u2", 'python3 $SD/findings.py set t4 --draft "Bitte den Fehler loggen"'),
            tool_result("u2", newer),
            assistant_text(newer),
        ])

    def test_stale_rerender_still_requires_the_newest(self):
        newer = QUOTE_OUT.replace("Dieser `except` verschluckt jeden Fehler.",
                                  "Bitte den Fehler loggen statt zu verschlucken.")
        self.assertBlocked([
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py quote t4 --iid 123"),
            tool_result("u1", QUOTE_OUT),
            bash_call("u2", 'python3 $SD/findings.py set t4 --draft "Bitte den Fehler loggen"'),
            tool_result("u2", newer),
            assistant_text("Draft updated for t4 — ok to post?"),
        ], contains="findings.py quote")

    def test_meta_row_does_not_cut_the_turn(self):
        """isMeta rows (skill load, system-reminder) carry a text block but do not start a
        new turn — if they did, `start` would jump past the gated call."""
        self.assertBlocked([
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py resume --iid 123"),
            tool_result("u1", RESUME_OUT),
            row("user", isMeta=True, message={"role": "user", "content": [
                {"type": "text", "text": "<system-reminder>skill loaded</system-reminder>"}]}),
            assistant_text("t1 needs you."),
        ])

    def test_previous_turn_is_not_rechecked(self):
        """A gated call in an EARLIER turn is out of scope, even if it was never pasted."""
        self.assertAllowed([
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py resume --iid 123"),
            tool_result("u1", RESUME_OUT),
            assistant_text(RESUME_OUT),
            user_prompt("what does src/client.py:88 do?"),
            assistant_text("It retries forever — no bound, no backoff."),
        ])

    def test_no_assistant_text_yet_allows(self):
        """The transcript is written asynchronously; judging an empty message would report
        every line as missing."""
        self.assertAllowed([
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py resume --iid 123"),
            tool_result("u1", RESUME_OUT),
        ])

    def test_string_result_shape(self):
        """tool_result content is sometimes a bare string rather than a block list."""
        self.assertBlocked([
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py resume --iid 123"),
            tool_result("u1", RESUME_OUT, as_blocks=False),
            assistant_text("t1 needs you."),
        ])

    def test_dict_result_shape_does_not_false_allow(self):
        """An unexpected content shape must not degrade to a silent allow — `in` against a
        non-string would just always miss."""
        rows = [
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py resume --iid 123"),
            row("user", message={"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "u1",
                 "content": {"stdout": RESUME_OUT}}]}),
            assistant_text("t1 needs you."),
        ]
        self.assertBlocked(rows)

    def test_ungated_command_allows(self):
        self.assertAllowed([
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py sync --iid 123"),
            tool_result("u1", RESUME_OUT),
            assistant_text("Synced. Nothing new."),
        ])


class TestForbidden(HookCase):
    def test_raw_suggestion_fence_blocks(self):
        self.assertBlocked([
            user_prompt(),
            assistant_text("Draft for t4:\n\n```suggestion\nlogger.exception(exc)\n```"),
        ], contains="raw ```suggestion fence")

    def test_blockquoted_suggestion_fence_blocks(self):
        """`[>\\s]*` not `\\s*` — the model hid it inside a blockquote once."""
        self.assertBlocked([
            user_prompt(),
            assistant_text("> ```suggestion\n> logger.exception(exc)\n> ```"),
        ])

    def test_mid_sentence_mention_allows(self):
        """quote's own trailing note mentions the fence mid-sentence."""
        self.assertAllowed([
            user_prompt(),
            assistant_text("The paste payload wraps the code in a ```suggestion fence."),
        ])


class TestRequired(HookCase):
    def test_ack_without_diff_view_blocks(self):
        self.assertBlocked([
            user_prompt(),
            bash_call("u1", "git diff"),
            tool_result("u1", "diff --git a/src/client.py b/src/client.py"),
            assistant_text("Fixed t3 by bounding the loop.\n\nACK to fix up and push?"),
        ], contains="without running `diff-view.sh`")

    def test_ack_with_pasted_diff_view_allows(self):
        self.assertAllowed([
            user_prompt(),
            bash_call("u1", "$SD/diff-view.sh t3"),
            tool_result("u1", DIFF_VIEW_OUT),
            assistant_text("Fixup target: commit 2 (`feat: add client`).\n\n" + DIFF_VIEW_OUT),
        ])

    def test_dropped_diff_view_blocks_on_the_gate_not_the_required_rule(self):
        self.assertBlocked([
            user_prompt(),
            bash_call("u1", "$SD/diff-view.sh t3"),
            tool_result("u1", DIFF_VIEW_OUT),
            assistant_text("Bounded the loop. ACK to fix up and push?"),
        ], contains="You ran `diff-view.sh`")

    def test_mid_sentence_mention_allows(self):
        """Editing the skill and quoting its own wording must not block."""
        self.assertAllowed([
            user_prompt(),
            assistant_text("SKILL.md says never to ask 'ACK to fix up and push?' without "
                           "the diff shown — that is now enforced."),
        ])


class TestFailOpen(HookCase):
    def test_stop_hook_active_allows(self):
        self.assertAllowed([
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py resume --iid 123"),
            tool_result("u1", RESUME_OUT),
            assistant_text("t1 needs you."),
        ], stop_hook_active=True)

    def test_leak_blocks_even_when_stop_hook_active(self):
        """Observed in production, on a real MR review: the model dropped `present`'s
        output, got blocked once for THAT (a different violation), and "fixed" it on
        retry by pasting the raw tool result wholesale — leaking the manifest as a side
        effect. That retry is exactly the turn where stop_hook_active is true, so the
        general loop guard would otherwise let this specific, brand-new violation
        through with no recourse — it was never the thing being retried for."""
        manifest = with_manifest(RESUME_OUT, RESUME_CRITICAL)
        self.assertBlocked([
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py resume --iid 123"),
            tool_result("u1", manifest),
            assistant_text(manifest),
        ], contains="paste-gate:critical", stop_hook_active=True)

    def test_no_transcript_path_allows(self):
        self.assertAllowed([user_prompt()], transcript=False)

    def test_missing_spec_is_skipped(self):
        """That is how "the skill is not installed" is expressed."""
        self.assertAllowed([
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py resume --iid 123"),
            tool_result("u1", RESUME_OUT),
            assistant_text("t1 needs you."),
        ], specs=("/nonexistent/paste-gates.json",))

    def test_other_skills_spec_still_enforces(self):
        """A missing spec must not disable the ones that ARE installed."""
        self.assertBlocked([
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py resume --iid 123"),
            tool_result("u1", RESUME_OUT),
            assistant_text("t1 needs you."),
        ], specs=("/nonexistent/paste-gates.json", REVIEW_SPEC))

    def test_no_specs_at_all_allows(self):
        self.assertAllowed([
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py resume --iid 123"),
            tool_result("u1", RESUME_OUT),
            assistant_text("t1 needs you."),
        ], specs=())

    def test_malformed_spec_is_skipped(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write('{"skill": "broken", "gates": [{"key": "x", "cmd": "([", '
                     '"signature": ["y"], "reason": "z"}]}')
            broken = fh.name
        try:
            self.assertAllowed([
                user_prompt(),
                bash_call("u1", "python3 $SD/findings.py resume --iid 123"),
                tool_result("u1", RESUME_OUT),
                assistant_text("t1 needs you."),
            ], specs=(broken,))
        finally:
            os.unlink(broken)

    def test_unreadable_transcript_allows(self):
        proc = subprocess.run(
            [sys.executable, HOOK, REVIEW_SPEC],
            input=json.dumps({"transcript_path": "/nonexistent/transcript.jsonl"}),
            capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "")

    def test_garbage_stdin_allows(self):
        proc = subprocess.run([sys.executable, HOOK, REVIEW_SPEC], input="not json",
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "")


class TestShippedSpecs(unittest.TestCase):
    """The shipped specs must actually LOAD.

    A malformed spec is dropped silently (that is how "not installed" is expressed), so a
    typo in a regex or a missing key would disable a skill's enforcement without a sound.
    Pinning the rule counts is what makes that loud instead.
    """

    @classmethod
    def setUpClass(cls):
        # Imported by path, not name: the file has a dash in it.
        import importlib.util
        spec = importlib.util.spec_from_file_location("paste_gate", HOOK)
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def test_both_specs_load(self):
        specs = self.mod.load_specs([REVIEW_SPEC, REWORK_SPEC])
        self.assertEqual(len(specs), 2, "a spec was dropped — malformed rule?")
        review, rework = specs
        self.assertEqual([g["key"] for g in review["gates"]],
                         ["review-mr:resume", "review-mr:present", "review-mr:todo",
                          "review-mr:quote", "review-mr:updates", "review-mr:diff"])
        self.assertEqual(len(review["forbidden"]), 1)
        self.assertEqual([g["key"] for g in rework["gates"]],
                         ["rework-mr:present", "rework-mr:todo", "rework-mr:quote",
                          "rework-mr:diff-view", "rework-mr:reply-view",
                          "rework-mr:change-preview"])
        self.assertEqual(len(rework["required"]), 1)

    def test_required_rules_name_a_real_gate(self):
        for spec in self.mod.load_specs([REVIEW_SPEC, REWORK_SPEC]):
            keys = {g["key"] for g in spec["gates"]}
            for req in spec["required"]:
                self.assertIn(req["gate"], keys,
                              "a `required` rule points at a gate that does not exist, so "
                              "it can never be satisfied")

    def test_every_gate_has_a_signature_and_a_real_reason(self):
        """A gate with no signature would fire on errored runs; a stub reason is the model's
        only instruction for fixing the message, so it cannot be a one-liner."""
        for spec in self.mod.load_specs([REVIEW_SPEC, REWORK_SPEC]):
            for gate in spec["gates"]:
                self.assertTrue(len(gate["reason"]) > 80, gate["key"])
                self.assertTrue(gate["signature"], gate["key"])


class TestBothSkills(HookCase):
    def test_change_preview_manifest_critical_line_blocks(self):
        """`rework-mr`'s `change-preview` gate, exercised end to end with a producer
        manifest: paste-gate.py no longer parses fence widths itself at all (that
        concern — including `fence()`'s own widening for a fence containing embedded
        ``` runs, at whatever width — moved entirely to threads.py, see its own
        `test_diff_view_signature_survives_a_widened_fence` and the multi-embed test
        added alongside it). All this hook does now is trust the manifest a gated
        command appends: a line the producer declared critical gets no drop tolerance,
        however deeply it sat inside a widened fence when threads.py built it."""
        change_out = ("**Change (t9):** bound the retry loop\n\n"
                      "````python\n"
                      "# before\n"
                      "while True:\n"
                      "    resp = self._send(req)\n\n"
                      "# illustrated fix, add a comment block like:\n"
                      "```\n"
                      "retries are now bounded\n"
                      "```\n"
                      "for _ in range(MAX_RETRIES):\n"
                      "    resp = self._send(req)\n"
                      "````\n\n"
                      "Agreed?")
        body = [ln for ln in change_out.splitlines() if "for _ in range" not in ln]
        manifest = with_manifest(change_out, [
            "while True:", "resp = self._send(req)", "retries are now bounded",
            "for _ in range(MAX_RETRIES):",
        ])
        self.assertBlocked([
            user_prompt(),
            bash_call("u1", "$SD/change-preview.sh t9"),
            tool_result("u1", manifest),
            assistant_text("\n".join(body)),
        ], contains="change-preview.sh")

    def test_keys_do_not_collide_across_skills(self):
        """Both skills have a `quote` gate. Namespacing keeps one from superseding the
        other in the per-key reduction."""
        self.assertBlocked([
            user_prompt(),
            bash_call("u1", "python3 $SD/threads.py quote t7 --iid 5"),
            tool_result("u1", REWORK_QUOTE_OUT),
            bash_call("u2", "python3 $SD/findings.py quote t4 --iid 123"),
            tool_result("u2", QUOTE_OUT),
            assistant_text(QUOTE_OUT),          # review-mr's pasted, rework-mr's dropped
        ], contains="threads.py quote")

    def test_rework_present_gate(self):
        out = RESUME_OUT.split("---", 1)[1]
        self.assertBlocked([
            user_prompt(),
            bash_call("u1", "python3 $SD/threads.py present --iid 5"),
            tool_result("u1", out),
            assistant_text("t1 is up first — the retry loop."),
        ], contains="threads.py present")

    def test_rework_sync_not_gated(self):
        """`sync` is silent prep in the opener; gating it would false-block every opener."""
        self.assertAllowed([
            user_prompt(),
            bash_call("u1", "python3 $SD/threads.py sync --iid 5"),
            tool_result("u1", RESUME_OUT),
            assistant_text("Synced — nothing new from the reviewer."),
        ])


if __name__ == "__main__":
    unittest.main(verbosity=2)
