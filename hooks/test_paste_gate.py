#!/usr/bin/env python3
"""Tests for the paste-enforcement Stop hook (`paste-gate.py`) — the fast ones.

Run: `python3 hooks/test_paste_gate.py` (stdlib only, no deps).

Every case here is either the hook's contract or a bug that was found in production and
fixed — the tests exist so the next engine change cannot quietly bring one back. The hook
is exercised as a SUBPROCESS with a synthetic transcript, because its contract is exactly
that: transcript JSONL + stdin JSON in, `{"decision":"block"}` or silence out.

Split from the "blocks" cases in test_paste_gate_slow.py: every gated command that ends
up BLOCKED pays the hook's own widening-delay retry (see hooks/README.md's "Patient")
before it commits to blocking — real time.sleep(), since the hook runs as a genuine
subprocess and there's no reaching into it to mock that from here. Measured: the ~20
"blocks" cases run ~1.85s each; every case here runs under 0.15s. Keeping them apart
means the tests you run on every small change stay fast, and the slow ones are there
when you're actually touching the retry/blocking logic (`python3
hooks/test_paste_gate_slow.py`, or the runner's `--slow` flag).
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

    def test_cwd_reset_noise_does_not_count_against_the_one_line_tolerance(self):
        """The Bash tool itself appends "Shell cwd was reset to ..." to a command's
        result whenever a gated command's own `cd` changes the shell's directory — that
        line is harness plumbing, not part of findings.py's own output, and the model was
        never going to paste it back. Observed in production: combined with an otherwise
        tolerable header swap (see test_one_dropped_line_tolerated above), this noise
        line alone pushed the count of missing lines from one to two and forced a block
        that had nothing real to correct."""
        body = RESUME_OUT.strip().splitlines()[1:]
        self.assertAllowed([
            user_prompt(),
            bash_call("u1", "cd /some/wt && python3 $SD/findings.py resume --iid 123"),
            tool_result("u1", RESUME_OUT + "\nShell cwd was reset to /some/wt"),
            assistant_text("Two pushes since your last look.\n" + "\n".join(body)),
        ])

    def test_cwd_reset_noise_stripped_but_a_real_second_drop_still_blocks(self):
        """The noise line is invisible to the check, but it is not a second free pass —
        drop a genuine second line on top of it and the block still fires."""
        dropped = "- **push 1:** 3 files, +42/-7"
        body = [ln for ln in RESUME_OUT.strip().splitlines()[1:] if ln != dropped]
        self.assertBlocked([
            user_prompt(),
            bash_call("u1", "cd /some/wt && python3 $SD/findings.py resume --iid 123"),
            tool_result("u1", RESUME_OUT + "\nShell cwd was reset to /some/wt"),
            assistant_text("Two pushes since your last look.\n" + "\n".join(body)),
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

    def test_ungated_command_allows(self):
        self.assertAllowed([
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py sync --iid 123"),
            tool_result("u1", RESUME_OUT),
            assistant_text("Synced. Nothing new."),
        ])


class TestForbidden(HookCase):
    def test_mid_sentence_mention_allows(self):
        """quote's own trailing note mentions the fence mid-sentence."""
        self.assertAllowed([
            user_prompt(),
            assistant_text("The paste payload wraps the code in a ```suggestion fence."),
        ])


class TestRequired(HookCase):
    def test_ack_with_pasted_diff_view_allows(self):
        self.assertAllowed([
            user_prompt(),
            bash_call("u1", "$SD/diff-view.sh t3"),
            tool_result("u1", DIFF_VIEW_OUT),
            assistant_text("Fixup target: commit 2 (`feat: add client`).\n\n" + DIFF_VIEW_OUT),
        ])

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
