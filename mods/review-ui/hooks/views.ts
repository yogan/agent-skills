// Which tool calls print a view the person must see, and what of their output is the view.
//
// Pure, no `$`: the hooks module draws, this decides. Kept in step with the gate specs
// (skills/*/scripts/paste-gates.json): a command listed there is a view here.

// The subcommands that print a view, by script. `Q` is the quote a script's path may carry
// (`python3 "$SD/threads.py" present` is as common as the bare form).
const Q = `["']?`
const VIEW_COMMANDS = [
  String.raw`findings\.py${Q}\s+(?:resume|present|todo|quote|updates)\b`,
  String.raw`findings\.py${Q}\s+set\b[^\n]*--draft`,
  // A verdict the chat changed (Analyse more) shows the change alone; the background
  // agent's (`--unless-judged`) prints none.
  String.raw`findings\.py${Q}\s+verdict\b`,
  // The question whether to generate an explainer, with the agent's recommendation.
  String.raw`findings\.py${Q}\s+explainer\b[^\n]*--recommend`,
  String.raw`findings\.py${Q}\s+(?:proposal\b|own-review\s+(?:matched|done)\b)`,
  String.raw`findings\.py${Q}\s+diff\b[^\n]*--show`,
  String.raw`threads\.py${Q}\s+(?:present|todo|quote|reply-view|change-view)\b`,
  // A comment posted by hand that no longer holds: the verdict and its corrected version.
  String.raw`(?:findings|threads)\.py${Q}\s+posted\b[^\n]*--differs`,
  String.raw`(?:diff-view|change-preview)\.sh\b`,
].join('|')

// Only where the script is the program a segment of the command runs (`python3
// $SD/findings.py present`, optionally after `cd x &&` or an assignment), never where its
// name merely appears in an argument, a heredoc or a comment: a heredoc writing a test
// file once contained `findings.py quote`, and its output was drawn as a view.
const runs = (commands: string): RegExp => new RegExp(
  String.raw`(?:^|[;&|]|\n)\s*(?:\w+=\S*\s+)*(?:(?:python3?|sh|bash)\s+)?${Q}[^\s'"#]*?(?:${commands})`,
)
const VIEW = runs(VIEW_COMMANDS)

// The scripts' commands that post or resolve on GitLab, which are the person's press alone,
// by the same rule: a note, a commit message or a heredoc that only names one runs nothing.
const POST = runs(String.raw`(?:findings|threads)\.py${Q}\s+(?:post|resolve)\b`)

// The payload the scripts append to a view for the paste gate (lib/critical_manifest.py). It
// ends the view; anything after it is another command's output, chained behind.
const MANIFEST = /\n*<!-- paste-gate:critical\n([\s\S]*?)\n-->[^\n]*/g

import type { ReviewSession } from '../types'

// The manifest's fields. `review` and `draft` are this mod's: which review the view belongs
// to, and the version of the draft it shows (lib/critical_manifest.py's `extra`).
export type Manifest = {
  first?: string
  topic?: string
  focus?: string | null
  review?: ReviewSession
  draft?: string
  // rework-mr: the step the view shows its topic at (threads.py's `step_of`).
  step?: string
}

export type View = { text: string; topic?: string; step?: string; draft?: string }

// A script the command names through a variable it assigns first (`S=$SD/threads.py &&
// python3 $S quote t1`) is the same script: the variable is read as what it holds.
const ASSIGN = /(?:^|[\s;&|])(\w+)=(["']?)([^\s;&|'"]+)\2/g
const expanded = (command: string): string => {
  let out = command
  for (const [, name, , value] of command.matchAll(ASSIGN)) {
    out = out.replace(new RegExp(String.raw`\$(?:\{${name}\}|${name}\b)`, 'g'), value ?? '')
  }
  return out
}

export const isViewCommand = (command: unknown): command is string =>
  typeof command === 'string' && (VIEW.test(command) || VIEW.test(expanded(command)))

export const isPostCommand = (command: unknown): command is string =>
  typeof command === 'string' && (POST.test(command) || POST.test(expanded(command)))

