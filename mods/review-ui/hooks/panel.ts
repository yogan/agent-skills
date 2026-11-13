// The panel's topic list: which rows it shows, how each one reads, what collapses.
// Pure: register.tsx draws what this returns.

import type { ReviewState, ReviewTopic } from '../types'

// Nerd Fonts v3 Material Design glyphs by name, as lib/ui.py has them (the codepoints are
// checked against glyphnames.json there). Mirrors lib/ui.py: change both at once.
export const STATE_ICON: Record<string, string> = {
  new: '\u{f0182}', // md-comment_outline
  draft: '\u{f12c4}', // md-comment_edit_outline
  waiting: '\u{f09e4}', // md-comment_arrow_right_outline
  answered: '\u{f09e1}', // md-comment_arrow_left
  done: '\u{f017f}', // md-comment_check
  wontfix: '\u{f15e0}', // md-comment_minus_outline
  dropped: '\u{f0187}', // md-comment_remove_outline
}
// What arrived, in the sidebar's inbox, and where a topic stands in it, on its row.
export const INBOX_ICON: Record<string, string> = {
  reply: '\u{f0189}', // md-comment_text_outline
  resolved: '\u{f05e1}', // md-check_circle_outline
  push: '\u{f0718}', // md-source_commit
  thread: '\u{f05cb}', // md-account_voice
  own: '\u{f017c}', // md-comment_account_outline
  new: '\u{f09de}', // md-circle_medium
  judging: '\u{f01d8}', // md-dots_horizontal
  unjudged: '\u{f05d6}', // md-alert_circle_outline
  draft: '\u{f0cb6}', // md-pencil_outline
  rebase: '\u{f14ce}', // md-source_branch_sync
  merged: '\u{f062d}', // md-source_merge
  closed: '\u{f015a}', // md-close_circle_outline
  'auto-merge': '\u{f051f}', // md-timer_sand
  approved: '\u{f0514}', // md-thumb_up_outline
  left: '\u{f05d6}', // md-alert_circle_outline
}
// What happened to the MR as a whole, each kind in one colour wherever it is drawn: the
// sidebar's heading (the icon alone) and the News box (the icon and what it says).
export const MR_COLOR: Record<string, string> = { merged: 'magenta', closed: 'red', 'auto-merge': 'yellow', approved: 'green' }
export const INBOX_WORD: Record<string, string> = {
  new: 'news', judging: 'being judged', unjudged: 'not judged',
  draft: 'draft to post',
}
// A row's inbox mark: the slot is there on every row, so titles line up.
// A topic with a draft to post and nothing else to say has its pencil there: in review-mr the
// group says where a topic stands, and this is the one thing about it the group does not.
export const inboxMark = (t: ReviewTopic, unjudged: string[], judging: string[]): { key: string; icon: string; color?: string; isDim?: boolean } | undefined => {
  const isDraft = t.draft != null && (t.status === 'draft' || t.status === 'needs_ack')
  if (!t.inbox) return isDraft ? { key: 'draft', icon: INBOX_ICON.draft as string } : undefined
  if (unjudged.includes(t.id)) return { key: 'unjudged', icon: INBOX_ICON.unjudged as string, color: 'yellow' }
  // Being judged: dim, but in the colour of news on a topic the heading counts as new, so
  // the count is marked where it is counted.
  if (judging.includes(t.id)) {
    return t.inbox === 'new'
      ? { key: 'judging', icon: INBOX_ICON.judging as string, color: 'yellow' }
      : { key: 'judging', icon: INBOX_ICON.judging as string, isDim: true }
  }
  if (t.inbox === 'new') return { key: 'new', icon: INBOX_ICON.new as string, color: 'yellow' }
  return isDraft ? { key: 'draft', icon: INBOX_ICON.draft as string } : undefined
}

export const STATE_WORD: Record<string, string> = {
  new: 'new', draft: 'draft', waiting: 'waiting', answered: 'answered',
  done: 'done', wontfix: "won't fix", dropped: 'dropped',
}
export const SEVERITY_ICON: Record<string, string> = {
  critical: '\u{f0dbc}', // md-chevron_triple_up
  high: '\u{f013f}', // md-chevron_double_up
  medium: '\u{f0143}', // md-chevron_up
  low: '\u{f0140}', // md-chevron_down
}
const NO_SEVERITY = '\u{f0374}' // md-minus
export const KIND_ICON: Record<string, string> = {
  question: '\u{f0625}', // md-help_circle_outline
  praise: '\u{f194b}', // md-hand_clap: not the thumbs-up, which is Ack and an approval
}
// Where colour is drawn; the shape alone still reads where it is not.
export const SEVERITY_COLOR: Record<string, string> = {
  critical: 'red', high: '#ff8700', medium: 'yellow', low: 'blue',
}

