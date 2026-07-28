# Review MR — reference

Draft rules and the re-review loop in full. `SD=~/.claude/skills/review-mr/scripts`.
The skill is **read-only against GitLab** — you draft, the user posts and resolves.

## Draft rules (Phase 2 and follow-ups)

- **Language: German. Hardcoded** — the MR title/description are English, but review
  comments are German. Informal **du**. As short as possible.
- **Concrete code only when it earns its place.** For a line-precise fix, prefer a GitLab
  ` ```suggestion ` block (renders as one-click-apply for the author). Bigger changes → a
  normal fenced snippet in the right language.
- No headings. Identifiers in `backticks`. Bullets only if they genuinely help.
- **Per kind:**
  - **issue** → crisp "what's off" + optional suggestion/snippet.
  - **question** (❓) → just the question, one or two lines — you're asking, not demanding.
  - **praise** (💚) → one warm line. (You typically post it and resolve right after.)
- Show the draft as GitLab-compatible markdown *in your chat reply* so the user can copy it,
  and offer `clip.sh` too. **Never post it yourself.**

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
   It prints, per push, the **compare URL** (`…/diffs?diff_id=<v>&start_sha=<prev>`), a
   **diffstat** (`2 files, +33 −23`), and the **topics whose files it touches**. Add your prose
   summary of *what* changed on top, and offer to `open` any URL. Diffstats/topic-mapping need
   the review worktree fetched (objects present); without a worktree the URLs still print.
   If the user wants a fresh critique of the newly pushed code, review the range ad hoc and
   `add`/`import` any new issues as fresh topics — dedup against existing ones and **flag
   anything you drop**, never truncate silently. (This is optional and costs tokens — ask.)

3. **Work the `◐ needs-ack` topics, one at a time.** For each:
   ```bash
   python3 $SD/findings.py quote <t>   # the thread's notes (author's reply, resolved flag)
   python3 $SD/findings.py diff <t>    # compare URL + inline git cmd for THIS topic's change
   ```
   Paste `quote`, add a **short summary of what the author did**, and judge it:
   - **Looks clearly addressed** (a "trivially fixed" case — the change matches the ask, or a
     question got a clean answer) → say so plainly so the user can ack fast. Still **offer to
     show the change** via `diff <t>`: small → run its inline `git diff` and show it; big →
     paste the compare URL and offer to `open` it.
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
(reviewers' court); a reviewer (you or a peer) last ⇒ `○ open` (awaiting the author). Dropping
an adopted topic is temporary — the next `sync` re-adopts the live thread.

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