const parseManifest = (stdout: string): { body: string; manifest?: Manifest } => {
  const m = [...stdout.matchAll(MANIFEST)].pop()
  if (!m) return { body: stdout }
  let manifest: Manifest | undefined
  try {
    manifest = JSON.parse(m[1] ?? '') as Manifest
  } catch {
    manifest = undefined
  }
  return { body: stdout.slice(0, m.index), manifest }
}

// The manifest a view command's stdout ends with, or undefined.
export const manifestOf = (stdout: unknown): Manifest | undefined =>
  typeof stdout === 'string' ? parseManifest(withoutNote(stdout)).manifest : undefined

// The view a view command's stdout holds, or undefined when there is none to show:
// nothing printed, or a view of a topic that is not the current one. That one is research
// (lib/focus.py): the model reads it, the person is not shown it.
//
// `first` names the view's opening line. Output ahead of it is not the view: the model
// chains a formatter or a linter in front of a view command, and `2>&1` folds stderr in.
export const viewOf = (stdout: unknown): View | undefined => {
  if (typeof stdout !== 'string') return undefined
  // Every view the scripts print ends on its manifest. Output without a whole one is the
  // model reading a view through `sed`, `head` or `grep`: its own research, not a view.
  // So the manifest is what makes a card: whatever replaces the paste gate's must keep an
  // end marker like it, in the same change, or every view falls back to a raw tool row.
  const { body, manifest } = parseManifest(withoutNote(stdout))
  if (!manifest) return undefined
  if (manifest?.topic && manifest.focus !== undefined && manifest.topic !== manifest.focus) {
    return undefined
  }
  let text = body
  if (manifest?.first) {
    const lines = body.split('\n')
    const at = lines.findIndex(line => line.trim() === manifest.first)
    // Its first line gone, the view was cut at the top (`| tail`): research, like a `head`.
    if (at < 0) return undefined
    if (at > 0) text = lines.slice(at).join('\n')
  }
  text = text.trim()
  if (!text) return undefined
  if (!manifest?.topic) return { text }
  return { text, topic: manifest.topic, ...(manifest.step ? { step: manifest.step } : {}), ...(manifest.draft ? { draft: manifest.draft } : {}) }
}

// The line the model reads at the end of a view command's output once the person has the
// view as a card. It states a fact and gives no order: instructions inside a tool result are
// rightly ignored by the model, so what to DO with the line is the skills' own rule ("A card
// is a paste already made", in both SKILL.md files), which a mod cannot add to a skill's text
// either: an organization's plugin may sit above this one and pass over `skill.prompt` and
// `prompt.compose`, as one did on the machine this was built on.
//
// hooks/paste-gate.py stands down for a session where a tool result ENDS with this line.
// Change it in all four places at once.
export const NOTE = '[review-ui] Shown to the user as a card.'

// The output with the note after it, as the model reads it.
export const withNote = (stdout: string): string => stdout.replace(/\s*$/, '') + '\n\n' + NOTE + '\n'

// The output as the script printed it, the note taken off again: the row and the card are
// drawn from the result the model read.
export const withoutNote = (stdout: string): string => {
  const at = stdout.lastIndexOf(NOTE)
  return at < 0 ? stdout : stdout.slice(0, at).replace(/\s*$/, '\n')
}

// A topic card's title line as the scripts write it where the mod draws: the topic's label
// (state icon, bold handle, and in review-mr the severity or kind icon), the bold summary,
// and for a posted topic the thread's place after it (`· file:line · drafts de · rest en`).
// The icons are Nerd Font glyphs, which sit in Unicode's private use areas: that is what tells
// this label from a pasted heading of the same shape.
const TITLE = /^(\p{Co}) \*\*(t\d+)\*\*(?: (\p{Co}))?(?: \*\*([^\n]*?)\*\*)?(?: · )?([^\n]*)\n*/u

export type CardTitle = { state: string; kind?: string; id: string; summary?: string; tail: string; rest: string }
export const titleOf = (text: string): CardTitle | undefined => {
  const m = TITLE.exec(text)
  if (!m) return undefined
  return {
    state: m[1] as string, id: m[2] as string, kind: m[3] || undefined,
    summary: m[4] || undefined, tail: (m[5] ?? '').trim(), rest: text.slice(m[0].length),
  }
}

