#!/usr/bin/env python3
r"""
render.py — render a structured explain-diff spec into the self-contained HTML
page format used by the `explain-diff` skill (see ~/.claude/skills/explain-diff/
SKILL.md). Recipe originally from
https://gist.github.com/geoffreylitt/a29df1b5f9865506e8952488eac3d524, with
render.py extraction and quiz-randomization fix from
https://gist.github.com/ankitg12/8e808d387799de4e9839bc393f8e6405.

Why this exists: the CSS, quiz JavaScript, and page scaffolding are identical
across every invocation of the explain-diff skill — only the content (prose,
diagrams, quiz questions) actually changes per diff. Regenerating the full
~250 lines of boilerplate CSS/JS by hand every time wastes tokens. This script
takes a small JSON spec with just the content and renders the final page.

Usage:
    python render.py spec.json [-o output.html]

If -o is omitted, writes to <dir-of-spec>/YYYY-MM-DD-<slug>.html (matching the
skill's filename convention), where <slug> comes from the spec's "slug" field.

Code blocks get client-side syntax highlighting (hand-rolled, no CDN dependency —
the highlighter is inlined in the page, see HIGHLIGHT_JS below). Tag a code
block by adding a language class to the `<code>` element:

    <pre><code class="language-javascript">...</code></pre>
    <pre><code class="language-python">...</code></pre>

Supported names: javascript/js/jsx/ts/typescript/tsx, python/py, bash/sh/shell,
go, rust, java/kotlin, csharp/cs, c, cpp, php, ruby, sql, yaml, json (aliases
included). Partial/incomplete snippets (an object literal fragment, a couple of
lines out of a function) tokenize fine — the highlighter doesn't require valid,
complete syntax.

For a git-style diff, use `language-diff-<lang>` so each `+`/`-` line gets both
the red/green diff background AND nested syntax highlighting of the changed
code; use plain `language-diff` if you just want the diff coloring with no
language known. Lines are plain unified-diff text (leading `+`, `-`, or a
space for context; `@@ ... @@` hunk headers and `diff --git`/`---`/`+++`
headers are recognized and dimmed automatically):

    <pre><code class="language-diff-javascript">-export const devAuthEnabled = import.meta.env.MODE !== 'production'
    +export const devAuthAvailable = import.meta.env.MODE !== 'production'</code></pre>

As always with raw HTML content, escape `<`, `>`, `&` in the code text itself
(e.g. `=&gt;` not `=>`) — the highlighter reads `.textContent` at render time so
escaping doesn't interfere with tokenization.

Spec format (JSON):
{
  "title": "Rewriting the retry logic: exponential backoff with jitter",
  "subtitle": "Prepared 2026-07-15 · PR #482",
  "slug": "retry-backoff-refactor",
  "diagrams": {
    "retry-flow": {
      "direction": "LR",
      "nodes": [
        {"id": "a", "label": "Request fails"},
        {"id": "b", "label": "Backoff\nwith jitter"},
        {"id": "c", "label": "Retry"}
      ],
      "edges": [["a", "b"], ["b", "c", "up to 3x"]]
    }
  },
  "diffstat": {"files": 27, "insertions": 736, "deletions": 19},
  "sections": [
    {"id": "background", "heading": "Background", "html": "<p>...</p>"},
    {"id": "intuition", "heading": "Intuition", "html": "<p>...</p>{{diagram:retry-flow}}"},
    {
      "id": "code", "heading": "Code walkthrough", "html": "<pre><code>...</code></pre>",
      "commit": {
        "hash": "a1b2c3d4", "subject": "fix: drop legacy-auth-adapter",
        "url": "https://gitlab.example.com/.../-/commit/a1b2c3d4...",
        "diffstat": {"files": 3, "insertions": 40, "deletions": 12}
      }
    }
  ],
  "quiz": [
    {
      "question": "Why did `origin/main` resolution fail before the fix?",
      "options": [
        {"text": "`git merge-base` returned an empty string.", "correct": false},
        {"text": "`HEAD_REF` was compared against the wrong branch.", "correct": true}
      ]
    }
  ]
}

Diagrams are defined once in the top-level "diagrams" dict, keyed by name, and dropped into
any section's "html" via a `{{diagram:name}}` token - it expands into the whole bordered card,
click-to-enlarge included; don't wrap it in your own `<div class="diagram">`, that would double
up the border/padding. Each diagram is a small directed graph rendered through Graphviz's `dot`
CLI (must be on PATH - `brew install graphviz` if it's missing) into an inline, responsive SVG:
a real layout engine, so diagrams never clip or wrap unexpectedly the way hand-rolled flexbox
boxes could. Every embedded diagram gets a click-to-enlarge lightbox automatically, and the
whole card is the click target (not just the svg's own drawn area) - no spec changes needed
for any of that.

- "direction": "LR" (left-to-right, the default) or "TB" (top-to-bottom) - matches Graphviz's
  `rankdir`.
- "nodes": each needs "id" (referenced by edges, not shown) and "label" - a `\n` in the label
  puts the first line at normal size and any further lines smaller, like a title + detail line.
  Add `"fail": true` for a red/error-styled node (e.g. a rejected or failing state).
- "edges": `["from_id", "to_id"]`, or `["from_id", "to_id", "edge label"]` for a labeled arrow.
  Nothing stops a node from having more than one outgoing or incoming edge - it's a real graph,
  not just a linear chain, so branching/merging flows work too, not only straight A→B→C ones.

Node/edge label text defaults to a proportional font (space-efficient - most of a label is
prose, not code). Wrap an identifier, path, or literal in backticks (`` `POST /validation` ``)
to render just that span in monospace, same convention as quiz text. Always use a real ellipsis
character ("…"), never three dots - diagram labels normalize "..." to "…" automatically as a
safety net, but write "…" directly rather than relying on it.

A top-level "diffstat" ({"files", "insertions", "deletions"}, all optional except
"files") renders as a small GitLab-style summary (file count + colored +/-) appended to
the end of the subtitle line - used for a whole-branch/whole-diff total. A section's own
"commit" ({"hash", "subject", "url"?, "diffstat"?}) renders as a muted byline directly
under that section's heading - "hash" is the short SHA (a plain <code> chip), "subject"
is linked to "url" when given (omit "url" entirely when there's no resolvable commit
page, e.g. no MR context - it then renders as plain text), and "diffstat" (same shape as
the top-level one) is appended after an em dash. Used by the explain-branch skill to cite
each chapter's own commit - don't hand-write this line into a section's "html" instead,
the styling/linking/diffstat formatting is centralized here so it stays consistent.

A section may carry its own "quiz" array (same question/options shape as the
top-level one). When present, it renders as a "Check your understanding"
mini-quiz directly under that section's content instead of the trailing global
Quiz block — used by the explain-branch skill to attach a few questions to
each per-commit chapter. Sections without a "quiz" key are unaffected. The
length-bias check (below) scans the top-level quiz and every section's quiz
together, so the 1/3 allowance is shared across the whole document, not
computed per section.

Option order within each quiz question is randomized by the renderer at render time —
list them in whatever order reads naturally when writing the spec; don't try to
manually vary position to "seem random", the script already guarantees it.

Quiz `question` and option `text` fields are plain text, not HTML — backtick
spans like `` `origin/main` `` are auto-converted to inline `<code>` (everything
else is HTML-escaped). The "html" fields on sections are raw HTML you write
directly — use real markup (headings, <pre> blocks, tables, ".callout" divs per
the CSS classes below, `{{diagram:name}}` tokens per the "diagrams" section
above), not markdown.

"subtitle" is plain text too, with the same backtick-to-`<code>` convention, plus one
more: a markdown link `[label](https://...)` renders as a real `<a target="_blank">`.
Use this for a source link picked up during data gathering (e.g. an MR's `web_url` from
`glab`) — don't hand-write raw backticks or a bare URL where a link is available:

    "subtitle": "[MR !123](https://gitlab.example.com/.../merge_requests/123) · `fix/drop-legacy-auth-adapter` · commit `a1b2c3d4`"

The page defaults to the reader's OS light/dark preference
(`prefers-color-scheme`) and includes a manual toggle button that overrides it,
persisted in localStorage — no spec changes needed for this.

The renderer refuses to render (exit 1) if the correct option is the longest,
or the shortest, one in more than 1/3 of quiz questions (checked independently
per direction) — a common tell that lets readers guess without understanding
the content. Fix flagged questions by adjusting option lengths. Override with
--allow-length-bias if you're sure it's fine.

All writing judgment (what to explain, which diagrams to draw) still belongs to
the LLM following the explain-diff skill, same as before — this script just
removes the repetitive boilerplate.

Run `python3 render.py --help` for the language-class / diff-highlighting
convention for code blocks.
"""
import argparse
import datetime
import functools
import html
import json
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path

