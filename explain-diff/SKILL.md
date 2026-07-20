---
name: explain-diff
description: Generates a rich, interactive, self-contained HTML explanation of a code change — background, intuition, code walkthrough, and a quiz — then opens it in the browser. Accepts the current branch, a named local/remote branch, a GitLab MR (e.g. "MR !123", resolved via glab), or just the last commit. Use when the user wants a diff, branch, commit, or MR explained, turned into a learning doc, or invokes /explain-diff.
---

# Explain Diff

<!-- Source: adapted from https://gist.github.com/geoffreylitt/a29df1b5f9865506e8952488eac3d524
     (explain-diff-html.md), with the render.py extraction + quiz-randomization
     fix from https://gist.github.com/ankitg12/8e808d387799de4e9839bc393f8e6405.
     Dropped the Notion export step; reworked target resolution to reuse this
     project's detached-HEAD-safe branch/MR handling (see ~/.claude/skills/review-branch). -->

Output is critique-free: a teaching document, not a review. No findings, no severity — just explanation.

## Workflow

### Step 1 — Resolve the target

Translate the user's request to one canonical spec:

| User says | Spec |
|---|---|
| nothing, "this branch", "this diff" | `""` (blank) |
| "branch foo/bar", "remote branch feat/bar" | `branch:foo/bar` |
| "MR !123", "PR 123", "merge request 123" | `mr:123` |
| "last commit", "most recent commit" | `commit:HEAD` |
| "commit abc1234" | `commit:abc1234` |

Run the bundled script (handles detached HEAD transparently, same as `/review-branch`):

```bash
bash ~/.claude/skills/explain-diff/scripts/resolve-target.sh "<spec>"
```

Source the output variables: `BASE`, `HEAD_REF`, `LABEL`, `IS_SINGLE_COMMIT`.

If the script errors (not a git repo, unknown branch, MR not found, `glab` unavailable), report the error and stop.

### Step 2 — Collect raw material

Run in parallel:

```bash
git log --oneline "$BASE".."$HEAD_REF"
git diff "$BASE".."$HEAD_REF"
git diff --name-only "$BASE".."$HEAD_REF"
```

### Step 3 — Explore surrounding code

For the **Background** section you need more than the diff. Read the touched files in full (not just hunks) and skim the modules/functions that call into or are called by the changed code — enough to explain the system the change lives in, not just the change itself.

### Step 4 — Write the content spec (don't hand-write HTML)

**Use `render.py`** (bundled at `~/.claude/skills/explain-diff/scripts/render.py`) instead of writing the full HTML page by hand. It owns all CSS, quiz JavaScript, page scaffolding, table of contents, and quiz-option randomization — regenerating that boilerplate per invocation wastes tokens and drifts in quality. Run `python3 ~/.claude/skills/explain-diff/scripts/render.py --help` if you need the exact JSON schema.

Write only a small JSON content spec covering these sections, in this order:

- **Background** — Explain the existing system relevant to this change (from Step 3's exploration). Assume the reader's prior knowledge is unknown: include a deep background for beginners (note it can be skipped by the already-familiar), then a narrower background directly relevant to the change.
- **Intuition** — Explain the core intuition behind the change. Focus on the essence, not every detail. Use concrete examples with toy data. Use figures/diagrams liberally.
- **Code** — A high-level walkthrough of the changes, grouped/ordered so it reads as a narrative rather than a file-by-file dump.
- **Quiz** — Five questions that test understanding, medium difficulty (must understand the substance to answer, no gotchas). Give each a `question` and `options` (each `{text, correct}`) — list options in whatever order reads naturally; `render.py` randomizes their displayed order itself, so don't try to fake randomness by hand. `question`/`text` are plain text: wrap identifiers, branch names, function calls, etc. in backticks (e.g. `` `origin/main` ``, `` `HEAD_REF` ``) — the renderer turns those into inline `<code>` automatically.

Content rules for each section's `html` field (raw HTML, not markdown):

- Write with the clarity and flow of Martin Kleppmann — engaging, classic technical-writing style, smooth transitions between sections.
- Diagrams: reuse a small number of diagram families throughout (e.g. a simplified UI mock for UI changes, a system/data-flow diagram with example data for backend changes). Define each as a small graph (nodes/edges) in the spec's top-level `"diagrams"` dict, drop it into a section's `html` via a bare `{{diagram:name}}` token — it already expands into the full bordered, click-to-enlarge card, don't wrap it in your own `<div class="diagram">`. `render.py` renders it through Graphviz into a scalable, zoomable inline SVG. Run `render.py --help` for the exact node/edge JSON shape. Never ASCII art, and don't hand-write flowchart HTML — the diagrams dict is the only path now.
- Code blocks: use `<pre><code class="language-XXX">` for syntax highlighting (renderer does the tokenizing client-side, no CDN) — `<pre>` alone still works but loses highlighting. For a diff-style snippet, use `language-diff-XXX` so `+`/`-` lines get diff coloring *and* nested syntax highlighting (plain `language-diff` if the language isn't worth specifying); partial/incomplete snippets tokenize fine, no need for valid complete syntax. See `render.py --help` for the full language list and diff-line format.
- Use `.callout` divs for key concepts, definitions, and important edge cases; plain `<table>` for comparisons.
- Use a real ellipsis character ("…") wherever one is called for — never three dots ("...").
- If `IS_SINGLE_COMMIT` is `true`, the Background/Code sections should scope to just that commit's change, not the whole branch history.
- Set the spec's `"slug"` to `$LABEL` so the output filename matches the resolved target.

Dark/light mode is handled entirely by the renderer — it defaults to the reader's OS preference (`prefers-color-scheme`) and adds a toggle button that overrides it (persisted in `localStorage`). No spec fields needed for this.

### Step 5 — Render & open

```bash
python3 ~/.claude/skills/explain-diff/scripts/render.py spec.json
```

It refuses to render (exit 1) if the correct option is the longest — or the shortest — one in more than 1/3 of quiz questions (each direction checked independently) — a classic tell that lets readers guess without understanding. If it errors, adjust lengths in the flagged questions and re-run; don't reach for `--allow-length-bias` unless you're sure the flagged cases are fine.

It prints the path it wrote (`/tmp/YYYY-MM-DD-explanation-${LABEL}.html`). Open that path:

```bash
open "$FILE_PATH"
```

(macOS `open` launches the default browser; this is a throwaway artifact, not a repo file — never save inside the project working tree.)