// A card's code excerpt, the label above it and its fence, as both skills print it. While
// the sidebar shows the current item's file beside the chat, the card says where the code is
// instead of repeating a cut-down copy of it.
const EXCERPT = /(^|\n)_Code (?:currently in MR|the comment is on) — ([^\n]*?):_\n\n(`{3,}|~{3,})[^\n]*\n[\s\S]*?\n\3[ \t]*(?=\n|$)/

// The card's title line already says where the code is, so nothing stands in its place.
export const withoutExcerpt = (text: string): string => text.replace(EXCERPT, (_, lead: string) => lead)

// The excerpt without its label: the card's title line already names the file and line.
const EXCERPT_LABEL = /(^|\n)_Code (?:currently in MR|the comment is on) — [^\n]*?:_\n\n(?=`{3,}|~{3,})/
export const withoutExcerptLabel = (text: string): string => text.replace(EXCERPT_LABEL, (_, lead: string) => lead)

// A card's thread: each comment (its head, then its text in `>` lines) and the line that
// counts the comments left out between two. While the sidebar beside the chat shows the
// current topic's thread, that topic's card leaves it out rather than show it twice.
const MORE = /^_… \d+ more …_$/
export const withoutThread = (text: string): string => {
  const out: string[] = []
  let inNote = false
  for (const line of text.split('\n')) {
    if (NOTE_HEAD.test(line)) {
      inNote = true
      continue
    }
    if (inNote && line.startsWith('>')) continue
    inNote = false
    if (MORE.test(line.trim())) continue
    out.push(line)
  }
  return out.join('\n').replace(/\n{3,}/g, '\n\n').trim()
}

// A card's text in blocks, so the card can lay it out by one rule: a label sits directly on
// what it labels, and one blank line sets every part apart from the one before.
//
// - a label: a line that only says where something is (an excerpt's, the draft's), drawn
//   dim, or a heading standing alone on its line (`**Analysis**`);
// - a comment of a thread (lib/fences.py's `note_md`, compact: `> [link](url) **Author**
//   _time_`, then its text in `>` lines), its head apart, so the card can draw the person's
//   own name in its colour, which markdown cannot; its text stays a quote, so the bar runs
//   down every line however the card wraps it;
// - everything else, run together, its own blank lines kept as paragraphs.
// A draft's label is a heading; where it goes is the aside under it (older views: one line).
const ASIDE = /^_(?:Code currently in MR|Code the comment is on|Draft of |As a (?:reply|new thread) |MR-level: )[^\n]*_$/
const HEADING = /^\*\*[^*]+\*\*$/
// A comment's author is followed by its link glyph: what tells it from a draft that starts in
// bold. Mirrors lib/fences.py's LINK_ICON.
const NOTE_HEAD = /^> (\[\u{f03cc}\]\([^)]*\)) \*\*([^*]+)\*\*(?: (.*))?$/u

export type Block = { text: string; isAside: boolean; isLabel?: boolean; author?: string; link?: string; when?: string; isApart?: boolean }

// A comment head's rest: its time where the head carries one, else (an older view) the
// comment's first line.
const WHEN = /^_([^_]+)_$/

export const blocksOf = (text: string): Block[] => {
  const out: Block[] = []
  let isBlank = false
  let inNote = false
  const push = (b: Block) => {
    const prev = out[out.length - 1]
    out.push({ ...b, isApart: isBlank && prev !== undefined && !prev.isLabel })
    isBlank = false
  }
  for (const line of text.split('\n')) {
    const last = out[out.length - 1]
    if (!line.trim()) {
      isBlank = true
      inNote = false
      continue
    }
    const head = NOTE_HEAD.exec(line)
    if (head) {
      const when = WHEN.exec(head[3] ?? '')?.[1]
      push({ text: when || !head[3] ? '' : `> ${head[3]}`, isAside: false, author: head[2], link: head[1], ...(when ? { when } : {}) })
      inNote = true
      continue
    }
    if (inNote && last?.author !== undefined && /^>/.test(line)) {
      last.text = last.text ? `${last.text}\n${line}` : line
      continue
    }
    inNote = false
    const isAside = ASIDE.test(line.trim())
    if (isAside || HEADING.test(line.trim())) {
      push({ text: line, isAside, isLabel: true })
      continue
    }
    if (last && !last.isLabel && last.author === undefined) {
      last.text += `\n${isBlank ? '\n' : ''}${line}`
      isBlank = false
      continue
    }
    push({ text: line, isAside: false })
  }
  return out.filter(b => b.text.trim() || b.author !== undefined)
}