export const CLOSED = ['done', 'wontfix', 'dropped']

// One row, every column in a fixed slot: an empty slot stays a slot, so no mark ever
// shifts a title. `kind` is the slot severity and kind share; review-mr alone has it.
export type Row = {
  id: string
  state: string
  icon: string
  kind?: { icon: string; color?: string; isDim?: boolean }
  title: string
  isCurrent: boolean
  isClosed: boolean
}

export const kindSlot = (t: ReviewTopic): NonNullable<Row['kind']> => {
  if (t.kind && KIND_ICON[t.kind]) return { icon: KIND_ICON[t.kind] as string }
  const icon = t.severity ? SEVERITY_ICON[t.severity] : undefined
  return icon ? { icon, color: SEVERITY_COLOR[t.severity as string] } : { icon: NO_SEVERITY, isDim: true }
}

export const rowOf = (review: ReviewState, t: ReviewTopic): Row => {
  const isReview = review.skill === 'review-mr'
  return {
    id: t.id,
    state: t.state,
    icon: STATE_ICON[t.state] ?? ' ',
    kind: isReview ? kindSlot(t) : undefined,
    title: plainTitle(t.summary ?? '(no summary yet)'),
    isCurrent: t.id === review.current,
    isClosed: CLOSED.includes(t.state),
  }
}

// The open topics in full, the closed ones behind one line until it is expanded.
export const panelRows = (review: ReviewState): { open: Row[]; closed: Row[] } => {
  const rows = review.topics.map(t => rowOf(review, t))
  return { open: rows.filter(r => !r.isClosed), closed: rows.filter(r => r.isClosed) }
}

// The list in groups, each under a header of one look, in the order the person is likely to
// act: what is to do (in the topics' own order; between rounds, the next round's),
// what waits on the other side (the MR's author, or in rework-mr the
// reviewers), other reviewers' threads the person only watches, then what is closed. Each
// topic is in one group; an empty group is left out.
export type Section = { key: string; title: string; rows: Row[] }

export const sectionsOf = (review: ReviewState): Section[] => {
  const { open, closed } = panelRows(review)
  // A group keeps the topics' own order, whatever order the round visits them in (the current
  // one first): a row moves only when its topic changes group.
  const inRound = new Set(review.round ?? [])
  const topic = (id: string) => review.topics.find(t => t.id === id)
  if (review.skill !== 'review-mr') {
    const round = open.filter(r => inRound.has(r.id))
    const taken = new Set(round.map(r => r.id))
    // The round's topics by what is left on them, as the passes go: while the plans are made,
    // to plan or planned; in the fix pass, a fix to make or push, or only the reply left.
    const step = (id: string) => topic(id)?.step
    const groups = review.pass === 'fix'
      ? [{ key: 'round', title: 'To fix', rows: round.filter(r => step(r.id) !== 'reply') },
        { key: 'reply', title: 'To reply', rows: round.filter(r => step(r.id) === 'reply') }]
      : [{ key: 'round', title: 'To plan', rows: round.filter(r => [undefined, 'plan'].includes(step(r.id))) },
        { key: 'planned', title: 'Planned', rows: round.filter(r => ![undefined, 'plan'].includes(step(r.id))) }]
    return [
      ...groups,
      { key: 'waiting', rows: open.filter(r => !taken.has(r.id)), title: 'Waiting for reviewers' },
      { key: 'closed', rows: closed, title: 'Closed' },
    ].filter(x => x.rows.length > 0)
  }
  const watched = review.topics.filter(t => t.watching).map(t => rowOf(review, t))
  const round = open.filter(r => inRound.has(r.id))
  const taken = new Set([...round, ...watched].map(r => r.id))
  // Merged or closed, nothing waits on the author any more: what did not come up is closed.
  const isOver = !!review.merged
  const rest = isOver ? [] : open.filter(r => !taken.has(r.id))
  const done = [...(isOver ? open.filter(r => !taken.has(r.id)) : []), ...closed.filter(r => !taken.has(r.id))]
  return [
    { key: 'round', rows: round, title: 'To do' },
    { key: 'waiting', rows: rest, title: 'Waiting for author' },
    { key: 'watching', rows: watched, title: 'From other reviewers' },
    { key: 'closed', rows: done, title: 'Closed' },
  ].filter(x => x.rows.length > 0)
}