_LIGHT_VARS = """
    --bg: #fafaf8; --fg: #1a1a1a; --accent: #b5541f; --muted: #6b6b6b;
    --code-bg: #f6f8fa; --code-fg: #24292f; --callout-bg: #fff4e8; --border: #e0ddd6;
    --surface: #ffffff; --surface-hover: #f5f2ec; --th-bg: #f0ede6;
    --inline-code-bg: #eee; --quiz-bg: #e8effc;
    --feedback-correct-bg: #ecfdf3; --feedback-correct-fg: #166534;
    --feedback-incorrect-bg: #fef2f2; --feedback-incorrect-fg: #991b1b;
    --badge-new-bg: #dcfce7; --badge-new-fg: #166534;
    --tok-keyword: #d73a49; --tok-string: #032f62; --tok-comment: #6a737d; --tok-number: #005cc5;
    --tok-literal: #005cc5; --tok-builtin: #6f42c1; --tok-function: #6f42c1;
    --tok-property: #005cc5; --tok-variable: #e36209;
    --diff-add-bg: #e6ffec; --diff-add-fg: #1a7f37;
    --diff-del-bg: #ffebe9; --diff-del-fg: #cf222e;
"""

# Applied twice below (system-dark-preference override, and explicit user toggle) - kept as one
# definition so the two can't drift apart, which plain repeated CSS blocks are prone to.
_DARK_VARS = """
    --bg: #16181d; --fg: #e8e6e1; --accent: #e0895a; --muted: #9a9a9a;
    --code-bg: #1a1d23; --code-fg: #d8dee9; --callout-bg: #2a2114; --border: #3a3d44;
    --surface: #1f2229; --surface-hover: #262a32; --th-bg: #262a32;
    --inline-code-bg: #2a2d34; --quiz-bg: #232e4a;
    --feedback-correct-bg: #123822; --feedback-correct-fg: #4ade80;
    --feedback-incorrect-bg: #3a1414; --feedback-incorrect-fg: #f87171;
    --badge-new-bg: #123822; --badge-new-fg: #4ade80;
    --tok-keyword: #c678dd; --tok-string: #98c379; --tok-comment: #7f848e; --tok-number: #d19a66;
    --tok-literal: #56b6c2; --tok-builtin: #e5c07b; --tok-function: #61afef;
    --tok-property: #e06c75; --tok-variable: #e5c07b;
    --diff-add-bg: rgba(46, 160, 67, .15); --diff-add-fg: #3fb950;
    --diff-del-bg: rgba(248, 81, 73, .15); --diff-del-fg: #f85149;
"""