// A text's code in diff form (lib/code_view.py's `diff_fence`, lib/fences.py's `diff_info`:
// ```diff path=<file> at=<n>), apart from the markdown around it, so a card draws it as the
// sidebar draws its file: each hunk a diff with both line numbers, the comment's own hunk
// (`at`, 1-based) on a background. One inside a quote (a draft's, a comment's suggestion)
// keeps the quote's bar.
//
// `loc` (a proposal's change): the block says where it is itself, one line above its hunks
// with the file and the lines they span, as the sidebar heads its file. Every other block
// sits where its card already says where (an excerpt, a suggestion in a comment).
export type Part = { md: string } | { path: string; at?: number; hunks: string[]; isQuoted: boolean; loc?: string }
const DIFF_OPEN = /^(> ?)?```diff path=(\S+)(?: at=(\d+))?( loc)?[ \t]*$/

// The lines of the file as it is now that hunks span: `path:60–67`, or `path:60` for one.
const NEW_SIDE = /^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@/
export const locOf = (path: string, hunks: string[]): string => {
  const spans = hunks.map(h => NEW_SIDE.exec(h)).filter((m): m is RegExpExecArray => !!m)
    .map(m => [Number(m[1]), Number(m[1]) + Math.max(1, Number(m[2] ?? 1)) - 1] as const)
  if (!spans.length) return path
  const from = Math.min(...spans.map(x => x[0]))
  const to = Math.max(...spans.map(x => x[1]))
  return `${path}:${from}${to > from ? `–${to}` : ''}`
}

export const partsOf = (text: string): Part[] => {
  const out: Part[] = []
  let md: string[] = []
  const flush = () => {
    // A quote's own bar line left at the edge of a run would draw an empty quoted row.
    while (md.length && /^>?\s*$/.test(md[md.length - 1] as string)) md.pop()
    while (md.length && /^>?\s*$/.test(md[0] as string)) md.shift()
    if (md.length) out.push({ md: md.join('\n') })
    md = []
  }
  const lines = text.split('\n')
  for (let i = 0; i < lines.length; i += 1) {
    const m = DIFF_OPEN.exec(lines[i] as string)
    const close = m ? lines.indexOf(`${m[1] ?? ''}\`\`\``.trimEnd(), i + 1) : -1
    if (!m || close < 0) {
      md.push(lines[i] as string)
      continue
    }
    flush()
    const prefix = m[1] ?? ''
    const body = lines.slice(i + 1, close).map(ln => (ln.startsWith(prefix) ? ln.slice(prefix.length) : ln.replace(/^>/, '')))
    const hunks = body.join('\n').split(/\n(?=@@ )/).filter(h => h.startsWith('@@ '))
    out.push({ path: m[2] as string, at: m[3] ? Number(m[3]) : undefined, hunks, isQuoted: prefix !== '',
      ...(m[4] ? { loc: locOf(m[2] as string, hunks) } : {}) })
    i = close
  }
  flush()
  return out.length ? out : [{ md: '' }]
}

// A reply's text without what it repeats of a card: a paragraph that is word for word one of
// a drawn view's says to the person what the card above it already did. The model is told the
// view is on screen and still pastes it at times; what it adds of its own stays.
const paragraphs = (text: string): string[] => text.split(/\n\s*\n/).map(p => p.trim()).filter(Boolean)
const same = (p: string) => p.replace(/\s+/g, ' ')

export const withoutPasted = (reply: string, views: Iterable<string>): string => {
  const shown = new Set<string>()
  for (const v of views) for (const p of paragraphs(v)) shown.add(same(p))
  return paragraphs(reply).filter(p => !shown.has(same(p))).join('\n\n')
}

