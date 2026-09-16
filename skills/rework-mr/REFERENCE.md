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
   ENTIRE output verbatim as the rest of your message, then STOP**: first a short summary —
   what the reviewer asked for, by name, and what you did about it ("Robin asked for X; rewrote
   `Foo.bar()` to … and added two tests") — then the fixup target(s), one per commit,
   `→ fixup into <sha> ("<subject>")`, in 1–2 lines, followed by the diff-view
   block (what changed + the script's own closing ACK question, whose exact wording names the
   answers other than an ACK — paste it, never retype it). The summary is prose about
   the reasoning, not a retelling of the diffstat the block already prints, and it scales with
   the change: one line ("Applied exactly as suggested.") where the fix is short and literally
   what was asked, nothing at all on a reply-only topic. Iterate until the user
   explicitly **ACKs**. Nothing gets committed before that. The diff itself goes to a diff
   viewer in its own tmux window, named in the block; `diff-view.sh` falls back to an inline
   fenced diff by itself when there is no viewer, so you never choose between the two shapes.
   Up to 3 short notes can be anchored in that window with `--note FILE:LINE:TEXT`, only where
   the change deviates from the agreed plan, does more than was asked, or is not obvious from
   the diff. **Postcondition:** the diff-view block must be pasted verbatim; if you summarised
   or described it instead of showing it, you dropped it — redo it. (The repeated bug was
   showing the diff, then
   blaming/naming targets afterward — the `git blame` call in between pushed the diff out of mind
   by the time the message was written. A `Stop` hook enforces the diff-view block actually
   reaching the user, the same way it does for `present`/`quote`/`reply-view`/`change-preview` —
   and it blocks an ACK request that has no `diff-view.sh` run behind it at all.)
6. On ACK, **read the viewer's notes first**: `python3 $SD/threads.py hunk-notes`. The user
   may answer on the diff's own lines rather than in chat, and any output at all means do not
   push — answer each note and go back to step 5. No output means go ahead. Run it whatever
   they typed, so there is nothing for them to remember. Reading a note **removes it from
   the window**, so that output is your only copy — address every line of it in this turn.
   Your own notes are cleared each time a diff goes up, so the window never shows an
   annotation belonging to a diff that has been replaced.
   Then **fixup — not a new commit** (the target(s) were already determined in step 4). A
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
   python3 $SD/threads.py hunk-close
   ```
   The rebase left the working tree clean, so the viewer would otherwise sit there showing a
   diff that no longer exists while the conversation moves to the next topic. `hunk-close` is
   silent when it closes the window and when there was none; it speaks only if a note arrived
   after the last read, and then it leaves the window alone — that note is a further change on
   this topic, to be read with `hunk-notes` and worked from step 1.
9. Build the topic's diff URL (spans every push for this topic via the stored baseline):
   ```bash
   python3 $SD/diff-url.py url --start-sha <stored-start-sha>
   python3 $SD/threads.py set <t> --diff-url <url>
   ```
   Never a commit URL — fixup + force-push rewrites commit hashes and rots the link.
10. Reply — thread + draft + URL are shown via **one** command so none can be dropped:
   a. Store the reply **body only** (raw, no `>` prefixes) in the state file with a **quoted
      heredoc** — `<<'REPLY_EOF'`, so backticks and `$` in the body are not expanded by the
      shell. Draft rules (SKILL.md): body in the thread's language, **scaffolding in the
      session language**, and **NEVER an internal topic handle** (`t5`…) in the body — reword
      (link another thread's URL); `set` refuses it anyway.
      ```bash
      python3 $SD/threads.py set <t> --reply - <<'REPLY_EOF'
      <the reply body, verbatim — NO leading "> " on any line>
      REPLY_EOF
      ```
   b. `python3 $SD/threads.py reply-view <t>` — **paste its ENTIRE output verbatim as your whole
      message, then STOP**: the fenced code the comment is anchored to + the whole thread
      (original + every reply) + `**Draft reply:**` (blockquoted) + thread URL + the `c`/`p`/`n`
      prompt, one block. The prompt is the last line, so **no `AskUserQuestion` menu** — pasting
      the block is the ask. It **hard-refuses a draft with a topic handle** (`t<number>`) — reword
      and re-run if it errors. Postcondition: the message
      *is* that output (code → reviewer's blockquoted note → `**Draft reply:**` → prompt line);
      if any is missing you dropped it → re-run and paste. Don't replace it with a stub even across topics.
      That is the topic's **first** reply view only. `reply-view <t> --refine` is the render for
      every later one on the same topic (draft + thread URL + prompt, no code, no thread): the
      context is already on screen and unchanged, and repeating it buries the reworded draft.
      It checks rather than trusts that — a context that moved since (a new reviewer note, a
      re-anchored comment) or was never shown comes back in full, with a line saying why.
   c. Interpret the user's reply — **`c`** = copy, **`p`** = post, **`n`** = next topic (already
      replied/resolved: `set <t> --state waiting`, then **open the next one with
      `quote <next-t>` pasted as the whole message, and stop** — `n` names the next comment to
      SHOW, not the next fix to start), **anything else** = discussion (no
      `d` command: engage with it, refine, store it again with `set <t> --reply -`, re-run
      `reply-view <t> --refine`, paste that):
   ```bash
   # c — Copy (`reply` guards, and so does clip.sh):
   python3 $SD/threads.py reply <t> | $SD/clip.sh
   # p — Post (the skill's one allowed write; <discussion_id> = thread_ids[0]):
   body=$(python3 $SD/threads.py reply <t>) && printf '%s\n' "$body" | \
   glab api projects/<enc>/merge_requests/<iid>/discussions/<discussion_id>/notes \
     -X POST -F body=@-
   ```
   The `&&` is load-bearing: a draft `reply` refuses (internal topic handle) must not reach
   `glab` as an empty body.
   After Post (or a confirmed paste) mark it `set <t> --state waiting` → `sync` shows `◐ waiting`.
   Don't resolve the thread yourself — the reviewer resolves it, and then it flips to `done`.