# Plain string concatenation (not an f-string) for the whole CSS blob below - the bulk of it is
# ordinary CSS full of literal `{`/`}`, which an f-string would need doubled-up everywhere.
CSS = (
    "\n  :root { " + _LIGHT_VARS + " }\n"
    "  @media (prefers-color-scheme: dark) {\n"
    '    :root:not([data-theme="light"]) { ' + _DARK_VARS + " }\n"
    "  }\n"
    '  [data-theme="dark"] { ' + _DARK_VARS + " }\n"
    "  html { font-size: 19px; }\n"
) + """
  body { font-family: Georgia, 'Times New Roman', serif; background: var(--bg); color: var(--fg);
    max-width: 880px; margin: 0 auto; padding: 2rem 1.5rem 6rem; line-height: 1.65;
    transition: background-color .15s ease, color .15s ease; }
  h1 { font-size: 1.9rem; border-bottom: 3px solid var(--accent); padding-bottom: .5rem; }
  h2 { font-size: 1.4rem; margin-top: 3rem; color: var(--accent); }
  h3 { font-size: 1.1rem; margin-top: 1.8rem; }
  a { color: var(--accent); }
  a:visited { color: var(--accent); }
  a:hover { text-decoration: none; }
  code { font-family: 'SF Mono', Consolas, monospace; background: var(--inline-code-bg); color: inherit;
    padding: .1rem .3rem; border-radius: 3px; font-size: .92em; }
  pre { background: var(--code-bg); color: var(--code-fg); padding: 1rem 1.2rem; border-radius: 8px;
    overflow-x: auto; white-space: pre-wrap; font-family: 'SF Mono', Consolas, monospace; font-size: .88rem; line-height: 1.5; }
  pre code { background: none; padding: 0; color: inherit; }
  .tok-keyword { color: var(--tok-keyword); }
  .tok-string { color: var(--tok-string); }
  .tok-comment { color: var(--tok-comment); font-style: italic; }
  .tok-number { color: var(--tok-number); }
  .tok-literal { color: var(--tok-literal); }
  .tok-builtin { color: var(--tok-builtin); }
  .tok-function { color: var(--tok-function); }
  .tok-property { color: var(--tok-property); }
  .tok-variable { color: var(--tok-variable); }
  pre.pre-diff { padding: .4rem 0; }
  .diff-line { display: block; padding: 0 1.2rem; }
  .diff-add { background: var(--diff-add-bg); }
  .diff-del { background: var(--diff-del-bg); }
  .diff-hunk, .diff-meta { color: var(--muted); }
  .diff-marker { display: inline-block; width: 1ch; margin-right: .6em; font-weight: bold; }
  .diff-add .diff-marker { color: var(--diff-add-fg); }
  .diff-del .diff-marker { color: var(--diff-del-fg); }
  .callout { background: var(--callout-bg); border-left: 4px solid var(--accent); padding: .9rem 1.2rem;
    border-radius: 0 6px 6px 0; margin: 1.2rem 0; }
  .toc { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1rem 1.5rem; margin: 1.5rem 0; }
  .toc a { color: var(--accent); text-decoration: none; }
  .toc ul { margin: .3rem 0; }
  .diagram { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 1.2rem;
    margin: 1.2rem 0; }
  /* Diagrams are real SVGs laid out by Graphviz (see render_diagram()) - they scale to fit
     their container exactly, no clipping/wrapping possible the way ad-hoc flexbox boxes could. */
  .diagram-embed { position: relative; cursor: zoom-in; }
  /* max-width: 100% (not width: 100%) - a small diagram renders at its natural point size
     instead of being stretched up to fill the container (which was inflating its font past
     the surrounding prose); a wide diagram still shrinks to fit. Needs the <svg>'s own
     intrinsic width/height (see render_diagram()) - without one, max-width alone has nothing
     to cap and the element defaults to stretching anyway. */
  .diagram-embed svg { display: block; max-width: 100%; height: auto; margin: 0 auto; }
  .diagram-lightbox { position: fixed; inset: 0; background: rgba(0,0,0,.78); z-index: 200;
    display: flex; align-items: center; justify-content: center; padding: 3rem; cursor: zoom-out; }
  .diagram-lightbox[hidden] { display: none; }
  /* A fixed-size frame (not sized to the diagram) that fills most of the viewport regardless of
     its size - a small px cap (e.g. 900px) looks fine on a laptop but wastes most of a large
     monitor. The svg below then "contains" itself within this frame, growing to whichever of
     width/height binds first while keeping its own aspect ratio - same idea as object-fit:
     contain on an <img>, just via max-width/max-height + auto sizing since it's a bare <svg>. */
  /* cursor: zoom-out (not default) - clicking the enlarged diagram itself also closes the
     lightbox, same as clicking the dark backdrop; there's no separate close button. */
  .diagram-lightbox-content { width: 90vw; height: 90vh; background: var(--surface);
    border: 1px solid var(--border); border-radius: 12px; padding: 2rem; cursor: zoom-out;
    box-sizing: border-box; display: flex; align-items: center; justify-content: center; }
  /* width/height: 100% + object-fit: contain (not max-width/height + auto) - auto sizing only
     ever shrinks a replaced element down to fit, it never enlarges past the svg's own natural
     size, which left the diagram tiny in the middle of an otherwise-huge frame. object-fit lets
     it grow to fill the frame on whichever axis binds first, still preserving aspect ratio. */
  .diagram-lightbox-content svg { display: block; width: 100%; height: 100%; object-fit: contain; }
  table { border-collapse: collapse; width: 100%; margin: 1rem 0; font-size: .92rem; }
  th, td { border: 1px solid var(--border); padding: .5rem .7rem; text-align: left; }
  th { background: var(--th-bg); }
  .quiz-q { position: relative; overflow: hidden; background: var(--quiz-bg); border: 1px solid var(--border);
    border-radius: 10px; padding: 1.2rem 1.5rem; margin: 1.2rem 0; }
  /* Big enough that it's a background motif, not a corner badge - overflow: hidden on .quiz-q
     clips it to the card shape. Kept faint (opacity) since it now shows through the
     semi-transparent option/code backgrounds below, not just the bare card background. */
  .quiz-q::before { content: "?"; position: absolute; z-index: 0; top: -1rem; right: 1.2rem; font-size: 16rem;
    font-weight: bold; color: var(--accent); opacity: .07; line-height: 1; pointer-events: none; }
  .quiz-q > * { position: relative; z-index: 1; }
  /* Semi-transparent (not opaque like a plain surface) so the "?" motif reads through the
     option rows instead of being fully hidden behind them - color-mix keeps each option's
     own theme color, just lets some of the card (and the glyph) bleed through. */
  /* Zero the question <p>'s default top margin and the last option's bottom margin -
     otherwise they stack on top of .quiz-q's own padding and make the box look
     top-heavy (nothing balances them on the bottom side). */
  .quiz-q > p:first-child { margin-top: 0; }
  .quiz-opt { display: block; width: 100%; text-align: left; padding: .6rem 1rem; margin: .4rem 0;
    border: 1px solid var(--border); border-radius: 6px;
    background: color-mix(in srgb, var(--surface) 82%, transparent); color: var(--fg);
    cursor: pointer; font-family: inherit; font-size: .95rem; }
  .quiz-opt:last-child { margin-bottom: 0; }
  .quiz-opt:hover { background: color-mix(in srgb, var(--surface-hover) 88%, transparent); }
  .quiz-opt code, .quiz-q code {
    font-size: .88em; background: color-mix(in srgb, var(--inline-code-bg) 65%, transparent); }
  .feedback { display: none; margin-top: .6rem; padding: .6rem 1rem; border-radius: 6px; font-size: .9rem; }
  .feedback.correct { background: var(--feedback-correct-bg); color: var(--feedback-correct-fg); border-left: 3px solid #16a34a; }
  .feedback.incorrect { background: var(--feedback-incorrect-bg); color: var(--feedback-incorrect-fg); border-left: 3px solid #dc2626; }
  .badge { display: inline-block; font-size: .75rem; padding: .15rem .5rem; border-radius: 10px; font-family: sans-serif; }
  .badge.new { background: var(--badge-new-bg); color: var(--badge-new-fg); }
  #theme-toggle { position: fixed; top: 1rem; right: 1rem; z-index: 10; width: 2.4rem; height: 2.4rem;
    border: 1px solid var(--border); background: var(--surface); color: var(--fg); border-radius: 999px;
    font-size: 1.1rem; cursor: pointer; display: flex; align-items: center; justify-content: center;
    box-shadow: 0 1px 4px rgba(0,0,0,.15); }
  @media (max-width: 600px) { body { padding: 1rem; } #theme-toggle { top: .5rem; right: .5rem; } }
"""