// The size of a diff as every skill writes it (lib/diffsize.py): the file-diff icon, the file
// count, then the lines added and removed. Markdown cannot colour, so a line holding one is
// drawn apart, the counts in green and red. A push stored before that form (`+A/−D` · N
// files) reads the same.
export type DiffSize = { files: number; added: number; removed: number }
// One space apart, or two as written before.
const DIFF_SIZE = /\u{f4d2} (\d+) files? {1,2}\+(\d+) {1,2}−(\d+)/u
const OLD_SIZE = /^`?\+(\d+)\/−(\d+)`? · (\d+) files?$/

export const sizeOf = (text: string): { before: string; size: DiffSize; after: string } | undefined => {
  const m = DIFF_SIZE.exec(text)
  if (m) {
    return { before: text.slice(0, m.index), size: { files: Number(m[1]), added: Number(m[2]), removed: Number(m[3]) }, after: text.slice(m.index + m[0].length) }
  }
  const old = OLD_SIZE.exec(text.trim())
  return old ? { before: '', size: { files: Number(old[3]), added: Number(old[1]), removed: Number(old[2]) }, after: '' } : undefined
}

// Markdown in runs, each line holding a diff's size apart from the rest; never one in a fence
// or a quote, where it is someone's text.
// `gap`: a blank line stood between it and the run before, which the card keeps.
export type SizeRun = ({ md: string } | { before: string; size: DiffSize; after: string } | CommitRun | WordsRun | DraftHeadRun) & { gap?: true }
// A draft's heading (lib/ui.py's DRAFT_ICON, then "Draft …"), which a card draws with its
// Copy beside it.
export type DraftHeadRun = { draftHead: string }
const DRAFT_HEAD = /^\*\*\u{F12C4} Draft [^*]+\*\*$/u
// A proposed edit of a text, word by word (lib/worddiff.py): the text in pieces, each kept,
// taken out or put in.
export type WordsRun = { words: { text: string; kind: 'same' | 'removed' | 'added'; isCode?: boolean }[] }
const WORD_MARK = /\[-([\s\S]*?)-\]|\{\+([\s\S]*?)\+\}/g
// The pieces again, with markdown's code spans told apart: a span may run across a change,
// so whether the text is inside backticks is carried from piece to piece, and the backticks
// themselves are left out, as markdown draws them.
export const withCode = (words: WordsRun['words']): WordsRun['words'] => {
  const out: WordsRun['words'] = []
  let isCode = false
  for (const w of words) {
    w.text.split('`').forEach((part, i) => {
      if (i > 0) isCode = !isCode
      if (part) out.push({ ...w, text: part, ...(isCode ? { isCode: true } : {}) })
    })
  }
  return out
}