// What the panel's icons mean, one group per slot, in the order a row reads them. Rework
// has the state slot alone. Where colour is drawn, a severity's word takes its colour.
export type LegendItem = { icon: string; word: string; color?: string }
export const legend = (isReview: boolean): LegendItem[][] => {
  const states = Object.keys(STATE_ICON)
    .filter(s => isReview || s !== 'dropped')
    .map(s => ({ icon: STATE_ICON[s] as string, word: STATE_WORD[s] as string }))
  if (!isReview) return [states]
  // review-mr's rows carry no state or source: the group says where a topic stands, and who
  // raised it is the card's to say.
  return [[
    ...Object.keys(SEVERITY_ICON).map(s => ({ icon: SEVERITY_ICON[s] as string, word: s, color: SEVERITY_COLOR[s] })),
    ...Object.keys(KIND_ICON).map(k => ({ icon: KIND_ICON[k] as string, word: k })),
  ]]
}

// The legend for what the list shows now: only the marks that appear in it.
export const legendFor = (review: ReviewState, isOwn: boolean): LegendItem[][] => {
  if (isOwn) {
    const own = review.own_threads ?? []
    const marks: LegendItem[] = []
    if (own.some(x => !x.topic)) marks.push({ icon: STATE_ICON.new as string, word: 'thread without topic' })
    if (own.some(x => x.topic)) marks.push({ icon: STATE_ICON.done as string, word: 'on a topic' })
    if (own.some(x => x.proposal)) marks.push({ icon: '→ t?', word: 'proposed for a topic' })
    return own.length ? [marks] : []
  }
  const isReview = review.skill === 'review-mr'
  const shown = review.topics
  const has = (pred: (t: ReviewTopic) => boolean) => shown.some(pred)
  const groups = legend(isReview).map(g => g.filter(item => {
    const state = Object.keys(STATE_WORD).find(k => STATE_WORD[k] === item.word)
    if (state) return has(t => t.state === state)
    if (SEVERITY_ICON[item.word]) return has(t => (t.kind ?? 'issue') === 'issue' && t.severity === item.word)
    if (KIND_ICON[item.word]) return has(t => t.kind === item.word)
    return true
  })).filter(g => g.length > 0)
  return shown.length ? groups : []
}

// A title as plain text, where no markdown is drawn (a row, a card's title line): its code
// spans without the backticks that mark them, which would show as they are.
export const plainTitle = (title: string): string => title.replace(/`+([^`]*)`+/g, '$1').replace(/`/g, '')

// A title cut to the room left beside the fixed slots, so every row is one line.
export const fitTitle = (title: string, room: number): string =>
  title.length <= room ? title : `${title.slice(0, Math.max(0, room - 1))}…`

// A comment's text with the line breaks its writer typed to wrap a paragraph joined up, so
// the panel wraps it to its own width instead of breaking it twice. Code, lists, quotes,
// tables and headings keep their lines; a blank line still ends a paragraph.
export const reflow = (md: string): string => {
  const out: string[] = []
  let inFence = false
  const keeps = (line: string) => /^\s*(?:[-*+>|#]|\d+[.)]|```|~~~|$)/.test(line) || /^\s{4}/.test(line)
  for (const line of md.split('\n')) {
    if (/^\s*(```|~~~)/.test(line)) inFence = !inFence
    const prev = out[out.length - 1]
    if (!inFence && prev !== undefined && !keeps(line) && !keeps(prev) && !/^\s*(```|~~~)/.test(line)) {
      out[out.length - 1] = `${prev} ${line.trim()}`
    } else {
      out.push(line)
    }
  }
  return out.join('\n')
}

// A comment cut to one line of `room` characters, for a thread set in under its code: its
// paragraphs run together, each code block (a suggestion too) a mark in its place, links
// as their text, and the end cut off where it does not fit. `isCut` says something was left
// out, so the full thread is worth opening.
export const CODE_MARK = '[code]'
export const SUGGESTION_MARK = '[suggestion]'
export const oneLineNote = (body: string, room: number): { text: string; isCut: boolean } => {
  const parts: string[] = []
  let isCut = false
  let fence: string | null = null
  for (const line of reflow(body).split('\n')) {
    const open = /^\s*(`{3,}|~{3,})\s*(\S*)/.exec(line)
    if (fence === null && open) {
      fence = open[1] as string
      parts.push(open[2] === 'suggestion' || open[2]?.startsWith('suggestion:') ? SUGGESTION_MARK : CODE_MARK)
      isCut = true
      continue
    }
    if (fence !== null) {
      if (line.trim().startsWith(fence)) fence = null
      continue
    }
    if (line.trim()) parts.push(line.trim())
  }
  let text = parts.join(' ').replace(/\[([^\]]*)\]\([^)]*\)/g, '$1').replace(/\s+/g, ' ').trim()
  if (text.length > room) {
    isCut = true
    text = text.slice(0, Math.max(0, room - 1))
    // A cut inside inline code would leave its backtick open and colour the rest.
    if ((text.match(/`/g) ?? []).length % 2 === 1) text = text.slice(0, text.lastIndexOf('`'))
    text = `${text.trimEnd()}…`
  }
  return { text, isCut }
}

