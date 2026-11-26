// What the sidebar shows, in each phase of a review: which list, which item of it is current,
// which file goes under it and which stretch of that file fits. Pure: register.tsx draws it.

import type { ReviewCode, ReviewOwnThread, ReviewState } from '../types'

// While the person's own review is open (and while its comments are being matched), the
// sidebar lists their own posted threads: the agent's topics stay out of sight so they do
// not steer it. After that, the topics; and the own threads again while a round is at a
// comment of theirs to match (between rounds such a comment waits in the inbox).
export type Phase = 'own' | 'topics'

export const phaseOf = (r: ReviewState): Phase =>
  r.skill === 'review-mr' && (r.own_review === 'pending' || r.own_review === 'matching'
    || ((r.proposals?.length ?? 0) > 0 && !r.current && r.in_round !== false))
    ? 'own'
    : 'topics'

// The own thread that is current: in the matching, the one whose proposal the band asks
// about; before it, only the one the person opened.
//
// The person's pick always wins, so every row they click or step to becomes current. With
// none, while their review is open, none is current: they open their comments themselves,
// and one they just posted is listed without coming up. In the matching, the first comment
// whose proposal waits, else the first comment.
// The pick of none: the person closed the comment's card (its ✕) while their review is open,
// back to the list of their comments with none open.
export const NO_OWN = 'none'

export const currentOwn = (r: ReviewState, picked: string | null): ReviewOwnThread | undefined => {
  const own = r.own_threads ?? []
  if (r.own_review === 'pending' && !own.some(x => x.id === picked)) return undefined
  const asked = r.proposals?.[0]?.thread
  return own.find(x => x.id === picked) ?? (asked ? own.find(x => x.id === asked) : undefined) ?? own[0]
}

// The proposal the band answers: the current comment's own, or none when that comment is
// already settled (the band then offers the way back to one that is not).
export const askedFor = (r: ReviewState, own: ReviewOwnThread | undefined): ReviewState => {
  const all = r.proposals ?? []
  const mine = all.find(p => p.thread === own?.id)
  return { ...r, proposals: mine ? [mine, ...all.filter(p => p !== mine)] : [] }
}

// The neighbour of the current own thread, for Previous and Next.
export const stepOwn = (r: ReviewState, from: string | undefined, by: 1 | -1): string | undefined => {
  const own = r.own_threads ?? []
  if (own.length === 0) return undefined
  const at = Math.max(0, own.findIndex(x => x.id === from))
  return own[(at + by + own.length) % own.length]?.id
}

// What the code command is asked for the current item, and the key its answer is kept under.
// For a topic whose lines a push changed, the file shows what the push did: the change since
// the comment, the question at that moment. Every other topic, the MR's whole change.
export const sinceOf = (r: ReviewState): boolean =>
  (r.topics.find(x => x.id === r.current)?.news ?? []).includes('push')

// rework-mr, a fix made and not pushed yet: the fix itself, in the comment's own file, its
// files listed under it.
// `picks`: the file the person picked from a fix's list, by the item's own key (`base`); its
// file is asked for and kept under a key of its own.
// `pushes`: rework-mr, the push the person picked of a topic's pushed fix, by topic; none
// picked, or one past the last, is the latest.
export const codeArgsOf = (r: ReviewState, own: ReviewOwnThread | undefined, picks: Record<string, string> = {},
  pushes: Record<string, number> = {}): { key: string; base: string; args: string[] } | undefined => {
  if (phaseOf(r) === 'own') return own ? { key: `thread:${own.id}`, base: `thread:${own.id}`, args: ['code', '--thread', own.id] } : undefined
  if (!r.current) return undefined
  const topic = r.topics.find(x => x.id === r.current)
  const push = pushOf(topic, pushes)
  const asked = topic?.step === 'fixed'
    ? { base: `fix:${r.current}`, args: ['code', r.current, '--fix'] }
    : sinceOf(r)
      ? { base: `topic:${r.current}:since`, args: ['code', r.current, '--since-comment'] }
      : push < (topic?.pushes ?? 0)
        ? { base: `topic:${r.current}:push=${push}`, args: ['code', r.current, '--push', String(push)] }
        : { base: `topic:${r.current}`, args: ['code', r.current] }
  const pick = picks[asked.base]
  return pick
    ? { key: `${asked.base}:file=${pick}`, base: asked.base, args: [...asked.args, '--file', pick] }
    : { key: asked.base, ...asked }
}