# Runs in <head>, before first paint, so an explicit saved preference applies
# immediately instead of flashing the system-preference theme first.
THEME_INIT_JS = """
(function() {
  var saved = localStorage.getItem('explain-diff-theme');
  if (saved === 'light' || saved === 'dark') {
    document.documentElement.setAttribute('data-theme', saved);
  }
})();
"""

DIAGRAM_JS = """
(function() {
  var lightbox = document.getElementById('diagram-lightbox');
  if (!lightbox) return;
  var content = lightbox.querySelector('.diagram-lightbox-content');
  // Hidden while zoomed - it's the only other interactive control on the page, and it sits
  // near where the enlarged diagram renders; hiding it avoids an unrelated click target
  // competing with "click anywhere closes this".
  var themeToggle = document.getElementById('theme-toggle');
  function open(svg) {
    content.innerHTML = '';
    content.appendChild(svg.cloneNode(true));
    lightbox.hidden = false;
    if (themeToggle) themeToggle.hidden = true;
  }
  function close() {
    lightbox.hidden = true;
    content.innerHTML = '';
    if (themeToggle) themeToggle.hidden = false;
  }
  document.querySelectorAll('.diagram-embed').forEach(function(el) {
    var svg = el.querySelector('svg');
    if (!svg) return;
    el.addEventListener('click', function() { open(svg); });
    el.addEventListener('keydown', function(e) {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(svg); }
    });
  });
  // No dedicated close button - any click anywhere in the lightbox (backdrop or the enlarged
  // diagram itself) dismisses it, so this listens on the lightbox itself rather than checking
  // e.target against a specific element.
  lightbox.addEventListener('click', close);
  document.addEventListener('keydown', function(e) { if (e.key === 'Escape' && !lightbox.hidden) close(); });
})();
"""

QUIZ_JS = """
document.querySelectorAll('.quiz-q').forEach(q => {
  q.querySelectorAll('.quiz-opt').forEach(opt => {
    opt.addEventListener('click', () => {
      const correct = opt.dataset.correct === 'true';
      let fb = opt.nextElementSibling;
      if (!fb || !fb.classList.contains('feedback')) {
        fb = document.createElement('div');
        fb.className = 'feedback';
        opt.insertAdjacentElement('afterend', fb);
      }
      fb.textContent = correct ? '\\u2705 Correct.' : '\\u274c Not quite \\u2014 reread the section above.';
      fb.className = 'feedback ' + (correct ? 'correct' : 'incorrect');
      fb.style.display = 'block';
    });
  });
});

(function() {
  var btn = document.getElementById('theme-toggle');
  function currentTheme() {
    var explicit = document.documentElement.getAttribute('data-theme');
    if (explicit) return explicit;
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  function updateIcon() {
    btn.textContent = currentTheme() === 'dark' ? '\\u2600\\ufe0f' : '\\ud83c\\udf19';
  }
  btn.addEventListener('click', function() {
    var next = currentTheme() === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('explain-diff-theme', next);
    updateIcon();
  });
  updateIcon();
})();
"""