// When a comment was written, as its header in the sidebar says it: `Nov 5, 12:09`, in the
// machine's own time; nothing where the time is not known.
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
export const whenOf = (at: string | null | undefined): string => {
  const d = at ? new Date(at) : null
  if (!d || Number.isNaN(d.getTime())) return ''
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  return `${MONTHS[d.getMonth()]} ${d.getDate()}, ${hm}`
}

// A thread box opened in the sidebar, as rows of exactly one line each, so its height is
// known and a box taller than its room scrolls a row at a time. Text is wrapped here to the
// box's width (never inside inline code); each code block (a suggestion, a snippet) is one
// row that folds and opens it, folded by default, and opened, a row per line of it, cut at
// the edge rather than wrapped. A suggestion opens as the diff it makes of the lines it is on.
export type BoxRow =
  | { kind: 'head'; note: number }
  | { kind: 'md'; text: string }
  | { kind: 'blank' }
  | { kind: 'fold'; key: string; label: string; isOpen: boolean }
  | { kind: 'code'; key: string; line: string; lang?: string; diff?: { old: number; new: number } }

// A bare URL as the box shows it: a short label, the link whole behind it. GitLab's own
// links say what they point at (`!12 · commit a1b2c3d`, `#7`); any other its host and path.
export const urlLabel = (url: string): string => {
  const m = /\/-\/merge_requests\/(\d+)(\/diffs)?(?:[^#]*?[?&]commit_id=([0-9a-f]{7,}))?[^#]*(#note_\d+)?/.exec(url)
  if (m) return [`!${m[1]}`, m[3] ? `commit ${m[3].slice(0, 7)}` : m[4] ? 'comment' : m[2] ? 'changes' : ''].filter(Boolean).join(' · ')
  const c = /\/-\/commit\/([0-9a-f]{7,})/.exec(url)
  if (c) return `commit ${(c[1] as string).slice(0, 7)}`
  const i = /\/-\/(issues|work_items)\/(\d+)/.exec(url)
  if (i) return `#${i[2]}`
  const plain = url.replace(/^[a-z]+:\/\//, '').replace(/[?#].*$/, '').replace(/\/$/, '')
  return plain.length > 40 ? `${plain.slice(0, 39)}…` : plain
}

// Bare URLs in a line of text as links with a short label; links already written as such,
// and code, are left alone. Spaces in a label are kept from breaking it across rows.
const withLinks = (line: string): string =>
  line.split(/(`[^`]*`|\[[^\]]*\]\([^)]*\)|<https?:\/\/[^>]+>)/).map((part, k) => {
    if (k % 2 === 1) {
      const auto = /^<(https?:\/\/[^>]+)>$/.exec(part)
      return auto ? `[${urlLabel(auto[1] as string).replace(/ /g, '\u00a0')}](${auto[1]})` : part
    }
    return part.replace(/https?:\/\/[^\s<>()]+[^\s<>().,;:!?'"]/g, u => `[${urlLabel(u).replace(/ /g, '\u00a0')}](${u})`)
  }).join('')

// How wide a line of markdown draws: a link as its label.
const shownLength = (text: string): number => text.replace(/\[([^\]]*)\]\([^)]*\)/g, '$1').length

const wrapLine = (line: string, width: number): string[] => {
  if (shownLength(line) <= width) return [line]
  const lead = /^(\s*(?:[-*+]|\d+[.)]|>)\s+)/.exec(line)?.[1] ?? ''
  // A quote's continuation stays in the quote; a list item's sits under its text.
  const indent = lead.trim() === '>' ? '> ' : '\u00a0'.repeat(lead.length)
  // Words are cut at spaces outside inline code and links, so neither breaks across rows.
  const words = line.slice(lead.length).split(/ (?=(?:[^`]*`[^`]*`)*[^`]*$)(?![^[]*\]\()(?![^(]*\))/)
  const out: string[] = []
  let cur = lead
  let isEmpty = true
  for (const word of words) {
    if (!isEmpty && shownLength(cur) + 1 + shownLength(word) > width) {
      out.push(cur)
      cur = indent
      isEmpty = true
    }
    cur = isEmpty ? `${cur}${word}` : `${cur} ${word}`
    isEmpty = false
    // A word wider than the row is cut; a link never is, and overflows its row instead.
    while (shownLength(cur) > width && !/\]\([^)]*\)/.test(cur)) {
      out.push(cur.slice(0, width))
      cur = `${indent}${cur.slice(width)}`
    }
  }
  if (!isEmpty) out.push(cur)
  return out
}

