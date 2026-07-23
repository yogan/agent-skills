# Rework MR — Phase 3 reference

Full code-and-reply mechanics. `SD=~/.claude/skills/rework-mr/scripts`.

**One topic all the way through before the next.** Take a topic change → diff → ACK → fixup →
push → diff URL → reply, then start the next. Never edit a second topic's code while one is in
flight. **Never batch several topics into one diff/push unless the user explicitly asks** — one
push and one `Fixed: <url>` per topic by default. Two ACK stops per topic: after the diff
(before any commit/push) and after the reply draft (before posting).

A topic whose plan is reply-only / push-back / question skips steps 1–8 — just draft and send
the reply (step 9).

1. **Bug/problem pointed out → write a failing test first** (use the `tdd` skill), then fix.
   For non-bug changes, make the change directly.
2. Keep the code clean for the *merged* result: no "changed from before" / "we no longer do X"
   comments, no leftovers of earlier iterations.
3. **Light QA while iterating** (unit tests, linter/formatter — partial is fine). You know the
   project's commands; this skill doesn't hardcode them.
4. Show the working diff (`git diff`) and let the user check **personally**. Iterate until they
   explicitly **ACK**. Nothing gets committed before that.
5. On ACK, **fixup — not a new commit**. A `fix(...)`/`refactor(...)` commit for code this MR
   branch introduced is almost always wrong (keep history clean for merge). Blame the changed
   hunks, group by the branch commit that introduced them, and create one fixup per target (a
   single topic's change may touch **several** original commits):
   ```bash
   git commit --fixup=<sha>          # repeat per target commit
   ```
   **Exception:** if a hunk fixes/refactors code the branch did *not* add (blame older than the
   branch point), that is a separate real commit made *before* the fixups — never fold
   pre-existing-code changes into MR commits. Flag such splits to the user.
6. **Capture the diff baseline before the first push** of this topic:
   ```bash
   python3 $SD/diff-url.py baseline
   python3 $SD/threads.py set <t> --start-sha <sha>
   ```
7. `git rebase --autosquash` onto the base (non-interactive), then **full QA** (the hard gate —
   always run the complete check suite before any push), then:
   ```bash
   git push --force-with-lease --force-if-includes
   ```
8. Build the topic's diff URL (spans every push for this topic via the stored baseline):
   ```bash
   python3 $SD/diff-url.py url --start-sha <stored-start-sha>
   python3 $SD/threads.py set <t> --diff-url <url>
   ```
   Never a commit URL — fixup + force-push rewrites commit hashes and rots the link.
9. Draft the reply (rules in SKILL.md), then **on the user's explicit ACK** either post it or
   copy it — offer both:
   ```bash
   # post:
   glab api projects/<enc>/merge_requests/<iid>/discussions/<discussion_id>/notes \
     -X POST -F body=@reply.md
   # or copy for manual paste:
   python3 $SD/threads.py path        # <state-dir> for scratch files
   $SD/clip.sh reply.md
   ```
   Posting your reply makes your note the thread's last → next `sync` shows it `◐ replied`.
   Don't resolve the thread yourself — the reviewer resolves it, and then it flips to `done`.