// Which of a topic's pushed fixes the sidebar shows (1-based): the one picked, else its latest.
export const pushOf = (topic: { id: string; pushes?: number } | undefined, pushes: Record<string, number>): number => {
  const n = topic?.pushes ?? 0
  const picked = topic ? pushes[topic.id] : undefined
  return picked && picked >= 1 && picked <= n ? picked : n
}

// The file as the code command answers it.
export type CodeView = ReviewCode

// A row of the file as the MR changes it: a line it kept (" "), removed ("-") or added
// ("+"), its number on each side (null where it has none), its text.
export type CodeRow = CodeView['rows'][number]

// The stretch of the file that fits `rows` rows, starting at row `offset` (1-based), in runs:
// each run either the rows the comment is on (drawn on a background) or the rest, as a
// unified-diff hunk the code element draws with both line numbers; and the rows changed
// since the comment, marked in a gutter.
export type CodeRun = { from: number; to: number; source: string; isDiscussed: boolean }
export type CodeWindow = { start: number; end: number; runs: CodeRun[]; changed: boolean[] }

// Where a file's window starts when it first comes up: the comment's rows a third of the way
// down, so what leads up to them shows and more of what follows.
//
// Where the scripts know where the code the topic names begins (`from`: the function a
// finding sits inside), the window starts there instead, as long as the rows still fit.
export const firstOffset = (v: CodeView, rows: number): number => {
  if (!v.at) return 1
  const [a, b] = v.at
  const top = Math.min(a, v.from ?? b)
  if (top < b && b - top < rows - 2) return Math.max(1, top - Math.max(1, Math.min(5, Math.floor((rows - (b - top + 1)) / 2))))
  // A third of the window above the lines, and never fewer than five where it has the room:
  // the code before a comment is what it is read against.
  const lead = Math.max(Math.floor(rows / 3), Math.min(5, Math.floor((rows - (b - a + 1)) / 2)))
  return Math.max(1, a - lead)
}

export const clampOffset = (v: CodeView, rows: number, offset: number): number =>
  Math.min(Math.max(1, offset), Math.max(1, v.rows.length - rows + 1))

// Where an area that scrolls stands once something in it was opened by a press: moved down
// the least that brings what opened into view to its last row, and never so far that the row
// pressed leaves the top, so what is taller than the window shows from that row down. What
// already shows, and an area that cannot move, stay put; it never moves up.
//
// In the area's own offsets: `from`, where its window starts; `fit`, the least start that has
// the opened part's last row in the window; `toggle`, the start that puts the row pressed at
// the window's top; `max`, the last start there is.
export const revealedAt = (from: number, fit: number, toggle: number, max: number): number =>
  fit <= from ? from : Math.min(max, Math.max(from, Math.min(fit, toggle)))

// The `@@ -a,b +c,d @@` line of rows `lo`..`hi` (1-based): a side with no line in them
// starts at the line before, as unified diff counts. Mirrors lib/code_view.py's hunk_header.
export const hunkHeader = (rows: CodeRow[], lo: number, hi: number): string => {
  const side = (k: 1 | 2): [number, number] => {
    const nums = rows.slice(lo - 1, hi).map(r => r[k]).filter((x): x is number => x !== null)
    if (nums.length) return [nums[0] as number, nums.length]
    const before = rows.slice(0, lo - 1).map(r => r[k]).filter((x): x is number => x !== null)
    return [before[before.length - 1] ?? 0, 0]
  }
  const [a, b] = side(1)
  const [c, d] = side(2)
  return `@@ -${a},${b} +${c},${d} @@`
}

