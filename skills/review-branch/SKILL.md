---
name: review-branch
description: Performs a focused code review of all commits on the current git branch since it diverged from the remote default branch (origin/main or origin/master). Handles detached HEAD state. Output is critique-only: a prioritized list of concrete flaws with actionable improvement hints. Use when user wants to review a branch, do a code review, check a PR, or invokes /review-branch.
---

# review-branch

## Workflow

### Step 1 — Gather the branch range

Run the bundled script to resolve the review scope (handles detached HEAD transparently):

```bash
bash ~/.claude/skills/review-branch/scripts/branch-range.sh
```

Source the output variables: `MAIN_REF`, `BASE`, `HEAD_SHA`, `COMMIT_COUNT`, `BRANCH_NAME`.

If the script errors (not a git repo, no remote), report the error and stop.

### Step 2 — Collect raw material

Run these in parallel:

```bash
# Commit log (context only)
git log --oneline "$BASE"..HEAD

# Full diff of the entire branch
git diff "$BASE"..HEAD

# List of changed files
git diff --name-only "$BASE"..HEAD

# Test-related files changed
git diff --name-only "$BASE"..HEAD | grep -iE '(test|spec)' || true
```

### Step 3 — Load project conventions

Look for convention documents in the repo root and docs. Read any that exist:

```bash
# Resolve symlinks so duplicates (e.g. CLAUDE.md → AGENTS.md) are read only once
for f in CLAUDE.md AGENTS.md README.md CONTEXT.md; do [ -f "$f" ] && realpath "$f"; done \
  | sort -u \
  | xargs ls 2>/dev/null
find docs/ -type f -name '*.md' 2>/dev/null | head -20
```

Extract rules, conventions, and constraints that apply to code in this repo. These become additional criteria for the review — treat violations as 🟠 `HIGH` severity.

### Step 4 — Analyze

Read changed source files in full when context around the diff matters. Also read existing test files for the touched modules — not just the diff — to judge test coverage honestly.

Apply the review criteria below plus any project-specific rules found in Step 3. Take notes internally. **Do not output anything yet.**

### Step 5 — Output

Emit **only** a flat, prioritized list of findings. No preamble. No praise. No summary of what the code does. Use Markdown throughout — it renders in the terminal.

Severity levels (use exactly one icon per finding):
- 🔴 `CRITICAL` — correctness bug, security issue, data loss risk
- 🟠 `HIGH` — design flaw, missing test coverage for non-trivial logic, violates a project convention
- 🟡 `MEDIUM` — poor clarity, unnecessary complexity, fragile pattern
- 🔵 `LOW` — minor style, naming, or redundancy issue

Sort by severity descending, then by file path.

**File paths and identifiers:** always wrap in backticks — `` `src/foo.ts:42` ``, `` `functionName` ``. Never write a bare path or symbol.

**Trivial findings** (LOW, and simple MEDIUM style nits) — keep it to one line:

```
🔵 **`src/utils/format.ts:12`** — Redundant `String()` cast, `value` is already a string.
```

**Non-trivial findings** (CRITICAL/HIGH, and any MEDIUM worth explaining) — use this structure:

```
🔴 **`src/api/session.ts:88`** — `token` is read before the null check, so an expired session throws instead of redirecting to login.

​```ts
// src/api/session.ts:85-92
function getUser(req) {
  const token = req.session.token.value;   // <-- crashes here if session is null
  if (!req.session) return redirectToLogin();
  return decode(token);
}
​```

**How this bites:** a logged-out user hits any route calling `getUser()` — e.g. `middleware/auth.ts:14` — and gets a 500 instead of a login redirect.

**Fix:** check `req.session` before touching `.token`:

​```ts
function getUser(req) {
  if (!req.session) return redirectToLogin();
  return decode(req.session.token.value);
}
​```
```

Rules for the non-trivial structure:
- Always show the offending code, not just a prose description. Include enough surrounding lines (or the calling site, in a second snippet) for the flaw to be self-evident.
- Add a **How this bites** scenario only when the failure mode isn't obvious from the code alone — e.g. concurrency, a specific input shape, an interaction between two call sites. Skip it when the snippet already speaks for itself.
- Always end with a **Fix** — concrete replacement code, not just advice in prose. Keep it as small as the actual fix; don't pad with unrelated cleanup.
- Use the real language for code fences (`ts`, `py`, `go`, ...), and a `// path:lines` comment on the first line when the snippet is longer than a couple of lines. When the file is a template layered on a well-known format — a Helm/Go `.tpl`, Jinja, EJS — fence it as the underlying format (`yaml`, `html`, ...), not the template engine's own name: the engine's tag is rarely a real highlighter language, and the underlying format still covers most of the snippet's lines.
- If you run a command to verify a claim, show it in a fenced block (` ```bash ` for the command, or ` ```console ` if you're showing a `$ command` line together with its output) — a bare `$ command` line outside a fence renders as flat, unhighlighted text.

If there are zero findings, output exactly: `No issues found.`

---

## Review criteria

### Correctness & robustness
- Off-by-one errors, unhandled edge cases, incorrect assumptions about input ranges
- Missing null/undefined/empty checks at system boundaries (user input, external API responses)
- Race conditions, unhandled async errors, missing await
- Error paths that silently swallow exceptions

### Tests
- Non-trivial logic (branches, transformations, error handling) without any test
- Tests that only assert the happy path for logic with meaningful edge cases
- Trivial tests (testing a getter, a type assertion, a constant) — flag as redundant
- Tests that duplicate production logic rather than asserting outcomes

### Code quality
- Functions doing more than one thing; could be decomposed without ceremony
- Abstractions introduced before they are needed (YAGNI violations)
- Duplication that could share a single well-named helper
- Overuse of comments explaining *what* instead of *why*; or comments that are just noise

### Project conventions
Derived from Step 3 — flag any violation of rules found in CLAUDE.md, AGENTS.md, README.md, CONTEXT.md, or docs/**. If no convention documents exist, skip this category.

### Simplicity & clarity
- Variable or function names that require a comment to understand
- Deeply nested conditionals that could be flattened with early returns
- Unnecessary state; derived values stored instead of computed