export const wordsOf = (marked: string): WordsRun['words'] => {
  const out: WordsRun['words'] = []
  let at = 0
  for (const m of marked.matchAll(WORD_MARK)) {
    if ((m.index ?? 0) > at) out.push({ text: marked.slice(at, m.index), kind: 'same' })
    out.push(m[1] !== undefined ? { text: m[1], kind: 'removed' } : { text: m[2] ?? '', kind: 'added' })
    at = (m.index ?? 0) + m[0].length
  }
  if (at < marked.length) out.push({ text: marked.slice(at), kind: 'same' })
  return out
}
// A commit a fix goes into, on its own line (lib/ui.py's REWRITTEN_ICON and NEW_COMMIT_ICON):
// its icon, its hash where it has one, and its subject.
export type CommitRun = { commit: { icon: string; sha: string | null; subject: string } }
const COMMIT_LINE = /^([\u{F071B}\u{F071C}]) (?:`([0-9a-f]{7,40})` )?(.*)$/u
export const sizeRunsOf = (md: string): SizeRun[] => {
  const out: SizeRun[] = []
  let run: string[] = []
  let fence: string | null = null
  // Whether a blank line came since the last run: the runs are drawn apart, so the blank
  // line between two of them is drawn as a gap, or the card loses it.
  let isGap = false
  // A heading sits on what it heads, as a card draws every other one: no gap under a
  // heading that carries a size (the fix's Changes).
  const isHeadingLine = (r: SizeRun | undefined) => !!r && (('size' in r && /^\*\*[^*]+\*\*$/.test(r.before.trim())) || 'draftHead' in r)
  const push = (r: SizeRun) => {
    out.push(isGap && out.length > 0 && !isHeadingLine(out[out.length - 1]) ? { ...r, gap: true } : r)
    isGap = false
  }
  const flushRun = () => {
    const isBlank = (l: string | undefined) => l !== undefined && !l.trim()
    if (run.length && run.every(l => !l.trim())) isGap = true
    if (isBlank(run[0])) isGap = true
    const isTrailing = isBlank(run[run.length - 1])
    while (run.length && !(run[run.length - 1] as string).trim()) run.pop()
    while (run.length && !(run[0] as string).trim()) run.shift()
    if (run.length) {
      push({ md: run.join('\n') })
      isGap = isTrailing
    }
    run = []
  }
  let words: string[] | null = null
  for (const line of md.split('\n')) {
    // A proposed edit's block, drawn word by word rather than as code.
    if (words !== null) {
      if (/^\s*`{3,}\s*$/.test(line)) {
        push({ words: withCode(wordsOf(words.join('\n'))) })
        words = null
      } else {
        words.push(line)
      }
      continue
    }
    if (fence === null && /^\s*`{3,}worddiff\s*$/.test(line)) {
      flushRun()
      words = []
      continue
    }
    const open = /^\s*(`{3,}|~{3,})/.exec(line)?.[1]
    if (open && (fence === null || open.startsWith(fence))) fence = fence === null ? open : null
    if (fence === null && !open && DRAFT_HEAD.test(line.trim())) {
      flushRun()
      push({ draftHead: line.trim() })
      continue
    }
    const commit = fence === null && !open ? COMMIT_LINE.exec(line) : null
    if (commit) {
      flushRun()
      push({ commit: { icon: commit[1] as string, sha: commit[2] ?? null, subject: commit[3] as string } })
      continue
    }
    const hit = fence === null && !open && !line.startsWith('>') ? DIFF_SIZE.exec(line) && sizeOf(line) : undefined
    if (!hit) {
      run.push(line)
      continue
    }
    flushRun()
    push(hit)
  }
  flushRun()
  return out
}

// A GitLab ```suggestion in a comment, as the sidebar shows it: a diff against the lines it
// replaces, the way GitLab and the cards show one (lib/fences.py's `suggestion_diff`), where
// the file is at hand; else plain code in the file's language. `anchor` is the line the
// comment is on, `file` the file's lines as they are now.
const SUGGESTION_OPEN = /^(\s*)(`{3,})suggestion(?::-(\d+)\+(\d+))?\s*$/

export const withSuggestions = (body: string, path: string | null | undefined, anchor: number | null, file: string[] | null): string => {
  const lines = body.split('\n')
  const out: string[] = []
  for (let i = 0; i < lines.length; i += 1) {
    const m = SUGGESTION_OPEN.exec(lines[i] as string)
    const close = m ? lines.findIndex((ln, j) => j > i && ln.trim() === m[2]) : -1
    if (!m || close < 0) {
      out.push(lines[i] as string)
      continue
    }
    const lead = (m[1] ?? '').length
    const code = lines.slice(i + 1, close).map(ln => ln.slice(Math.min(lead, ln.length - ln.trimStart().length)))
    const hunk = anchor && file ? suggestionHunk(code, Number(m[3] ?? 0), Number(m[4] ?? 0), anchor, file) : undefined
    const ext = (path ?? '').split('.').pop() ?? ''
    out.push(...(hunk ? [`\`\`\`diff path=${path}`, ...hunk, '```'] : [`\`\`\`${ext}`, ...code, '```']))
    i = close
  }
  return out.join('\n')
}

