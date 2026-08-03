# Rework MR — Phase 3 reference

Full code-and-reply mechanics. `SD=~/.claude/skills/rework-mr/scripts`.

**One topic all the way through before the next.** Take a topic change → blame → diff → ACK →
fixup → push → diff URL → reply, then start the next. Never edit a second topic's code while one
is in flight. **Never batch several topics into one diff/push unless the user explicitly asks** —
one push and one `Fixed: <url>` per topic by default. Two ACK stops per topic: after the diff
(before any commit/push) and after the reply draft (before posting).

A topic whose plan is reply-only / push-back / question skips steps 1–9 — just draft and send
the reply (step 10).

1. **Bug/problem pointed out → write a failing test first** (use the `tdd` skill), then fix.
   For non-bug changes, make the change directly.
2. Keep the code clean for the *merged* result: no "changed from before" / "we no longer do X"
   comments, no leftovers of earlier iterations.
3. **Light QA while iterating** (unit tests, linter/formatter — partial is fine). You know the
   project's commands; this skill doesn't hardcode them.
4. **Silently, determine the fixup target(s)** — blame the changed hunks, group by the branch
   commit that introduced them (a single topic's change may touch **several** original commits).
   **Exception:** if a hunk fixes/refactors code the branch did *not* add (blame older than the
   branch point), that's a separate real commit made *before* the fixups — never fold
   pre-existing-code changes into MR commits; flag such splits to the user. Do this *before*
   showing the diff — blaming *after* is the bug (see below).
5. Only THEN, as your **final** action before replying, run `diff-view.sh <t>` and **paste its
   ENTIRE output verbatim as the rest of your message, then STOP**: state the fixup target(s) —
   one per commit, `→ fixup into <sha> ("<subject>")` — in 1–2 lines, followed by the diff-view
   block (the diff + the "ACK to fix up and push?" question). Iterate until the user explicitly
   **ACKs**. Nothing gets committed before that. **Postcondition:** the diff-view block (fenced
   diff + the ACK question) must be pasted verbatim; if you summarised or described the diff
   instead of showing it, you dropped it — redo it. (The repeated bug was showing the diff, then
   blaming/naming targets afterward — the `git blame` call in between pushed the diff out of mind
   by the time the message was written. A `Stop` hook enforces the diff-view block actually
   reaching the user, the same way it does for `present`/`quote`/`reply-view`/`change-preview`.)
6. On ACK, **fixup — not a new commit** (the target(s) were already determined in step 4). A
   `fix(...)`/`refactor(...)` commit for code this MR branch introduced is almost always wrong
   (keep history clean for merge):
   ```bash
   git commit --fixup=<sha>          # repeat per target commit
   ```
7. **Capture the diff baseline before the first push** of this topic:
   ```bash
   python3 $SD/diff-url.py baseline
   python3 $SD/threads.py set <t> --start-sha <sha>
   ```
8. `git rebase --autosquash` onto the base (non-interactive), then **full QA** (the hard gate —
   always run the complete check suite before any push), then:
   ```bash
   git push --force-with-lease --force-if-includes
   ```
9. Build the topic's diff URL (spans every push for this topic via the stored baseline):
   ```bash
   python3 $SD/diff-url.py url --start-sha <stored-start-sha>
   python3 $SD/threads.py set <t> --diff-url <url>
   ```
   Never a commit URL — fixup + force-push rewrites commit hashes and rots the link.
10. Reply — thread + draft + URL are shown via **one** command so none can be dropped:
   a. Write the reply **body only** (raw, no `>` prefixes) to the draft file with a **quoted
      heredoc, not the Write tool** (Write can't overwrite a file unread this session → fails on
      resume). Draft rules (SKILL.md): body in the thread's language, **scaffolding in the
      session language**, and **NEVER an internal topic handle** (`t5`…) in the body — reword
      (link another thread's URL).
      ```bash
      d=$(dirname "$(python3 $SD/threads.py path --iid <n>)")
      cat > "$d/reply-<t>.md" <<'REPLY_EOF'
      <the reply body, verbatim — NO leading "> " on any line>
      REPLY_EOF
      ```
   b. `python3 $SD/threads.py reply-view <t>` — **paste its ENTIRE output verbatim as your whole
      message, then STOP**: whole thread (original + every reply) + `**Draft reply:**`
      (blockquoted) + thread URL + the `c`/`p`/`n` prompt, one block. The prompt is the last line, so
      **no `AskUserQuestion` menu** — pasting the block is the ask. It **hard-refuses a draft with
      a topic handle** (`t<number>`) — reword and re-run if it errors. Postcondition: the message
      *is* that output (reviewer's blockquoted note → `**Draft reply:**` → prompt line); if any is
      missing you dropped it → re-run and paste. Don't replace it with a stub even across topics.
   c. Interpret the user's reply — **`c`** = copy, **`p`** = post, **`n`** = next topic (already
      replied/resolved: `set <t> --state waiting`, move on), **anything else** = discussion (no
      `d` command: engage with it, refine, rewrite the file, re-run reply-view, paste again):
   ```bash
   # c — Copy (guard-reply runs inside clip.sh):
   $SD/clip.sh "$d/reply-<t>.md"
   # p — Post: guard, then post (the skill's one allowed write; <discussion_id> = thread_ids[0]):
   $SD/guard-reply.sh "$d/reply-<t>.md" && \
   glab api projects/<enc>/merge_requests/<iid>/discussions/<discussion_id>/notes \
     -X POST -F body=@"$d/reply-<t>.md"
   ```
   After Post (or a confirmed paste) mark it `set <t> --state waiting` → `sync` shows `◐ waiting`.
   Don't resolve the thread yourself — the reviewer resolves it, and then it flips to `done`.