export const codeWindow = (v: CodeView, rows: number, offset: number): CodeWindow => {
  const start = clampOffset(v, rows, offset)
  const end = Math.min(v.rows.length, start + Math.max(1, rows) - 1)
  const [lo, hi] = v.at ?? [0, -1]
  const changedSet = new Set(v.changed)
  const runs: CodeRun[] = []
  for (let i = start; i <= end; i += 1) {
    const isDiscussed = i >= lo && i <= hi
    const last = runs[runs.length - 1]
    if (last && last.isDiscussed === isDiscussed) last.to = i
    else runs.push({ from: i, to: i, source: '', isDiscussed })
  }
  // A file the MR adds or removes whole would be one colour from top to bottom, which marks
  // nothing: it is drawn as plain lines, numbered on the side it has, and its heading says it.
  const rs: CodeView['rows'] = v.whole
    ? v.rows.map(r => [' ', r[1] ?? r[2], r[2] ?? r[1], r[3]])
    : v.rows
  // An empty line holds a space after its marker, so no line of a hunk reads as blank.
  for (const run of runs) {
    const body = rs.slice(run.from - 1, run.to).map(r => `${r[0]}${r[3] || ' '}`)
    run.source = [hunkHeader(rs, run.from, run.to), ...body].join('\n')
  }
  const changed: boolean[] = []
  for (let i = start; i <= end; i += 1) changed.push(changedSet.has(i))
  return { start, end, runs, changed }
}

// The line numbers a window shows of the file as it is now, for its heading: the first and
// last now-numbered rows in it, and how long the file is.
export const shownLines = (v: CodeView, w: CodeWindow): { from: number; to: number; of: number } | undefined => {
  const nums = v.rows.slice(w.start - 1, w.end).map(r => r[2]).filter((x): x is number => x !== null)
  const of = v.rows.filter(r => r[2] !== null).length
  return nums.length ? { from: nums[0] as number, to: nums[nums.length - 1] as number, of } : undefined
}

// A scrollbar: one column beside an area that holds more rows than it shows, its thumb as long
// as the share shown and where the window is, in eighths of a cell. The ends of the thumb are
// block elements filled from the bottom (where it starts inside a cell) and from the top (where
// it ends inside one); the upper ones past ▔ and ▀ are from Symbols for Legacy Computing.
export const BAR_LOWER = [' ', '▁', '▂', '▃', '▄', '▅', '▆', '▇', '█'] as const
export const BAR_UPPER = [' ', '▔', '\u{1FB82}', '\u{1FB83}', '▀', '\u{1FB84}', '\u{1FB85}', '\u{1FB86}', '█'] as const

// The thumb over a track of `rows` cells, for `shown` of `count` rows scrolled `from` down: the
// eighth it starts at and the one it ends before. Never shorter than a cell, the whole track
// when everything shows, and at the track's end exactly when the window is at the last row.
export const thumbOf = (count: number, shown: number, from: number, rows: number): { top: number; end: number } => {
  const total = rows * 8
  if (count <= shown || count <= 0) return { top: 0, end: total }
  const size = Math.min(total, Math.max(8, Math.round(total * shown / count)))
  const max = count - shown
  const top = Math.round((total - size) * Math.min(max, Math.max(0, from)) / max)
  return { top, end: top + size }
}

// What a scrollbar draws, as plain data: the sidebar works it out and hands it to the bar's
// own drawing (scrollbar.tsx), which draws it again as the pointer moves over it, with no
// round trip to the sidebar.
//
// `look`: its colours. `topic`: the cells standing for the lines the area is about, and the
// row a press on them takes the window to. `changes`: the cells standing for changed rows,
// as a diff viewer's overview ruler marks them.
export type BarLook = {
  track: string; thumb: string; lit: string; former: string
  added: string; removed: string; modified: string; topic: string
}
export type ChangeKind = 'added' | 'removed' | 'modified'
export type BarSpec = {
  count: number; shown: number; from: number; rows: number; look: BarLook
  topic?: { cells: number[]; to: number }
  changes?: [number, ChangeKind][]
}
// What the pointer is doing to it: where it rests, in eighths of a cell from the track's top
// (null: off the bar), and the row a drag holds the window at (null: no drag).
export type BarPointer = { at: number | null; drag: number | null }
// One cell as drawn, whether it lies above the thumb, in it or below it, and whether it stands
// for the topic's lines.
export type BarCell = {
  glyph: string; color: string; backgroundColor: string
  at: 'above' | 'thumb' | 'below'; isTopic: boolean
}

// The cell a row of the area stands on.
const cellOf = (count: number, rows: number, m: number): number =>
  Math.min(rows - 1, Math.max(0, Math.floor(m * rows / Math.max(1, count))))

