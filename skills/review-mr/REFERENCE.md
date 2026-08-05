# Review MR — reference

Draft rules and the re-review loop in full. `SD=~/.claude/skills/review-mr/scripts`.
The skill is **read-only against GitLab** — you draft, the user posts and resolves.

## Draft rules (Phase 2 and follow-ups)

- **Language: whatever `drafts in <lang>` says.** Every view you draft from — `sync`,
  `todo`, `present` and `quote <t>` — ends its header with `· drafts in <lang>`. That is the
  instruction; it wins for the whole session.
  - **Never guess it, and never carry it over from an earlier session.** If the marker is
    not visible in the output you are looking at, run `python3 $SD/findings.py lang` and use
    what it prints. Assuming a default is how an all-English project got a German draft.
  - Do **not** infer the language from the MR itself: title and description are typically
    English while review comments may be German, so mirroring them is wrong.
  - It is configured per repo (`findings.py lang --set en`), stored at
    `~/.claude/review-mr/<slug>/lang`.
  - For **de**: informal **du**. Any language: as short as possible.
- **Concrete code only when it earns its place.** For a line-precise fix, prefer a GitLab
  ` ```suggestion ` block (renders as one-click-apply for the author). Bigger changes → a
  normal fenced snippet in the right language.
- No headings. Identifiers in `backticks`. Bullets only if they genuinely help.
- **Per kind:**
  - **issue** → crisp "what's off" + optional suggestion/snippet.
  - **question** (❓) → just the question, one or two lines — you're asking, not demanding.
  - **praise** (💚) → one warm line. (You typically post it and resolve right after.)
- Show the draft *in your chat reply* via `quote <t>` (it also states where to open the
  thread — `file:line`), and offer the clipboard with `clip.sh <(… draft <t>)`. Copy with
  **`draft <t>`** (body only), never `quote <t>` (its meta header must not reach the posted
  comment). **Never post it yourself.**

## Reconciling the user's hand-posted comments

The user reviews the code in parallel and often posts some comments by hand before you finish
drafting (they're slower than you). Fold those in — **never silently**:

```bash
python3 $SD/findings.py candidates      # threads YOU authored that no topic references yet
```

For each candidate, propose the match to an existing draft topic by `file:line` + gist, or
propose it as a **new** topic (guess a severity, show the user, let them adjust). On the
user's confirmation:

```bash
python3 $SD/findings.py link <t> <discussion_id>            # match to an existing topic (✎→○)
python3 $SD/findings.py add --file … --line … --summary … --source human   # or a brand-new topic
```

When the same point was found by both you and the agent, `merge` them — the source becomes
👥 and, per the design, **your** wording/severity wins.

## The re-review loop — one check, step by step

A review spans days; the state file persists across sessions. Each check:

1. **Sync.** Reconcile the live threads and surface inbound ones:
   ```bash
   python3 $SD/findings.py sync        # overview table + a one-line push banner; paste verbatim
   ```
   `sync` reconciles toward GitLab (the source of truth for a thread's existence and its
   resolved flag). The local file only overlays *your* ack/wontfix. A thread the author
   resolved shows `◐ needs-ack`, not closed — your ack is the only close. `sync` also adopts
   threads you didn't open (see *Inbound threads* below).

2. **Any updates? (author push).** Rework is almost always a force-push, so detection is by
   **head-SHA delta, never commit count**. The banner says if the tip moved; for the detail:
   ```bash
   python3 $SD/findings.py updates     # each push since your baseline; paste verbatim
   ```
   Each push is a `- **push N:** <url>` bullet with a nested `  - ` detail line — either a
   **diffstat** (`2 files, +33 −23`) + **topics touched**, or — when the branch was rebased
   (its base SHA moved) — a rebase classification:
   - **↻ pure rebase** — only the base moved, no author content change; nothing to re-review.
   - **⚠️ rebase + N real change(s) folded in** — the annoying case: someone rebased *and*
     edited/added commits in one push. It lists the new/edited commit subjects so you know real
     work is hidden in there — inspect via the URL. **Call this out to the user explicitly.**
   - **↻ rebase (couldn't classify)** — the API didn't return version commits; use the URL.

   Add your **one-line summary as a `  - ` sub-bullet** under each push (see SKILL *Resuming*),
   and offer to `open` any URL. This is all **server-side** (GitLab compare API + version commit
   lists), so it needs no worktree and — crucially — **survives force-push**: the intermediate
   version heads a force-push prunes from the local repo still exist on GitLab, so diffstats and
   the rebase classification keep working where a local `git diff`/`range-diff` would fail.
   If the user wants a fresh critique of the newly pushed code, review the range ad hoc and
   `add`/`import` any new issues as fresh topics — dedup against existing ones and **flag
   anything you drop**, never truncate silently. (This is optional and costs tokens — ask.)

3. **Work the `◐ needs-ack` topics, one at a time.** For each:
   ```bash
   python3 $SD/findings.py quote <t>   # the thread's notes (author's reply, resolved flag)
   python3 $SD/findings.py diff <t>    # THIS topic's change since you posted (server-side)
   ```
   Paste `quote`, add a **short summary of what the author did**, and judge it. **The thread is
   the source of truth — judge against what was *agreed there*, not against the finding's
   original one-line summary.** Points get down-scoped in discussion: if you said a fix was
   optional and the author added a TODO / opened a ticket and resolved, the bar is *that*, not
   the original defect. Re-deriving the original problem from the code and calling it "not
   fixed" is a classic mistake — read the notes first.
   - **Agreed & done** — the author did what the thread converged on (the fix, or the agreed
     TODO/defer, or a clean answer to a question) → say so plainly so the user can ack fast, or
     `⊘ wontfix --ticket` for a tracked defer. **Confirm via `diff <t>`** (topic file's diff
     inline when small, else the compare URL) — server-side, so it survives the force-pushes
     that prune the baseline sha locally. Use the diff to *confirm the agreed change landed*,
     not to reopen a settled scope.
   - **Author replied without a code change** (pushed back, or asked *you* something) → this is
     **not** a "fixed" case. Surface their point; it needs *your reply* (draft one) or your
     agreement (→ wontfix), never a silent ack.
   - **Diverged / partial / subtle** → walk it carefully, show the diff, don't rush.

   On the user's decision:
   ```bash
   python3 $SD/findings.py set <t> --state acked                 # ● satisfied
   python3 $SD/findings.py set <t> --state wontfix --ticket ABC-1  # ⊘ agreed not to fix / deferred
   python3 $SD/findings.py set <t> --state reset                 # clear an overlay (re-open)
   ```
   Not satisfied → draft a follow-up reply (draft rules above); the topic stays `○`/`◐`.

4. **Advance the baseline** once you've reviewed the current push, so the next check's diffs
   and re-review start from here:
   ```bash
   python3 $SD/findings.py set-head
   ```

5. **Always end with what's left:**
   ```bash
   python3 $SD/findings.py todo        # ✎ still to post + ◐ still needing your ack
   ```

6. **Merge readiness.** `sync`/`present`/`status` fetch approval + `detailed_merge_status` and
   render an Approvals/Merge footer plus a nudge. When every topic is closed on your side *and*
   GitLab reports all threads resolved, it nudges to **approve** — on the user's explicit ACK,
   `glab mr approve <iid>` (from the worktree). If the merge is still blocked afterwards, name
   the blocker and whose turn (rebase/CI/conflict → author). If a later re-review reopens work
   after you approved, it offers `glab mr revoke <iid>` (explicit ACK only). Approve/revoke are
   the *only* GitLab writes — never comment or resolve for the user.

## Inbound threads (peer reviewers / the author)

`sync` auto-adopts every unresolved thread you didn't open as a topic, so it shows in your
tables and needs-ack flow — you're not blind to a discussion just because someone else started
it:

- **💬 peer** — another reviewer's thread. First-class: you can ack it, push back, or `merge`
  it into one of your findings when it's the same point (merge sets source 👥).
- **🖊️ author** — the author's own thread on their MR (rare). Shown so an author question
  pinging the reviewers doesn't get lost.

Adopted topics start at severity ⚪ (unknown) — reclassify with `set <t> --severity …` if you
want. Whose-turn is derived around the **author**: the author speaking last ⇒ `◐ needs-ack`
(reviewers' court); a reviewer (you or a peer) last ⇒ `○ open` (awaiting the author). **`drop`
an adopted topic to dismiss a thread you don't want to track** — its discussion id is remembered
in `ignored`, so `sync` won't re-adopt it.

Notes on a couple of edges:
- **Location on a *posted* topic is the thread's** — `set <t> --file/--line` only affects an
  unposted draft; once linked, GitLab's position wins (and is what you want).
- **`link` promptly after posting.** `link` captures the diff baseline for `diff <t>` at link
  time; linking long after posting (and after a `set-head`) can make that baseline too new.

## Praise

Praise is **detect-only** — you never draft or send it from here. If you posted a 💚 comment
(usually resolved right after), label the matched topic `set <t> --kind praise`; a resolved
praise topic lands terminal (`●`) with no ack loop.

## New findings at any time

Second-look issues (yours or the agent's) can appear whenever — during curation, after a push,
mid-loop. Just `add` (source 👤) or `import` (source 🤖) them; ids are append-only and never
reused, so a late finding slots in cleanly. Draft and post it through the normal path.

## State & files

- Per-MR state: `~/.claude/review-mr/<slug>--mr<iid>/findings.json` (survives sessions).
- Repo review-worktree path: `~/.claude/review-mr/<slug>/worktree`.
- `findings.py path` prints the state file; scratch drafts can live beside it.