// The hunk a suggestion makes of lines `anchor - above` to `anchor + below`: the lines it
// keeps as context, the rest removed and added, by a longest common run of lines.
const suggestionHunk = (code: string[], above: number, below: number, anchor: number, file: string[]): string[] | undefined => {
  if (anchor < 1 || anchor > file.length) return undefined
  const first = Math.max(1, anchor - above)
  const old = file.slice(first - 1, Math.min(file.length, anchor + below))
  const neu = code.join('').trim() ? code : []
  const n = old.length
  const m = neu.length
  const lcs: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0))
  for (let a = n - 1; a >= 0; a -= 1) {
    for (let b = m - 1; b >= 0; b -= 1) {
      const row = lcs[a] as number[]
      row[b] = old[a] === neu[b] ? ((lcs[a + 1] as number[])[b + 1] as number) + 1 : Math.max((lcs[a + 1] as number[])[b] as number, row[b + 1] as number)
    }
  }
  const body: string[] = []
  let a = 0
  let b = 0
  while (a < n || b < m) {
    if (a < n && b < m && old[a] === neu[b]) { body.push(` ${old[a]}`); a += 1; b += 1 }
    else if (b >= m || (a < n && ((lcs[a + 1] as number[])[b] as number) >= ((lcs[a] as number[])[b + 1] as number))) { body.push(`-${old[a]}`); a += 1 }
    else { body.push(`+${neu[b]}`); b += 1 }
  }
  return [`@@ -${first},${n} +${m ? first : first - 1},${m} @@`, ...body]
}

// GitLab's emoji shortcodes (`:ok_hand:`) as the emoji, for a comment's text: the ones people
// commonly use in review comments. One not listed stays as written; code (a span, a fence)
// is left alone, as a shortcode there is someone's text.
const EMOJI: Record<string, string> = {
  '+1': '👍', thumbsup: '👍', '-1': '👎', thumbsdown: '👎', ok_hand: '👌', tada: '🎉', clap: '👏',
  pray: '🙏', muscle: '💪', raised_hands: '🙌', wave: '👋', point_up: '☝️', point_right: '👉',
  smile: '😄', smiley: '😃', grinning: '😀', laughing: '😆', joy: '😂', rofl: '🤣', wink: '😉',
  slight_smile: '🙂', slightly_smiling_face: '🙂', upside_down: '🙃', sweat_smile: '😅',
  sunglasses: '😎', heart_eyes: '😍', nerd: '🤓', thinking: '🤔', confused: '😕', grimacing: '😬',
  neutral_face: '😐', disappointed: '😞', cry: '😢', sob: '😭', see_no_evil: '🙈',
  facepalm: '🤦', shrug: '🤷', partying_face: '🥳', heart: '❤️', eyes: '👀', rocket: '🚀',
  fire: '🔥', sparkles: '✨', star: '⭐', boom: '💥', zap: '⚡', bulb: '💡', bug: '🐛', memo: '📝',
  construction: '🚧', lock: '🔒', recycle: '♻️', coffee: '☕', beers: '🍻', '100': '💯',
  white_check_mark: '✅', heavy_check_mark: '✔️', x: '❌', warning: '⚠️', question: '❓',
  exclamation: '❗', ok: '🆗',
}
const SHORTCODE = /:([a-z0-9_+-]+):/g
export const withEmoji = (md: string): string => {
  let fence: string | null = null
  return md.split('\n').map(line => {
    const open = /^\s*(`{3,}|~{3,})/.exec(line)?.[1]
    if (open && (fence === null || open.startsWith(fence))) {
      fence = fence === null ? open : null
      return line
    }
    if (fence !== null) return line
    // Outside code spans only: the odd pieces of a split on backticks are code.
    return line.split('`').map((part, i) => (i % 2 ? part : part.replace(SHORTCODE, (m, code: string) => EMOJI[code] ?? m))).join('`')
  }).join('\n')
}

// A card's text without its draft part (the heading, where it goes, the quoted draft), for
// a card drawn with a draft that is no longer the topic's: posted, recorded as posted, or
// redrafted since, its newer version on a card below.
export const withoutDraft = (text: string): string => {
  const lines = text.split('\n')
  const at = lines.findIndex(l => DRAFT_HEAD.test(l.trim()))
  if (at < 0) return text
  let end = at + 1
  // Up to the next part's heading, or the end: the aside, the quote and blank lines.
  while (end < lines.length && !/^\*\*[^*]+\*\*$/.test((lines[end] as string).trim())) end += 1
  return [...lines.slice(0, at), ...lines.slice(end)].join('\n').replace(/\n{3,}/g, '\n\n').trim()
}
