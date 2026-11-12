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
// about; before it, the one the person picked, else the first.
//
// The person's pick always wins, so every row they click or step to becomes current; with
// none, the first comment whose proposal waits, else the first comment.
export const currentOwn = (r: ReviewState, picked: string | null): ReviewOwnThread | undefined => {
  const own = r.own_threads ?? []
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