// `suggest` turns a suggestion's block into the fenced diff (or code) the sidebar shows of it
// (views.ts's `withSuggestions`, given the file); `isOpen` says which blocks the person opened.
export const boxRowsOf = (
  bodies: string[], width: number, keyOf: (note: number, block: number) => string,
  isOpen: (key: string, byDefault: boolean) => boolean, suggest: (block: string) => string, ext: string,
): BoxRow[] => {
  const rows: BoxRow[] = []
  bodies.forEach((body, i) => {
    if (i > 0) rows.push({ kind: 'blank' })
    rows.push({ kind: 'head', note: i })
    const lines = reflow(body).split('\n')
    let block = 0
    for (let j = 0; j < lines.length; j += 1) {
      const line = lines[j] as string
      const open = /^\s*(`{3,}|~{3,})\s*(\S*)/.exec(line)
      if (!open) {
        if (!line.trim()) {
          if (rows[rows.length - 1]?.kind !== 'blank' && rows[rows.length - 1]?.kind !== 'head') rows.push({ kind: 'blank' })
        } else {
          rows.push(...wrapLine(withLinks(line), width).map(text => ({ kind: 'md' as const, text })))
        }
        continue
      }
      const fence = open[1] as string
      const end = lines.findIndex((ln, k) => k > j && ln.trim().startsWith(fence))
      const close = end < 0 ? lines.length : end
      const info = open[2] ?? ''
      const isSuggestion = info === 'suggestion' || info.startsWith('suggestion:')
      const code = lines.slice(j + 1, close)
      const key = keyOf(i, block)
      block += 1
      // A suggestion that is the whole comment is what the comment says: open, until folded.
      // One beside prose stays folded, the prose saying what it is about.
      const isWhole = isSuggestion && lines.every((ln, k) => (k >= j && k <= close) || !ln.trim())
      const shown = isOpen(key, isWhole)
      const lang = isSuggestion ? '' : info
      const label = `${shown ? '\u{F47C}' : '\u{F460}'} ${isSuggestion ? 'suggestion' : 'code'}${lang ? ` · ${lang}` : ''} · ${code.length} line${code.length === 1 ? '' : 's'}`
      rows.push({ kind: 'fold', key, label, isOpen: shown })
      if (shown) {
        const fenced = (isSuggestion ? suggest(lines.slice(j, close + 1).join('\n')) : '').split('\n')
        const isDiff = /^`{3,}diff\b/.test(fenced[0] ?? '') && /^@@ -(\d+)(?:,\d+)? \+(\d+)/.test(fenced[1] ?? '')
        if (isDiff) {
          const m = /^@@ -(\d+)(?:,\d+)? \+(\d+)/.exec(fenced[1] as string) as RegExpExecArray
          let a = Number(m[1])
          let b = Number(m[2])
          for (const ln of fenced.slice(2, -1)) {
            rows.push({ kind: 'code', key, line: ln, diff: { old: a, new: b } })
            if (!ln.startsWith('+')) a += 1
            if (!ln.startsWith('-')) b += 1
          }
        } else {
          rows.push(...code.map(ln => ({ kind: 'code' as const, key, line: ln, lang: lang || ext })))
        }
      }
      j = close
    }
  })
  while (rows[rows.length - 1]?.kind === 'blank') rows.pop()
  return rows
}