# Dependency-free syntax highlighter for <pre><code class="language-XXX"> blocks,
# plus a diff-aware variant (class="language-diff" or "language-diff-XXX") that
# colors +/- lines like a unified diff while still tokenizing the code content of
# each line. Hand-rolled instead of vendoring hljs/Prism so the output HTML stays
# a single dependency-free file — see SKILL.md for the class-name convention.
HIGHLIGHT_JS = """
(function() {
  function escHtml(s) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  function escapeRe(s) {
    return s.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&');
  }
  function words(str) {
    return new Set((str || '').split(/\\s+/).filter(Boolean));
  }

  var LANGS = {};
  function def(names, o) {
    names.split(' ').forEach(function(n) { LANGS[n] = o; });
  }

  def('javascript js jsx mjs cjs', {
    kw: words('break case catch class const continue debugger default delete do else export extends finally for function if import in instanceof new return super switch this throw try typeof var void while with yield let async await static'),
    lit: words('true false null undefined NaN Infinity'),
    bi: words('console Math JSON Object Array String Number Boolean Symbol Promise Map Set WeakMap WeakSet Date RegExp Error globalThis window document require module exports process'),
    lc: '//', bc: ['/*', '*/'], str: ['`', '"', "'"],
  });
  def('typescript ts tsx', {
    kw: words('break case catch class const continue debugger default delete do else export extends finally for function if import in instanceof new return super switch this throw try typeof var void while with yield let async await static interface type enum implements namespace declare readonly public private protected abstract satisfies keyof infer'),
    lit: LANGS.javascript.lit,
    bi: LANGS.javascript.bi,
    lc: '//', bc: ['/*', '*/'], str: ['`', '"', "'"],
  });
  def('python py', {
    kw: words('and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield match case'),
    lit: words('True False None'),
    bi: words('print len range str int float dict list set tuple type isinstance super self cls object Exception'),
    lc: '#', bc: null, str: ['\"\"\"', "'''", '"', "'"],
  });
  def('bash sh shell zsh', {
    kw: words('if then else elif fi for while until do done case esac function in return exit break continue local export readonly declare select'),
    lit: words('true false'),
    bi: words('echo printf cd ls grep sed awk curl wget git cat rm mv cp mkdir chmod chown source set exec eval'),
    lc: '#', bc: null, str: ['"', "'"],
    varRe: '\\\\$\\\\{[^}]*\\\\}|\\\\$[A-Za-z_][A-Za-z0-9_]*|\\\\$[0-9@*#?$!-]',
  });
  def('go golang', {
    kw: words('break case chan const continue default defer else fallthrough for func go goto if import interface map package range return select struct switch type var'),
    lit: words('true false nil iota'),
    bi: words('len cap make new append copy delete panic recover print println error string int int8 int16 int32 int64 uint uint8 uint16 uint32 uint64 float32 float64 bool byte rune'),
    lc: '//', bc: ['/*', '*/'], str: ['`', '"'],
  });
  def('rust rs', {
    kw: words('as break const continue crate dyn else enum extern false fn for if impl in let loop match mod move mut pub ref return self Self static struct super trait true type unsafe use where while async await'),
    lit: words('true false None Some Ok Err'),
    bi: words('println print vec String Vec Option Result Box Rc Arc HashMap HashSet'),
    lc: '//', bc: ['/*', '*/'], str: ['"'],
  });
  def('java kotlin kt', {
    kw: words('abstract assert boolean break byte case catch char class const continue default do double else enum extends final finally float for goto if implements import instanceof int interface long native new package private protected public return short static strictfp super switch synchronized this throw throws transient try void volatile while var record sealed permits yield fun val'),
    lit: words('true false null'),
    bi: words('System String Object List Map Set ArrayList HashMap Integer Double Boolean Optional Stream'),
    lc: '//', bc: ['/*', '*/'], str: ['"'],
  });
  def('csharp cs', {
    kw: words('abstract break case catch class const continue default do double else enum extends final finally float for goto if implements import instanceof int interface long new package private protected public return short static super switch this throw throws try void while var using namespace foreach async await readonly internal sealed override record'),
    lit: words('true false null'),
    bi: words('Console String Object List Dictionary Task Enumerable'),
    lc: '//', bc: ['/*', '*/'], str: ['"'],
  });
  def('c h', {
    kw: words('auto break case char const continue default do double else enum extern float for goto if inline int long register restrict return short signed sizeof static struct switch typedef union unsigned void volatile while'),
    lit: words('true false NULL'),
    bi: words('printf scanf malloc free memcpy strlen strcpy sizeof'),
    lc: '//', bc: ['/*', '*/'], str: ['"', "'"],
  });
  def('cpp c++ hpp cc cxx', {
    kw: words('auto break case char const continue default do double else enum extern float for goto if inline int long register restrict return short signed sizeof static struct switch typedef union unsigned void volatile while class namespace template typename public private protected virtual override new delete this try catch throw using friend operator explicit constexpr nullptr'),
    lit: words('true false NULL nullptr'),
    bi: words('std cout cin endl printf scanf malloc free memcpy strlen strcpy sizeof vector string map set'),
    lc: '//', bc: ['/*', '*/'], str: ['"', "'"],
  });
  def('php', {
    kw: words('abstract and array as break callable case catch class clone const continue declare default die do echo else elseif empty enddeclare endfor endforeach endif endswitch endwhile enum extends final finally fn for foreach function global goto if implements include include_once instanceof insteadof interface isset list match namespace new or print private protected public readonly require require_once return static switch throw trait try unset use var while xor yield'),
    lit: words('true false null'),
    bi: words('echo print_r var_dump strlen array_map array_filter implode explode isset empty'),
    lc: '//', bc: ['/*', '*/'], str: ['"', "'"],
    varRe: '\\\\$[A-Za-z_][A-Za-z0-9_]*',
  });
  def('ruby rb', {
    kw: words('begin break case class def defined do else elsif end ensure false for if in module next nil not or redo rescue retry return self super then true undef unless until when while yield attr_accessor attr_reader attr_writer require require_relative'),
    lit: words('true false nil'),
    bi: words('puts print p raise loop lambda proc'),
    lc: '#', bc: null, str: ['"', "'"],
    varRe: '@{1,2}[A-Za-z_][A-Za-z0-9_]*|\\\\$[A-Za-z_][A-Za-z0-9_]*',
  });
  def('sql', {
    kw: words('select from where and or not null is in like between join left right inner outer on group by order having limit offset insert into values update set delete create table alter drop index view as distinct union all case when then else end exists primary key foreign references default constraint'),
    lit: words('true false null'),
    bi: words('count sum avg min max coalesce'),
    lc: '--', bc: ['/*', '*/'], str: ["'"], ci: true,
  });
  def('yaml yml', {
    kw: words(''), lit: words('true false null yes no'), bi: words(''),
    lc: '#', bc: null, str: ['"', "'"],
  });
  def('json', {
    kw: words(''), lit: words('true false null'), bi: words(''),
    lc: null, bc: null, str: ['"'],
  });

  function buildRegex(d) {
    var parts = [];
    if (d.bc) parts.push(escapeRe(d.bc[0]) + '[\\\\s\\\\S]*?(?:' + escapeRe(d.bc[1]) + '|$)');
    if (d.lc) parts.push(escapeRe(d.lc) + '.*');
    var strDelims = (d.str || []).slice().sort(function(a, b) { return b.length - a.length; });
    strDelims.forEach(function(s) {
      var e = escapeRe(s);
      if (s.length >= 3) {
        parts.push(e + '[\\\\s\\\\S]*?(?:' + e + '|$)');
      } else {
        parts.push(e + '(?:\\\\\\\\[\\\\s\\\\S]|[^' + e + '\\\\\\\\\\\\n])*(?:' + e + '|$)');
      }
    });
    if (d.varRe) parts.push(d.varRe);
    parts.push('\\\\b0[xX][0-9a-fA-F]+\\\\b');
    parts.push('\\\\b\\\\d+(?:\\\\.\\\\d+)?(?:[eE][+-]?\\\\d+)?\\\\b');
    parts.push('[A-Za-z_$][A-Za-z0-9_$]*');
    return new RegExp(parts.join('|'), 'g');
  }

  var regexCache = new Map();
  function span(cls, text) {
    return '<span class="tok-' + cls + '">' + escHtml(text) + '</span>';
  }

  function highlightGeneric(code, d) {
    var re = regexCache.get(d);
    if (!re) { re = buildRegex(d); regexCache.set(d, re); }
    re.lastIndex = 0;
    var last = 0, out = '', m;
    while ((m = re.exec(code))) {
      if (m.index > last) out += escHtml(code.slice(last, m.index));
      var tok = m[0];
      var c0 = tok[0];
      if (d.lc && tok.indexOf(d.lc) === 0) {
        out += span('comment', tok);
      } else if (d.bc && tok.indexOf(d.bc[0]) === 0) {
        out += span('comment', tok);
      } else if (c0 === '"' || c0 === "'" || c0 === '`') {
        out += span('string', tok);
      } else if ((c0 === '$' || c0 === '@') && d.varRe) {
        out += span('variable', tok);
      } else if (/^[0-9]/.test(tok)) {
        out += span('number', tok);
      } else {
        var key = d.ci ? tok.toLowerCase() : tok;
        var inSet = function(set) {
          if (!d.ci) return set.has(tok);
          var found = false;
          set.forEach(function(w) { if (w.toLowerCase() === key) found = true; });
          return found;
        };
        if (inSet(d.lit)) out += span('literal', tok);
        else if (inSet(d.kw)) out += span('keyword', tok);
        else if (inSet(d.bi)) out += span('builtin', tok);
        else {
          var after = code.slice(re.lastIndex, re.lastIndex + 20).match(/^\\s*\\(/);
          var before = code.slice(0, m.index).match(/\\.\\s*$/);
          if (after) out += span('function', tok);
          else if (before) out += span('property', tok);
          else out += escHtml(tok);
        }
      }
      last = re.lastIndex;
      if (re.lastIndex === m.index) re.lastIndex++;
    }
    out += escHtml(code.slice(last));
    return out;
  }

  function highlightCode(code, lang) {
    var d = LANGS[lang];
    if (!d) return escHtml(code);
    return highlightGeneric(code, d);
  }

  function highlightDiffLine(line, lang) {
    if (/^(diff --git|index |new file mode|deleted file mode|--- |\\+\\+\\+ |old mode|new mode|similarity index|rename (from|to))/.test(line)) {
      return { cls: 'diff-meta', html: escHtml(line) };
    }
    if (/^@@.*@@/.test(line)) {
      return { cls: 'diff-hunk', html: escHtml(line) };
    }
    if (line[0] === '+') {
      var rest = line.slice(1);
      return { cls: 'diff-add', marker: '+', html: lang ? highlightCode(rest, lang) : escHtml(rest) };
    }
    if (line[0] === '-') {
      var rest2 = line.slice(1);
      return { cls: 'diff-del', marker: '-', html: lang ? highlightCode(rest2, lang) : escHtml(rest2) };
    }
    var rest3 = line[0] === ' ' ? line.slice(1) : line;
    return { cls: 'diff-ctx', marker: ' ', html: lang ? highlightCode(rest3, lang) : escHtml(rest3) };
  }

  function highlightDiff(code, lang) {
    var trimmed = code.charAt(code.length - 1) === '\\n' ? code.slice(0, -1) : code;
    return trimmed.split('\\n').map(function(line) {
      var r = highlightDiffLine(line, lang);
      var marker = r.marker !== undefined ? '<span class="diff-marker">' + r.marker + '</span>' : '';
      return '<span class="diff-line ' + r.cls + '">' + marker + r.html + '</span>';
    }).join('');
  }

  document.querySelectorAll('pre code[class*="language-"]').forEach(function(el) {
    var cls = Array.prototype.find.call(el.classList, function(c) { return c.indexOf('language-') === 0; });
    if (!cls) return;
    var name = cls.slice('language-'.length);
    var code = el.textContent;
    var pre = el.closest('pre');
    var out;
    if (name === 'diff') {
      out = highlightDiff(code, null);
      if (pre) pre.classList.add('pre-diff');
    } else if (name.indexOf('diff-') === 0) {
      out = highlightDiff(code, name.slice('diff-'.length));
      if (pre) pre.classList.add('pre-diff');
    } else {
      out = highlightCode(code, name);
    }
    el.innerHTML = out;
  });
})();
"""