// The cells that stand for rows `lo`..`hi` (row indexes, from 0): every one between, so a
// long stretch is as long on the bar as in the area.
export const cellsOf = (count: number, rows: number, lo: number, hi: number): number[] => {
  const a = cellOf(count, rows, lo)
  const b = cellOf(count, rows, Math.max(lo, hi))
  return Array.from({ length: b - a + 1 }, (_, i) => a + i)
}

// Of kinds that cover a cell equally, the one it shows: a replaced line before a removed one,
// the line no longer in the file and easiest to miss, before an added one.
const KIND_RANK: ChangeKind[] = ['modified', 'removed', 'added']

// The cells of the track that stand for changed rows, each with its one kind, as an IDE's
// gutter and overview ruler mark them: a stretch of changed rows with removed and added rows
// both is a replaced (modified) stretch, every row of it; one with only added rows is added,
// one with only removed rows removed. The kind is the stretch's, not the row's: a replaced
// line is a removed row and an added one, and where the two fall in two cells, each kind by
// its row would mark a removal and an addition that are not there. A cell that stands for
// rows of several stretches shows the kind most of its changed rows have.
//
// `signs` is each row's diff sign at its place on the track; a row that is no line, such as a
// box set in among them, is '', which neither ends a stretch nor counts in it, so a box set
// between the two halves of a replaced line leaves it one. Rows in the window count too: a
// change does not stop being one while it shows.
export const changeKinds = (count: number, rows: number, signs: string[]): [number, ChangeKind][] => {
  const isChange = (s: string | undefined) => s === '-' || s === '+'
  const tally = new Map<number, Record<ChangeKind, number>>()
  for (let lo = 0; lo < signs.length;) {
    if (!isChange(signs[lo])) { lo++; continue }
    let hi = lo
    while (hi + 1 < signs.length && (isChange(signs[hi + 1]) || signs[hi + 1] === '')) hi++
    const stretch = signs.slice(lo, hi + 1)
    const kind: ChangeKind = stretch.includes('-') && stretch.includes('+') ? 'modified' : stretch.includes('+') ? 'added' : 'removed'
    stretch.forEach((s, k) => {
      if (!isChange(s)) return
      const cell = cellOf(count, rows, lo + k)
      const t = tally.get(cell) ?? { modified: 0, removed: 0, added: 0 }
      t[kind] += 1
      tally.set(cell, t)
    })
    lo = hi + 1
  }
  return [...tally].map(([cell, t]) => [cell, KIND_RANK.reduce((best, k) => (t[k] > t[best] ? k : best))])
}

// Where the pointer is on the track, in eighths from its top: the place within the cell where
// the terminal reports one (none does through tmux), else the cell's middle.
export const eighthOf = (y: number, fine?: number): number => Math.floor((fine ?? y + 0.5) * 8)

// The row the window starts at for a thumb whose top is `top` eighths down the track: the
// thumb's share of the way it can go, of the way the window can.
export const fromTop = (count: number, shown: number, rows: number, top: number): number => {
  const { top: t, end } = thumbOf(count, shown, 0, rows)
  const room = rows * 8 - (end - t)
  const max = count - shown
  if (max <= 0 || room <= 0) return 0
  return Math.round(max * Math.min(room, Math.max(0, top)) / room)
}

// Where a press on the track takes the window, `at` eighths down it: the thumb's middle under
// the pointer, as an IDE's scrollbar does, so the top of the track is the top of the area and
// its foot the end. The preview a hover shows is exactly the thumb this puts there.
export const centredOn = (count: number, shown: number, rows: number, at: number): number => {
  const { top, end } = thumbOf(count, shown, 0, rows)
  return fromTop(count, shown, rows, at - Math.floor((end - top) / 2))
}

// Whether `at` is on the thumb, which a press there grabs.
export const isOnThumb = (b: BarSpec, at: number): boolean => {
  const { top, end } = thumbOf(b.count, b.shown, b.from, b.rows)
  return at >= top && at < end
}

// Whether `at` is on the topic's mark off the thumb, where a press takes the window to the
// topic's lines.
export const isOnTopic = (b: BarSpec, at: number): boolean =>
  !isOnThumb(b, at) && !!b.topic?.cells.includes(Math.floor(at / 8))

// What a press at `at` does: on the thumb, nothing (it is grabbed, for a drag); on the
// topic's mark, the window to the topic's lines; elsewhere the window to where the thumb
// would be centred there.
export const pressOf = (b: BarSpec, at: number): number | null => {
  if (isOnThumb(b, at)) return null
  if (b.topic && isOnTopic(b, at)) return Math.min(Math.max(0, b.count - b.shown), Math.max(0, b.topic.to))
  return centredOn(b.count, b.shown, b.rows, at)
}

// Where the thumb would go, while the pointer rests on the bar with no button down: the row a
// press there takes the window to, or null where it would stay put.
export const previewOf = (b: BarSpec, p: BarPointer): number | null => {
  if (p.at === null || p.drag !== null) return null
  const to = pressOf(b, p.at)
  return to === null || to === b.from ? null : to
}

// Each cell of the bar as drawn.
//
// At rest the thumb is the share shown, where the window is, its ends block elements filled
// from the bottom (where it starts inside a cell) or from the top (where it ends inside one).
// The pointer on it, or dragging it, lights it. The pointer elsewhere on the track shows the
// thumb where a press would put it, at its full length and lit, and the thumb where the window
// is now in a faint tone, so the person sees both where they are and where they would go.
//
// A cell holds one glyph in two colours, which is the terminal's limit: where parts of two of
// these meet in one, the later (the preview over the faint thumb, a thumb over the track) is
// the glyph and the other its background. The topic's mark (the cell's left half) and a change
// (its right half) are drawn over whichever covers most of the cell, so they read on the track
// and on the thumb alike; such a cell gives up the eighths of a thumb's end.
export const barPaint = (b: BarSpec, p: BarPointer = { at: null, drag: null }): BarCell[] => {
  const { look } = b
  const real = thumbOf(b.count, b.shown, p.drag ?? b.from, b.rows)
  const preview = previewOf(b, p)
  const isHovered = p.at !== null && p.at >= real.top && p.at < real.end
  const layers = preview !== null
    ? [{ ...real, color: look.former }, { ...thumbOf(b.count, b.shown, preview, b.rows), color: look.lit }]
    : [{ ...real, color: p.drag !== null || isHovered ? look.lit : look.thumb }]
  const topic = new Set(b.topic?.cells ?? [])
  const changes = new Map(b.changes ?? [])
  const colorOf = (k: number) => layers[k]?.color ?? look.track
  // The layer that covers most of `tones`, the later on a tie.
  const most = (tones: number[]) => {
    const n = new Map<number, number>()
    tones.forEach(k => n.set(k, (n.get(k) ?? 0) + 1))
    return [...n].sort((x, y) => y[1] - x[1] || y[0] - x[0])[0]?.[0] ?? -1
  }
  return Array.from({ length: b.rows }, (_, i) => {
    const a = i * 8
    const at = a + 8 <= real.top ? 'above' as const : a >= real.end ? 'below' as const : 'thumb' as const
    const isTopic = topic.has(i)
    // Each eighth's layer: the last one over it, else the track (-1).
    const tones = Array.from({ length: 8 }, (_, e) => layers.reduce((k, l, j) => (a + e >= l.top && a + e < l.end ? j : k), -1))
    const change = changes.get(i)
    if (isTopic) return { glyph: '▌', color: look.topic, backgroundColor: change ? look[change] : colorOf(most(tones)), at, isTopic }
    if (change) return { glyph: '▐', color: look[change], backgroundColor: colorOf(most(tones)), at, isTopic }
    const upper = Math.max(...tones)
    if (upper < 0) return { glyph: ' ', color: look.track, backgroundColor: look.track, at, isTopic }
    const n = tones.filter(k => k === upper).length
    const rest = tones.filter(k => k !== upper)
    // Starting inside the cell, a layer fills its lower part; ending inside, its upper part.
    const glyph = n === 8 ? '█' : tones[7] === upper ? BAR_LOWER[n] as string : BAR_UPPER[n] as string
    return { glyph, color: colorOf(upper), backgroundColor: rest.length ? colorOf(most(rest)) : look.track, at, isTopic }
  })
}