# Literal colors fed to `dot` (it doesn't understand CSS `var()`) mapped back to this page's
# theme variables after rendering, so the embedded SVG still reacts to the light/dark toggle
# like everything else on the page. Values must match the light-mode CSS custom properties above.
_DIAGRAM_COLOR_VARS = [
    ("#fff4e8", "var(--callout-bg)"),
    ("#b5541f", "var(--accent)"),
    ("#1a1a1a", "var(--fg)"),
    ("#6b6b6b", "var(--muted)"),
    ("#fef2f2", "var(--feedback-incorrect-bg)"),
]

@functools.lru_cache(maxsize=1)
def _dot_path() -> str:
    found = shutil.which("dot")
    if not found:
        raise RuntimeError(
            "diagram rendering needs Graphviz's `dot` CLI, not found on PATH. "
            "Install it (e.g. `brew install graphviz`) and retry."
        )
    return found


_BACKTICK_RE = re.compile(r"`([^`]+)`")


def _format_label_segment(text: str) -> str:
    """Escape one label line for Graphviz's HTML-like label syntax, rendering `` `code` `` spans
    in monospace and leaving the rest in the node's default (narrower, proportional) font - matches
    the page's own inline-code convention and keeps labels compact."""
    text = text.replace("...", "…")  # a real ellipsis character, never three dots
    parts = []
    last = 0
    for m in _BACKTICK_RE.finditer(text):
        if m.start() > last:
            parts.append(html.escape(text[last : m.start()]))
        code = m.group(1)
        parts.append(f'<FONT FACE="Menlo">{html.escape(code)}</FONT>')
        last = m.end()
        # Graphviz's built-in width estimate for a FONT-FACE run tends to come in ~4-5%
        # narrow versus how the font actually renders (measured via getComputedTextLength()
        # in a real browser), and that gap scales with the run's own length - so it's not
        # just tight-touching punctuation (a comma, a period, ...) that can visually overlap
        # the code span's last glyph, a real following space can be fully swallowed too once
        # the run is long enough that the deficit exceeds the space glyph's own width (e.g.
        # "`documents` row" rendering as "documentsrow"). Compensate with thin spaces scaled
        # to the run's length, unconditionally - not only when no whitespace already follows.
        parts.append("&#8201;" * max(1, len(code) // 8))
    parts.append(html.escape(text[last:]))
    return "".join(parts)


def _html_label(text: str) -> str:
    """Graphviz HTML-like label: first line at normal size, further lines smaller (subtitle).

    Built as a borderless TABLE with explicit CELLSPACING rather than `<BR/>`-joined FONT tags -
    `<BR/>` packs lines with no gap, so a line's descenders (g, y, p) touch the next line's
    ascenders; CELLSPACING gives real breathing room between lines."""
    lines = text.split("\n")
    rows = [f'<TR><TD><FONT POINT-SIZE="13">{_format_label_segment(lines[0])}</FONT></TD></TR>']
    for line in lines[1:]:
        rows.append(f'<TR><TD><FONT POINT-SIZE="10">{_format_label_segment(line)}</FONT></TD></TR>')
    table = f'<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="3" CELLPADDING="0">{"".join(rows)}</TABLE>'
    return "<" + table + ">"


def render_diagram(diagram: dict) -> str:
    """Render a {"direction", "nodes": [{"id","label","fail"?}], "edges": [[from,to,label?]]}
    spec into an inline <svg> fragment via Graphviz `dot` - a real layout engine, so the result
    never clips or wraps unexpectedly the way ad-hoc flexbox boxes could."""
    direction = diagram.get("direction", "LR")
    lines = [
        "digraph G {",
        f"  rankdir={direction};",
        '  bgcolor="transparent";',
        '  node [shape=box, style="rounded,filled", fillcolor="#fff4e8", color="#b5541f", '
        'penwidth=2, fontname="Georgia", fontcolor="#1a1a1a", margin="0.15,0.1"];',
        '  edge [color="#6b6b6b", penwidth=1.4, arrowsize=0.8, fontname="Georgia", fontsize=10, fontcolor="#6b6b6b"];',
    ]
    for node in diagram["nodes"]:
        attrs = [f'label={_html_label(node["label"])}']
        if node.get("fail"):
            attrs.append('fillcolor="#fef2f2"')
            attrs.append('color="#b91c1c"')
        lines.append(f'  "{node["id"]}" [{", ".join(attrs)}];')
    for edge in diagram["edges"]:
        src, dst = edge[0], edge[1]
        attrs = f' [label=<{_format_label_segment(edge[2])}>]' if len(edge) > 2 and edge[2] else ""
        lines.append(f'  "{src}" -> "{dst}"{attrs};')
    lines.append("}")
    dot_source = "\n".join(lines)

    result = subprocess.run(
        [_dot_path(), "-Tsvg"], input=dot_source, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(f"dot failed to render a diagram: {result.stderr.strip()}")

    svg = result.stdout
    svg = svg[svg.index("<svg") :]  # drop the XML prolog/DOCTYPE/leading comments
    svg = re.sub(r"<!--.*?-->", "", svg, flags=re.S)  # per-node/edge comments graphviz adds
    svg = re.sub(r' id="[^"]*"', "", svg)  # would collide across multiple diagrams on one page
    # NB: Graphviz's own pt-based width/height attributes are deliberately left in place (not
    # stripped) - see the CSS comment on `.diagram-embed svg` for why.
    for literal, var in _DIAGRAM_COLOR_VARS:
        svg = svg.replace(literal, var)
    return svg


_DIAGRAM_TOKEN_RE = re.compile(r"\{\{diagram:([a-zA-Z0-9_-]+)\}\}")

# A content spec that still wraps the token in its own `<div class="diagram">` (the pre-merge
# convention) would otherwise double up the card: two nested borders/padding rings, the outer
# one dead to clicks. Collapsing it here means an out-of-date spec (e.g. written against an
# older cached copy of this file's own docs) still renders correctly instead of just looking
# subtly broken.
_REDUNDANT_DIAGRAM_WRAPPER_RE = re.compile(
    r'<div class="diagram">\s*(<div class="diagram diagram-embed".*?</div>)\s*</div>', re.DOTALL
)


def render_diagrams_in_html(html_str: str, diagrams: dict) -> str:
    """Replace every `{{diagram:key}}` token with its rendered, zoomable SVG embed."""

    def replace(m: re.Match) -> str:
        key = m.group(1)
        if key not in diagrams:
            raise KeyError(f"diagram '{key}' referenced via {{{{diagram:{key}}}}} but not defined in spec['diagrams']")
        svg = render_diagram(diagrams[key])
        # "diagram" (bordered card look) and "diagram-embed" (click-to-enlarge) on the SAME div,
        # not diagram-embed nested inside a separately-authored .diagram wrapper - otherwise the
        # clickable area is only the svg's own tight box, leaving the card's border/padding ring
        # dead to clicks. render_diagram() output already IS the whole card; nothing left for the
        # content spec to wrap.
        return (
            '<div class="diagram diagram-embed" role="button" tabindex="0" aria-label="Enlarge diagram">'
            f"{svg}"
            "</div>"
        )

    substituted = _DIAGRAM_TOKEN_RE.sub(replace, html_str)
    return _REDUNDANT_DIAGRAM_WRAPPER_RE.sub(r"\1", substituted)


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def format_inline(text: str) -> str:
    """Escape plain text, then turn `` `code` `` spans into <code> (each side already escaped)."""
    escaped = html.escape(text)
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)


def format_meta(text: str) -> str:
    """Like format_inline, plus markdown links `[label](url)` -> a real <a> tag. Used for
    the subtitle line (e.g. an MR link picked up from `glab`), not for quiz text."""
    escaped = html.escape(text)
    escaped = re.sub(
        r"\[([^\]]+)\]\((https?://[^\s)]+)\)",
        r'<a href="\2" target="_blank" rel="noopener noreferrer">\1</a>',
        escaped,
    )
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    # Break onto a new line after a long MR title instead of an arbitrary width-based wrap.
    return re.sub(r"(</a>)\s*·\s*", r"\1<br>", escaped, count=1)


def format_diffstat(diffstat: dict) -> str:
    """Render a {"files", "insertions", "deletions"} diffstat as a small GitLab-style
    summary: file count plus colored +insertions/-deletions, reusing the page's existing
    diff-highlight palette. A zero count omits its span, matching GitLab's own behavior."""
    parts = [f'{diffstat["files"]} file{"s" if diffstat["files"] != 1 else ""}']
    if diffstat.get("insertions"):
        parts.append(f'<span style="color:var(--diff-add-fg)">+{diffstat["insertions"]}</span>')
    if diffstat.get("deletions"):
        parts.append(f'<span style="color:var(--diff-del-fg)">−{diffstat["deletions"]}</span>')
    return " ".join(parts)


def format_commit_byline(commit: dict) -> str:
    """Render a section's commit citation line: hash chip + subject (linked to `url` when
    given) + optional diffstat - see the module docstring for the field shapes."""
    subject = html.escape(commit["subject"])
    if commit.get("url"):
        subject = f'<a href="{html.escape(commit["url"])}" target="_blank" rel="noopener noreferrer">{subject}</a>'
    line = f'commit <code>{html.escape(commit["hash"])}</code> — {subject}'
    if commit.get("diffstat"):
        line += " · " + format_diffstat(commit["diffstat"])
    return f'<p style="color:var(--muted); margin-top:-.5rem; font-size:.85em;">{line}</p>'


def collect_all_quiz_questions(spec: dict) -> list:
    """Every quiz question in the spec: the top-level "quiz" plus each section's own "quiz"."""
    questions = list(spec.get("quiz", []))
    for s in spec.get("sections", []):
        questions.extend(s.get("quiz", []))
    return questions


def render_quiz_blocks(quiz: list) -> str:
    """Render a list of quiz questions as shuffled-option quiz-q blocks (no heading)."""
    blocks = []
    for q in quiz:
        options = list(q["options"])
        random.shuffle(options)
        opts = "\n".join(
            f'<button class="quiz-opt" data-correct="{"true" if o["correct"] else "false"}">{format_inline(o["text"])}</button>'
            for o in options
        )
        blocks.append(f'<div class="quiz-q">\n<p><strong>{format_inline(q["question"])}</strong></p>\n{opts}\n</div>')
    return "\n\n".join(blocks)


def check_length_bias(quiz: list) -> None:
    """Fail if the correct option is the (uniquely) longest, or the (uniquely) shortest,
    one in too many questions — both are well-known tells that let readers guess without
    understanding the content. Each direction is allowed in up to 1/3 of questions
    (fine for a couple to shake out that way, just not most)."""
    if not quiz:
        return

    allowed = len(quiz) // 3

    def offenders_for(extreme):
        result = []
        for i, q in enumerate(quiz):
            lengths = [len(o["text"]) for o in q["options"]]
            correct_len = next(len(o["text"]) for o in q["options"] if o["correct"])
            if correct_len == extreme(lengths) and lengths.count(correct_len) == 1:
                result.append(i + 1)
        return result

    for label, extreme in (("longest", max), ("shortest", min)):
        offenders = offenders_for(extreme)
        if len(offenders) > allowed:
            print(
                f"ERROR: quiz length bias — the correct option is the {label} one in "
                f"{len(offenders)}/{len(quiz)} questions (#{', #'.join(map(str, offenders))}), "
                f"but at most {allowed} of {len(quiz)} may have that shape. Readers can pick "
                f"correct answers by guessing '{label} = right' without understanding anything. "
                f"Rewrite the flagged questions so length stops correlating with correctness "
                f"(adjust the correct option's and/or a distractor's length).",
                file=sys.stderr,
            )
            sys.exit(1)


def render(spec: dict) -> str:
    title = spec["title"]
    subtitle = spec.get("subtitle", "")
    sections = spec.get("sections", [])
    quiz = spec.get("quiz", [])
    diagrams = spec.get("diagrams", {})

    toc_items = "\n".join(
        f'  <li><a href="#{s["id"]}">{html.escape(s["heading"])}</a></li>' for s in sections
    )
    if quiz:
        toc_items += '\n  <li><a href="#quiz">Quiz</a></li>'

    def render_section(s: dict) -> str:
        body = render_diagrams_in_html(s["html"], diagrams)
        byline = format_commit_byline(s["commit"]) + "\n" if s.get("commit") else ""
        out = f'<h2 id="{s["id"]}">{html.escape(s["heading"])}</h2>\n{byline}{body}'
        if s.get("quiz"):
            out += '\n\n<h3>Check your understanding</h3>\n\n' + render_quiz_blocks(s["quiz"])
        return out

    body_sections = "\n\n".join(render_section(s) for s in sections)

    subtitle_html = format_meta(subtitle) if subtitle else ""
    if spec.get("diffstat"):
        sep = " · " if subtitle_html else ""
        subtitle_html += f'{sep}{format_diffstat(spec["diffstat"])}'

    quiz_html = ""
    if quiz:
        quiz_html = '<h2 id="quiz">Quiz</h2>\n\n' + render_quiz_blocks(quiz)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{html.escape(title)}</title>
<style>{CSS}</style>
<script>{THEME_INIT_JS}</script>
</head>
<body>

<button id="theme-toggle" aria-label="Toggle dark mode" title="Toggle dark mode"></button>

<div id="diagram-lightbox" class="diagram-lightbox" aria-label="Enlarged diagram - click anywhere to close" hidden>
<div class="diagram-lightbox-content"></div>
</div>

<h1>{html.escape(title)}</h1>
{f'<p style="color:var(--muted); margin-top:-.5rem;">{subtitle_html}</p>' if subtitle_html else ''}

<div class="toc">
<strong>Contents</strong>
<ul>
{toc_items}
</ul>
</div>

{body_sections}

{quiz_html}

<script>{HIGHLIGHT_JS}</script>
<script>{DIAGRAM_JS}</script>
<script>{QUIZ_JS}</script>

</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec", type=Path, help="path to the JSON content spec")
    ap.add_argument("-o", "--output", type=Path, default=None, help="output HTML path")
    ap.add_argument(
        "--allow-length-bias",
        action="store_true",
        help="skip the check that fails when the correct quiz option is too often the longest one",
    )
    args = ap.parse_args()

    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    if not args.allow_length_bias:
        check_length_bias(collect_all_quiz_questions(spec))
    out_html = render(spec)

    if args.output:
        out_path = args.output
    else:
        date_prefix = datetime.date.today().strftime("%Y-%m-%d")
        slug = spec.get("slug") or slugify(spec["title"])
        out_path = Path(f"/tmp/{date_prefix}-explanation-{slug}.html")

    out_path.write_text(out_html, encoding="utf-8")
    print(str(out_path))


if __name__ == "__main__":
    main()
