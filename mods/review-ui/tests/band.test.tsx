import type { On } from 'claude-code'
import type { Engine } from 'claude-code/testing'
import { expect, mock, test } from 'claude-code/testing'
import { inboxMark } from '../hooks/panel.ts'
import { boxLookOf, buttonsFor, CI_PROMPT, endOf, iconOf, inboxOf, isMergeWatched, judgingOf, kindCheck, proposePrompt, sentenceOf, untilOf } from '../hooks/review.ts'
import { BAR_LOWER, BAR_UPPER, barPaint, type BarLook, type BarSpec } from '../hooks/sidebar.ts'

// The test runtime's timer, which the engine's types leave out (a mod has no timers of its own).
declare const setTimeout: (run: () => void, ms: number) => unknown

// review-mr's buttons, against stand-ins for the script: every `$.process.run` the mod makes
// is answered here, and recorded, so a test reads what a press would have run.

const REVIEW = { skill: 'review-mr', script: '/skills/review-mr/scripts/findings.py', cwd: '/wt', iid: 42 }

const WORD: Record<string, string> = { open: 'waiting', needs_ack: 'answered', acked: 'done' }
const topic = (id: string, status: string, draft: string | null = null) => ({
  id, status, state: status === 'draft' ? (draft ? 'draft' : 'new') : (WORD[status] ?? status), kind: 'issue', severity: 'high', source: 'llm', summary: `summary of ${id}`,
  file: 'src/cache.ts', line: 17, posted: status !== 'draft', draft,
})

type Topic = Omit<ReturnType<typeof topic>, 'severity' | 'source'> & { severity: string | null; source: string | null; fix_kept?: boolean; follow_up?: string | null; verdict?: string | null; verdict_text?: string | null; resolved?: boolean; peer?: boolean; peer_view?: { says: string; advice: string; into?: string | null } | null; watching?: boolean; inbox?: string | null; news?: string[]; later?: number }

const stateOf = (current: string | null, topics: Topic[], extra: object = {}) =>
  JSON.stringify({ skill: 'review-mr', iid: 42, title: 'Add a widget cache', current, topics, ...extra })

const view = (tid: string, draft?: string) =>
  `◈ **${tid}** \`src/cache.ts:17\`\n\nsome view\n\n<!-- paste-gate:critical\n` +
  JSON.stringify({ first: `◈ **${tid}** \`src/cache.ts:17\``, critical: [], topic: tid, focus: tid, review: REVIEW, ...(draft ? { draft } : {}) }) +
  '\n-->'

const BAND = {
  plugin: 'review-ui', component: 'AbovePrompt',
  props: { hasSurvey: false, isCollapsed: false, isWorking: false, maxRows: 6, bodyColumns: 120, scroll: { offset: 0, bodyRows: 6 }, view: {} },
} as const

// `hang`: the script never answers, as one cut off by a quit in the middle of it.
type Out = { stdout: string; exitCode?: number; stderr?: string; hang?: boolean }

// Everything beneath the plugin, registered before the test's first call on `$` as the kit
// requires: the script, as a list of states it moves through (each command that changes
// something moves to the next one), the engine's own drawing, and the model's tool calls.
// `ran` is every argument vector the mod ran, in order.
const world = (on: On, states: string[], extra: Record<string, Out> = {}, store: Record<string, unknown> = {}) => {
  const ran: { argv: readonly string[]; env?: Record<string, string>; cwd?: string; stdin?: string }[] = []
  const placed: string[] = []
  const prompts: string[] = []
  const tool = { stdout: '' }
  const held: Promise<void>[] = []
  let at = 0
  const answer = (o: Out) => ({
    value: { exitCode: o.exitCode ?? 0, stdout: o.stdout, stderr: o.stderr ?? '', isStdoutTruncated: false, isStderrTruncated: false },
  })
  on('process.run', ($, e) => {
    ran.push({ argv: e.argv, env: e.init?.env, cwd: e.init?.cwd, stdin: e.init?.stdin })
    const cmd = e.argv[2] ?? ''
    if (cmd === 'state') {
      // Read as the script starts, answered once the test lets it (`w.held`): a read that
      // is slow to come back, with whatever happened meanwhile not in it.
      const read = answer({ stdout: states[Math.min(at, states.length - 1)] ?? '' })
      const until = held.shift()
      return until ? until.then(() => read) as never : read
    }
    // `quote moves`: a view that moves its topic on as it is shown, to the next state.
    if (cmd === 'quote' && extra['quote moves']) { at += 1; return answer(extra['quote moves']) }
    if (cmd === 'quote') return answer(extra.quote ?? { stdout: view(e.argv[3] ?? '') })
    // A file or a push picked in the sidebar is answered apart where the test gives it
    // (`code --file <f>`, `code --push <n>`).
    const picked = (flag: string) => {
      const i = e.argv.indexOf(flag)
      return i > 0 ? extra[`${cmd} ${flag} ${e.argv[i + 1]}`] : undefined
    }
    const hit = picked('--file') ?? picked('--push') ?? extra[cmd]
    if (hit?.hang) return new Promise(() => {}) as never
    if (hit) return answer(hit)
    at += 1
    return answer({ stdout: '' })
  })
  on('ui.place', ($, e) => { placed.push(e.id); return { value: { isPlaced: true as const, requestId: e.id } } })
  on('prompt.submit', ($, e) => { prompts.push(e.text); return { text: e.text } })
  on('turn.complete', ($, e) => ({ text: (e as { answer?: string }).answer ?? '' }))
  // How many prompts the transcript holds: 0 for a fresh session, more for one carried on.
  const turns = { n: 0 }
  on('session.turns', () => ({ value: turns.n }))
  const filled: string[] = []
  on('prompt.fill', ($, e) => { filled.push(e.text); return { isFilled: true as const } })
  on('tool.call', () => ({ result: { stdout: tool.stdout, stderr: '', interrupted: false } }))
  on('ui.render', ($, e) => {
    const { Text } = $.ui.resolve(e)
    return <Text>engine</Text>
  })
  const rows = mock.session(on)
  const written: string[] = []
  const files: Record<string, string> = {}
  mock.env(on, { HOME: '/home/me' })
  on('session.id', () => ({ value: 's-1' }))
  on('fs.exists', ($, e) => ({ value: written.includes(e.path) || e.path in files }))
  on('fs.write', ($, e) => { written.push(e.path); files[e.path] = e.text; return { value: undefined } })
  on('fs.read', ($, e) => ({ value: files[e.path] ?? '' }))
  // The mod's store, kept in the object the test passed, so a test reads what the mod wrote.
  on('store.get', ($, e) => ({ value: store[e.key] }))
  on('store.set', ($, e) => { store[e.key] = e.value; return { value: undefined } })
  on('store.delete', ($, e) => { delete store[e.key]; return { value: undefined } })
  on('store.keys', () => ({ value: Object.keys(store) }))
  on('session.cwd', () => ({ value: '/wt' }))
  // The engine's clock, which a test moves by setting `w.clock.now`.
  const clock = { now: Date.parse('2026-11-05T12:00:00Z') }
  on('clock.now', () => ({ value: clock.now }))
  const panes: string[] = []
  const opened: string[] = []
  on('ui.open', ($, e) => { opened.push(e.id); if (!panes.includes(e.id)) panes.push(e.id); return { value: { isPlaced: true as const } } })
  on('ui.close', ($, e) => { panes.splice(panes.indexOf(e.id), 1); return undefined as never })
  on('ui.panes', () => ({ value: panes.map(id => ({ id, title: 'x', isShown: true, isFocused: false, isPlaced: true })) }))
  // `move`: the script's state moves on by something the mod did not run (the model's call).
  const w = { ran, placed, prompts, filled, turns, tool, rows, written, files, panes, opened, clock, held, move: () => { at += 1 } }
  lastWorld = w
  return w
}

type World = { move: () => void; held: Promise<void>[]; clock: { now: number }; ran: { argv: readonly string[]; env?: Record<string, string>; cwd?: string; stdin?: string }[]; placed: string[]; prompts: string[]; filled: string[]; turns: { n: number }; tool: { stdout: string }; rows: ReturnType<typeof mock.session>; written: string[]; files: Record<string, string>; panes: string[]; opened: string[] }
let lastWorld: World | undefined

// Whether a run went with a press's proof (lib/press.py): a secret drawn for it, and a file
// named after the secret's hash, written for the script to find. A constant would be one the
// model could set itself.
const isPress = async (w: World, env?: Record<string, string>): Promise<boolean> => {
  const secret = env?.REVIEW_UI_PRESS
  if (!secret || !/^[0-9a-f]{64}$/.test(secret)) return false
  const digest = [...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(secret)))]
    .map(b => b.toString(16).padStart(2, '0')).join('')
  return w.written.includes(`/home/me/.claude/review-ui/presses/${digest}`)
}

// The step's buttons as the person meets them: on the newest card in the chat (the view the
// model opened with, or a card a press placed), and the band under it. One mount of each,
// searched and pressed as one.
type Found = { props: Record<string, unknown>; text?: string }
type Q = { type: string; text?: string | RegExp; key?: string }
type Mounted = { findAll: (q: Q) => Promise<Found[]>; find: (q: Q) => Promise<Found | undefined>; press: (q: { key: string }) => Promise<unknown>; unmount: () => Promise<void> }
const mountStep = async ($: Engine, surface: 'terminal' | 'desktop' | 'vscode' | 'mobile', band: Partial<typeof BAND.props> = {}) => {
  const w = lastWorld as World
  const parts: Mounted[] = [
    await $.ui.mount({
      plugin: 'review-ui', component: 'ToolUse', surface,
      props: { tool_use_id: 'tu-open', tool: 'Bash', input: { command: 'python3 $SD/findings.py present --iid 42' }, output: { stdout: w.tool.stdout, stderr: '', interrupted: false }, isRunning: false, isErrored: false },
    } as never) as unknown as Mounted,
    ...await Promise.all(w.placed.map(async id => (await $.ui.mount({ plugin: 'review-ui', component: 'Placed', surface, props: { plugin: 'review-ui', id } } as never)) as unknown as Mounted)),
    (await $.ui.mount({ ...BAND, surface, props: { ...BAND.props, ...band } })) as unknown as Mounted,
  ]
  return {
    findAll: async (q: Q) => (await Promise.all(parts.map(x => x.findAll(q)))).flat(),
    find: async (q: Q) => {
      for (const x of parts) {
        const hit = await x.find(q)
        if (hit) return hit
      }
      return undefined
    },
    press: async (q: { key: string }) => {
      for (const x of parts) {
        if ((await x.findAll({ type: 'Button' })).some(b => b.props.key === q.key || (b as { key?: string }).key === q.key)) return x.press(q)
      }
      return parts[parts.length - 1]?.press(q)
    },
    unmount: async () => { for (const x of parts) await x.unmount() },
  }
}

// The first view of the session: the model ran a view command and the review is known.
const openReview = async ($: Engine, w: World, stdout: string) => {
  w.tool.stdout = stdout
  await $.tool.call({ tool: 'Skill', skill: 'review-mr' } as never)
  await $.tool.call({ tool: 'Bash', command: 'python3 $SD/findings.py present --iid 42', tool_use_id: 'tu-open' } as never)
}

const labels = async (ui: { findAll: (q: { type: string }) => Promise<{ props: Record<string, unknown> }[]> }) =>
  (await ui.findAll({ type: 'Button' })).map(b => b.props.label)

// An area's scrollbar, by its name (`list`, `files`, `code`, `box`, `mr`, `thread`), drawn by
// its own module under the key `bar-<name>`: a press on its track jumps there, `page` presses
// its top cell (`up`) or its bottom one (`down`). `bar` is the thumb's cells and the track's,
// in order, as the sidebar handed them to the bar (null where the area has no bar).
type Scrolls = {
  find: (q: { type: string; key?: string }) => Promise<{ props: Record<string, unknown> } | undefined>
  findAll: (q: { type: string; in?: string }) => Promise<{ text?: unknown; props: Record<string, unknown> }[]>
  pointer: (e: { type: 'down' | 'up' | 'move' | 'leave'; x: number; y: number; button?: 'left'; in: string }) => Promise<void>
}
// The bar as its module drew it: each cell's glyph and colours.
const drawnBar = async (ui: Scrolls, tag: string) => (await ui.findAll({ type: 'Text', in: `bar-${tag}` }))
  .map(t => ({ glyph: String(t.text), color: t.props.color, bg: t.props.backgroundColor }))
// A press on cell `y` of an area's bar, as a click there: the button down, then up.
const click = async (ui: Scrolls, tag: string, y: number) => {
  await ui.pointer({ type: 'down', x: 0, y, button: 'left', in: `bar-${tag}` })
  await ui.pointer({ type: 'up', x: 0, y, button: 'left', in: `bar-${tag}` })
}
const specOf = async (ui: Scrolls, tag: string) =>
  (await ui.find({ type: 'Client', key: `bar-${tag}` }))?.props.props as BarSpec | undefined
const bar = async (ui: Scrolls, tag: string) => {
  const spec = await specOf(ui, tag)
  return spec ? barPaint(spec).map(c => c.at) : null
}
const page = async (ui: Scrolls, tag: string, dir: 'up' | 'down') => {
  const cells = await bar(ui, tag)
  const y = dir === 'up' ? 0 : (cells?.length ?? 0) - 1
  if (!cells || cells[y] === 'thumb') throw new Error(`${tag}'s scrollbar has no track ${dir === 'up' ? 'above' : 'below'} its thumb`)
  await click(ui, tag, y)
}

test("the band's buttons are chips with no hotkey, Show sidebar too, with its icon", async ($, on) => {
  const w = world(on, [stateOf(null, [], { explainer: { recommend: 'skip', why: 'A refactor.' } })])
  await openReview($, w, view('t1'))
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  const all = await band.findAll({ type: 'Button' })
  expect(all.map(b => b.props.label)).toEqual(['Skip explainer', 'Generate explainer', 'Show sidebar'])
  // A hotkey is drawn inside the chip, between its cap and its fill.
  expect(all.every(b => b.props.hotkey === undefined && b.props.action === undefined)).toBe(true)
  expect(JSON.stringify(await band.findAll({ type: 'Text' }))).toContain('\u{F06FE} Show sidebar')
  // One column off each edge, as the sidebar's foot and a card keep theirs: the first box
  // that pads, as the engine may wrap the band in one of its own (2.1.325 does).
  expect((await band.findAll({ type: 'Box' })).find(x => x.props.paddingX !== undefined)?.props.paddingX).toBe(1)
  await band.unmount()
})

test('no band before a review is open', async ($, on) => {
  world(on, [stateOf('t1', [topic('t1', 'draft')])])
  const ui = await mountStep($, 'terminal')
  expect(await ui.find({ type: 'Button' })).toBeUndefined()
  await ui.unmount()
})

test('each step has its own buttons, on every surface that takes input', async ($, on) => {
  const w = world(on, [stateOf('t1', [topic('t1', 'draft')])])
  await openReview($, w, view('t1'))
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await mountStep($, surface)
    expect(await labels(ui)).toEqual(['Comment', 'Drop', 'Explain', 'Next', 'Show sidebar'])
    expect(await ui.find({ type: 'Text', text: /^t1$/ })).toBeUndefined()   // the card and the sidebar name it
    await ui.unmount()
  }
})

test('a drafted topic offers Post', async ($, on) => {
  const w = world(on, [stateOf('t1', [topic('t1', 'draft', 'v1')])])
  await openReview($, w, view('t1', 'v1'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Post', 'Check my post', 'Next', 'Show sidebar'])
  await ui.unmount()
})

test('a topic the author answered offers Ack', async ($, on) => {
  const w = world(on, [stateOf('t2', [topic('t1', 'open'), topic('t2', 'needs_ack')])])
  await openReview($, w, view('t2'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Ack', 'Follow up', 'Explain', 'Next', 'Show sidebar'])
  expect(await ui.find({ type: 'Text', text: /^t2$/ })).toBeUndefined()   // the card and the sidebar name it
  expect(await ui.find({ type: 'Text', text: / of / })).toBeUndefined()
  await ui.unmount()
})

test('an answer the agent judged settled is accepted first: the thread resolved and the topic closed in one press', async ($, on) => {
  const answered = { ...topic('t2', 'needs_ack'), verdict: 'settled' }
  const w = world(on, [stateOf('t2', [answered]), stateOf(null, [{ ...answered, status: 'acked', state: 'done' }])])
  await openReview($, w, view('t2'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Accept + resolve', 'Not enough…', 'Analyse more', 'Next', 'Show sidebar'])
  await ui.press({ key: 'resolve' })
  await ui.unmount()
  const ran = w.ran.find(r => r.argv.includes('resolve'))
  expect(ran?.argv.slice(2).join(' ')).toBe('resolve t2 --iid 42')
  expect(await isPress(w, ran?.env)).toBe(true)
})

test("review-mr: the author's push that changed the topic's lines is shown in the diff viewer on a press", async ($, on) => {
  const w = world(on, [stateOf('t2', [{ ...topic('t2', 'needs_ack'), verdict: 'settled', fix_kept: true } as Topic])])
  await openReview($, w, view('t2'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Accept + resolve', 'Not enough…', 'Show fix in hunk', 'Analyse more', 'Next', 'Show sidebar'])
  await ui.press({ key: 'hunk-pushed' })
  await ui.unmount()
  expect(w.ran.map(r => r.argv.slice(2).join(' '))).toContain('hunk-show t2 --pushed --iid 42')
})

test('review-mr: no way on to a next topic while no round runs', async ($, on) => {
  const w = world(on, [stateOf('t2', [topic('t1', 'open'), topic('t2', 'needs_ack')], { in_round: false })])
  await openReview($, w, view('t2'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Ack', 'Follow up', 'Explain', 'Show sidebar'])
  await ui.unmount()
})

test('a settled answer on a thread resolved already is accepted alone', async ($, on) => {
  const w = world(on, [stateOf('t2', [{ ...topic('t2', 'needs_ack'), verdict: 'settled', resolved: true }])])
  await openReview($, w, view('t2'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Accept', 'Not enough…', 'Analyse more', 'Next', 'Show sidebar'])
  await ui.unmount()
})

test("review-mr: the author resolving with nothing pushed recommends waiting, the ack a press away", async ($, on) => {
  const w = world(on, [stateOf('t2', [{ ...topic('t2', 'needs_ack'), verdict: 'settled', resolved: true, author_resolved: 'unpushed' } as Topic])])
  await openReview($, w, view('t2'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Wait for author', 'Ack', 'Follow up', 'Next', 'Show sidebar'])
  await ui.unmount()
})

test("review-mr: the author resolving with a fix pushed recommends the ack, the fix a press away", async ($, on) => {
  const w = world(on, [stateOf('t2', [{ ...topic('t2', 'needs_ack'), resolved: true, author_resolved: 'pushed', fix_kept: true } as Topic])])
  await openReview($, w, view('t2'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Ack', 'Follow up', 'Show fix in hunk', 'Explain', 'Next', 'Show sidebar'])
  await ui.unmount()
})

test('an answer that needs more offers the follow-up first, or resolving it all the same', async ($, on) => {
  const w = world(on, [stateOf('t2', [{ ...topic('t2', 'needs_ack'), verdict: 'more', verdict_text: 'Writes still skip the locale. Ask for writes.' }], { lang: 'de' })])
  await openReview($, w, view('t2'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Send follow-up', 'Wait for author', 'Resolve', 'Analyse more', 'Next', 'Show sidebar'])
  await ui.press({ key: 'follow' })
  await ui.unmount()
  // The chat did not judge it: the prompt hands it the stored verdict, and the language.
  expect(w.prompts).toEqual(['Draft the follow-up reply for t2, written in de. The verdict on its card, which the background check stored: Writes still skip the locale. Ask for writes.'])
})

test('while the model works the band holds no decisions', async ($, on) => {
  const w = world(on, [stateOf('t1', [topic('t1', 'draft')])])
  await openReview($, w, view('t1'))
  const ui = await $.ui.mount({ ...BAND, props: { ...BAND.props, isWorking: true }, surface: 'terminal' })
  expect(await labels(ui)).toEqual(['Show sidebar'])
  expect(await ui.find({ type: 'Text', text: /^t1$/ })).toBeUndefined()   // the card and the sidebar name it
  await ui.unmount()
})

test('Drop runs the script, shows the next topic as a card and tells the model', async ($, on) => {
  const w = world(on, [
    stateOf('t1', [topic('t1', 'draft'), topic('t2', 'draft')]),
    stateOf('t2', [topic('t2', 'draft')]),
  ])
  await openReview($, w, view('t1'))

  const ui = await mountStep($, 'terminal')
  await ui.press({ key: 'drop' })
  await ui.unmount()

  expect(w.ran.map(r => r.argv.slice(2).join(' '))).toContain('drop t1 --iid 42')
  expect(w.placed.length).toBe(1)
  const told = JSON.stringify(w.rows.appended())
  expect(told).toContain('t1: dropped it')
  expect(told).toContain('t2 is the current topic now')

  const card = await $.ui.mount({
    plugin: 'review-ui', component: 'Placed', surface: 'terminal',
    props: { id: w.placed[0] as string, plugin: 'review-ui' },
  })
  expect((await card.find({ type: 'Markdown' }))?.props.text).toContain('◈ **t2**')
  await card.unmount()
})

test('a card a press finished ends with what happened, and a link to it', async ($, on) => {
  const w = world(on, [stateOf('t1', [topic('t1', 'draft', 'v7')]), stateOf('t1', [topic('t1', 'open')])],
    { post: { stdout: 'posted t1: https://gl/g/p/-/merge_requests/42#note_9\n' } })
  await openReview($, w, view('t1', 'v7'))
  const card = await $.ui.mount(cardRow('tu-open', view('t1', 'v7')))
  await card.press({ key: 'post' })
  await card.unmount()
  const again = await $.ui.mount(cardRow('tu-open', view('t1', 'v7')))
  expect(JSON.stringify(await again.findAll({ type: 'Text' }))).toContain('"text":"\u{F048A}"')   // the button's icon, one sentence
  // The words themselves are the link, to the very comment: no "· thread" after them.
  expect((await again.find({ type: 'Link' }))?.props).toMatchObject({ href: 'https://gl/g/p/-/merge_requests/42#note_9', label: 'posted the initial comment \u{f03cc}' })
  expect(JSON.stringify(await again.findAll({ type: 'Text' }))).not.toContain(' · ')
  await again.unmount()
})

test("a press that closed its topic ends its card on the script's closing line, the one the card says when drawn again", async ($, on) => {
  const answered = { ...topic('t1', 'needs_ack'), verdict: 'settled' as const }
  const line = 'You accepted Robin\'s answer and resolved it'
  const w = world(on, [stateOf('t1', [answered]), stateOf(null, [{ ...answered, status: 'acked', state: 'done' }])],
    { resolve: { stdout: `resolved and acked t1\nclosed: 14:02 \u{F012C} ${line} · [thread \u{F03CC}](https://gl/g/p/-/merge_requests/42#note_3)\n` } })
  await openReview($, w, view('t1'))
  const card = await $.ui.mount(cardRow('tu-open', view('t1')))
  await card.press({ key: 'resolve' })
  await card.unmount()
  const again = await $.ui.mount(cardRow('tu-open', view('t1')))
  const texts = (await again.findAll({ type: 'Text' })).map(t => JSON.stringify(t))
  expect(texts.some(t => t.includes(line.slice(4)))).toBe(true)
  // Its time first and dim, as the card's steps say theirs: the theme's grey as a colour,
  // which a repaint under the pointer keeps.
  expect((await again.findAll({ type: 'Text' })).some(t => t.props.color === 'inactive' && String(t.text).trim() === '14:02')).toBe(true)
  expect(texts.some(t => t.includes('Accepted the answer'))).toBe(false)
  // Its thread's link, clickable once the sidebar has moved on to another topic.
  expect((await again.find({ type: 'Link' }))?.props).toMatchObject({ href: 'https://gl/g/p/-/merge_requests/42#note_3', label: 'thread \u{f03cc}' })
  await again.unmount()
})

test("Drop ends its card on the script's closing line, once", async ($, on) => {
  const w = world(on, [stateOf('t1', [topic('t1', 'draft')]), stateOf(null, [{ ...topic('t1', 'dropped'), state: 'dropped' }])],
    { drop: { stdout: 'closed: 14:02 \u{F0A7A} You dropped it\n' } })
  await openReview($, w, view('t1'))
  const card = await $.ui.mount(cardRow('tu-open', view('t1')))
  await card.press({ key: 'drop' })
  await card.unmount()
  const again = await $.ui.mount(cardRow('tu-open', view('t1')))
  const texts = (await again.findAll({ type: 'Text' })).map(t => JSON.stringify(t))
  expect(texts.filter(t => t.includes('You dropped it')).length).toBe(1)
  // The script's time, not the press's.
  expect((await again.findAll({ type: 'Text' })).some(t => String(t.text).trim() === '14:02')).toBe(true)
  await again.unmount()
})

test('Post posts the draft its card showed, as a press', async ($, on) => {
  const w = world(on, [
    stateOf('t1', [topic('t1', 'draft', 'v7')]),
    stateOf(null, [topic('t1', 'open')]),
  ])
  await openReview($, w, view('t1', 'v7'))

  const ui = await mountStep($, 'terminal')
  await ui.press({ key: 'post' })
  await ui.unmount()

  const post = w.ran.find(r => r.argv[2] === 'post')
  expect(post?.argv.slice(2)).toEqual(['post', 't1', '--expect', 'v7', '--iid', '42'])
  expect(Object.keys(post?.env ?? {}).sort()).toEqual(['CLAUDE_CODE_SESSION_ID', 'REVIEW_UI_MOD', 'REVIEW_UI_PRESS'])
  expect(await isPress(w, post?.env)).toBe(true)
  expect(JSON.stringify(w.rows.appended())).toContain('No topic needs the user now')
})

test("Post sends the draft version of the card it is on, though another topic's view came last", async ($, on) => {
  const both = [topic('t1', 'draft', 'v9'), topic('t2', 'draft', 'v2')]
  const w = world(on, [
    stateOf(null, both, { own_review: 'pending', own_threads: [] }),
    stateOf('t1', both, { own_review: 'done' }),
  ], { 'own-review': { stdout: view('t1', 'v9') } })
  await openReview($, w, view('t2', 'v2'))
  let ui = await mountStep($, 'terminal')
  w.move()                                 // what the press's sync reads: the review done
  await ui.press({ key: 'own-done' })      // its next card placed: t1's
  await ui.unmount()
  ui = await mountStep($, 'terminal')
  await ui.press({ key: 'post' })
  await ui.unmount()
  const post = w.ran.find(r => r.argv[2] === 'post')
  expect(post?.argv.slice(2)).toEqual(['post', 't1', '--expect', 'v9', '--iid', '42'])
})

test('a refused press says why in the band', async ($, on) => {
  const w = world(on, [stateOf('t1', [topic('t1', 'draft', 'v7')])], {
    post: { exitCode: 1, stdout: '', stderr: 'error: the draft of ◈ t1 changed after the card you pressed Post under was drawn' },
  })
  await openReview($, w, view('t1', 'v7'))
  const ui = await mountStep($, 'terminal')
  await ui.press({ key: 'post' })
  expect(await ui.find({ type: 'Text', text: /changed after the card/ })).toBeDefined()
  await ui.unmount()
  expect(w.placed.length).toBe(0)
})

test('a Post whose comment went up while resolving its thread failed is posted, with a warning', async ($, on) => {
  const but = 'posted ◈ t1: https://gl/g/p/-/merge_requests/42#note_9; but resolving its thread failed: 403 Forbidden'
  const w = world(on, [stateOf('t1', [topic('t1', 'draft', 'v7')])], {
    post: { exitCode: 4, stdout: 'posted ◈ t1: https://gl/g/p/-/merge_requests/42#note_9\n', stderr: `${but}\n` },
  })
  await openReview($, w, view('t1', 'v7'))
  const ui = await mountStep($, 'terminal')
  await ui.press({ key: 'post' })
  const said = await ui.find({ type: 'Text', text: /resolving its thread failed/ })
  await ui.unmount()
  expect(said?.props.color).toBe('yellow')
  // Done all the same: the state is read again after it, and the model hears what failed.
  const at = w.ran.findIndex(r => r.argv[2] === 'post')
  expect(w.ran.slice(at + 1).some(r => r.argv[2] === 'state')).toBe(true)
  expect(JSON.stringify(w.rows.appended())).toContain('resolving its thread failed')
})

test('a double click on a button presses it once', async ($, on) => {
  const w = world(on, [stateOf('t1', [topic('t1', 'draft', 'v7')]), stateOf(null, [topic('t1', 'open')])])
  await openReview($, w, view('t1', 'v7'))
  const ui = await mountStep($, 'terminal')
  await Promise.all([ui.press({ key: 'post' }), ui.press({ key: 'post' })])
  await ui.unmount()
  expect(w.ran.filter(r => r.argv[2] === 'post').length).toBe(1)
})

test('a button that needs thought hands the model a turn', async ($, on) => {
  const w = world(on, [stateOf('t1', [topic('t1', 'draft')])])
  await openReview($, w, view('t1'))
  const ui = await mountStep($, 'terminal')
  await ui.press({ key: 'draft' })
  await ui.unmount()
  expect(w.prompts).toEqual(['Draft the comment for t1, written in en.'])
})

test("whether to generate an explainer is the person's press, the agent's recommendation first", async ($, on) => {
  const ask = (recommend: 'skip' | 'generate') => stateOf(null, [], { explainer: { recommend, why: 'A refactor.' } })
  const w = world(on, [ask('skip')])
  await openReview($, w, view('t1'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Skip explainer', 'Generate explainer', 'Show sidebar'])
  await ui.press({ key: 'explainer-skip' })
  await ui.unmount()
  expect(w.ran.map(r => r.argv.slice(2).join(' '))).toContain('explainer --choose skip --iid 42')
  expect(w.prompts).toEqual(['Skip the explainer for this MR and carry on with the review.'])
})

test('a recommended explainer comes first', async ($, on) => {
  const w = world(on, [stateOf(null, [], { explainer: { recommend: 'generate', why: 'A new sync engine.' } })])
  await openReview($, w, view('t1'))
  const ui = await mountStep($, 'terminal')
  expect((await labels(ui)).slice(0, 2)).toEqual(['Generate explainer', 'Skip explainer'])
  await ui.unmount()
})

test('the model cannot post', async ($, on) => {
  const w = world(on, [])
  w.tool.stdout = 'posted'
  const out = await $.tool.call({ tool: 'Bash', command: 'python3 "$SD/findings.py" post t1 --iid 42' })
  expect(JSON.stringify(out)).toContain("user's press")
  expect(JSON.stringify(out)).not.toContain('posted')
  const resolved = await $.tool.call({ tool: 'Bash', command: 'python3 "$SD/findings.py" resolve t1 --iid 42' })
  expect(JSON.stringify(resolved)).toContain("user's press")
})

test('the model cannot write into the press folder, by shell or by a file tool', async ($, on) => {
  const w = world(on, [])
  w.tool.stdout = 'written'
  const ran = await $.tool.call({ tool: 'Bash', command: 'touch ~/.claude/review-ui/presses/0a1b' })
  expect(JSON.stringify(ran)).toContain('only the mod may touch it')
  for (const tool of ['Write', 'Edit', 'NotebookEdit']) {
    const key = tool === 'NotebookEdit' ? 'notebook_path' : 'file_path'
    const out = await $.tool.call({ tool, [key]: '~/.claude/review-ui/presses/0a1b', content: '' } as never)
    expect(JSON.stringify(out)).toContain('only the mod may touch it')
  }
  const elsewhere = await $.tool.call({ tool: 'Write', file_path: '/home/me/notes.md', content: '' } as never)
  expect(JSON.stringify(elsewhere)).toContain('written')
})

const PANEL = {
  plugin: 'review-ui', component: 'Pane', requestId: 'review-ui-topics',
  props: { title: 'Review · MR !42', isFocused: true, bodyColumns: 60, placement: 'dock', scroll: { offset: 0, bodyRows: 30 }, view: {} },
} as const

test('the panel lists open topics, the current one highlighted, closed ones behind one line', async ($, on) => {
  const w = world(on, [stateOf('t2', [
    topic('t1', 'acked'), topic('t2', 'needs_ack'), topic('t3', 'draft'), topic('t4', 'wontfix'), topic('t5', 'acked'),
  ])])
  await openReview($, w, view('t2'))
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...PANEL, surface })
    const texts = (await ui.findAll({ type: 'Text' })).map(t => t.text)
    expect(texts.some(t => /t2/.test(t))).toBe(true)
    expect(texts.some(t => /t1 /.test(t))).toBe(false)
    const current = (await ui.findAll({ type: 'Text', text: /summary of t2/ }))[0]
    expect(current?.props.bold).toBe(true)
    expect(current?.props.inverse).toBeUndefined()
    expect(await ui.find({ type: 'Text', text: /^▌$/ })).toBeDefined()
    const line = await ui.find({ type: 'Button', key: 'group-closed' })
    expect(line?.props.label).toBe('\u{F460} Closed (3)')                // folded, it says how many
    expect(line?.props.hotkey).toBeUndefined()                   // a click folds it, no key
    await ui.press({ key: 'group-closed' })
    const after = (await ui.findAll({ type: 'Text' })).map(t => t.text)
    expect(after.some(t => /summary of t1/.test(t))).toBe(true)
    await ui.press({ key: 'group-closed' })
    await ui.unmount()
  }
})

test('the list is in groups of one look: the round in the topics\' own order, waiting, other reviewers, closed', async ($, on) => {
  const w = world(on, [stateOf('t3', [
    topic('t1', 'needs_ack'), topic('t2', 'open'), topic('t3', 'needs_ack'),
    { ...topic('t5', 'acked'), peer: true, watching: true }, topic('t6', 'acked'),
  ], { in_round: true, round: ['t3', 't1'], author: 'Alex' })])
  await openReview($, w, view('t3'))
  // Tall enough for every row: a list over a quarter of the height scrolls (its own test).
  const ui = await $.ui.mount({ ...PANEL, props: { ...PANEL.props, scroll: { offset: 0, bodyRows: 60 } }, surface: 'terminal' })
  const texts = (await ui.findAll({})).filter(t => t.type === 'Text' || t.type === 'Button').map(t => String(t.type === 'Button' ? t.props.label : t.text))
  const at = (re: RegExp) => texts.findIndex(t => re.test(t))
  // The round visits t3 first; the list keeps t1 above it, as rows move only between groups.
  const order = [/^\u{F47C} To do$/u, /summary of t1/, /summary of t3/,
    /^\u{F47C} Waiting for author$/u, /summary of t2/, /^\u{F47C} From other reviewers$/u, /summary of t5/,
    /^\u{F460} Closed \(1\)$/u].map(at)
  expect(order.every(i => i >= 0)).toBe(true)
  expect(order).toEqual([...order].sort((a, b) => a - b))
  await ui.press({ key: 'group-round' })                                  // folds like any other
  expect((await ui.find({ type: 'Button', key: 'group-round' }))?.props.label).toBe('\u{F460} To do (2)')
  expect((await ui.findAll({ type: 'Text' })).some(t => /summary of t3/.test(String(t.text)))).toBe(false)
  await ui.unmount()
})

test('a list over a quarter of the sidebar scrolls inside its area, the current topic in view', async ($, on) => {
  const ids = Array.from({ length: 12 }, (_, i) => `t${i + 1}`)
  const w = world(on, [stateOf('t10', ids.map(id => topic(id, 'open')))])
  await openReview($, w, view('t10'))
  const ui = await $.ui.mount({ ...PANEL, surface: 'terminal' })        // 30 rows: the list gets 7
  const shown = async () => (await ui.findAll({ type: 'Text' })).map(t => String(t.text)).filter(t => / summary of t\d+$/.test(t))
  expect(await shown()).toHaveLength(7)
  expect((await shown()).some(t => t.endsWith('summary of t10'))).toBe(true)
  expect((await shown()).some(t => t.endsWith('summary of t1'))).toBe(false)
  // Its scrollbar: 7 cells, the thumb 7 of 15 rows long, away from the top.
  const before = await bar(ui, 'list')
  expect(before).toHaveLength(7)
  expect(before?.[0]).toBe('above')
  await page(ui, 'list', 'up')                                            // a window up: the top
  expect((await shown()).some(t => t.endsWith('summary of t1'))).toBe(true)
  expect((await bar(ui, 'list'))?.[0]).toBe('thumb')                       // at the top: nothing above
  await ui.unmount()
})

test('news in a folded group is marked on its header; a scrolled list marks none on its scrollbar', async ($, on) => {
  const ids = Array.from({ length: 12 }, (_, i) => `t${i + 1}`)
  const topics = [...ids.map(id => ({ ...topic(id, 'open'), ...(id === 't1' ? { inbox: 'new' } : {}) })),
    { ...topic('t13', 'acked'), inbox: 'new' }]
  const w = world(on, [stateOf('t10', topics)])
  await openReview($, w, view('t10'))
  const ui = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  const news = '\u{f09de}'
  // Closed is folded: its header carries the mark its row would.
  expect((await ui.findAll({ type: 'Text' })).some(t => t.text === ` ${news}` && t.props.color === 'yellow')).toBe(true)
  // t1 is scrolled out above: its row's own mark says it when it comes into view, the bar
  // carries no mark for it.
  expect((await bar(ui, 'list'))?.[0]).toBe('above')
  expect((await specOf(ui, 'list'))?.topic).toBeUndefined()
  await ui.unmount()
})

test("a row is the topic's handle, severity and title: a pencil marks a draft, a dropped one is struck through", async ($, on) => {
  const w = world(on, [stateOf('t1', [topic('t1', 'draft', 'v1'), topic('t2', 'dropped')])])
  await openReview($, w, view('t1'))
  const ui = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: '\u{f0cb6} ' })).toBeDefined()             // the draft's pencil
  expect(await ui.find({ type: 'Text', text: /^\u{f12c4}/u })).toBeUndefined()             // no state icon
  await ui.press({ key: 'group-closed' })
  expect((await ui.find({ type: 'Text', text: / summary of t2/ }))?.props.strikethrough).toBe(true)
  await ui.unmount()
})

test('every row keeps its columns where it has nothing to show', async ($, on) => {
  const q = { ...topic('t1', 'draft'), kind: 'question', severity: null, source: null }
  const w = world(on, [stateOf('t1', [q, topic('t2', 'draft')])])
  await openReview($, w, view('t1'))
  const ui = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  const titles = (await ui.findAll({ type: 'Text', text: /summary of/ })).map(t => t.text)
  expect(new Set(titles.map(t => t.length)).size).toBe(1)
  await ui.unmount()
})

test('the band offers the sidebar only while it is closed', async ($, on) => {
  const w = world(on, [stateOf('t1', [topic('t1', 'draft')])])
  const { panes, opened } = w
  await openReview($, w, view('t1'))
  const ui = await mountStep($, 'terminal')
  const toggle = await ui.find({ type: 'Button', key: 'topics' })
  expect(toggle?.props.label).toBe('Show sidebar')
  await ui.press({ key: 'topics' })
  expect(opened).toEqual(['review-ui-topics'])
  expect(await ui.find({ type: 'Button', key: 'topics' })).toBeUndefined()

  // Closed behind the mod's back (its ✕): the button is back, and opens it again.
  panes.length = 0
  await ui.press({ key: 'topics' }).catch(() => undefined)
  const again = await mountStep($, 'terminal')
  await again.press({ key: 'topics' })
  expect(opened).toEqual(['review-ui-topics', 'review-ui-topics'])
  await again.unmount()
  await ui.unmount()
})

test('the session is marked for the scripts before a review script runs', async ($, on) => {
  const w = world(on, [stateOf('t1', [topic('t1', 'draft')])])
  await openReview($, w, view('t1'))
  expect(w.written).toEqual(['/home/me/.claude/review-ui/sessions/s-1'])
  await openReview($, w, view('t1'))
  expect(w.written.length).toBe(1)
})

test('a view whose manifest the model filtered away still opens the review, from the mark', async ($, on) => {
  const w = world(on, [stateOf('t1', [topic('t1', 'draft')])])
  w.files['/home/me/.claude/review-ui/sessions/s-1'] = JSON.stringify({ review: REVIEW, topic: 't1', focus: 't1', draft: null })
  await openReview($, w, '◈ **t1** `src/cache.ts:17`\n\nsome view')
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Comment', 'Drop', 'Explain', 'Next', 'Show sidebar'])
  await ui.unmount()
})

test('a conversation carried on under a new session id finds its review again', async ($, on) => {
  const w = world(on, [stateOf('t1', [topic('t1', 'draft')])])
  on('turn.start', ($, e) => ({ turnId: e.turnId }))
  w.files['/home/me/.claude/review-ui/sessions/s-1'] = JSON.stringify({ review: REVIEW, topic: 't1', focus: 't1', draft: null })
  await $.turn.start({ text: 'go on', turnId: 'turn-1' })
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Comment', 'Drop', 'Explain', 'Next', 'Show sidebar'])
  await ui.unmount()
})

const FOLDER = { 'last-review-2:/wt': { session: REVIEW, review: JSON.parse(stateOf('t1', [topic('t1', 'draft')])) } }

test('back under a new session id, the conversation finds its review from the folder', async ($, on) => {
  const w = world(on, [stateOf('t1', [topic('t1', 'draft')])], {}, FOLDER)
  on('turn.start', ($, e) => ({ turnId: e.turnId }))
  w.turns.n = 4                           // a conversation carried on: its turns came with it
  on('session.messages', () => ({ value: [{ role: 'user', text: '<command-name>/review-mr</command-name><command-args>!42</command-args>', toolUses: [] }] }))
  await $.turn.start({ text: 'go on', turnId: 'turn-1' })
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Comment', 'Drop', 'Explain', 'Next', 'Show sidebar'])
  await ui.unmount()
})

test('a review the folder had is drawn from its state as it is now, never from the copy kept of it', async ($, on) => {
  // The copy says two topics; the review was cleared and started again, and waits for the
  // person's own review.
  const old = { 'last-review-2:/wt': { session: REVIEW, review: JSON.parse(stateOf('t1', [topic('t1', 'draft'), topic('t2', 'draft')])) } }
  world(on, [stateOf(null, [], { own_review: 'pending', own_threads: [] })], {}, old)
  on('turn.start', ($, e) => ({ turnId: e.turnId }))
  on('session.messages', () => ({ value: [{ role: 'user', text: '<command-name>/review-mr</command-name><command-args>!42</command-args>', toolUses: [] }] }))
  // Drawn between the person's `/review-mr` and the turn it starts.
  await $.prompt.submit({ text: '/review-mr !42', command: { name: 'review-mr', args: '!42' } } as never)
  const before = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  expect(await before.find({ type: 'Text', text: /summary of t/ })).toBeUndefined()
  await before.unmount()
  await $.turn.start({ text: 'go', turnId: 'turn-1' })
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  expect(await panel.find({ type: 'Text', text: /summary of t/ })).toBeUndefined()
  await panel.unmount()
  const ui = await mountStep($, 'terminal')
  expect((await labels(ui))[0]).toBe('My review is done')
  await ui.unmount()
})

test("back under a new session id, a review run on the folder's own branch is found again, though no MR was named", async ($, on) => {
  const w = world(on, [stateOf('t1', [topic('t1', 'draft')])], {}, FOLDER)
  on('turn.start', ($, e) => ({ turnId: e.turnId }))
  w.turns.n = 4
  // Started as `/rework-mr this MR`, its scripts run on the branch checked out: no `--iid`.
  on('session.messages', () => ({ value: [
    { role: 'user', text: '<command-name>/review-mr</command-name><command-args>this MR, we got comments</command-args>', toolUses: [] },
    { role: 'assistant', text: '', toolUses: [{ tool: 'Bash', input: { command: 'python3 ~/.claude/skills/review-mr/scripts/findings.py present' } }] },
  ] as never }))
  await $.turn.start({ text: 'show the sidebar again', turnId: 'turn-1' })
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Comment', 'Drop', 'Explain', 'Next', 'Show sidebar'])
  await ui.unmount()
})

test("a branch's review is not the folder's last one where another MR was named since", async ($, on) => {
  const w = world(on, [stateOf('t1', [topic('t1', 'draft')])], {}, FOLDER)
  on('turn.start', ($, e) => ({ turnId: e.turnId }))
  w.turns.n = 4
  on('session.messages', () => ({ value: [
    { role: 'user', text: '<command-name>/review-mr</command-name><command-args>!77</command-args>', toolUses: [] },
    { role: 'assistant', text: '', toolUses: [{ tool: 'Bash', input: { command: 'python3 ~/.claude/skills/review-mr/scripts/findings.py present' } }] },
  ] as never }))
  await $.turn.start({ text: 'go', turnId: 'turn-1' })
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual([])
  await ui.unmount()
})

test('a review of another MR started in the same folder never shows the last one there', async ($, on) => {
  const w = world(on, [stateOf('t1', [topic('t1', 'draft')])], {}, FOLDER)
  on('turn.start', ($, e) => ({ turnId: e.turnId }))
  w.turns.n = 4
  // The person starts a review of !77 where the folder last had !42: !42 is not this one.
  on('session.messages', () => ({ value: [{ role: 'user', text: '<command-name>/review-mr</command-name><command-args>!77</command-args>', toolUses: [] }] }))
  await $.turn.start({ text: 'go', turnId: 'turn-1' })
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual([])
  await ui.unmount()
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  expect(await panel.find({ type: 'Text', text: /summary of t1/ })).toBeUndefined()
  await panel.unmount()
})

test('a review the mod took up by itself is let go at the next turn once nothing bears it out', async ($, on) => {
  const w = world(on, [stateOf('t1', [topic('t1', 'draft')])], {}, FOLDER)
  on('turn.start', ($, e) => ({ turnId: e.turnId }))
  w.turns.n = 4
  const said = { messages: [{ role: 'user', text: '<command-name>/review-mr</command-name><command-args>!42</command-args>', toolUses: [] }] as unknown[] }
  on('session.messages', () => ({ value: said.messages as never }))
  await $.turn.start({ text: 'go on', turnId: 'turn-1' })
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toContain('Next')
  // The same words, but printed by a tool the session ran: no review skill of its own.
  said.messages = [{ role: 'user', text: '<command-name>/review-mr</command-name><command-args>!42</command-args>', toolUses: [], toolResults: [{ tool_use_id: 'x', text: '' }] }]
  await $.turn.start({ text: 'and now', turnId: 'turn-2' })
  expect(await labels(ui)).toEqual([])
  await ui.unmount()
})

test('a session with turns of its own but no review skill in them takes up no review of its folder', async ($, on) => {
  const w = world(on, [stateOf('t1', [topic('t1', 'draft')])], {}, FOLDER)
  on('turn.start', ($, e) => ({ turnId: e.turnId }))
  w.turns.n = 40                          // a session working on the skills, in their folder
  on('session.messages', () => ({ value: [{ role: 'user', text: 'I ran /review-mr in another window', toolUses: [{ tool_use_id: 'x', tool: 'Skill', input: { skill: 'grilling' } }] }] }))
  await $.turn.start({ text: 'go on', turnId: 'turn-1' })
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual([])
  await ui.unmount()
})

test('a command that only names a review script takes up no review of its folder', async ($, on) => {
  const w = world(on, [stateOf('t1', [topic('t1', 'draft')])], {}, FOLDER)
  on('turn.start', ($, e) => ({ turnId: e.turnId }))
  w.turns.n = 40                          // a session working on the skills drives a test session
  on('session.messages', () => ({ value: [{ role: 'assistant', text: '', toolUses: [{ tool_use_id: 'x', tool: 'Bash', input: { command: 'tmux send-keys -t test -l "/review-mr run python3 x/findings.py quote t1 --iid 42"' } }] }] }))
  await $.turn.start({ text: 'go on', turnId: 'turn-1' })
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual([])
  await ui.unmount()
})

test("a fresh session in the folder of an earlier review shows nothing of it", async ($, on) => {
  const w = world(on, [stateOf('t1', [topic('t1', 'draft')])], {}, FOLDER)
  on('turn.start', ($, e) => ({ turnId: e.turnId }))
  await $.turn.start({ text: 'hello', turnId: 'turn-1' })
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual([])
  await ui.unmount()
  expect(w.ran.length).toBe(0)
})

const OWN = [
  { id: 'd1', file: 'db.ts', line: 59, where: 'db.ts:59', text: 'null here?', topic: null, proposal: null },
  { id: 'd2', file: 'api.ts', line: 3, where: 'api.ts:3', text: 'naming', topic: null, proposal: null },
]

test('before the topics: your own review first, your own comments in the sidebar, no topic', async ($, on) => {
  const w = world(on, [
    stateOf(null, [topic('t1', 'draft')], { own_review: 'pending', own_threads: OWN }),
    stateOf('t1', [topic('t1', 'draft')], { own_review: 'done' }),
  ], { 'own-review': { stdout: view('t1') } })
  await openReview($, w, view('t1'))
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  expect(await panel.find({ type: 'Text', text: /summary of t1/ })).toBeUndefined()
  expect(await panel.find({ type: 'Text', text: /Your comments on GitLab/ })).toBeUndefined()   // the rows say it
  expect(await panel.find({ type: 'Text', text: /Add a widget cache/ })).toBeDefined()           // the MR's heading
  expect(await panel.find({ type: 'Text', text: /null here\?/ })).toBeDefined()
  await panel.unmount()

  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['My review is done', 'Show sidebar'])
  // No comment of the person's own to match: the script opens the first topic, its card
  // placed, with no turn of the model's.
  await ui.press({ key: 'own-done' })
  await ui.unmount()
  expect(w.ran.map(r => r.argv.slice(2).join(' '))).toContain('own-review done --iid 42')
  expect(w.placed.length).toBe(1)
  expect(w.prompts).toEqual([])
})

test('before the topics, with no comments of your own: the sidebar shows no topic and no code', async ($, on) => {
  const w = world(on, [stateOf(null, [topic('t1', 'draft'), topic('t2', 'draft')], { own_review: 'pending', own_threads: [] })])
  await openReview($, w, view('t1'))
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  expect(await panel.find({ type: 'Text', text: /summary of t/ })).toBeUndefined()
  expect(await panel.find({ type: 'Text', text: /cache\.ts/ })).toBeUndefined()
  expect(await panel.find({ type: 'Text', text: /Your comments on GitLab show here/ })).toBeDefined()
  await panel.unmount()
  expect(w.ran.some(r => r.argv.includes('code'))).toBe(false)
})

test('"My review is done" hands the model the matching where comments of yours wait', async ($, on) => {
  const w = world(on, [stateOf(null, [topic('t1', 'draft')], { own_review: 'pending' })],
    { 'own-review': { stdout: 'Your comments not yet on a topic:\n- d1 db.ts:59 null here?\n' } })
  await openReview($, w, view('t1'))
  const ui = await mountStep($, 'terminal')
  await ui.press({ key: 'own-done' })
  await ui.unmount()
  expect(w.prompts).toEqual(['My own review in GitLab is done: pull in my comments.'])
})

test('a comment the agent matched waits for the press: merge, or keep separate', async ($, on) => {
  const proposal = { thread: 'd7', into: 't3', summary: 'x', where: 'db.ts:59', comment: 'loadDb can return null here', why: 'Both are about the null.' }
  const w = world(on, [
    stateOf(null, [topic('t3', 'draft')], { own_review: 'done', proposals: [proposal], own_threads: [{ id: 'd7', file: 'db.ts', line: 59, where: 'db.ts:59', text: 'c', topic: null, proposal: 't3' }] }),
    stateOf('t3', [topic('t3', 'draft')], { own_review: 'done', proposals: [] }),
  ])
  await openReview($, w, view('t3'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Merge into t3', 'Keep separate', 'Explain', 'Show sidebar'])
  await ui.press({ key: 'settle-merge' })
  await ui.unmount()
  expect(w.ran.map(r => r.argv.slice(2).join(' '))).toContain('settle d7 merge --iid 42')
  expect(w.placed.length).toBe(1)
  expect(w.prompts).toEqual([`I merged my comment into t3. What does your own analysis add to it? ${kindCheck('t3')}`])
})

test('a topic your comment was merged into offers a follow-up, or nothing to add', async ($, on) => {
  const merged = { ...topic('t3', 'needs_ack'), state: 'new', follow_up: 'offer' }
  const w = world(on, [stateOf('t3', [merged]), stateOf(null, [{ ...merged, follow_up: null }])])
  await openReview($, w, view('t3'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Follow up', 'Nothing to add', 'Explain', 'Next', 'Show sidebar'])
  await ui.press({ key: 'no-follow' })
  await ui.unmount()
  expect(w.ran.map(r => r.argv.slice(2).join(' '))).toContain('follow-up t3 none --iid 42')
})

test('the agent recommending to keep a comment apart puts that answer first', async ($, on) => {
  const proposal = { thread: 'd7', into: 't3', summary: 'x', where: 'db.ts:59', comment: 'c', why: 'Same line, other point.', recommend: 'separate' as const }
  const w = world(on, [stateOf(null, [topic('t3', 'draft')], { own_review: 'done', proposals: [proposal], own_threads: [{ id: 'd7', file: 'db.ts', line: 59, where: 'db.ts:59', text: 'c', topic: null, proposal: 't3' }] })])
  await openReview($, w, view('t3'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Keep separate', 'Merge into t3', 'Explain', 'Show sidebar'])
  await ui.press({ key: 'explain' })
  await ui.unmount()
  expect(w.prompts[0]).toMatch(/^Explain whether my comment at db\.ts:59 is the same point as t3:/)
})

test('one space stands on either side of the severity icon, whatever the ids', async ($, on) => {
  const w = world(on, [stateOf('t1', [topic('t1', 'draft'), topic('t10', 'draft')])])
  await openReview($, w, view('t1'))
  const ui = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  const texts = (await ui.findAll({ type: 'Text' })).map(t => t.text)
  const id = texts.find(t => /^t1 *$/.test(t)) ?? ''
  const sev = texts[texts.indexOf(id) + 1] ?? ''
  const title = texts[texts.indexOf(id) + 2] ?? ''
  expect(id.endsWith('t1 ')).toBe(true)          // padded to t10's width
  expect(sev.startsWith(' ') && !sev.startsWith('  ')).toBe(true)
  expect(title.startsWith(' ') && !title.startsWith('  ')).toBe(true)
  await ui.unmount()
})

test('a row is one line, cut to the pane, and every row but the current one is dimmed', async ($, on) => {
  const long = { ...topic('t2', 'draft'), summary: 'x'.repeat(200) }
  const w = world(on, [stateOf('t1', [topic('t1', 'draft'), long])])
  await openReview($, w, view('t1'))
  const ui = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  const titles = (await ui.findAll({ type: 'Text' })).filter(t => /^ x+…$/.test(t.text))
  expect(titles.length).toBe(1)
  expect(titles[0]?.text.length).toBeLessThan(PANEL.props.bodyColumns)
  expect(titles[0]?.props.dimColor).toBe(true)
  const current = (await ui.findAll({ type: 'Text' })).find(t => t.text === ' summary of t1'.padEnd(t.text.length))
  expect(current?.props.bold).toBe(true)
  expect(current?.props.dimColor).toBeUndefined()
  await ui.unmount()
})

test('the legend is a key in the list\'s line, opening under the list with every mark on screen', async ($, on) => {
  const news = { ...topic('t1', 'needs_ack'), inbox: 'new', news: ['reply'], verdict: 'more' }
  const w = world(on, [stateOf(null, [news], { in_round: false, author: 'Jonas', inbox: inbox({ replies: ['t1'], first: 't1' }) })])
  await openReview($, w, view('t1'))
  const ui = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  const texts = async () => (await ui.findAll({ type: 'Text' })).map(t => String(t.text ?? ''))
  expect((await texts()).some(t => t.endsWith(' high'))).toBe(false)   // closed at first
  expect((await ui.find({ type: 'Button', key: 'legend' }))?.props.label).toBe('\u{F064E}')
  await ui.press({ key: 'legend' })
  // Only what is on screen: one high topic, and the mark of a topic flagged new, in one
  // word: "news", "FYI" and "needs you" were three words for one icon.
  expect((await texts()).some(t => t.endsWith(' high'))).toBe(true)
  expect((await texts()).some(t => t.endsWith(' new'))).toBe(true)
  for (const word of [' news', ' FYI', ' needs you']) expect((await texts()).some(t => t.endsWith(word))).toBe(false)
  for (const word of [' critical', ' question', ' being judged']) expect((await texts()).some(t => t.endsWith(word))).toBe(false)
  await ui.press({ key: 'legend' })
  expect((await texts()).some(t => t.endsWith(' high'))).toBe(false)
  await ui.unmount()
})

test('the legend is offered while a topic\'s file is shown, and sync is not', async ($, on) => {
  const w = world(on, [stateOf('t1', [topic('t1', 'draft')])], { code: { stdout: JSON.stringify(codeOf(50, [17, 17])) } })
  await openReview($, w, view('t1'))
  const ui = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  expect(await ui.find({ type: 'Button', key: 'legend' })).toBeDefined()
  expect(await ui.find({ type: 'Button', key: 'check-now' })).toBeUndefined()
  await ui.unmount()
})

// A file as the scripts' `code` command answers it (lib/code_view.py's `view`): `n` lines the
// MR kept, the comment on rows `at`.
const codeOf = (n: number, at: [number, number] | null, more: Record<string, unknown> = {}) => ({
  file: 'src/cache.ts', side: 'new', line: at?.[1] ?? null, start: at?.[0] ?? null, moved: false, changed: [], at,
  rows: Array.from({ length: n }, (_, i) => [' ', i + 1, i + 1, `const line${i + 1} = ${i + 1}`]),
  ...more,
})
// The first new-side line number of a hunk the sidebar drew.
const firstLine = (source: unknown) => Number(/^@@ -\d+,\d+ \+(\d+),/.exec(String(source))?.[1])

// A comment on code is never shown without code: a file removed since is shown as the
// comment saw it, and the line above it says so, as GitLab marks an outdated thread.
test('a file removed since the comment is shown as it was reviewed, and said to be gone', async ($, on) => {
  const code = codeOf(20, [3, 3], { gone: true })
  const w = world(on, [stateOf('t1', [topic('t1', 'open')])], { code: { stdout: JSON.stringify(code) } })
  await openReview($, w, view('t1'))
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  expect((await panel.find({ type: 'Text', text: 'this code is gone now' }))?.props.color).toBe('red')
  expect(JSON.stringify(await panel.findAll({ type: 'Code' }))).toContain('const line3 = 3')
  await panel.unmount()
})

test("the sidebar shows the current topic's file, the discussed lines on a background, and moves it on a key", async ($, on) => {
  const code = codeOf(200, [98, 100], { changed: [101] })
  const w = world(on, [stateOf('t1', [topic('t1', 'draft')])], { code: { stdout: JSON.stringify(code) } })
  await openReview($, w, view('t1'))
  expect(w.ran.map(r => r.argv.slice(2).join(' '))).toContain('code t1 --iid 42')
  const ui = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  const runs = await ui.findAll({ type: 'Code' })
  const discussed = runs.find(c => firstLine(c.props.source) === 98)
  expect(discussed?.props.source).toBe('@@ -98,3 +98,3 @@\n const line98 = 98\n const line99 = 99\n const line100 = 100')
  expect(discussed?.props.format).toBe('diff')
  const start = firstLine(runs[0]?.props.source)
  expect(start).toBeLessThan(98)
  expect(await ui.find({ type: 'Text', text: '▎' })).toBeDefined()     // line 101 changed since
  // The file's scrollbar beside it, a press below its thumb a window on.
  expect(await bar(ui, 'code')).not.toBeNull()
  await page(ui, 'code', 'down')
  expect(firstLine((await ui.findAll({ type: 'Code' }))[0]?.props.source)).toBeGreaterThan(start + 3)
  await ui.unmount()
})

const inbox = (over: object = {}) => ({
  since: '2026-11-05T13:02:00Z', replies: [], resolved: [], threads: [], pushes: [], own: [], matching: 0,
  later: [], aside: [], waiting: 0, first: null, newer_push: false, ...over,
})

// News said "t1 a reviewer replied" where every card names them, and said nothing of a
// reviewer resolving a thread, which they often do without a word.
test('rework-mr: news names who replied, and says a reviewer resolved a thread', () => {
  const r = JSON.parse(reworkState(null, [reworkTopic('t1', 'none'), { ...reworkTopic('t2', 'plan'), inbox: 'new', news: ['reply'], news_at: '2026-11-09T12:00:00Z' }],
    { inbox: inbox({ replies: ['t2'], by: { t2: 'Miriam' }, resolves: [{ id: 't1', by: 'Miriam', at: '2026-11-09T12:10:00Z' }] }) }))
  const lines = inboxOf(r)?.lines.map(l => `${l.topic} ${l.tone} ${l.what}`)
  expect(lines).toEqual(['t2 needs Miriam replied', 't1 info Miriam resolved it'])
})

test("a box line about a topic links to its thread on GitLab", async ($, on) => {
  const t2 = { ...topic('t2', 'needs_ack'), thread_url: 'https://gl/g/p/-/merge_requests/42#note_7' }
  const box = inboxOf(JSON.parse(stateOf(null, [t2, topic('t3', 'open')], { author: 'Jonas', inbox: inbox({ replies: ['t2'], threads: [{ id: 't3' }] }) })))
  expect(box?.lines.map(l => [l.topic, l.url])).toEqual([['t2', 'https://gl/g/p/-/merge_requests/42#note_7'], ['t3', undefined]])
})

test('a comment of yours matched mid-topic waits for the topic; deciding it brings the match up', async ($, on) => {
  const proposal = { thread: 'd9', into: 't2', summary: 'x', where: 'db.ts:59', comment: 'c', recommend: 'merge' as const }
  const own = [{ id: 'd9', file: 'db.ts', line: 59, where: 'db.ts:59', text: 'c', topic: null, proposal: 't2' }]
  const w = world(on, [
    stateOf('t1', [topic('t1', 'draft'), topic('t2', 'draft')], { own_review: 'done', in_round: true, proposals: [proposal], own_threads: own }),
    stateOf(null, [topic('t2', 'draft')], { own_review: 'done', in_round: true, proposals: [proposal], own_threads: own }),
  ], { proposal: { stdout: view('t2') } })
  await openReview($, w, view('t1'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Comment', 'Drop', 'Explain', 'Next', 'Show sidebar'])
  await ui.press({ key: 'drop' })
  await ui.unmount()
  expect(w.ran.map(r => r.argv.slice(2).join(' '))).toContain('proposal d9 --iid 42')
  expect(w.prompts).toEqual([])
})

test('rework-mr: with no news the box opens the pass that runs by its name, and the sidebar\'s foot says when GitLab is asked next', async ($, on) => {
  const idle = reworkState(null, [reworkTopic('t1', 'plan')], { in_round: false, pass: 'plan', inbox: inbox({ first: 't1' }) })
  const w = world(on, [idle])
  await openRework($, w, reworkView('t1'))
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  expect((await panel.find({ type: 'Button', key: 'open-round' }))?.props.label).toBe('Start planning')
  expect((await panel.find({ type: 'Button', key: 'check-now' }))?.props.label).toBe('sync')
  await panel.unmount()
})

test('between rounds news stays out of the chat: the band counts it, the box says who did what and opens a round', async ($, on) => {
  const news = { ...topic('t2', 'needs_ack'), inbox: 'new', news: ['reply'], verdict: 'settled' }
  const idle = stateOf(null, [topic('t1', 'open'), news], {
    in_round: false, author: 'Jonas', inbox: inbox({ replies: ['t2'], first: 't2', waiting: 1, pushes: [{ at: '2026-11-05T13:20:00Z', stat: '\u{f4d2} 1 file +2 −1', topics: ['t2'], rebase: false }] }),
  })
  const opened = stateOf('t2', [topic('t1', 'open'), { ...news, inbox: 'yours' }], { in_round: true, inbox: inbox() })
  const w = world(on, [idle, opened])
  await openReview($, w, view('t1'))
  const band = await mountStep($, 'terminal')
  expect(await band.find({ type: 'Text', text: '1 topic has news' })).toBeDefined()
  expect(await labels(band)).toEqual(['Show sidebar'])
  await band.unmount()
  expect(w.placed.length).toBe(0)
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  const texts = (await panel.findAll({ type: 'Text' })).map(t => String(t.text ?? ''))
  // The heading is one word for where the MR stands; when things happened is each line's.
  expect(texts).toContain('News')
  expect(texts.some(t => /^Since /.test(t))).toBe(false)
  // One line a topic, who did what and the verdict, marked as needing the person.
  expect(texts).toContain('Jonas answered: looks fixed')
  expect((await panel.findAll({ type: 'Text' })).find(t => t.text === 'Jonas answered: looks fixed')?.props.dimColor).toBeUndefined()
  // Its kind's icon leads it, plain: the list's row carries the yellow news mark, the box does not repeat it.
  const all = await panel.findAll({ type: 'Text' })
  expect(all.find(t => t.text === '\u{f0189}')).toBeDefined()
  expect(all.find(t => t.text === '\u{f0189}')?.props.color).toBeUndefined()
  expect(all.filter(t => t.text === '\u{f09de}' && t.props.color === 'yellow').length).toBe(0)
  // A push: when first, then who and the topics it is news of, dim: the topics carry what to do.
  expect(texts).toContain('Jonas pushed · t2')
  // Led by the push's icon the cards use, the upload arrow; no files or lines.
  expect(all.find(t => t.text === '\u{f0552}')).toBeDefined()
  expect(texts.some(t => /\d+ files? \+/.test(t))).toBe(false)
  expect(texts.some(t => /^ *\d\d:\d\d$/.test(t) || /^Nov 5, \d\d:\d\d$/.test(t))).toBe(true)
  // The button opens the news, and says which topic.
  expect((await panel.find({ type: 'Button', key: 'open-round' }))?.props.label).toBe('Open t2')
  await panel.press({ key: 'open-round' })
  await panel.unmount()
  expect(w.ran.map(r => r.argv.slice(2).join(' '))).toContain('open t2 --iid 42')
  expect(w.placed.length).toBe(1)
})

test("a news line's topic handle opens the topic here; the rest of the line is the link to GitLab", async ($, on) => {
  const news = { ...topic('t2', 'needs_ack'), inbox: 'new', news: ['reply'], verdict: 'settled', thread_url: 'https://gl/g/p/-/merge_requests/42#note_7' }
  const idle = stateOf(null, [topic('t1', 'open'), news], { in_round: false, author: 'Jonas', inbox: inbox({ replies: ['t2'], first: 't2', waiting: 1 }) })
  const opened = stateOf('t2', [topic('t1', 'open'), { ...news, inbox: 'yours' }], { in_round: true, inbox: inbox() })
  const w = world(on, [idle, opened])
  await openReview($, w, view('t1'))
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  const link = (await panel.findAll({ type: 'Markdown' })).map(m => String(m.props.text ?? '')).find(t => t.includes('note_7'))
  // The link mark joined to the last word: it never wraps onto a line alone.
  expect(link).toBe('[Jonas answered: looks fixed\u00a0\u{f03cc}](https://gl/g/p/-/merge_requests/42#note_7)')
  await panel.press({ key: 'inbox-topic-0' })
  await panel.unmount()
  expect(w.ran.map(r => r.argv.slice(2).join(' ')).some(c => /\bt2\b/.test(c))).toBe(true)
})

test('an answer still being judged in the background offers nothing to decide, only the way on', async ($, on) => {
  const w = world(on, [stateOf('t1', [{ ...topic('t1', 'needs_ack'), inbox: 'yours' }], { in_round: true, judge: { topics: ['t1'], own: [] } })])
  await openReview($, w, view('t1'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Next', 'Show sidebar'])
  // The card says so itself, where its buttons will be: with the line under the chat alone,
  // a card with no verdict and no buttons looked finished. Said once, not in the band too.
  expect(JSON.stringify(await ui.findAll({ type: 'Text' }))).toMatch(/Judging what [^"]* did…/)
  expect(await ui.find({ type: 'Text', text: 't1: judging the news…' })).toBeUndefined()
  await ui.press({ key: 'next' })
  await ui.unmount()
  expect(w.ran.map(r => r.argv.slice(2).join(' '))).toContain('leave --iid 42')
})

// The topic was open when its news came: the new comment was in the sidebar alone, the card
// as before, and the topic still marked new.
const newsAfter = async ($: Engine, on: On, isTurnBetween: boolean) => {
  const tick = heldClock(on)
  on('tool.register' as never, (() => ({ value: undefined })) as never)
  on('session.start' as never, ((_: unknown, e: object) => e) as never)
  on('turn.start', ($, e) => ({ turnId: e.turnId }))
  // A fix waiting for its push: the one state every update check reads again by itself.
  const at = (face: string, judge: string[] = []) => reworkState('t1', [reworkTopic('t1', 'fixed', { checks: 'passed' })],
    { face, judge: { topics: judge, own: [] }, hunk: { open: false, notes: 0 } })
  const states = [at('f1')]
  const fresh = reworkView('t1', undefined, 'fixed', 'f3').replace('some view', 'the news taken in')
  const w = world(on, states, { quote: { stdout: fresh } })
  await (($ as unknown as { session: { start: (e: object) => Promise<unknown> } }).session.start({ source: 'startup', cwd: '/wt' }))
  await openRework($, w, reworkView('t1', undefined, 'fixed', 'f1'))
  if (isTurnBetween) {
    await $.turn.start({ text: 'what does the fix do?', turnId: 'turn-1' })
    await (($ as unknown as { turn: { complete: (e: object) => Promise<unknown> } }).turn.complete({ turnId: 'turn-1', answer: 'It renames it.' }))
  }
  await tick()
  expect(w.placed).toEqual([])                         // nothing new: the card stands
  states[0] = at('f2', ['t1'])                         // a reviewer wrote: being judged
  await tick()
  expect(w.placed).toEqual([])
  states[0] = at('f3')                                 // judged: the card takes it in
  await tick()
  // Then the state read again, as a move to a topic does: the card marked the news seen, so
  // the sidebar's file is the news's own (a push as the fix's diff) at once, not at the
  // next check minutes later.
  const cmds = w.ran.map(r => r.argv[2])
  expect(cmds.slice(cmds.lastIndexOf('quote'))).toContain('state')
  return { w, tick, fresh }
}

test("news for the topic whose card ends the chat is drawn into that card, once it is judged", async ($, on) => {
  const { w, tick, fresh } = await newsAfter($, on, false)
  expect(w.placed).toEqual([])                         // in place: no second card of t1
  const ui = await $.ui.mount(cardRow('tu-open', fresh))
  expect(JSON.stringify(await ui.findAll({ type: 'Markdown' }))).toContain('the news taken in')
  await ui.unmount()
  const quotes = w.ran.filter(r => r.argv[2] === 'quote').length
  await tick()
  expect(w.ran.filter(r => r.argv[2] === 'quote').length).toBe(quotes)    // once
})

test("news for the topic whose card a turn has followed brings a fresh card of it at the end", async ($, on) => {
  const { w, tick } = await newsAfter($, on, true)
  expect(w.placed.length).toBe(1)
  await tick()
  expect(w.placed.length).toBe(1)                      // once
})

// The engine's own model call, answered here: what the mod asked is kept in `calls`.
const modelCall = (on: On, answer: { isAnswered: true; text: string } | { isAnswered: false; reason: string }) => {
  const calls: { model: string; prompt: string; maxTokens?: number; timeoutMs?: number }[] = []
  on('session.model' as never, (() => ({ value: 'claude-test' })) as never)
  on('model.complete' as never, ((_: unknown, e: { model: string; prompt: string; maxTokens?: number; timeoutMs?: number }) => {
    calls.push({ model: e.model, prompt: e.prompt, maxTokens: e.maxTokens, timeoutMs: e.timeoutMs })
    return { value: { ...answer, usage: { input_tokens: 1, output_tokens: 1, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 } } }
  }) as never)
  return calls
}
const settle = () => new Promise<void>(done => setTimeout(() => done(), 30))

test('what the check found is judged by one model call, with no turn: the script writes the prompt and stores the answers', async ($, on) => {
  const news = { ...topic('t2', 'needs_ack'), inbox: 'new', news: ['reply'] }
  const idle = stateOf(null, [topic('t1', 'open'), news], { in_round: false, judge: { topics: ['t2'], own: [] }, inbox: inbox({ replies: ['t2'], first: 't2' }) })
  const ask = { prompt: 'Judge t2.', items: { t2: 'm1' }, topics: ['t2'], own: [], max_tokens: 1700 }
  const w = world(on, [idle], {
    refresh: { stdout: idle }, 'judge-prompt': { stdout: JSON.stringify(ask) },
    'judge-store': { stdout: JSON.stringify({ stored: ['t2'], stale: [], unjudged: [] }) },
  })
  const calls = modelCall(on, { isAnswered: true, text: '{"t2": {"verdict": "settled"}}' })
  w.clock.now = new Date(2026, 10, 5, 12, 27).getTime()
  await openReview($, w, view('t1'))
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  await panel.press({ key: 'check-now' })
  await settle()
  await panel.unmount()
  // The session's own model, the reply's cap the script's, and a time limit.
  expect(calls).toEqual([{ model: 'claude-test', prompt: 'Judge t2.', maxTokens: 1700, timeoutMs: 180_000 }])
  // Nothing failed before, so nothing is left out of the prompt (`--skip`, one per failure).
  expect(w.ran.map(r => r.argv.slice(2).join(' '))).toContain('judge-prompt --iid 42')
  const stored = w.ran.find(r => r.argv[2] === 'judge-store')
  expect(JSON.parse(stored?.stdin ?? '{}')).toEqual({ items: { t2: 'm1' }, reply: '{"t2": {"verdict": "settled"}}' })
  // Nothing of it reaches the chat.
  expect(w.placed.length).toBe(0)
  expect(w.prompts).toEqual([])
})

test('an answer the call could not judge is the chat\'s: Judge, from the whole thread', async ($, on) => {
  const tick = heldClock(on)
  on('tool.register' as never, (() => ({ value: undefined })) as never)
  on('session.start' as never, ((_: unknown, e: object) => e) as never)
  const answered = stateOf('t2', [topic('t1', 'open'), { ...topic('t2', 'needs_ack'), inbox: 'yours' }], { in_round: true, judge: { topics: ['t2'], own: [] } })
  const ask = { prompt: 'Judge t2.', items: { t2: 'm1' }, topics: ['t2'], own: [], max_tokens: 1700 }
  const w = world(on, [answered], {
    refresh: { stdout: answered }, 'judge-prompt': { stdout: JSON.stringify(ask) },
    // No reply at all (the call ran out of time): the script stores nothing, and says so.
    'judge-store': { stdout: JSON.stringify({ stored: [], stale: [], unjudged: ['t2'] }) },
  })
  modelCall(on, { isAnswered: false, reason: 'aborted' })
  await (($ as unknown as { session: { start: (e: object) => Promise<unknown> } }).session.start({ source: 'startup', cwd: '/wt' }))
  await openReview($, w, view('t2'))
  let ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Next', 'Show sidebar'])         // being judged: nothing to decide yet
  await ui.unmount()
  await tick()
  await settle()
  expect(JSON.parse(w.ran.find(r => r.argv[2] === 'judge-store')?.stdin ?? '{}').reply).toBe('')
  ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toContain('Judge')
  await ui.unmount()
  // What was agreed halfway through a thread counts: never its first and last comment alone.
  for (const extra of [{}, { peer: true }]) {
    const review = JSON.parse(stateOf('t1', [{ ...topic('t1', 'needs_ack'), inbox: 'yours', ...extra }], { in_round: true, judge: { topics: ['t1'], own: [] } }))
    const judge = buttonsFor({ ...review, unjudged: ['t1'] }).find(x => x.key === 'judge')
    expect(judge?.press).toMatchObject({ kind: 'ask', prompt: expect.stringContaining('`bodies t1`, every comment in order') })
  }
})

// A reload of the mod cuts a call off: what it recorded of it is no call of this load, so
// nothing stays marked as being judged, and the next check asks again.
test('a call cut off by a reload of the mod leaves nothing marked as being judged', () => {
  expect(judgingOf({ run: 'load-1', topics: ['t1'] }, 'load-1')).toEqual(['t1'])
  expect(judgingOf({ run: 'load-1', topics: ['t1'] }, 'load-2')).toEqual([])
  expect(judgingOf({ run: null, topics: [] }, 'load-2')).toEqual([])
})

test("another reviewer's thread that needs you offers the agent's advice first; Close marks it read after the last", async ($, on) => {
  const peer = { ...topic('t6', 'needs_ack'), source: 'peer', peer: true }
  const w = world(on, [stateOf('t6', [{ ...peer, peer_view: { says: 'needs' as const, advice: 'reply' as const } }])])
  await openReview($, w, view('t6'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Reply…', 'Close', 'Analyse', 'Show sidebar'])
  await ui.press({ key: 'peer-next' })
  await ui.unmount()
  expect(w.ran.map(r => r.argv.slice(2).join(' '))).toContain('leave --ack t6 --iid 42')
})

test("another reviewer's thread that needs you goes on to the first of the others, marked read on the way", async ($, on) => {
  const needs = { ...topic('t6', 'needs_ack'), source: 'peer', peer: true, peer_view: { says: 'needs' as const, advice: 'seen' as const } }
  const fyi = (id: string) => ({ ...topic(id, 'acked'), source: 'peer', peer: true, watching: true, peer_view: { says: 'fyi' as const, advice: 'seen' as const } })
  const w = world(on, [stateOf('t6', [topic('t1', 'needs_ack'), fyi('t3'), fyi('t4'), needs], { in_round: true, round: ['t6', 't1'] })])
  await openReview($, w, view('t6'))
  const ui = await mountStep($, 'terminal')
  // Its advice was only to know of it: the way on takes the first place, and no round Next.
  expect(await labels(ui)).toEqual(['Next', 'Reply…', 'Analyse', 'Show sidebar'])
  await ui.press({ key: 'peer-next' })
  await ui.unmount()
  expect(w.ran.map(r => r.argv.slice(2).join(' '))).toContain('leave --ack t6 --to t3 --iid 42')
})

test('Merge into tN is offered only where the agent advised it, and merges there', async ($, on) => {
  const peer = { ...topic('t6', 'needs_ack'), source: 'peer', peer: true }
  const w = world(on, [stateOf('t6', [topic('t1', 'open'), { ...peer, peer_view: { says: 'needs' as const, advice: 'merge' as const, into: 't1' } }])])
  await openReview($, w, view('t6'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Merge into t1', 'Reply…', 'Close', 'Analyse', 'Show sidebar'])
  await ui.press({ key: 'merge' })
  await ui.unmount()
  expect(w.ran.map(r => r.argv.slice(2).join(' '))).toContain('merge t1 t6 --iid 42')
  // Nothing else asks once the thread's own topic is gone: what it adds, and whether t1's
  // kind and severity still fit.
  expect(w.prompts).toEqual([expect.stringMatching(/^I merged t6, another reviewer's thread, into t1\. Read t1's threads whole \(`bodies t1`\)/)])
  expect(w.prompts[0]).toContain(kindCheck('t1'))
})

test("another reviewer's thread opened for your information offers Close after the last of them, and Reply…", async ($, on) => {
  const peer = { ...topic('t6', 'acked'), source: 'peer', peer: true, watching: true }
  const w = world(on, [stateOf('t6', [{ ...peer, peer_view: { says: 'fyi' as const, advice: 'seen' as const } }])])
  await openReview($, w, view('t6'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Close', 'Reply…', 'Analyse', 'Show sidebar'])
  await ui.press({ key: 'peer-next' })
  await ui.unmount()
  expect(w.ran.map(r => r.argv.slice(2).join(' '))).toContain('leave --iid 42')
})

test("Next on another reviewer's thread goes to the next of them in the sidebar's order, never the round's", async ($, on) => {
  const fyi = (id: string) => ({ ...topic(id, 'acked'), source: 'peer', peer: true, watching: true, peer_view: { says: 'fyi' as const, advice: 'seen' as const } })
  // t2 needs the user and is the round's next; t5 is the next of the other reviewers' threads.
  const w = world(on, [stateOf('t4', [fyi('t1'), topic('t2', 'needs_ack'), fyi('t4'), fyi('t5')], { in_round: true, round: ['t2'] })])
  await openReview($, w, view('t4'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Next', 'Reply…', 'Analyse', 'Show sidebar'])
  await ui.press({ key: 'peer-next' })
  await ui.unmount()
  expect(w.ran.map(r => r.argv.slice(2).join(' '))).toContain('leave --to t5 --iid 42')
})

test("Analyse on another reviewer's thread asks for the whole thread, the user's own topics, and a new view only where it needs them", () => {
  const peer = { ...topic('t6', 'acked'), source: 'peer', peer: true, watching: true, peer_view: { says: 'fyi' as const, advice: 'seen' as const } }
  const review = JSON.parse(stateOf('t6', [peer]))
  const analyse = buttonsFor(review).find(b => b.label === 'Analyse')
  expect(analyse?.press).toMatchObject({ kind: 'ask', prompt: expect.stringContaining('`bodies t6`, every comment in order') })
  const prompt = (analyse?.press as { prompt: string }).prompt
  expect(prompt).toContain('my own topics')
  expect(prompt).toContain("`peer t6 needs --why '…' --advice reply|merge|seen`")
  expect(prompt).toContain('do not show t6 again')
  expect(prompt).toContain(kindCheck('t6'))
})

test('Analyse more asks whether the kind and severity still fit', () => {
  const review = JSON.parse(stateOf('t2', [{ ...topic('t2', 'needs_ack'), verdict: 'settled' }]))
  const analyse = buttonsFor(review).find(b => b.label === 'Analyse more')
  expect((analyse?.press as { prompt: string }).prompt).toContain(kindCheck('t2'))
  expect(kindCheck('t2')).toContain('`set t2 --kind … --severity …`')
})

test('a later comment that is a draft posted by hand is linked to its topic', async ($, on) => {
  const proposal = { thread: 'd9', into: 't2', summary: 'x', where: 'db.ts:59', comment: 'c', why: 'Same words as the draft.', recommend: 'link' as const }
  const w = world(on, [stateOf(null, [topic('t2', 'draft', 'v1')], { own_review: 'done', proposals: [proposal], own_threads: [{ id: 'd9', file: 'db.ts', line: 59, where: 'db.ts:59', text: 'c', topic: null, proposal: 't2' }] })])
  await openReview($, w, view('t2'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(["Link as t2's comment", 'Keep separate', 'Explain', 'Show sidebar'])
  await ui.press({ key: 'settle-link' })
  await ui.unmount()
  expect(w.ran.map(r => r.argv.slice(2).join(' '))).toContain('settle d9 link --iid 42')
})

test('a session working on the skills, not with them, gets no band from a view it ran', async ($, on) => {
  const w = world(on, [stateOf('t1', [topic('t1', 'draft')])])
  w.tool.stdout = view('t1')
  await $.tool.call({ tool: 'Bash', command: 'python3 $SD/findings.py present --iid 42', tool_use_id: 'tu-open' } as never)
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual([])
  await ui.unmount()
  expect(w.written).toEqual([])           // and no mark: its scripts render as without the mod
})

test('typing the skill puts the session in use', async ($, on) => {
  const w = world(on, [stateOf('t1', [topic('t1', 'draft')])])
  await $.prompt.submit({ text: '/review-mr !42', command: { name: 'review-mr', args: '!42' } } as never)
  w.tool.stdout = view('t1')
  await $.tool.call({ tool: 'Bash', command: 'python3 $SD/findings.py present --iid 42', tool_use_id: 'tu-open' } as never)
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Comment', 'Drop', 'Explain', 'Next', 'Show sidebar'])
  await ui.unmount()
})

test('a topic whose lines a push changed shows the change since the comment', async ($, on) => {
  const code = codeOf(50, [17, 17], { mode: 'since', since_at: '2026-11-05T13:49:00Z' })
  const thread = [{ url: 'u', file: 'src/cache.ts', line: 17, notes: [{ author: 'You', body: 'seems bad', mine: true }] }]
  const pushed = { ...topic('t1', 'needs_ack'), inbox: 'yours', news: ['push'], verdict: 'settled' }
  const w = world(on, [stateOf('t1', [pushed], { thread, in_round: true })], { code: { stdout: JSON.stringify(code) } })
  await openReview($, w, view('t1'))
  expect(w.ran.map(r => r.argv.slice(2).join(' '))).toContain('code t1 --since-comment --iid 42')
  const ui = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  // One word in the file's line, and no time: when the push came does not change the code.
  expect(await ui.find({ type: 'Text', text: 'changed' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /\d\d:\d\d/ })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /^written on the code before the push/ })).toBeDefined()
  await ui.unmount()
})

test("the sidebar's thread draws a comment's emoji shortcode as the emoji, open or folded", async ($, on) => {
  const thread = [{ url: 'u', file: 'src/cache.ts', line: 17, notes: [{ author: 'Kim', body: 'good catch :ok_hand:', url: 'http://gl/x#note_2' }] }]
  const w = world(on, [stateOf('t1', [topic('t1', 'open')], { thread })], { code: { stdout: JSON.stringify(codeOf(50, [17, 17])) } })
  await openReview($, w, view('t1'))
  const ui = await $.ui.mount({ ...PANEL, props: { ...PANEL.props, scroll: { offset: 0, bodyRows: 60 } }, surface: 'terminal' })
  const all = async () => JSON.stringify(await ui.findAll({}))
  expect(await all()).toContain('good catch 👌')
  expect(await all()).not.toContain(':ok_hand:')
  await ui.unmount()
})

test('a thread sits under the lines it is about, in full, and its fold key folds it to a line per comment', async ($, on) => {
  const code = codeOf(50, [17, 17])
  const long = `seems bad, ${'and more words '.repeat(20)}\n\n\`\`\`suggestion\nfixed()\n\`\`\``
  const thread = [{ url: 'u', file: 'src/cache.ts', line: 17, notes: [
    { author: 'You', body: 'seems bad', mine: true, url: 'http://gl/x#note_1' },
    { author: 'Kim', body: long, url: 'http://gl/x#note_2' },
  ] }]
  const w = world(on, [stateOf('t1', [topic('t1', 'open')], { thread })], { code: { stdout: JSON.stringify(code) } })
  await openReview($, w, view('t1'))
  const ui = await $.ui.mount({ ...PANEL, props: { ...PANEL.props, scroll: { offset: 0, bodyRows: 60 } }, surface: 'terminal' })
  const md = async () => (await ui.findAll({ type: 'Markdown' })).map(m => m.props.text as string)
  const codes = async () => (await ui.findAll({ type: 'Code' })).map(c => String(c.props.source))
  // In full, the suggestion is one folded row; opened, a diff on line 17.
  expect((await codes()).some(t => t.includes('fixed()'))).toBe(false)
  expect((await ui.find({ type: 'Button', key: 'fold-1-0' }))?.props.label).toBe('\u{F460} suggestion · 1 line')
  await ui.press({ key: 'fold-1-0' })
  expect(await codes()).toContain('@@ -17,1 +17,1 @@\n-const line17 = 17\n+fixed()')
  // The fold key stands on the box's top border near its right corner, whichever way the box
  // is: restore on an open box, maximize on a folded one.
  const label = async () => (await ui.find({ type: 'Button', key: 'thread-open' }))?.props.label
  const onBorder = async () => (await ui.find({ type: 'Box', key: 'box-key' }))?.props
  expect(await label()).toBe('\u{F05B2}')
  expect(await onBorder()).toMatchObject({ position: 'absolute', top: 0, right: 2 })
  await ui.press({ key: 'thread-open' })              // folded
  expect(await label()).toBe('\u{F05AF}')
  expect(await onBorder()).toMatchObject({ position: 'absolute', top: 0, right: 2 })
  expect(await md()).toEqual(expect.arrayContaining(['[\u{f03cc}](http://gl/x#note_1)', 'seems bad']))
  expect((await ui.findAll({ type: 'Text' })).find(t => t.text === 'You')?.props.color).toBe('magenta')
  const kim = (await md()).find(t => t.startsWith('seems bad, and')) ?? ''
  expect(kim).not.toContain('\n')                     // one line
  expect(kim).toMatch(/…$/)                           // cut, and says so
  expect(kim).not.toContain('fixed()')
  expect((await ui.findAll({ type: 'Code' })).length).toBeGreaterThan(0)
  expect((await codes()).some(t => t.includes('fixed()'))).toBe(false)
  await ui.press({ key: 'thread-open' })              // in full again, the suggestion as opened
  expect(await codes()).toContain('@@ -17,1 +17,1 @@\n-const line17 = 17\n+fixed()')
  await ui.unmount()
})

test('a thread taller than its room keeps five lines of the file above and below, and scrolls on its own', async ($, on) => {
  const code = codeOf(50, [20, 20])
  const body = Array.from({ length: 60 }, (_, i) => `paragraph ${i + 1}`).join('\n\n')
  const thread = [{ url: 'u', file: 'src/cache.ts', line: 20, notes: [{ author: 'Kim', body, url: 'http://gl/x#note_2' }] }]
  const w = world(on, [stateOf('t1', [topic('t1', 'open')], { thread })], { code: { stdout: JSON.stringify(code) } })
  await openReview($, w, view('t1'))
  const ui = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  const md = async () => (await ui.findAll({ type: 'Markdown' })).map(m => m.props.text as string)
  const lines = async () => (await ui.findAll({ type: 'Code' })).flatMap(c => String(c.props.source).split('\n').slice(1))
  // At its end, where the newest of the thread is, as a chat opens on its last message:
  // opened at the top, a reply stood cut off below the box's edge.
  expect(await md()).toContain('paragraph 60')
  expect(await md()).not.toContain('paragraph 1')
  // The file keeps five lines around line 20.
  for (const n of [15, 16, 17, 18, 19, 21, 22, 23, 24, 25]) expect(await lines()).toContain(` const line${n} = ${n}`)
  // The box's own scrollbar, its thumb at the end; a press above it moves the box, and the
  // file stays where it was.
  expect((await bar(ui, 'box'))?.at(-1)).toBe('thumb')
  const file = await lines()
  await page(ui, 'box', 'up')
  expect(await md()).not.toContain('paragraph 60')
  expect(await lines()).toEqual(file)
  await ui.unmount()
})

// The fold key a rework topic's thread box shows on the code view `mode` names: the fix's, or
// the comment's own while planning.
const reworkFoldKey = async ($: Engine, on: Parameters<typeof world>[0], mode?: string, threadNew = 0) => {
  const long = `seems bad, ${'and more words '.repeat(20)}`
  const thread = [{ url: 'u', file: 'src/cache.ts', line: 3, notes: [{ author: 'Kim', body: long, url: 'http://gl/x#note_2' }] }]
  const code = codeOf(20, [3, 3], mode ? { mode } : {})
  const w = world(on, [reworkState('t1', [reworkTopic('t1', mode ? 'fixed' : 'plan')], { thread, thread_new: threadNew })], { code: { stdout: JSON.stringify(code) } })
  await openRework($, w, reworkView('t1'))
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  const label = (await panel.find({ type: 'Button', key: 'thread-open' }))?.props.label
  await panel.unmount()
  return label
}

test('rework-mr: while planning, the thread under the code is in full', async ($, on) => {
  expect(await reworkFoldKey($, on)).toBe('\u{F05B2}')          // the key folds it
})

test('rework-mr: under the fix the thread is folded, so the code has the room', async ($, on) => {
  expect(await reworkFoldKey($, on, 'fix')).toBe('\u{F05AF}')   // the key opens it
})

// A topic opened for a reviewer's new comment showed its thread one line a comment, at the
// top: the comment it was opened for had to be unfolded and scrolled to.
test('rework-mr: under a pushed fix the thread is in full where a reviewer wrote since', async ($, on) => {
  expect(await reworkFoldKey($, on, 'pushed', 1)).toBe('\u{F05B2}')   // the key folds it
})

test('rework-mr: under a pushed fix with nothing new in the thread, it stays folded', async ($, on) => {
  expect(await reworkFoldKey($, on, 'pushed', 0)).toBe('\u{F05AF}')   // the key opens it
})

test('a file renamed since the comment is shown under its name now, said in a word', async ($, on) => {
  const code = codeOf(20, [3, 3], { file: 'src/renamed.ts', renamed_from: 'src/cache.ts' })
  const thread = [{ url: 'u', file: 'src/cache.ts', line: 3, notes: [{ author: 'Kim', body: 'seems bad' }] }]
  const w = world(on, [stateOf('t1', [topic('t1', 'open')], { thread })], { code: { stdout: JSON.stringify(code) } })
  await openReview($, w, view('t1'))
  const ui = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  expect((await ui.find({ type: 'Text', text: /^renamed$/ }))?.props.dimColor).toBe(true)
  expect(await ui.find({ type: 'Text', text: /^src\/renamed\.ts/ })).toBeDefined()
  await ui.unmount()
})

test('a thread with nothing to leave out offers no folding', async ($, on) => {
  const thread = [{ url: 'u', file: 'src/cache.ts', line: 17, notes: [{ author: 'You', body: 'seems bad', mine: true }] }]
  const w = world(on, [stateOf('t1', [topic('t1', 'open')], { thread })], { code: { stdout: JSON.stringify(codeOf(50, [17, 17])) } })
  await openReview($, w, view('t1'))
  const ui = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  expect(await ui.find({ type: 'Button', key: 'thread-open' })).toBeUndefined()
  await ui.unmount()
})

test('once the agent explained a topic, the button asks it to explain further', async ($, on) => {
  const w = world(on, [stateOf('t1', [topic('t1', 'draft')])])
  await openReview($, w, view('t1'))
  const ui = await mountStep($, 'terminal')
  await ui.press({ key: 'explain' })
  expect(w.prompts[0]).toMatch(/^Explain t1 in detail/)
  expect(await labels(ui)).toContain('Explain further')
  await ui.press({ key: 'explain' })
  await ui.unmount()
  expect(w.prompts[1]).toMatch(/^Explain t1 further: .*do not repeat/)
})

test("the thread scrolls out of view with its lines, marked on the file's scrollbar, a press there bringing them back", async ($, on) => {
  const code = codeOf(200, [10, 10])
  const thread = [{ url: 'u', file: 'src/cache.ts', line: 10, notes: [{ author: 'You', body: 'odd', mine: true, url: 'http://gl/n1' }] }]
  const w = world(on, [stateOf('t1', [topic('t1', 'open')], { thread })], { code: { stdout: JSON.stringify(code) } })
  await openReview($, w, view('t1'))
  const ui = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  const look = (await specOf(ui, 'code'))?.look as BarLook
  const md = async () => (await ui.findAll({ type: 'Markdown' })).map(m => m.props.text as string).join('\n')
  const marks = async () => (await drawnBar(ui, 'code')).flatMap((c, i) => (c.glyph === '▌' && c.color === look.topic ? [i] : []))
  expect(await md()).toContain('odd')
  // Its line and the thread under it, in view: marked all the same, near the top.
  const at = await marks()
  expect(at.length).toBeGreaterThan(0)
  expect(Math.max(...at)).toBeLessThan(3)
  await page(ui, 'code', 'down')                                            // to the end
  expect(firstLine((await ui.findAll({ type: 'Code' }))[0]?.props.source)).toBeGreaterThan(10)
  // Out of view with its lines: gone from the file, its mark where it was.
  expect(await md()).not.toContain('odd')
  expect(await ui.find({ type: 'Button', key: 'thread-back' })).toBeUndefined()
  expect(await marks()).toEqual(at)
  await click(ui, 'code', at[0] as number)
  expect(firstLine((await ui.findAll({ type: 'Code' }))[0]?.props.source)).toBeLessThanOrEqual(10)
  expect(await md()).toContain('odd')
  await ui.unmount()
})

test("the file's scrollbar marks where the file has changes, in their colours, a press there going to them", async ($, on) => {
  // 200 lines: line 150 replaced, line 180 added, out of the window that opens on line 10.
  const rows: unknown[][] = [...codeOf(200, [10, 10]).rows]
  rows.splice(149, 1, ['-', 150, null, 'old'], ['+', null, 150, 'new'])
  rows.splice(180, 0, ['+', null, 181, 'added'])
  const code = codeOf(200, [10, 10], { rows })
  const w = world(on, [stateOf('t1', [topic('t1', 'open')])], { code: { stdout: JSON.stringify(code) } })
  await openReview($, w, view('t1'))
  const ui = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  const look = (await specOf(ui, 'code'))?.look as BarLook
  const cells = await drawnBar(ui, 'code')
  // The right half of a cell, over the track: a replaced line in yellow, an added one in green,
  // in order down the lower part of the bar.
  const ticks = cells.flatMap((c, i) => (c.glyph === '▐' ? [{ i, color: c.color, bg: c.bg }] : []))
  expect(ticks.map(t => t.color)).toEqual([look.modified, look.added])
  expect(ticks.every(t => t.bg === look.track)).toBe(true)
  expect(ticks[0]?.i).toBeGreaterThan(cells.length / 2)
  await click(ui, 'code', ticks[0]?.i as number)
  expect(firstLine((await ui.findAll({ type: 'Code' }))[0]?.props.source)).toBeGreaterThan(100)
  // Under the thumb now, the change reads on the thumb: the same half, the thumb behind it.
  const after = (await drawnBar(ui, 'code'))[ticks[0]?.i as number]
  expect(after?.glyph).toBe('▐')
  expect([look.thumb, look.lit]).toContain(after?.bg)
  await ui.unmount()
})

test('hovering the track shows the thumb where a press would put it, whole; the thumb where it is fades', async ($, on) => {
  // The topic near the end and the window at the top: its mark is apart from the thumb and from
  // where the pointer goes.
  const w = world(on, [stateOf('t1', [topic('t1', 'draft')])], { code: { stdout: JSON.stringify(codeOf(200, [190, 190])) } })
  await openReview($, w, view('t1'))
  const ui = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  const look = (await specOf(ui, 'code'))?.look as BarLook
  await page(ui, 'code', 'up')
  await ui.pointer({ type: 'leave', x: 0, y: 0, in: 'bar-code' })              // the pointer off it
  const spec = await specOf(ui, 'code') as BarSpec
  // How much of the bar is drawn in a colour, in eighths of a cell: a full block is eight, a
  // partial one as many as it fills.
  const eighths = (cells: Awaited<ReturnType<typeof drawnBar>>, color: string) => cells
    .filter(c => c.color === color && c.glyph !== ' ')
    .reduce((n, c) => n + Math.max(BAR_LOWER.indexOf(c.glyph as never), BAR_UPPER.indexOf(c.glyph as never)), 0)
  const lit = async () => (await drawnBar(ui, 'code')).flatMap((c, i) => (c.color === look.lit && c.glyph !== ' ' ? [`${i}${c.glyph}`] : []))
  const atRest = await drawnBar(ui, 'code')
  const thumb = eighths(atRest, look.thumb)
  expect(thumb).toBeGreaterThan(16)
  expect(eighths(atRest, look.lit)).toBe(0)
  // Over the track below the thumb: the thumb there, at its full length and lit, the one at
  // the window faint, and nothing scrolled yet.
  const y = Math.floor(spec.rows / 2) + 2
  await ui.pointer({ type: 'move', x: 0, y, in: 'bar-code' })
  const hovered = await drawnBar(ui, 'code')
  expect(eighths(hovered, look.lit)).toBe(thumb)
  expect(eighths(hovered, look.former)).toBe(thumb)
  expect(eighths(hovered, look.thumb)).toBe(0)
  expect(hovered[y]?.color).toBe(look.lit)
  expect((await specOf(ui, 'code'))?.from).toBe(spec.from)
  // The press puts it exactly there: the thumb where the preview was, lit under the pointer,
  // and the faint one gone.
  const preview = await lit()
  await click(ui, 'code', y)
  expect(await lit()).toEqual(preview)
  expect(eighths(await drawnBar(ui, 'code'), look.former)).toBe(0)
  // Off the bar: at rest again.
  await ui.pointer({ type: 'leave', x: 0, y, in: 'bar-code' })
  expect(eighths(await drawnBar(ui, 'code'), look.thumb)).toBe(thumb)
  await ui.unmount()
})

test('a drag of the thumb moves the file with it, from where it was grabbed', async ($, on) => {
  const w = world(on, [stateOf('t1', [topic('t1', 'draft')])], { code: { stdout: JSON.stringify(codeOf(200, [10, 10])) } })
  await openReview($, w, view('t1'))
  const ui = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  const top = async () => firstLine((await ui.findAll({ type: 'Code' }))[0]?.props.source)
  const grab = ((await bar(ui, 'code')) ?? []).indexOf('thumb')
  const before = await top()
  await ui.pointer({ type: 'down', x: 0, y: grab, button: 'left', in: 'bar-code' })
  expect(await top()).toBe(before)                                          // grabbed: not moved
  await ui.pointer({ type: 'move', x: 0, y: grab + 5, button: 'left', in: 'bar-code' })
  const mid = await top()
  expect(mid).toBeGreaterThan(before + 20)                                  // five cells: a fifth of the way
  // Past the bar's foot the drag goes on, to the end.
  await ui.pointer({ type: 'move', x: 0, y: 99, button: 'left', in: 'bar-code' })
  await ui.pointer({ type: 'up', x: 0, y: 99, button: 'left', in: 'bar-code' })
  expect(((await bar(ui, 'code')) ?? []).at(-1)).toBe('thumb')
  await ui.unmount()
})

test("the topic's lines are marked on the file's scrollbar in view and out, with no thread yet too", async ($, on) => {
  const w = world(on, [stateOf('t1', [topic('t1', 'draft')])], { code: { stdout: JSON.stringify(codeOf(200, [100, 100])) } })
  await openReview($, w, view('t1'))
  const ui = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  const look = (await specOf(ui, 'code'))?.look as BarLook
  const mark = async () => (await drawnBar(ui, 'code')).findIndex(c => c.glyph === '▌' && c.color === look.topic)
  // In view: on the thumb, which stays behind it.
  const at = await mark()
  expect(at).toBeGreaterThan(0)
  expect([look.thumb, look.lit]).toContain((await drawnBar(ui, 'code'))[at]?.bg)
  await page(ui, 'code', 'up')                                              // to the top
  expect(await mark()).toBe(at)
  expect((await drawnBar(ui, 'code'))[at]?.bg).toBe(look.track)
  // Hovered, the mark shows where a press on it goes: back to the topic's lines.
  await ui.pointer({ type: 'move', x: 0, y: at, in: 'bar-code' })
  expect((await drawnBar(ui, 'code')).some(c => c.color === look.lit)).toBe(true)
  await click(ui, 'code', at)
  expect(firstLine((await ui.findAll({ type: 'Code' }))[0]?.props.source)).toBeGreaterThan(50)
  await ui.unmount()
})

test("a file the MR adds whole has no change marks: one colour from top to bottom marks nothing", async ($, on) => {
  const code = codeOf(200, [10, 10], { whole: 'added', rows: codeOf(200, [10, 10]).rows.map(r => ['+', null, r[2], r[3]]) })
  const w = world(on, [stateOf('t1', [topic('t1', 'open')])], { code: { stdout: JSON.stringify(code) } })
  await openReview($, w, view('t1'))
  const ui = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  expect((await specOf(ui, 'code'))?.changes).toBeUndefined()
  expect((await drawnBar(ui, 'code')).some(c => c.glyph === '▐')).toBe(false)
  await ui.unmount()
})

test("a thread on a line near the file's end is in view whenever its line is, scrolled to the end too", async ($, on) => {
  const code = codeOf(77, [73, 73])
  const thread = [{ url: 'u', file: 'src/cache.ts', line: 73, notes: [{ author: 'Kim', body: 'risky default', url: 'http://gl/x#note_2' }] }]
  const w = world(on, [stateOf('t1', [topic('t1', 'open')], { thread })], { code: { stdout: JSON.stringify(code) } })
  await openReview($, w, view('t1'))
  const ui = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  const md = async () => (await ui.findAll({ type: 'Markdown' })).map(m => m.props.text as string).join('\n')
  const lines = async () => (await ui.findAll({ type: 'Code' })).flatMap(c => String(c.props.source).split('\n').slice(1))
  await page(ui, 'code', 'up')                                              // the top: line 73 out of view
  expect(await lines()).not.toContain(' const line73 = 73')
  expect(await md()).not.toContain('risky default')
  await page(ui, 'code', 'down')                                            // the end: its line, and its thread
  expect(await lines()).toContain(' const line73 = 73')
  expect(await lines()).toContain(' const line77 = 77')
  expect(await md()).toContain('risky default')
  await ui.unmount()
})

test('a comment on a line the MR removed sits under that removed line, which keeps its old number', async ($, on) => {
  // Old line 23 removed; the line now numbered 23 is the one after it.
  const rows = [
    [' ', 21, 21, 'End-to-End'], [' ', 22, 22, ''], ['-', 23, null, 'NOTE: mocked API'], ['-', 24, null, ''],
    [' ', 25, 23, '[E2E Example Code]'], [' ', 26, 24, ''],
  ]
  const code = { file: 'docs/testing.md', side: 'old', line: 23, start: 23, moved: false, changed: [], at: [3, 3], rows }
  const thread = [{ url: 'u', file: 'docs/testing.md', line: 23, notes: [{ author: 'You', body: 'why was that deleted?', mine: true, url: 'http://gl/n1' }] }]
  const w = world(on, [stateOf('t1', [topic('t1', 'open')], { thread })], { code: { stdout: JSON.stringify(code) } })
  await openReview($, w, view('t1'))
  const ui = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  const sources = (await ui.findAll({ type: 'Code' })).map(c => String(c.props.source))
  expect(sources).toContain('@@ -23,1 +22,0 @@\n-NOTE: mocked API')
  expect(await ui.find({ type: 'Text', text: 'removed' })).toBeDefined()
  await ui.unmount()
})

test('"Check my post" with no comment on GitLab yet warns and says what to do', async ($, on) => {
  const none = world(on, [stateOf('t3', [topic('t3', 'draft', 'abc')])], {
    link: { exitCode: 1, stdout: '', stderr: 'error: no comment of yours on a.ts is on GitLab yet\n' },
  })
  await openReview($, none, view('t3'))
  const ui = await mountStep($, 'terminal')
  await ui.press({ key: 'link' })
  const warning = await ui.find({ type: 'Text', text: /^No comment of yours on a\.ts is on GitLab yet\. Post it there/ })
  expect(warning?.props.color).toBe('yellow')
  expect(await labels(ui)).toContain('Post')
  await ui.unmount()
})

test('"Check my post" with several comments of yours unlinked asks the model to link the right one', async ($, on) => {
  const many = world(on, [stateOf('t3', [topic('t3', 'draft', 'abc')])], {
    link: { exitCode: 1, stdout: '', stderr: 'error: 2 comments of yours on a.ts are not linked to a topic — pass the discussion id of the one for t3 (see `candidates`)\n' },
  })
  await openReview($, many, view('t3'))
  const ui = await mountStep($, 'terminal')
  await ui.press({ key: 'link' })
  await ui.unmount()
  expect(many.prompts).toEqual(['I posted my own comment for t3, and 2 comments of yours on a.ts are not linked to a topic: link the right one to t3.'])
})

test('"Check my post" links the comment, and the agent checks it before the round moves on', async ($, on) => {
  const w = world(on, [stateOf('t3', [topic('t3', 'draft', 'abc')]), stateOf('t3', [Object.assign(topic('t3', 'open'), { post_check: true })])])
  await openReview($, w, view('t3'))
  const ui = await mountStep($, 'terminal')
  await ui.press({ key: 'link' })
  expect(w.prompts).toHaveLength(1)
  expect(w.prompts[0]).toContain('Run `posted t3`')
  expect(await labels(ui)).toEqual(['Check again', 'Keep as posted', 'Next', 'Show sidebar'])
  await ui.press({ key: 'keep' })
  await ui.unmount()
  expect(w.ran.map(r => r.argv.slice(2).join(' '))).toContain('posted t3 --ok --iid 42')
})

test("rework-mr's comments on the MR itself: folded to their line with the count and what is new, open a box each", async ($, on) => {
  const long = `overall fine, ${'and some more words '.repeat(8)}`
  const state = JSON.stringify({
    skill: 'rework-mr', iid: 42, title: 'x', current: 't1', topics: [topic('t1', 'open')],
    thread: [], mr_comments: [
      { author: 'Kim', body: long, at: '2026-11-05T12:09:00', url: 'http://gl/x#note_7' },
      { author: 'Sam', body: 'agree', url: 'http://gl/x#note_8' }],
  })
  const w = world(on, [state], { code: { stdout: JSON.stringify(codeOf(50, [17, 17])) } })
  await openReview($, w, view('t1'))
  const ui = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  const md = async () => (await ui.findAll({ type: 'Markdown' })).map(m => String(m.props.text))
  const texts = async () => (await ui.findAll({ type: 'Text' })).map(t => String(t.text ?? ''))
  // Folded at first: the line alone, how many and how many are new.
  expect(await texts()).toEqual(expect.arrayContaining(['On the MR', ' · 2 comments', ' · 2 new']))
  expect((await md()).some(t => t.includes('and some more words'))).toBe(false)
  // Open: a box each, in full, with its own line for who wrote it and when, and new.
  await ui.press({ key: 'mr-fold' })
  expect((await md()).some(t => t.includes('and some more words and some more words'))).toBe(true)
  expect(await texts()).toContain('Nov 5, 12:09')
  expect((await texts()).filter(t => t === ' new').length).toBe(2)
  const boxes = (await ui.findAll({ type: 'Box' })).filter(b => String(b.props.key ?? '').startsWith('mr-box-'))
  expect(boxes.length).toBe(2)
  await ui.press({ key: 'mr-fold' })
  expect((await md()).some(t => t.includes('and some more words'))).toBe(false)
  await ui.unmount()
})

test("rework-mr: a thread under the code and the comments on the MR are drawn together, each row its own key", async ($, on) => {
  const block = 'see:\n\n```ts\nconst a = 1\n```'
  const state = JSON.stringify({
    skill: 'rework-mr', iid: 42, title: 'x', current: 't1', topics: [topic('t1', 'open')],
    thread: [{ url: 'u', file: 'src/cache.ts', line: 17, notes: [{ author: 'Kim', body: `odd, ${block}`, url: 'http://gl/x#note_2' }] }],
    mr_comments: [{ author: 'Sam', body: `overall, ${block}` }, { author: 'Jo', body: 'agree' }],
  })
  const w = world(on, [state], { code: { stdout: JSON.stringify(codeOf(50, [17, 17])) } })
  await openReview($, w, view('t1'))
  // Mounted at all: a key drawn twice has the engine refuse the whole sidebar.
  const ui = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  await ui.press({ key: 'mr-fold' })
  const md = (await ui.findAll({ type: 'Markdown' })).map(m => String(m.props.text))
  expect(md.some(t => t.startsWith('odd'))).toBe(true)
  expect(md.some(t => t.startsWith('overall'))).toBe(true)
  await ui.unmount()
})

// The box under a new topic's lines repeated its title, which the list and the card say
// already, and cut it. The place is marked by a bar in the gutter alone.
test("a topic's lines carry a bar in the gutter, in its severity's colour, and no box repeating its title", async ($, on) => {
  const w = world(on, [stateOf('t1', [topic('t1', 'draft')])], { code: { stdout: JSON.stringify(codeOf(50, [17, 18])) } })
  await openReview($, w, view('t1'))
  const ui = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  const runs = await ui.findAll({ type: 'Code' })
  expect(runs.some(c => firstLine(c.props.source) === 17)).toBe(true)
  // Over exactly its lines, one cell each, in the colour of the severity's icon in the list.
  const icon = (await ui.findAll({ type: 'Text' })).filter(t => t.text.trim() === '\u{f013f}')   // high: in the list alone
  expect(icon.length).toBe(1)
  const bars = (await ui.findAll({ type: 'Text' })).filter(t => t.text === '▌' && t.props.color === icon[0]?.props.color)
  expect(bars.length).toBe(2)
  // No title under the lines, and no background on them: on a diff the colours are the lines' own.
  expect((await ui.findAll({ type: 'Markdown' })).some(m => m.props.text === 'summary of t1')).toBe(false)
  expect(JSON.stringify(await ui.findAll({ type: 'Box' }))).not.toContain('#2b3a55')
  await ui.unmount()
})

test('a draft not posted yet stands under its lines, as the comment it will be', async ($, on) => {
  const w = world(on, [stateOf('t1', [topic('t1', 'draft', 'v1')], { draft_text: 'Nit: this map is rebuilt on every call.' })],
    { code: { stdout: JSON.stringify(codeOf(50, [17, 17])) } })
  await openReview($, w, view('t1', 'v1'))
  const ui = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  expect((await ui.findAll({ type: 'Markdown' })).some(m => String(m.props.text).includes('this map is rebuilt on every call'))).toBe(true)
  // Not on GitLab yet, which every other box in the sidebar is: dashed, and headed so in
  // place of who wrote it and when.
  const texts = (await ui.findAll({ type: 'Text' })).map(t => t.text)
  expect(texts.some(t => /^╭╌+╮$/.test(t))).toBe(true)
  expect(texts.some(t => /^╰╌+╯$/.test(t))).toBe(true)
  expect(texts.some(t => t.startsWith('┆'))).toBe(true)
  expect(texts.join('\n')).toContain('Draft · not posted')
  expect(texts).not.toContain('You')
  await ui.unmount()
})

test("a draft's box in the sidebar follows the stored draft, as its card does", async ($, on) => {
  const w = world(on, [
    stateOf('t1', [topic('t1', 'draft', 'v1')], { draft_text: 'Nit: this map is rebuilt on every call.' }),
    stateOf('t1', [topic('t1', 'draft', 'v2')], { draft_text: 'Could this map be built once?' }),
  ], { code: { stdout: JSON.stringify(codeOf(50, [17, 17])) } })
  await openReview($, w, view('t1', 'v1'))
  const md = async (ui: { findAll: (q: { type: string }) => Promise<{ props: Record<string, unknown> }[]> }) =>
    (await ui.findAll({ type: 'Markdown' })).map(m => String(m.props.text)).join('\n')
  let ui = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  expect(await md(ui)).toContain('this map is rebuilt')
  await ui.unmount()
  // The draft reworded: the script's echo of it is a view, which reads the state again.
  w.move()
  w.tool.stdout = view('t1', 'v2')
  await $.tool.call({ tool: 'Bash', command: "python3 $SD/findings.py set t1 --draft 'Could this map be built once?' --iid 42", tool_use_id: 'tu-2' } as never)
  ui = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  expect(await md(ui)).toContain('Could this map be built once?')
  expect(await md(ui)).not.toContain('this map is rebuilt')
  await ui.unmount()
})

// What a comment is about is as often at a line's end as at its start: cut at the edge, the
// `.catch` a verdict spoke of was the "…".
test('the lines a topic is about wrap where the rest of the file is cut at the edge', async ($, on) => {
  const code = codeOf(30, [5, 5])
  ;(code.rows[4] as unknown[])[3] = `const loaded = await ${'load'.repeat(60)}().catch(() => null)`
  const w = world(on, [stateOf('t1', [topic('t1', 'open')], { thread: [{ url: 'u', file: 'src/cache.ts', line: 5, notes: [{ author: 'You', body: 'x', mine: true }] }] })],
    { code: { stdout: JSON.stringify(code) } })
  await openReview($, w, view('t1'))
  const ui = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  const runs = await ui.findAll({ type: 'Code' })
  const about = runs.find(c => String(c.props.source).includes('.catch(() => null)'))
  expect(about?.props.wrap).toBe('wrap')
  expect(runs.filter(c => c !== about).every(c => c.props.wrap === 'truncate-end')).toBe(true)
  // The bar runs down every row the wrapped line takes.
  const icon = (await ui.findAll({ type: 'Text' })).find(t => t.text.trim() === '\u{f013f}')
  const bars = (await ui.findAll({ type: 'Text' })).filter(t => t.text === '▌' && t.props.color === icon?.props.color)
  expect(bars.length).toBeGreaterThan(1)
  await ui.unmount()
})

test('with every topic handled, the band says the review waits and the sidebar shows when GitLab is checked', async ($, on) => {
  const done = stateOf(null, [topic('t1', 'open'), topic('t2', 'open'), { ...topic('t3', 'open'), state: 'done' }])
  const w = world(on, [done], { refresh: { stdout: done } })
  w.clock.now = new Date(2026, 10, 5, 12, 27).getTime()
  await openReview($, w, view('t1'))
  const band = await mountStep($, 'terminal')
  expect(await labels(band)).toEqual(['Show sidebar'])
  expect(await band.find({ type: 'Text', text: /Done for now: 2 topics wait on the author\.$/ })).toBeDefined()
  await band.unmount()
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  expect(await panel.find({ type: 'Text', text: '2 topics wait on the author' })).toBeDefined()
  // Nothing flagged new: the box says so, above what waits.
  expect(await panel.find({ type: 'Text', text: 'No news' })).toBeDefined()
  // Before the first tick no check is scheduled: nothing says one.
  expect(await panel.find({ type: 'Text', text: /next check|–/i })).toBeUndefined()
  await panel.press({ key: 'check-now' })
  expect(w.ran.map(r => r.argv[2])).toContain('refresh')
  // When GitLab is asked next is said from now, the news lines keep their clock times.
  expect(await panel.find({ type: 'Text', text: /checked/ })).toBeUndefined()
  await panel.unmount()
})

test('a status line starts with a capital, but for a topic handle', () => {
  expect([sentenceOf('merged t2 into t3'), sentenceOf('t4: opened the fix'), sentenceOf('Checking GitLab')])
    .toEqual(['Merged t2 into t3', 't4: opened the fix', 'Checking GitLab'])
})

test('the next check is said from now: whole minutes, then seconds, then now', () => {
  const now = 1_000_000
  expect([untilOf(now + 150_000, now), untilOf(now + 60_000, now), untilOf(now + 4_200, now), untilOf(now - 1, now), untilOf(null, now)])
    .toEqual(['in 3 min', 'in 1 min', 'in 5 s', 'now', '–'])
})

test('while your comments are matched to the topics, nothing says the review is done', async ($, on) => {
  const matching = stateOf(null, [topic('t1', 'draft'), topic('t2', 'open')], { own_review: 'matching', own_threads: [] })
  const w = world(on, [matching])
  await openReview($, w, view('t1'))
  const band = await mountStep($, 'terminal')
  expect(await band.find({ type: 'Text', text: /^Done/ })).toBeUndefined()
  await band.unmount()
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  expect(await panel.find({ type: 'Text', text: /Done for now|wait on the author/ })).toBeUndefined()
  await panel.unmount()
})

test("a topic opened only to look at closes by the file's ✕ or a second press on its row", async ($, on) => {
  // No round runs: the topic was opened to look at, and the ✕ closes it.
  const opened = stateOf('t1', [topic('t1', 'open'), topic('t2', 'open')], { in_round: false })
  const left = stateOf(null, [topic('t1', 'open'), topic('t2', 'open')], { in_round: false })
  const w = world(on, [opened, left], { code: { stdout: JSON.stringify(codeOf(50, [17, 17])) } })
  await openReview($, w, view('t1'))
  const ui = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  const close = await ui.find({ type: 'Button', key: 'leave' })
  expect(close?.props.label).toBe('\u{F0156}')
  expect(close?.props.hotkey).toBeUndefined()                       // a click, as a window's
  await ui.press({ key: 'leave' })
  expect(w.ran.map(r => r.argv[2])).toContain('leave')
  expect(await ui.find({ type: 'Text', text: '2 topics wait on the author' })).toBeDefined()
  // Said once: the title does not repeat on whom the topics wait.
  expect(await ui.find({ type: 'Text', text: 'Done for now' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /^Waiting for/ })).toBeUndefined()
  await ui.unmount()
})

test("while the round has another topic the file has no ✕: leaving would only bring that one up, the card's Next", async ($, on) => {
  const w = world(on, [stateOf('t1', [topic('t1', 'open'), topic('t2', 'open')], { in_round: true, round: ['t1', 't2'] })],
    { code: { stdout: JSON.stringify(codeOf(50, [17, 17])) } })
  await openReview($, w, view('t1'))
  const ui = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  expect(await ui.find({ type: 'Button', key: 'leave' })).toBeUndefined()
  await ui.unmount()
})

test("another reviewer's thread opened to know of closes by the file's ✕ too", async ($, on) => {
  const peer = { ...topic('t5', 'acked'), source: 'peer', peer: true, peer_view: { says: 'fyi' as const, advice: 'seen' as const } }
  const w = world(on, [stateOf('t5', [topic('t1', 'open'), peer], { in_round: false })], { code: { stdout: JSON.stringify(codeOf(50, [10, 10])) } })
  await openReview($, w, view('t5'))
  const ui = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  expect((await ui.find({ type: 'Button', key: 'leave' }))?.props.label).toBe('\u{F0156}')
  await ui.unmount()
})

test('pressing the row of a topic opened only to look at closes it', async ($, on) => {
  const w = world(on, [stateOf('t1', [topic('t1', 'open'), topic('t2', 'open')], { in_round: false }), stateOf(null, [topic('t1', 'open'), topic('t2', 'open')], { in_round: false })],
    { code: { stdout: JSON.stringify(codeOf(50, [17, 17])) } })
  await openReview($, w, view('t1'))
  const ui = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  await ui.press({ key: 'row-t1' })
  expect(w.ran.map(r => r.argv[2])).toContain('leave')
  await ui.unmount()
})

test('a topic that needs you has no close', async ($, on) => {
  const w = world(on, [stateOf('t1', [topic('t1', 'draft')])], { code: { stdout: JSON.stringify(codeOf(50, [17, 17])) } })
  await openReview($, w, view('t1'))
  const ui = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  expect(await ui.find({ type: 'Button', key: 'leave' })).toBeUndefined()
  await ui.press({ key: 'row-t1' })
  expect(w.ran.map(r => r.argv[2])).not.toContain('leave')
  await ui.unmount()
})

test('a thread in full gives each comment a line of its own for its link, author and time', async ($, on) => {
  const long = `seems bad, ${'and more words '.repeat(20)}`
  const thread = [{ url: 'u', file: 'src/cache.ts', line: 17, notes: [
    { author: 'Kim', body: long, url: 'http://gl/x#note_2', at: '2026-11-05T12:09:00' },
  ] }]
  const w = world(on, [stateOf('t1', [topic('t1', 'open')], { thread })], { code: { stdout: JSON.stringify(codeOf(50, [17, 17])) } })
  await openReview($, w, view('t1'))
  const ui = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  // One line of text, the link inline, the time in it dim.
  expect((await ui.findAll({ type: 'Text' })).find(t => t.text === 'Nov 5, 12:09')?.props.color).toBe('inactive')
  expect((await ui.findAll({ type: 'Link' })).map(l => l.props.href)).toEqual(['http://gl/x#note_2'])
  expect((await ui.findAll({ type: 'Markdown' })).some(m => String(m.props.text).startsWith('seems bad, and more words'))).toBe(true)
  await ui.press({ key: 'thread-open' })
  expect(await ui.find({ type: 'Text', text: 'Nov 5, 12:09' })).toBeUndefined()      // folded: no time
  await ui.unmount()
})

const cardRow = (id: string, stdout: string) => ({
  plugin: 'review-ui', component: 'ToolUse', surface: 'terminal',
  props: { tool_use_id: id, tool: 'Bash', input: { command: 'python3 $SD/findings.py present --iid 42' }, output: { stdout, stderr: '', interrupted: false }, isRunning: false, isErrored: false },
}) as never

test("the step's buttons sit on the newest card, without keys, and the band keeps the rest", async ($, on) => {
  const w = world(on, [stateOf('t1', [topic('t1', 'draft')])])
  await openReview($, w, view('t1'))
  const card = await $.ui.mount(cardRow('tu-open', view('t1')))
  expect(await labels(card)).toEqual(['Comment', 'Drop', 'Explain', 'Next'])
  expect((await card.findAll({ type: 'Button' })).every(b => b.props.hotkey === undefined)).toBe(true)
  await card.unmount()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await labels(band)).toEqual(['Show sidebar'])
  await band.unmount()
  // A newer card takes them: the first one is history now.
  await $.tool.call({ tool: 'Bash', command: 'python3 $SD/findings.py quote t1 --focus --iid 42', tool_use_id: 'tu-2' } as never)
  const old = await $.ui.mount(cardRow('tu-open', view('t1')))
  expect(await labels(old)).toEqual([])
  await old.unmount()
  const now = await $.ui.mount(cardRow('tu-2', view('t1')))
  expect(await labels(now)).toEqual(['Comment', 'Drop', 'Explain', 'Next'])
  await now.unmount()
})

test("a pushed fix's card leaves the diff viewer's key to the sidebar beside it, and keeps it where there is none", async ($, on) => {
  const code = codeOf(20, [3, 3], { mode: 'pushed', file: 'src/a.ts', commented: 'src/a.ts', fix_files: [['src/a.ts', 2, 1], ['src/b.ts', 1, 0]] })
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'reply', { pushed: true, fix_kept: true })])], { code: { stdout: JSON.stringify(code) } })
  await openRework($, w, reworkView('t1'))
  const at = { viewport: { columns: 120, rows: 50, isFullscreen: true } }
  w.panes.push('review-ui-topics')
  const beside = await $.ui.mount({ ...(cardRow('tu-open', reworkView('t1')) as object), ...at } as never)
  expect(await labels(beside)).not.toContain('Show fix in hunk')
  expect(await labels(beside)).toContain('“Fixed” + resolve')
  await beside.unmount()
  w.panes.splice(0)                                   // the sidebar closed: the card is the one place for it
  const alone = await $.ui.mount({ ...(cardRow('tu-open', reworkView('t1')) as object), ...at } as never)
  expect(await labels(alone)).toContain('Show fix in hunk')
  await alone.unmount()
})

test('the recommended answer is the first button and drawn as the primary, on a card and in the band', async ($, on) => {
  const w = world(on, [stateOf('t1', [topic('t1', 'draft')])])
  await openReview($, w, view('t1'))
  // Every step button is a chip: its icon and label on a fill, the recommended one's the
  // accent fill, every other one the muted fill.
  const fillOf = async (ui: { findAll: (q: { type: string }) => Promise<{ props: Record<string, unknown> }[]> }) =>
    (await ui.findAll({ type: 'Text' })).map(t => t.props.backgroundColor).filter(Boolean)
  const card = await $.ui.mount(cardRow('tu-open', view('t1')))
  const onCard = await card.findAll({ type: 'Button' })
  expect(onCard[0]?.props.label).toBe('Comment')
  const fills = await fillOf(card)
  expect(fills[0]).not.toBe(fills[1])
  expect(new Set(fills.slice(1)).size).toBe(1)
  expect(JSON.stringify(await card.findAll({ type: 'Text' }))).toContain('\u{F03EB} Comment')
  await card.unmount()
  // A step with no card of its own keeps them in the band, drawn the same way.
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  const first = (await band.findAll({ type: 'Button' }))[0]
  if (first?.props.label === 'Comment') expect((await fillOf(band))[0]).toBe(fills[0])
  await band.unmount()
})

test("a step whose card is not the newest one keeps its buttons in the band", async ($, on) => {
  // The newest card shows t1, while t2 is current: t2's buttons stay in the band.
  const w = world(on, [stateOf('t2', [topic('t1', 'draft'), topic('t2', 'draft')])])
  await openReview($, w, view('t1'))
  const card = await $.ui.mount(cardRow('tu-open', view('t1')))
  expect(await labels(card)).toEqual([])
  await card.unmount()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await labels(band)).toEqual(['Comment', 'Drop', 'Explain', 'Next', 'Show sidebar'])
  await band.unmount()
})

test('after a card, the text the model writes is drawn as it wrote it, after a press for work too', async ($, on) => {
  const w = world(on, [stateOf('t1', [topic('t1', 'draft')])])
  await openReview($, w, view('t1'))
  await $.prompt.submit({ text: 'Make the fix for t1 as agreed', origin: { kind: 'plugin' } } as never)
  await $.tool.call({ tool: 'Bash', command: 'python3 $SD/findings.py quote t1 --focus --iid 42', tool_use_id: 'tu-2' } as never)
  const ui = await $.ui.mount({ plugin: 'review-ui', component: 'AssistantMessage', surface: 'terminal', props: { text: 'The repo has no checks. Nothing is pushed.', isFirstOfReply: true } } as never)
  expect(await ui.find({ type: 'Text', text: 'engine' })).toBeDefined()
  await ui.unmount()
})

test("beside the sidebar, the current topic's card leaves its code to the sidebar, however narrow the chat", async ($, on) => {
  const excerpt = '\u{f0182} **t1** **summary of t1** · `src/cache.ts:17`\n\n_Code currently in MR — `src/cache.ts:17`:_\n\n```ts\nconst x = 1\n```\n\nthe analysis'
  const out = excerpt + '\n\n<!-- paste-gate:critical\n' + JSON.stringify({ first: excerpt.split('\n')[0], critical: [], topic: 't1', focus: 't1', review: REVIEW }) + '\n-->'
  const w = world(on, [stateOf('t1', [topic('t1', 'draft')])])
  await openReview($, w, out)
  w.panes.push('review-ui-topics')
  for (const columns of [150, 80]) {
    const ui = await $.ui.mount({ ...(cardRow('tu-open', out) as object), viewport: { columns, rows: 50, isFullscreen: true } } as never)
    const md = (await ui.findAll({ type: 'Markdown' })).map(m => m.props.text as string).join('\n')
    expect([columns, md.includes('const x = 1')]).toEqual([columns, false])
    expect(md).toContain('the analysis')
    await ui.unmount()
  }
  w.panes.splice(0)                                   // the sidebar closed: the card keeps its code, not its label
  const shut = await $.ui.mount({ ...(cardRow('tu-open', out) as object), viewport: { columns: 80, rows: 50, isFullscreen: true } } as never)
  const text = (await shut.findAll({ type: 'Markdown' })).map(m => m.props.text as string).join('\n')
  expect(text).toContain('const x = 1')
  expect(text).not.toContain('Code currently in MR')
  await shut.unmount()
})

test("beside the sidebar, the current topic's card leaves its thread to the sidebar's box; elsewhere it keeps it", async ($, on) => {
  const head = '\u{f0182} **t1** **summary of t1** · `src/cache.ts:17`'
  const body = `${head}\n\n> [\u{f03cc}](http://gitlab.test/x#note_1) **Ana** _Nov 5, 11:38_\n> please rename it\n>\n> and test it\n\n_… 1 more …_\n\n> [\u{f03cc}](http://gitlab.test/x#note_3) **You** _Nov 5, 12:00_\n> done\n\n**Analysis**\n\nthe analysis`
  const out = body + '\n\n<!-- paste-gate:critical\n' + JSON.stringify({ first: head, critical: [], topic: 't1', focus: 't1', review: REVIEW }) + '\n-->'
  const thread = [{ url: 'http://gitlab.test/x#note_1', file: 'src/cache.ts', line: 17, notes: [{ author: 'Ana', body: 'please rename it' }] }]
  const w = world(on, [stateOf('t1', [topic('t1', 'needs_ack')], { thread })])
  await openReview($, w, out)
  const textOf = async () => {
    const ui = await $.ui.mount({ ...(cardRow('tu-open', out) as object), viewport: { columns: 120, rows: 50, isFullscreen: true } } as never)
    const md = (await ui.findAll({ type: 'Markdown' })).map(m => m.props.text as string).join('\n')
    const authors = (await ui.findAll({ type: 'Text' })).map(t => t.text ?? '').join('\n')
    await ui.unmount()
    return { md, authors }
  }
  w.panes.push('review-ui-topics')
  const beside = await textOf()
  expect([beside.md.includes('please rename it'), beside.md.includes('1 more'), beside.authors.includes('Ana')]).toEqual([false, false, false])
  expect(beside.md).toContain('the analysis')
  w.panes.splice(0)                                   // the sidebar closed: the card is the one place to read it
  expect((await textOf()).md).toContain('please rename it')
})

test('beside the sidebar, a waiting topic\'s card that only held its code and thread says where it stands, nothing else', async ($, on) => {
  const head = '\u{f0182} **t1** **summary of t1** · `src/cache.ts:17`'
  const body = `${head}\n\n_Code currently in MR — \`src/cache.ts:17\`:_\n\n\`\`\`ts\nconst x = 1\n\`\`\`\n\n> [\u{f03cc}](http://gitlab.test/x#note_1) **Ana** _Nov 5, 11:38_\n> please rename it`
  const out = body + '\n\n<!-- paste-gate:critical\n' + JSON.stringify({ first: head, critical: [], topic: 't1', focus: 't1', review: REVIEW }) + '\n-->'
  const thread = [{ url: 'http://gitlab.test/x#note_1', file: 'src/cache.ts', line: 17, notes: [{ author: 'Ana', body: 'please rename it' }] }]
  const w = world(on, [stateOf('t1', [topic('t1', 'open')], { thread })])
  await openReview($, w, out)
  w.panes.push('review-ui-topics')
  // Not the step's host: the buttons are on a newer card.
  const ui = await $.ui.mount({ ...(cardRow('tu-other', out) as object), viewport: { columns: 120, rows: 50, isFullscreen: true } } as never)
  // Its title and where it is, then the one line; no code, no thread.
  expect((await ui.findAll({ type: 'Markdown' })).map(m => m.props.text)).toEqual(['`src/cache.ts:17`'])
  expect((await ui.findAll({ type: 'Text' })).map(t => String(t.text ?? '')).filter(t => /^Waiting for /.test(t))).toEqual(['Waiting for the author.'])
  await ui.unmount()
})

// Another reviewer's thread closed for the person is still open on GitLab: its card, left in
// the chat, says whose it is to settle; "Closed" read as resolved. Once resolved on GitLab it
// is no longer one the person watches, and reads Closed as any other.
for (const [extra, said] of [[{ by: 'Sam' }, "Sam's to settle."], [{}, "Its reviewer's to settle."], [{ peer: false }, 'Closed.']] as const) {
  test(`another reviewer's thread closed for you reads ${said}`, async ($, on) => {
    const head = '\u{f0182} **t4** **summary of t4** · `src/cache.ts:17`'
    const body = `${head}\n\n> [\u{f03cc}](http://gitlab.test/x#note_1) **Sam** _Nov 5, 11:38_\n> why a map here?`
    const out = body + '\n\n<!-- paste-gate:critical\n' + JSON.stringify({ first: head, critical: [], topic: 't4', focus: 't4', review: REVIEW }) + '\n-->'
    const peer = { ...topic('t4', 'acked'), source: 'peer', peer: true, peer_view: { says: 'fyi' as const, advice: 'seen' as const } }
    const w = world(on, [stateOf('t1', [topic('t1', 'needs_ack'), { ...peer, ...extra }])])
    await openReview($, w, view('t1'))
    w.panes.push('review-ui-topics')
    const ui = await $.ui.mount({ ...(cardRow('tu-other', out) as object), viewport: { columns: 120, rows: 50, isFullscreen: true } } as never)
    expect((await ui.findAll({ type: 'Text' })).map(t => String(t.text ?? '')).filter(t => /settle\.$|^Closed\.$/.test(t))).toEqual([said])
    await ui.unmount()
  })
}

// The card itself ends on it (the script's story line), live or dimmed: never said twice.
test("another reviewer's card that says whose it is to settle gets no second line", async ($, on) => {
  const head = '\u{f0182} **t4** **summary of t4** · `src/cache.ts:17`'
  const body = `${head}\n\n> [\u{f03cc}](http://gitlab.test/x#note_1) **Sam** _Nov 5, 11:38_\n> why a map here?\n\n\u{f051f} Sam's to settle`
  const out = body + '\n\n<!-- paste-gate:critical\n' + JSON.stringify({ first: head, critical: [], topic: 't4', focus: 't4', review: REVIEW }) + '\n-->'
  const peer = { ...topic('t4', 'acked'), source: 'peer', peer: true, by: 'Sam', peer_view: { says: 'fyi' as const, advice: 'seen' as const } }
  const w = world(on, [stateOf('t1', [topic('t1', 'needs_ack'), peer])])
  await openReview($, w, view('t1'))
  w.panes.push('review-ui-topics')
  const ui = await $.ui.mount({ ...(cardRow('tu-other', out) as object), viewport: { columns: 120, rows: 50, isFullscreen: true } } as never)
  const texts = (await ui.findAll({ type: 'Text' })).map(t => String(t.text ?? ''))
  expect(texts.some(t => t.includes("Sam's to settle"))).toBe(true)
  expect(texts.filter(t => /to settle\.$/.test(t))).toEqual([])          // no line of the mod's own
  await ui.unmount()
})

test('beside the sidebar, a closed topic\'s card says where it stands, and keeps its thread out once another topic is current', async ($, on) => {
  const head = '\u{f0182} **t1** **summary of t1** · `src/cache.ts:17`'
  const body = `${head}\n\n_Code currently in MR — \`src/cache.ts:17\`:_\n\n\`\`\`ts\nconst x = 1\n\`\`\`\n\n> [\u{f03cc}](http://gitlab.test/x#note_1) **Ana** _Nov 5, 11:38_\n> please rename it`
  const out = body + '\n\n<!-- paste-gate:critical\n' + JSON.stringify({ first: head, critical: [], topic: 't1', focus: 't1', review: REVIEW }) + '\n-->'
  const w = world(on, [stateOf('t2', [topic('t1', 'acked'), topic('t2', 'draft')])])
  await openReview($, w, out)
  w.panes.push('review-ui-topics')
  const ui = await $.ui.mount({ ...(cardRow('tu-other', out) as object), viewport: { columns: 120, rows: 50, isFullscreen: true } } as never)
  const texts = (await ui.findAll({ type: 'Text' })).map(t => String(t.text ?? ''))
  expect(texts.some(t => /^Closed\.$/.test(t))).toBe(true)
  expect(texts.some(t => /in the sidebar/.test(t))).toBe(false)      // true only while it is current
  expect(JSON.stringify(await ui.findAll({ type: 'Markdown' }))).not.toContain('please rename it')
  await ui.unmount()
})

test("a topic opened again to look at has one card: the older ones shrink to their title line", async ($, on) => {
  const head = '\u{f0182} **t1** **summary of t1** · `src/cache.ts:17`'
  const out = `${head}\n\nsome view\n\n<!-- paste-gate:critical\n` + JSON.stringify({ first: head, critical: [], topic: 't1', focus: 't1', review: REVIEW }) + '\n-->'
  // Opened from the sidebar's Closed group: current, and closed.
  const w = world(on, [stateOf('t1', [topic('t1', 'acked'), topic('t2', 'draft')])])
  await openReview($, w, out)
  await $.tool.call({ tool: 'Bash', command: 'python3 $SD/findings.py quote t1 --iid 42', tool_use_id: 'tu-again' } as never)
  const texts = async (id: string) => {
    const ui = await $.ui.mount(cardRow(id, out))
    const all = [...(await ui.findAll({ type: 'Text' })).map(t => String(t.text ?? '')), ...(await ui.findAll({ type: 'Markdown' })).map(m => String(m.props.text))]
    await ui.unmount()
    return all
  }
  const older = await texts('tu-open')
  expect(older.some(t => t.includes('summary of t1'))).toBe(true)
  expect(older.some(t => t.includes('some view'))).toBe(false)
  const newer = await texts('tu-again')
  expect(newer.some(t => t.includes('some view'))).toBe(true)
})

test("a card's steps draw their times dim in one column, and the person as You in their colour", async ($, on) => {
  // Dim by the theme's grey as a colour, not by `dimColor`: a card with a hover control on it
  // (a draft's Copy) is repainted under the pointer, and that repaint dropped the dim.
  const head = '\u{f0182} **t1** **summary of t1** · `src/cache.ts:17`'
  const body = `${head}\n\n13:55 \u{F012C} You posted the initial comment · [thread \u{F03CC}](http://gl/n1)\nNov 5, 09:02 \u{F012C} Robin pushed a fix\n\u{F051F} Waits on Robin`
  const out = body + '\n\n<!-- paste-gate:critical\n' + JSON.stringify({ first: head, critical: [], topic: 't1', focus: 't1', review: REVIEW }) + '\n-->'
  const w = world(on, [stateOf('t1', [topic('t1', 'open')])])
  await openReview($, w, out)
  const ui = await $.ui.mount(cardRow('tu-open', out))
  const texts = await ui.findAll({ type: 'Text' })
  const times = texts.filter(t => t.props.color === 'inactive' && /^\s*(\d\d:\d\d|Nov 5, 09:02)?$/.test(String(t.text ?? ''))).map(t => String(t.text))
  // To the column's right: the clock of today's time under the clock of the dated one.
  expect(times).toEqual(['       13:55', 'Nov 5, 09:02', '            '])
  expect(texts.some(t => String(t.text) === 'You' && t.props.color === 'magenta')).toBe(true)
  await ui.unmount()
})

test("no dim text on a card is drawn with dimColor, which lights up under the pointer: older cards and the newest", async ($, on) => {
  // The engine repaints a card as the pointer moves over it, and that repaint drops
  // `dimColor` and keeps colours: dim text is the theme's grey as a colour, everywhere on a card.
  const card = (tid: string) => {
    const head = `\u{f0182} **${tid}** **summary of ${tid}** · \`src/cache.ts:17\``
    const body = `${head}\n\n**\u{f11a7} Agreed plan: Fix**\n\nRename it.\n\n**\u{f189b} Changes**  \u{f4d2} 5 files +18 −15  (2 commits rewritten)\n\n` +
      'Renamed `queryKeys` (t1).\n\n\u{f071b} `f75def5` extract keys\n\u{f071b} `e15b6a1` use the keys\n\n' +
      '13:55 \u{F012C} You agreed the plan · [thread \u{F03CC}](http://gl/n1)\n\u{F051F} Waits on Robin'
    return body + '\n\n<!-- paste-gate:critical\n' + JSON.stringify({ first: head, critical: [], topic: tid, focus: tid, review: REVIEW }) + '\n-->'
  }
  const w = world(on, [stateOf('t1', [topic('t1', 'open'), topic('t2', 'open')])])
  await openReview($, w, card('t1'))
  await $.tool.call({ tool: 'Bash', command: 'python3 $SD/findings.py quote t1 --iid 42', tool_use_id: 'tu-again' } as never)
  for (const [id, out] of [['tu-open', card('t1')], ['tu-again', card('t1')], ['tu-other', card('t2')]] as const) {
    const ui = await $.ui.mount(cardRow(id, out))
    const lit = (await ui.findAll({ type: 'Text' })).filter(t => t.props.dimColor === true).map(t => String(t.text))
    expect([id, lit]).toEqual([id, []])
    expect((await ui.findAll({ type: 'Box' })).some(b => b.props.borderDimColor === true)).toBe(false)
    await ui.unmount()
  }
})

test("a topic still being decided has one card too: the newest holds it, the older one is its title line", async ($, on) => {
  const head = '\u{f0182} **t1** **summary of t1** · `src/cache.ts:17`'
  const out = `${head}\n\nsome view\n\n<!-- paste-gate:critical\n` + JSON.stringify({ first: head, critical: [], topic: 't1', focus: 't1', review: REVIEW }) + '\n-->'
  const w = world(on, [stateOf('t1', [topic('t1', 'needs_ack')])])
  await openReview($, w, out)
  await $.tool.call({ tool: 'Bash', command: 'python3 $SD/findings.py quote t1 --iid 42', tool_use_id: 'tu-again' } as never)
  const ui = await $.ui.mount(cardRow('tu-open', out))
  expect(JSON.stringify(await ui.findAll({ type: 'Markdown' }))).not.toContain('some view')
  // One line, nothing under it: no buttons, no ✕, no text of the topic's.
  expect((await ui.findAll({ type: 'Text' })).filter(t => String(t.text ?? '').trim()).length).toBeLessThanOrEqual(3)
  expect(await ui.find({ type: 'Button' })).toBeUndefined()
  await ui.unmount()
  const newest = await $.ui.mount(cardRow('tu-again', out))
  expect(JSON.stringify(await newest.findAll({ type: 'Markdown' }))).toContain('some view')
  await newest.unmount()
})

test("a view of a topic that is not its card (a diff on its own) leaves the card above it whole", async ($, on) => {
  const head = '\u{f0182} **t1** **summary of t1** · `src/cache.ts:17`'
  const out = `${head}\n\nsome view\n\n<!-- paste-gate:critical\n` + JSON.stringify({ first: head, critical: [], topic: 't1', focus: 't1', review: REVIEW }) + '\n-->'
  const diff = '```diff\n-a\n+b\n```\n\n<!-- paste-gate:critical\n' + JSON.stringify({ first: '```diff', critical: [], topic: 't1', focus: 't1', review: REVIEW }) + '\n-->'
  const w = world(on, [stateOf('t1', [topic('t1', 'needs_ack')])])
  await openReview($, w, out)
  w.tool.stdout = diff
  await $.tool.call({ tool: 'Bash', command: 'python3 $SD/findings.py diff t1 --show --iid 42', tool_use_id: 'tu-diff' } as never)
  const ui = await $.ui.mount(cardRow('tu-open', out))
  expect(JSON.stringify(await ui.findAll({ type: 'Markdown' }))).toContain('some view')
  await ui.unmount()
})

test("a card of a topic that is not current shows no code, sidebar or not", async ($, on) => {
  const excerpt = '\u{f0182} **t1** **summary of t1** · `src/cache.ts:17`\n\n_Code currently in MR — `src/cache.ts:17`:_\n\n```ts\nconst x = 1\n```\n\nthe analysis'
  const out = excerpt + '\n\n<!-- paste-gate:critical\n' + JSON.stringify({ first: excerpt.split('\n')[0], critical: [], topic: 't1', focus: 't1', review: REVIEW }) + '\n-->'
  // t1's card is history: t2 is current now.
  const w = world(on, [stateOf('t2', [topic('t1', 'dropped'), topic('t2', 'draft')])])
  await openReview($, w, out)
  for (const isOpen of [true, false]) {
    if (isOpen) w.panes.push('review-ui-topics')
    else w.panes.splice(0)
    const ui = await $.ui.mount({ ...(cardRow('tu-open', out) as object), viewport: { columns: 120, rows: 50, isFullscreen: true } } as never)
    const text = (await ui.findAll({ type: 'Markdown' })).map(m => m.props.text as string).join('\n')
    expect([isOpen, text.includes('const x = 1'), text.includes('Code currently in MR')]).toEqual([isOpen, false, false])
    expect(text).toContain('the analysis')
    await ui.unmount()
  }
})

test('a file the MR adds whole is drawn plain, marked new beside its name; an edited one keeps its colours', async ($, on) => {
  const added = codeOf(30, [10, 10], { whole: 'added', rows: Array.from({ length: 30 }, (_, i) => ['+', null, i + 1, `const line${i + 1} = ${i + 1}`]) })
  const w = world(on, [stateOf('t1', [topic('t1', 'draft')])], { code: { stdout: JSON.stringify(added) } })
  await openReview($, w, view('t1'))
  const ui = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  const sources = (await ui.findAll({ type: 'Code' })).map(c => String(c.props.source))
  expect(sources.length).toBeGreaterThan(0)
  expect(sources.every(src => src.split('\n').slice(1).every(l => l.startsWith(' ')))).toBe(true)
  expect(sources.some(src => src.includes('@@ -10,1 +10,1 @@\n const line10 = 10'))).toBe(true)   // numbered as the file is
  expect(await ui.find({ type: 'Text', text: 'new' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'deleted' })).toBeUndefined()
  await ui.unmount()
})

test('a file the MR only edits keeps its marks and says nothing beside its name', async ($, on) => {
  const edited = codeOf(5, [2, 2], { rows: [[' ', 1, 1, 'a'], ['-', 2, null, 'b'], ['+', null, 2, 'B'], [' ', 3, 3, 'c']] })
  const w = world(on, [stateOf('t1', [topic('t1', 'draft')])], { code: { stdout: JSON.stringify(edited) } })
  await openReview($, w, view('t1'))
  const ui = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  const all = (await ui.findAll({ type: 'Code' })).map(c => String(c.props.source)).join('\n')
  expect(all).toContain('+B')
  expect(all).toContain('-b')
  expect(await ui.find({ type: 'Text', text: 'new' })).toBeUndefined()
  await ui.unmount()
})

// A reopened thread to plan again while the fix pass runs: the press beside its news opened
// the fix pass's own first topic, one with no news, under another name.
test('rework-mr: the box opens the first topic flagged new, named after it, not the round\'s first', async ($, on) => {
  const t1 = { ...reworkTopic('t1', 'plan'), inbox: 'new', news: ['reply'], news_at: '2026-11-09T12:36:00Z' }
  const idle = reworkState(null, [t1, reworkTopic('t2', 'reply')], {
    in_round: false, pass: 'fix', round: ['t2', 't1'], inbox: inbox({ replies: ['t1'], by: { t1: 'Miriam' }, first: 't2' }) })
  const opened = reworkState('t1', [{ ...t1, inbox: 'yours' }, reworkTopic('t2', 'reply')], { in_round: true, pass: 'fix', round: ['t1', 't2'], inbox: inbox() })
  const w = world(on, [idle, opened])
  await openRework($, w, reworkView('t1'))
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  expect((await panel.find({ type: 'Button', key: 'open-round' }))?.props.label).toBe('Open t1')
  await panel.press({ key: 'open-round' })
  await panel.unmount()
  expect(w.ran.map(r => r.argv.slice(2).join(' '))).toContain('open t1 --iid 42')
  // No news: the button names the work, and the script's round says where it starts.
  const quiet = JSON.parse(reworkState(null, [reworkTopic('t1', 'fix'), reworkTopic('t2', 'reply')], { pass: 'fix', round: ['t1', 't2'], inbox: inbox({ first: 't1' }) }))
  expect([inboxOf(quiet)?.button, inboxOf(quiet)?.firstNew]).toEqual(['Start fixing', null])
})

// rework-mr: the same band and cards, for the author's steps (threads.py's `step_of`).
const REWORK = { skill: 'rework-mr', script: '/skills/rework-mr/scripts/threads.py', cwd: '/wt', iid: 42 }
const reworkTopic = (id: string, step: string, extra: object = {}) => ({
  id, status: step === 'reply' ? 'reply_pending' : 'open', state: 'new', summary: `summary of ${id}`,
  file: 'src/cache.ts', posted: true, draft: null, step, ...extra,
})
const reworkState = (current: string | null, topics: object[], extra: object = {}) =>
  JSON.stringify({ skill: 'rework-mr', iid: 42, title: 'Add a widget cache', current, topics, ...extra })
const reworkView = (tid: string, draft?: string, step?: string, face?: string) =>
  `◈ **${tid}** \`src/cache.ts:17\`\n\nsome view\n\n<!-- paste-gate:critical\n` +
  JSON.stringify({ first: `◈ **${tid}** \`src/cache.ts:17\``, critical: [], topic: tid, focus: tid, review: REWORK, ...(draft ? { draft } : {}), ...(step ? { step } : {}), ...(face ? { face } : {}) }) +
  '\n-->'
const openRework = async ($: Engine, w: World, stdout: string) => {
  w.tool.stdout = stdout
  await $.tool.call({ tool: 'Skill', skill: 'rework-mr' } as never)
  await $.tool.call({ tool: 'Bash', command: 'python3 $SD/threads.py present --iid 42', tool_use_id: 'tu-open' } as never)
}

test('rework-mr: a proposed plan is agreed with one press, no model turn', async ($, on) => {
  const t1 = reworkTopic('t1', 'plan', { proposal: { says: 'needs', outcome: 'fix', plan: 'Rename it.' } })
  const w = world(on, [reworkState('t1', [t1]), reworkState('t2', [{ ...t1, step: 'fix' }, reworkTopic('t2', 'plan')])])
  await openRework($, w, reworkView('t1'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Agree plan', 'Agree and reply', 'Reply instead…', 'Explain', 'Next', 'Show sidebar'])
  await ui.press({ key: 'agree' })
  await ui.unmount()
  expect(w.ran.map(r => r.argv.slice(2).join(' '))).toContain('agree t1 --iid 42')
  expect(w.prompts).toEqual([])
  expect(JSON.stringify(w.rows.appended())).toContain('t1: agreed the proposed plan')
})

// The model call found that the reviewer's comment needs the person, so no plan was made
// ahead: the chat makes it as the topic comes up, with the whole repository at hand.
test('rework-mr: a topic that needs you gets its plan from the chat as its card comes up, once', async ($, on) => {
  const tick = heldClock(on)
  on('tool.register' as never, (() => ({ value: undefined })) as never)
  on('session.start' as never, ((_: unknown, e: object) => e) as never)
  const t1 = reworkTopic('t1', 'plan', { proposal: { says: 'needs', outcome: 'fix', plan: 'Rename it.' } })
  const t2 = reworkTopic('t2', 'plan', { plan_due: 'm1' })
  // `hunk`: the one state every update check reads again by itself.
  const on2 = (face: string) => reworkState('t2', [{ ...t1, step: 'fix' }, t2], { face, hunk: { open: false, notes: 0 } })
  const states = [reworkState('t1', [t1, t2]), on2('f1')]
  const w = world(on, states, { quote: { stdout: reworkView('t2', undefined, 'plan', 'f1') } })
  await (($ as unknown as { session: { start: (e: object) => Promise<unknown> } }).session.start({ source: 'startup', cwd: '/wt' }))
  await openRework($, w, reworkView('t1'))
  const ui = await mountStep($, 'terminal')
  await ui.press({ key: 'agree' })
  await ui.unmount()
  expect(w.prompts).toEqual([proposePrompt('t2')])
  // Its card is drawn again in its place (a comment on it edited meanwhile): the plan is
  // asked for once.
  states[1] = on2('f2')
  await tick()
  expect(w.placed.length).toBe(1)
  expect(w.ran.filter(r => r.argv[2] === 'quote').length).toBe(2)
  expect(w.prompts).toEqual([proposePrompt('t2')])
})

// Opened before the update check made its call (a quick press after the opener): the card
// came up with the topic still to be judged, and stays up as it is once the call found it
// needs the person.
test('rework-mr: a topic opened before the call judged it gets its plan from the chat once judged', async ($, on) => {
  const tick = heldClock(on)
  on('tool.register' as never, (() => ({ value: undefined })) as never)
  on('session.start' as never, ((_: unknown, e: object) => e) as never)
  const t2 = reworkTopic('t2', 'plan', { plan_due: 'm1' })
  const judgedNot = reworkState('t2', [t2], { judge: { topics: ['t2'], own: [] } })
  const ask = { prompt: 'Sort t2.', items: { t2: 'm1' }, topics: ['t2'], own: [], max_tokens: 1700 }
  const w = world(on, [reworkState('t2', [t2])], {
    refresh: { stdout: judgedNot }, 'judge-prompt': { stdout: JSON.stringify(ask) },
    'judge-store': { stdout: JSON.stringify({ stored: ['t2'], stale: [], unjudged: [] }) },
  })
  modelCall(on, { isAnswered: true, text: '{"t2": {"says": "needs"}}' })
  await (($ as unknown as { session: { start: (e: object) => Promise<unknown> } }).session.start({ source: 'startup', cwd: '/wt' }))
  await openRework($, w, reworkView('t2'))
  expect(w.prompts).toEqual([])
  await tick()
  await settle()
  expect(w.prompts).toEqual([proposePrompt('t2')])
})

// Opened from the sidebar while the call's judgment is being read back: that read started
// before the move and comes back during it, still at the topic before. Neither may drop the
// plan the card that came up wants.
test('rework-mr: a topic opened while the judgment is read back gets its plan once the move is through', async ($, on) => {
  const tick = heldClock(on)
  on('tool.register' as never, (() => ({ value: undefined })) as never)
  on('session.start' as never, ((_: unknown, e: object) => e) as never)
  const t1 = reworkTopic('t1', 'plan', { plan_due: 'm1' })
  const t2 = reworkTopic('t2', 'fix')
  const ask = { prompt: 'Sort t1.', items: { t1: 'm1' }, topics: ['t1'], own: [], max_tokens: 1700 }
  const states = [reworkState('t2', [t1, t2], { in_round: true })]
  const w = world(on, states, {
    refresh: { stdout: reworkState('t2', [t1, t2], { in_round: true, judge: { topics: ['t1'], own: [] } }) },
    'judge-prompt': { stdout: JSON.stringify(ask) },
    'judge-store': { stdout: JSON.stringify({ stored: ['t1'], stale: [], unjudged: [] }) },
    quote: { stdout: reworkView('t1', undefined, 'plan') },
  })
  modelCall(on, { isAnswered: true, text: '{"t1": {"says": "needs"}}' })
  await (($ as unknown as { session: { start: (e: object) => Promise<unknown> } }).session.start({ source: 'startup', cwd: '/wt' }))
  await openRework($, w, reworkView('t2'))
  let backAfterJudging = () => {}
  let backAfterMove = () => {}
  w.held.push(new Promise(done => { backAfterJudging = done }), new Promise(done => { backAfterMove = done }))
  await tick()                                         // judged and stored; its read back is slow
  states[0] = reworkState('t1', [t1, t2], { in_round: true })
  const pane = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  const moving = pane.press({ key: 'row-t1' })         // the card is up; the move reads the state
  await settle()
  backAfterJudging()                                   // the read from before the move lands
  await settle()
  backAfterMove()
  await moving
  await settle()
  await pane.unmount()
  expect(w.prompts).toEqual([proposePrompt('t1')])
})

test('rework-mr: a topic still being judged as it comes up waits for the judgment, with nothing asked', async ($, on) => {
  const t1 = reworkTopic('t1', 'plan', { proposal: { says: 'needs', outcome: 'fix', plan: 'Rename it.' } })
  const t2 = reworkTopic('t2', 'plan', { plan_due: 'm1' })
  const w = world(on, [reworkState('t1', [t1, t2]), reworkState('t2', [{ ...t1, step: 'fix' }, t2], { judge: { topics: ['t2'], own: [] } })])
  await openRework($, w, reworkView('t1'))
  const ui = await mountStep($, 'terminal')
  await ui.press({ key: 'agree' })
  await ui.unmount()
  expect(w.prompts).toEqual([])
})

test('rework-mr: a plan that answers the reviewer now recommends agreeing with that reply; the rest keep their places', async ($, on) => {
  const t1 = reworkTopic('t1', 'plan', { proposal: { says: 'needs', outcome: 'fix', plan: 'Take option B, and tell them.', answer_now: true } })
  const w = world(on, [reworkState('t1', [t1])])
  await openRework($, w, reworkView('t1'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Agree and reply', 'Agree plan', 'Reply instead…', 'Explain', 'Next', 'Show sidebar'])
  await ui.unmount()
})

test('rework-mr: Reply instead… leaves the reply\'s gist to the person, and its draft is the reply step\'s', async ($, on) => {
  const t1 = reworkTopic('t1', 'plan', { proposal: { says: 'needs', outcome: 'fix', plan: 'Rename it.' } })
  const w = world(on, [reworkState('t1', [t1])])
  await openRework($, w, reworkView('t1'))
  const ui = await mountStep($, 'terminal')
  await ui.press({ key: 'reply-instead' })
  await ui.unmount()
  expect(w.filled).toEqual(['Reply to t1 instead: '])
  expect(w.prompts).toEqual([])
})

test('rework-mr: a reply stored as the plan is at the reply step, its draft on the card', async ($, on) => {
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'reply', { draft: 'Not in this MR.', outcome: 'reply' })])])
  await openRework($, w, reworkView('t1'))
  const ui = await mountStep($, 'terminal')
  expect((await labels(ui)).slice(0, 2)).toEqual(['Post', 'Post + resolve'])
  await ui.unmount()
})

// Resolving a thread on a question you wait to have answered closed it on the reviewer.
for (const outcome of ['question', 'push-back']) {
  test(`rework-mr: a reply that is a ${outcome} is posted, never resolved with`, async ($, on) => {
    const w = world(on, [reworkState('t1', [reworkTopic('t1', 'reply', { draft: 'Which one?', outcome })])])
    await openRework($, w, reworkView('t1'))
    const ui = await mountStep($, 'terminal')
    expect(await labels(ui)).toEqual(['Post', 'Check my post', 'Next', 'Show sidebar'])
    await ui.unmount()
  })
}

test('rework-mr: a topic with no plan yet offers its research, or a reply instead; no Discuss…', async ($, on) => {
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'plan')])])
  await openRework($, w, reworkView('t1'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Propose a plan', 'Reply instead…', 'Next', 'Show sidebar'])
  await ui.unmount()
})

test('rework-mr: making the fix offers no Discuss…, the prompt takes that', async ($, on) => {
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'fix')])])
  await openRework($, w, reworkView('t1'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Fix it', 'Next', 'Show sidebar'])
  await ui.unmount()
})

test('rework-mr: Agree and reply agrees the plan and asks for a reply ahead of the fix, on the same topic', async ($, on) => {
  const t1 = reworkTopic('t1', 'plan', { proposal: { says: 'needs', outcome: 'fix', plan: 'Option 1.' } })
  const w = world(on, [reworkState('t1', [t1], { pass: 'plan' }), reworkState('t2', [{ ...t1, step: 'fix' }, reworkTopic('t2', 'plan')], { pass: 'plan' })])
  await openRework($, w, reworkView('t1'))
  const ui = await mountStep($, 'terminal')
  await ui.press({ key: 'agree-reply' })
  await ui.unmount()
  // --focus: agreeing moves the pass on, and the sidebar's highlight went to the next topic
  // while this one's reply was drafted.
  expect(w.ran.map(r => r.argv.slice(2).join(' '))).toContain('agree t1 --focus --iid 42')
  expect(w.placed).toEqual([])                         // no card of t2 to be left at once
  expect(w.prompts).toEqual([expect.stringContaining('set t1 --reply - --early')])
})

test('rework-mr: a plan that is itself a reply is drafted with one press, or turned into a fix', async ($, on) => {
  const t3 = reworkTopic('t3', 'plan', { proposal: { says: 'needs', outcome: 'push-back', plan: 'Decline, follow-up MR.' } })
  const w = world(on, [reworkState('t3', [t3], { pass: 'plan' }), reworkState(null, [{ ...t3, step: 'reply' }], { pass: 'plan' })])
  await openRework($, w, reworkView('t3'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Reply', 'Fix instead…', 'Explain', 'Next', 'Show sidebar'])
  await ui.press({ key: 'draft-reply' })
  await ui.unmount()
  expect(w.ran.map(r => r.argv.slice(2).join(' '))).toContain('agree t3 --focus --iid 42')
  expect(w.prompts).toEqual([expect.stringContaining('set t3 --reply -')])
  expect(w.prompts[0]).not.toContain('--early')
})

test('rework-mr: an agreed topic opened again in the plan pass offers a reply now or another plan, not the fix', async ($, on) => {
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'fix'), reworkTopic('t2', 'plan')], { pass: 'plan' })])
  await openRework($, w, reworkView('t1'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Reply now', 'Change plan…', 'Next', 'Show sidebar'])
  await ui.press({ key: 'replan' })
  await ui.unmount()
  expect(w.filled).toEqual(['Change the plan for t1: '])
})

test('rework-mr: Next in the plan pass moves on to the next topic and puts nothing off', async ($, on) => {
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'fix'), reworkTopic('t2', 'plan')], { pass: 'plan' }),
    reworkState('t2', [reworkTopic('t1', 'fix'), reworkTopic('t2', 'plan')], { pass: 'plan' })])
  await openRework($, w, reworkView('t1'))
  const ui = await mountStep($, 'terminal')
  await ui.press({ key: 'next' })
  await ui.unmount()
  const ran = w.ran.map(r => r.argv.slice(2).join(' '))
  expect(ran).toContain('leave --iid 42')
  expect(ran.some(x => x.startsWith('later'))).toBe(false)
})

test('rework-mr: a new thread in the fix pass waits for its plan: Next', async ($, on) => {
  const w = world(on, [reworkState('t3', [reworkTopic('t3', 'plan', { proposal: { says: 'needs', outcome: 'fix', plan: 'Rename it.' } })], { pass: 'fix' })])
  await openRework($, w, reworkView('t3'))
  const ui = await mountStep($, 'terminal')
  expect((await labels(ui)).slice(-2)).toEqual(['Next', 'Show sidebar'])
  await ui.unmount()
})

test('rework-mr: once a reply went up ahead of the fix, the planned topic offers no second one', async ($, on) => {
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'fix', { early_posted: true }), reworkTopic('t2', 'plan')], { pass: 'plan' })])
  await openRework($, w, reworkView('t1'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Change plan…', 'Next', 'Show sidebar'])
  await ui.unmount()
})

test('rework-mr: a reply drafted ahead of the fix is posted without resolving, in its own words', async ($, on) => {
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'fix', { draft: 'v1', early: true })], { pass: 'plan' }), reworkState(null, [reworkTopic('t1', 'fix')], { pass: 'plan' })])
  await openRework($, w, reworkView('t1', 'v1'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Post', 'Check my post', 'Next', 'Show sidebar'])
  await ui.press({ key: 'post' })
  await ui.unmount()
  expect(w.ran.find(r => r.argv[2] === 'post')?.argv.slice(2).join(' ')).toBe('post t1 --expect v1 --iid 42')
  expect(JSON.stringify(w.rows.appended())).toContain('t1: posted the reply ahead of its fix')
})

test('rework-mr: a topic at its fix is not a review that waits: no "topics have news" in the band', async ($, on) => {
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'fixed', { checks: 'passed', inbox: 'yours' }), reworkTopic('t2', 'fix', { inbox: 'yours' })], { pass: 'fix', inbox: inbox({ first: 't1' }) })])
  await openRework($, w, reworkView('t1'))
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Text', text: /have news|has news|Done for now/ })).toBeUndefined()
  await band.unmount()
})

test("rework-mr: a fix in several files shows the comment's file, every file listed with its size, the comment's marked", async ($, on) => {
  const fix = codeOf(20, [3, 3], { mode: 'fix', file: 'src/a/query-keys.ts', commented: 'src/a/query-keys.ts',
    fix_files: [['src/a/create.ts', 4, 4], ['src/a/query-keys.ts', 1, 1], ['src/b/query-keys.ts', 12, 3]] })
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'fixed', { checks: 'passed' })])], { code: { stdout: JSON.stringify(fix) } })
  await openRework($, w, reworkView('t1'))
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  const texts = (await panel.findAll({ type: 'Text' })).map(t => t.text ?? '')
  expect(texts).not.toContain('Your fix')
  // No count in the area's line: the rows are the files.
  expect(texts.some(t => / · \d+ files?/.test(t))).toBe(false)
  expect(texts).toEqual(expect.arrayContaining(['src/a/create.ts', 'src/b/query-keys.ts', '+12', '−3']))
  // The shown file's row carries the bar, the comment's file its mark; a TypeScript file its icon.
  const shown = JSON.stringify((await panel.find({ type: 'Box', key: 'file-src/a/query-keys.ts' }))?.children ?? [])
  expect(shown).toContain('▌')
  expect(shown).toContain('\u{F0189}')
  expect(JSON.stringify((await panel.find({ type: 'Box', key: 'file-src/a/create.ts' }))?.children ?? [])).toContain('\u{E628}')
  expect(w.ran.find(r => r.argv[2] === 'code')?.argv.slice(2, 5)).toEqual(['code', 't1', '--fix'])
  await panel.unmount()
})

test('rework-mr: picking keeps the list where it was scrolled, draws no topic box on another file, and comes back to a file without asking again', async ($, on) => {
  const listed = Array.from({ length: 12 }, (_, i) => [`src/f${i + 1}.ts`, 1, 1])
  const at = (f: string, line: number | null) => codeOf(20, [3, 3], { mode: 'pushed', file: f, line, commented: 'src/f1.ts', fix_files: listed })
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'reply', { pushed: true, fix_kept: true })], {
    thread: [{ url: 'http://gl/n1', file: 'src/f1.ts', line: 3, notes: [{ author: 'Robin', body: 'This needs a guard before the cache is read. '.repeat(6) }] }] })],
  { code: { stdout: JSON.stringify(at('src/f1.ts', 3)) }, 'code --file src/f9.ts': { stdout: JSON.stringify(at('src/f9.ts', null)) },
    'code --file src/f1.ts': { stdout: JSON.stringify(at('src/f1.ts', 3)) } })
  await openRework($, w, reworkView('t1'))
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  // Under the person's own pushed fix the thread starts folded: its author's line, not its text.
  const texts = async () => (await panel.findAll({ type: 'Text' })).map(t => String(t.text ?? ''))
  expect(await texts()).toContain('Robin')
  expect((await panel.find({ type: 'Button', key: 'thread-open' }))?.props.label).toBe('\u{F05AF}')   // the key that opens it
  await page(panel, 'files', 'down')
  // The list's rows, by their pick buttons: the code line above names the shown file too.
  const rows = async () => (await panel.findAll({ type: 'Button' })).map(b => String(b.key ?? '')).filter(k => k.startsWith('file-pick-'))
  const scrolled = await rows()
  expect(scrolled).toContain('file-pick-src/f9.ts')
  await panel.press({ key: 'file-pick-src/f9.ts' })
  expect(await rows()).toEqual(scrolled)
  // Back on the comment's own file: drawn from what was read before, nothing asked again.
  const asked = w.ran.filter(r => r.argv[2] === 'code').length
  await page(panel, 'files', 'up')
  await panel.press({ key: 'file-pick-src/f1.ts' })
  expect(w.ran.filter(r => r.argv[2] === 'code').length).toBe(asked + 1)   // the pick of f1, read once more
  await page(panel, 'files', 'down')
  await panel.press({ key: 'file-pick-src/f9.ts' })
  expect(w.ran.filter(r => r.argv[2] === 'code').length).toBe(asked + 1)   // f9 was read already
  await panel.unmount()
})

test('rework-mr: a file picked from the list is asked for and shown, without the thread', async ($, on) => {
  const files = [['src/a.ts', 1, 1], ['src/b.ts', 2, 0]]
  const mine = codeOf(20, [3, 3], { mode: 'fix', file: 'src/a.ts', commented: 'src/a.ts', fix_files: files })
  const other = codeOf(20, [1, 1], { mode: 'fix', file: 'src/b.ts', line: null, commented: 'src/a.ts', fix_files: files })
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'fixed', { checks: 'passed' })], {
    thread: [{ url: 'http://gl/n1', file: 'src/a.ts', line: 3, notes: [{ author: 'Robin', body: 'This needs a guard.' }] }] })],
  { code: { stdout: JSON.stringify(mine) }, 'code --file src/b.ts': { stdout: JSON.stringify(other) } })
  await openRework($, w, reworkView('t1'))
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  // Under the person's own fix the thread starts folded: its author's line stands for it.
  expect((await panel.findAll({ type: 'Text' })).map(t => t.text)).toContain('Robin')
  await panel.press({ key: 'file-pick-src/b.ts' })
  expect(w.ran.map(r => r.argv.slice(2).join(' '))).toContain('code t1 --fix --file src/b.ts --iid 42')
  expect((await panel.findAll({ type: 'Text' })).map(t => t.text)).not.toContain('Robin')
  expect(JSON.stringify((await panel.find({ type: 'Box', key: 'file-src/b.ts' }))?.children ?? [])).toContain('▌')
  // A file the comment is not on is one stretch, so its line numbers line up, and its area
  // takes its whole height, keeping the list at the foot.
  expect((await panel.findAll({ type: 'Box' })).filter(b => String(b.key ?? '').startsWith('run-'))).toHaveLength(1)
  expect((await panel.find({ type: 'Box', key: 'code' }))?.props.height).toBeGreaterThan(20)
  await panel.unmount()
})

test("rework-mr: a pushed fix is the sidebar's files area, all its files, its viewer's key in the line, the code marked pushed", async ($, on) => {
  const code = codeOf(20, [3, 3], { mode: 'pushed', file: 'src/a/query-keys.ts', commented: 'src/a/query-keys.ts',
    fix_files: [['src/a/query-keys.ts', 2, 1], ['src/b/create.ts', 4, 0]] })
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'reply', { pushed: true, fix_kept: true })])], { code: { stdout: JSON.stringify(code) } })
  await openRework($, w, reworkView('t1'))
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  const texts = (await panel.findAll({ type: 'Text' })).map(t => t.text ?? '')
  expect(texts).not.toContain('Your fix')
  expect(texts).not.toContain('Also changed')
  expect(texts).toContain('pushed')
  expect(texts).toContain('src/b/create.ts')
  await panel.press({ key: 'files-hunk' })
  await panel.unmount()
  expect(w.ran.map(r => r.argv.slice(2).join(' '))).toContain('hunk-show t1 --pushed --iid 42')
})

// "+15 / +2 / +3" stood left-aligned, the 5 over nothing; and the fix's total was on the
// card's "pushed a fix" line, a second count beside the files' own.
test("a fix's total stands over its files' sizes, every number right-aligned in its column", async ($, on) => {
  const code = codeOf(20, [3, 3], { mode: 'pushed', file: 'src/a.ts', commented: 'src/a.ts',
    fix_files: [['src/a.ts', 15, 4], ['src/b.ts', 2, 10], ['src/c.ts', 3, 0]] })
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'reply', { pushed: true, fix_kept: true, pushes: 1 })])], { code: { stdout: JSON.stringify(code) } })
  await openRework($, w, reworkView('t1'))
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  const sizes = async (key: string) => [...JSON.stringify(await panel.find({ type: 'Box', key })).matchAll(/"text":"([^"]*)"/g)]
    .map(m => m[1]).join('')
  expect(await sizes('files-total-size')).toBe('+20 −14')
  expect(await sizes('file-size-src/a.ts')).toBe('+15  −4')
  expect(await sizes('file-size-src/b.ts')).toBe(' +2 −10')
  expect(await sizes('file-size-src/c.ts')).toBe(' +3  −0')
  // The total ends the line: no rule after it, so it sits in the rows' columns.
  const line = JSON.stringify((await panel.find({ type: 'Box', key: 'files-line' }))?.children ?? [])
  expect(line.slice(line.indexOf('files-total-size'))).not.toMatch(/─/)
  await panel.unmount()
})

test("a fix of one file has no total over it: the file's own size says it", async ($, on) => {
  const code = codeOf(20, [3, 3], { mode: 'pushed', file: 'src/a.ts', commented: 'src/a.ts', fix_files: [['src/a.ts', 2, 1]] })
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'reply', { pushed: true, fix_kept: true, pushes: 1 })])], { code: { stdout: JSON.stringify(code) } })
  await openRework($, w, reworkView('t1'))
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  expect(await panel.find({ type: 'Box', key: 'files-total-size' })).toBeUndefined()
  await panel.unmount()
})

test("rework-mr: a fix pushed twice picks its push in the files area's line, the files and the viewer's key following it", async ($, on) => {
  const latest = codeOf(20, [3, 3], { mode: 'pushed', file: 'src/a.ts', commented: 'src/a.ts', fix_files: [['src/a.ts', 2, 1]] })
  const first = codeOf(20, [3, 3], { mode: 'pushed', file: 'src/a.ts', commented: 'src/a.ts', fix_files: [['src/a.ts', 1, 1], ['src/b.ts', 5, 0]] })
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'reply', { pushed: true, fix_kept: true, pushes: 2 })])],
    { code: { stdout: JSON.stringify(latest) }, 'code --push 1': { stdout: JSON.stringify(first) } })
  await openRework($, w, reworkView('t1'))
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  const texts = async () => (await panel.findAll({ type: 'Text' })).map(t => String(t.text ?? ''))
  // The latest at first, the key to it dim: there is no later one.
  expect(await texts()).toContain('push 2 of 2 ')
  // At the line's right end: the rule runs from the viewer's key to it.
  const line = JSON.stringify((await panel.find({ type: 'Box', key: 'files-line' }))?.children ?? [])
  expect(line.slice(line.indexOf('"key":"files-hunk"'), line.indexOf('push-earlier'))).toMatch(/─{3,}/)
  expect((await panel.find({ type: 'Button', key: 'push-earlier' }))?.props.label).toBe('\u{F0BB9}')
  expect((await panel.find({ type: 'Button', key: 'push-later' }))?.props.dimColor).toBe(true)
  await panel.press({ key: 'push-earlier' })
  expect(w.ran.map(r => r.argv.slice(2).join(' '))).toContain('code t1 --push 1 --iid 42')
  expect(await texts()).toEqual(expect.arrayContaining(['push 1 of 2 ', 'src/b.ts']))
  await panel.press({ key: 'files-hunk' })
  expect(w.ran.map(r => r.argv.slice(2).join(' '))).toContain('hunk-show t1 --pushed --push 1 --iid 42')
  await panel.unmount()
})

test("rework-mr: the files area's viewer key stays drawn while a press or the model works", async ($, on) => {
  const code = codeOf(20, [3, 3], { mode: 'pushed', file: 'src/a.ts', commented: 'src/a.ts', fix_files: [['src/a.ts', 2, 1]] })
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'reply', { pushed: true, fix_kept: true, pushes: 2 })])], { code: { stdout: JSON.stringify(code) } })
  on('turn.start', ($, e) => ({ turnId: e.turnId }))
  await openRework($, w, reworkView('t1'))
  await $.turn.start({ text: 'go on', turnId: 'turn-1' })
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  expect(await panel.find({ type: 'Button', key: 'files-hunk' })).toBeDefined()
  await panel.unmount()
})

test("rework-mr: a fix pushed once has no push to pick", async ($, on) => {
  const code = codeOf(20, [3, 3], { mode: 'pushed', file: 'src/a.ts', commented: 'src/a.ts', fix_files: [['src/a.ts', 2, 1]] })
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'reply', { pushed: true, fix_kept: true, pushes: 1 })])], { code: { stdout: JSON.stringify(code) } })
  await openRework($, w, reworkView('t1'))
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  expect(await panel.find({ type: 'Button', key: 'push-earlier' })).toBeUndefined()
  await panel.unmount()
})

test("rework-mr: a fix in one file lists that file, its size and the comment's mark", async ($, on) => {
  const code = codeOf(20, [3, 3], { mode: 'pushed', file: 'src/a.ts', commented: 'src/a.ts', fix_files: [['src/a.ts', 2, 1]] })
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'reply', { pushed: true, fix_kept: true })])], { code: { stdout: JSON.stringify(code) } })
  await openRework($, w, reworkView('t1'))
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  const texts = (await panel.findAll({ type: 'Text' })).map(t => t.text ?? '')
  expect(texts).toEqual(expect.arrayContaining(['src/a.ts', '+2', '−1']))
  expect(await panel.find({ type: 'Button', key: 'files-hunk' })).toBeDefined()
  expect(JSON.stringify((await panel.find({ type: 'Box', key: 'file-src/a.ts' }))?.children ?? [])).toContain('\u{F0189}')
  await panel.unmount()
})

test("review-mr: the author's pushed fix is the sidebar's files area, led by its viewer's key", async ($, on) => {
  const code = codeOf(20, [3, 3], { mode: 'since', file: 'src/cache.ts', commented: 'src/cache.ts', fix_files: [['src/cache.ts', 2, 1], ['src/b.ts', 1, 0]] })
  const w = world(on, [stateOf('t2', [{ ...topic('t2', 'needs_ack'), fix_kept: true } as Topic])], { code: { stdout: JSON.stringify(code) } })
  await openReview($, w, view('t2'))
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  const texts = (await panel.findAll({ type: 'Text' })).map(t => t.text ?? '')
  expect(texts).not.toContain("Author's fix")
  expect(texts).toContain('src/b.ts')
  expect(JSON.stringify((await panel.find({ type: 'Box', key: 'files-line' }))?.children ?? [])).toContain('files-hunk')
  await panel.unmount()
})

test("the list's line is the MR's: its number and title, its marks and the news right after it, a stretch of line to the legend's key", async ($, on) => {
  const mr = { state: 'opened', auto_merge: true, approved_by: [{ name: 'Jan', me: false }], pipeline: { status: 'running' } }
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'plan'), reworkTopic('t2', 'plan', { inbox: 'new' })], { mr, url: 'http://gl/g/p/-/merge_requests/42' })])
  await openRework($, w, reworkView('t1'))
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  const texts = (await panel.findAll({ type: 'Text' })).map(t => String(t.text ?? ''))
  expect(texts).not.toContain('Topics')
  const line = JSON.stringify((await panel.find({ type: 'Box', key: 'list-line' }))?.children ?? [])
  for (const part of ['!42', ' Add a widget cache', '\u{f0514}', '· ', '1 new', 'legend']) expect(line).toContain(part)
  // The marks right after the title, no stretch of line between; the approval first.
  expect(line).not.toContain(' ── ')
  const at = (x: string) => line.indexOf(x)
  expect(at('\u{f0514}')).toBeGreaterThan(at(' Add a widget cache'))
  expect(at('\u{f051f}')).toBeGreaterThan(at('\u{f0514}'))
  expect(at('\u{f0996}')).toBeGreaterThan(at('\u{f051f}'))
  expect(at('1 new')).toBeGreaterThan(at('\u{f0996}'))
  expect(at('legend')).toBeGreaterThan(at('1 new'))
  // The legend lists them in the same order.
  await panel.press({ key: 'legend' })
  const legend = JSON.stringify(await panel.find({ type: 'Box', key: 'legend-box' }))
  const place = (word: string) => legend.indexOf(word)
  expect(place(' approved')).toBeGreaterThanOrEqual(0)
  expect(place(' auto-merge set')).toBeGreaterThan(place(' approved'))
  expect(place(' pipeline running')).toBeGreaterThan(place(' auto-merge set'))
  await panel.unmount()
})

test("a title too long for the list's line is cut, and the line keeps to the sidebar's width", async ($, on) => {
  const title = 'Warn when the recipient tax identifier equals the supplier tax identifier on every document'
  const w = world(on, [JSON.stringify({ ...JSON.parse(reworkState('t1', [reworkTopic('t1', 'plan'), reworkTopic('t2', 'plan', { inbox: 'new' })])), title })])
  await openRework($, w, reworkView('t1'))
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  const texts = (await panel.findAll({ type: 'Text' })).map(t => String(t.text ?? ''))
  const cut = texts.find(t => t.includes('Warn when'))
  expect(cut?.endsWith('…')).toBe(true)
  // The line within the 60 columns: its title, the number, the marks and the dashes around them.
  // Its texts from the first dashes to the last, the legend's key among them (its icon a text).
  const from = texts.indexOf('── ')
  const parts = texts.slice(from, texts.indexOf(' ─', texts.indexOf(cut ?? '')) + 4)
  expect([...parts.join('')].length).toBe(60)
  await panel.unmount()
})

test('rework-mr: a topic with news is marked in the list, as in review-mr, and the heading counts it', async ($, on) => {
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'plan'), reworkTopic('t2', 'plan', { inbox: 'new' })])])
  await openRework($, w, reworkView('t1'))
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  const texts = await panel.findAll({ type: 'Text' })
  expect(texts.find(t => t.text === '\u{f09de} ')?.props.color).toBe('yellow')
  expect(texts.find(t => t.text === '1 new')?.props.color).toBe('yellow')
  await panel.unmount()
})

test("rework-mr: a fix's files are an area of a sixth of the sidebar, scrolling past that, with no fold, the diff viewer's key opening its line", async ($, on) => {
  const listed = Array.from({ length: 12 }, (_, i) => [`src/f${i + 1}.ts`, 1, 1])
  const fix = codeOf(20, [3, 3], { mode: 'fix', file: 'src/f1.ts', commented: 'src/f1.ts', fix_files: listed })
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'fixed', { checks: 'passed' })])], { code: { stdout: JSON.stringify(fix) } })
  await openRework($, w, reworkView('t1'))
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  const files = async () => (await panel.findAll({ type: 'Text' })).map(t => String(t.text ?? '')).filter(t => /^src\/f\d+\.ts$/.test(t))
  expect(await files()).toHaveLength(5)                                  // a sixth of 30 rows
  expect(await panel.find({ type: 'Button', key: 'files-open' })).toBeUndefined()
  expect(await bar(panel, 'files')).toHaveLength(5)
  expect(String((await panel.find({ type: 'Button', key: 'files-hunk' }))?.props.label)).toMatch(/Review in hunk$/)
  expect((await panel.find({ type: 'Text', text: 'unpushed' }))?.props.color).toBe('yellow')
  // The viewer's key opens the line, a chip between its caps, the rule after it.
  const line = JSON.stringify((await panel.find({ type: 'Box', key: 'files-line' }))?.children ?? [])
  expect(line.indexOf('files-hunk')).toBeGreaterThan(-1)
  expect(line.slice(0, line.indexOf('files-hunk'))).toContain('"── "')
  expect(line.slice(0, line.indexOf('"key":"files-hunk"'))).toContain('\u{E0B6}')
  expect(line.slice(line.indexOf('files-hunk'))).toMatch(/─{3,}/)
  await panel.unmount()
})

test('rework-mr: a turn that moved the rework on without a card brings up the current topic\'s', async ($, on) => {
  // The model finished a press it was handed (Push and resolve, a fix in two commits): t1
  // is done, and t2, now current, has had no card yet.
  const states = [reworkState('t1', [reworkTopic('t1', 'fixed', { checks: 'passed' })])]
  const w = world(on, states)
  await openRework($, w, reworkView('t1'))
  states[0] = reworkState('t2', [reworkTopic('t1', 'none'), reworkTopic('t2', 'fix')])
  await $.turn.complete({ answer: 'pushed' } as never)
  expect(w.placed.length).toBe(1)
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toContain('Fix it')
  await ui.unmount()
})

test("rework-mr: a push the model finished brings up the topic's next card, and its fix card says it was pushed", async ($, on) => {
  // A fix in two commits: the press handed the push to the model, which ran it in its turn.
  const states = [reworkState('t1', [reworkTopic('t1', 'fixed', { checks: 'passed' })])]
  const w = world(on, states)
  const fixCard = reworkView('t1', undefined, 'fixed')
  await openRework($, w, fixCard)
  states[0] = reworkState('t1', [reworkTopic('t1', 'reply', { pushed: true, diff_url: 'http://gl/g/p/-/merge_requests/42/diffs?diff_id=7' })])
  await $.turn.complete({ answer: 'pushed' } as never)
  expect(w.placed.length).toBe(1)
  const old = await $.ui.mount(cardRow('tu-open', fixCard))
  expect(JSON.stringify(await old.findAll({ type: 'Text' }))).toContain('pushed a fix')
  expect((await old.find({ type: 'Link' }))?.props).toMatchObject({ href: 'http://gl/g/p/-/merge_requests/42/diffs?diff_id=7', label: 'diff \u{f03cc}' })
  expect(await old.find({ type: 'Button', key: 'post-fixed' })).toBeUndefined()      // the buttons moved on
  await old.unmount()
})

test("rework-mr: a turn that left the topic at its card's step places no new card", async ($, on) => {
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'fixed', { checks: 'passed' })])])
  await openRework($, w, reworkView('t1', undefined, 'fixed'))
  await $.turn.complete({ answer: 'looked' } as never)
  expect(w.placed).toEqual([])
})

// The plan on the card was "Push back"; typed "plan it as a fix", the model stored the new
// plan and showed nothing, so the old card stayed and took the press that agreed the new one.
test("rework-mr: a turn that changed what the topic's card says, at the same step, places a new card", async ($, on) => {
  const plan = (face: string) => reworkState('t1', [reworkTopic('t1', 'plan', { proposal: { says: 'needs', outcome: 'fix', plan: 'Revert it.' } })], { face })
  const states = [plan('f1')]
  const w = world(on, states)
  await openRework($, w, reworkView('t1', undefined, 'plan', 'f1'))
  await $.turn.complete({ answer: 'looked' } as never)
  expect(w.placed).toEqual([])                         // the card says what the topic does
  states[0] = plan('f2')
  await $.turn.complete({ answer: 'planned it as a fix' } as never)
  expect(w.placed.length).toBe(1)
})

// "t1: posted “Fixed” with the diff link" once stood under the cards while the reviewer's
// doubt about that fix was already being answered, reading as if just posted.
test("a press's line goes once its topic has moved on from the moment of the press", async ($, on) => {
  const t1 = reworkTopic('t1', 'plan', { proposal: { says: 'needs', outcome: 'fix', plan: 'Rename it.' } })
  const states = [reworkState('t1', [t1]), reworkState('t1', [{ ...t1, step: 'fix' }])]
  const w = world(on, states)
  on('turn.start', ($, e) => ({ turnId: e.turnId }))
  await openRework($, w, reworkView('t1'))
  const ui = await mountStep($, 'terminal')
  await ui.press({ key: 'agree' })
  await ui.unmount()
  const said = async () => {
    const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
    const text = JSON.stringify(await band.findAll({ type: 'Text' }))
    await band.unmount()
    return text
  }
  expect(await said()).toContain('t1: agreed the proposed plan')
  // The topic moves on (a draft is stored for it): the line is about a moment that is over.
  states[1] = reworkState('t1', [{ ...t1, step: 'fix', draft: 'v1' }])
  await $.tool.call({ tool: 'Bash', command: 'python3 $SD/threads.py present --iid 42', tool_use_id: 'tu-2' } as never)
  expect(await said()).not.toContain('agreed the proposed plan')
})

test("a press's line goes once the model takes a turn", async ($, on) => {
  const t1 = reworkTopic('t1', 'plan', { proposal: { says: 'needs', outcome: 'fix', plan: 'Rename it.' } })
  const w = world(on, [reworkState('t1', [t1]), reworkState('t1', [{ ...t1, step: 'fix' }])])
  on('turn.start', ($, e) => ({ turnId: e.turnId }))
  await openRework($, w, reworkView('t1'))
  const ui = await mountStep($, 'terminal')
  await ui.press({ key: 'agree' })
  await ui.unmount()
  await $.turn.start({ text: 'Change the plan for t1: add a TODO, then reply', turnId: 'turn-1' })
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(JSON.stringify(await band.findAll({ type: 'Text' }))).not.toContain('agreed the proposed plan')
  await band.unmount()
})

test('rework-mr: a comment that asks nothing is seen, not planned', async ($, on) => {
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'plan', { proposal: { says: 'fyi' } })])])
  await openRework($, w, reworkView('t1'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Seen', 'Reply…', 'Next', 'Show sidebar'])
  await ui.press({ key: 'handled-Seen' })
  await ui.unmount()
  expect(w.ran.map(r => r.argv.slice(2).join(' '))).toContain('wait t1 --iid 42')
})

test('rework-mr: a fix offers Ack and push, and the diff viewer; nothing to put off', async ($, on) => {
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'fixed', { checks: 'passed' })])])
  await openRework($, w, reworkView('t1'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Ack and push', 'Review in hunk', 'Show sidebar'])
  await ui.press({ key: 'hunk' })
  await ui.unmount()
  expect(w.ran.map(r => r.argv.slice(2).join(' '))).toContain('hunk-show t1 --iid 42')
})

test('rework-mr: a push stopped on commits the remote holds offers taking them in, not the same push again', async ($, on) => {
  // Real run: Ack and push stayed on the card and failed the same way on every press.
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'fixed', { checks: 'passed', push_behind: true })])])
  await openRework($, w, reworkView('t1'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Take in and push', 'Review in hunk', 'Show sidebar'])
  await ui.press({ key: 'take-in' })
  await ui.unmount()
  expect(w.ran.map(r => r.argv.slice(2).join(' '))).toContain('push t1 --take-in --iid 42')
})

test('rework-mr: a fix made while another topic was current can be put aside, with no model turn', async ($, on) => {
  const fixed = reworkTopic('t1', 'fixed', { checks: 'passed', made_on: 't3' })
  const w = world(on, [reworkState('t1', [fixed, reworkTopic('t3', 'fix')]),
    reworkState('t3', [{ ...fixed, step: 'fix', made_on: null }, reworkTopic('t3', 'fix')])])
  await openRework($, w, reworkView('t1'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Ack and push', 'Review in hunk', 'Put aside', 'Show sidebar'])
  await ui.press({ key: 'aside' })
  await ui.unmount()
  expect(w.ran.find(r => r.argv[2] === 'drop-fix')?.argv.slice(2).join(' '))
    .toBe('drop-fix t1 --aside --why made while another topic was current; put aside for later --iid 42')
  expect(w.prompts).toEqual([])
  expect(w.ran.map(r => r.argv.slice(2).join(' '))).toContain('quote t3 --iid 42')
})

test('rework-mr: a fix put aside that comes back as its card comes up gets the fix\'s buttons', async ($, on) => {
  // Real run: the card showed the fix back, its buttons were Fix it's, from the state before.
  const t3 = reworkTopic('t3', 'reply', { draft: 'v1', pushed: true })
  const w = world(on, [reworkState('t3', [reworkTopic('t1', 'fix'), t3]),
    reworkState('t1', [reworkTopic('t1', 'fix'), { ...t3, step: 'none' }]),
    reworkState('t1', [reworkTopic('t1', 'fixed', { checks: 'stale' }), { ...t3, step: 'none' }])],
  { 'quote moves': { stdout: reworkView('t1', undefined, 'fixed') }, code: { stdout: '' } })
  await openRework($, w, reworkView('t3', 'v1'))
  const ui = await mountStep($, 'terminal')
  await ui.press({ key: 'post' })
  await ui.unmount()
  const next = await mountStep($, 'terminal')
  expect(await labels(next)).toEqual(['Run the checks', 'Review in hunk', 'Show sidebar'])
  await next.unmount()
})

test('rework-mr: with the fix open in the diff viewer, its notes are read on a press', async ($, on) => {
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'fixed', { checks: 'passed' })], { hunk: { open: true, notes: 0 } })])
  await openRework($, w, reworkView('t1'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Ack and push', 'Read hunk notes', 'Show sidebar'])
  await ui.unmount()
})

test('rework-mr: once the person left notes in the diff viewer, reading them comes first', async ($, on) => {
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'fixed', { checks: 'passed' })], { hunk: { open: true, notes: 2 } })])
  await openRework($, w, reworkView('t1'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Read hunk notes', 'Ack and push', 'Show sidebar'])
  await ui.press({ key: 'hunk-notes' })
  await ui.unmount()
  expect(w.prompts[0]).toMatch(/hunk-notes.*Pushing is my press/)
})

// The viewer forgets its notes with its window; the script keeps them, and the card must not
// go back to offering the push as if nothing had been written.
test('rework-mr: notes left in a diff viewer closed since are still read first', async ($, on) => {
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'fixed', { checks: 'passed' })], { hunk: { open: false, notes: 1 } })])
  await openRework($, w, reworkView('t1'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Read hunk notes', 'Ack and push', 'Show sidebar'])
  await ui.unmount()
})

// The viewer being open was learnt only from a state read, and the state was read again only
// once it was known open: notes showed up after some other press, never by themselves.
test('rework-mr: while a fix waits for its push, each update check reads the state again, so notes show up by themselves', async ($, on) => {
  const tick = heldClock(on)
  on('tool.register' as never, (() => ({ value: undefined })) as never)
  on('session.start' as never, ((_: unknown, e: object) => e) as never)
  const fixed = (hunk: object) => reworkState('t1', [reworkTopic('t1', 'fixed', { checks: 'passed' })], { hunk })
  const states = [fixed({ open: false, notes: 0 })]
  const w = world(on, states)
  await (($ as unknown as { session: { start: (e: object) => Promise<unknown> } }).session.start({ source: 'startup', cwd: '/wt' }))
  await openRework($, w, reworkView('t1'))
  const before = await mountStep($, 'terminal')
  expect(await labels(before)).toEqual(['Ack and push', 'Review in hunk', 'Show sidebar'])
  await before.unmount()
  states[0] = fixed({ open: true, notes: 1 })          // the person opened the viewer and wrote
  await tick()
  const after = await mountStep($, 'terminal')
  expect(await labels(after)).toEqual(['Read hunk notes', 'Ack and push', 'Show sidebar'])
  await after.unmount()
})

test('rework-mr: a fix that differs from the plan agreed is reviewed in hunk first', async ($, on) => {
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'fixed', { checks: 'passed', differs: true })])])
  await openRework($, w, reworkView('t1'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Review in hunk', 'Ack and push', 'Show sidebar'])
  await ui.unmount()
})

test('rework-mr: a differing fix looked at in hunk, with no notes left, is pushed next', async ($, on) => {
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'fixed', { checks: 'passed', differs: true })], { hunk: { open: true, notes: 0 } })])
  await openRework($, w, reworkView('t1'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Ack and push', 'Read hunk notes', 'Show sidebar'])
  await ui.unmount()
})

test('rework-mr: a fix whose checks have not passed offers no push', async ($, on) => {
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'fixed', { checks: 'stale' })])])
  await openRework($, w, reworkView('t1'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Run the checks', 'Review in hunk', 'Show sidebar'])
  await ui.press({ key: 'check' })
  await ui.unmount()
  expect(w.prompts[0]).toContain('verify t1')
})

test('rework-mr: Ack and push only pushes, with no model turn, and the next card offers what to say', async ($, on) => {
  const fixed = reworkTopic('t1', 'fixed', { checks: 'passed' })
  const w = world(on, [reworkState('t1', [fixed]), reworkState('t1', [{ ...fixed, step: 'reply', pushed: true }])],
    { push: { stdout: 'pushed the fix for t1' } })
  await openRework($, w, reworkView('t1'))
  const ui = await mountStep($, 'terminal')
  await ui.press({ key: 'push' })
  await ui.unmount()
  const push = w.ran.find(r => r.argv[2] === 'push')
  expect(push?.argv.slice(2).join(' ')).toBe('push t1 --iid 42')
  expect(await isPress(w, push?.env)).toBe(true)
  expect(w.prompts).toEqual([])
  expect(w.ran.map(r => r.argv.slice(2).join(' '))).toContain('quote t1 --iid 42')
})

test('rework-mr: after the push, a "Fixed" resolved or not, or a reply', async ($, on) => {
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'reply', { pushed: true })])])
  await openRework($, w, reworkView('t1'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['“Fixed” + resolve', '“Fixed”', 'Reply…', 'Next', 'Show sidebar'])
  await ui.press({ key: 'post-fixed' })
  await ui.unmount()
  expect(w.ran.find(r => r.argv[2] === 'post')?.argv.slice(2).join(' ')).toBe('post t1 --fixed --iid 42')
})

test('rework-mr: a reply defending a pushed fix is drafted, never a second "Fixed"', async ($, on) => {
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'reply', { pushed: true, fix_kept: true, outcome: 'push-back' })])])
  await openRework($, w, reworkView('t1'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Reply', 'Check my post', 'Show fix in hunk', 'Next', 'Show sidebar'])
  await ui.unmount()
})

test('rework-mr: a comment the person wrote last on GitLab offers to wait for the reviewer', async ($, on) => {
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'plan', { you_last: true, proposal: { says: 'needs', outcome: 'fix', plan: 'Rename it.' } })], { pass: 'plan' })])
  await openRework($, w, reworkView('t1'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toContain('Wait for reviewer')
  await ui.press({ key: 'handled-Wait for reviewer' })
  await ui.unmount()
  expect(w.ran.find(r => r.argv[2] === 'wait')?.argv.slice(2).join(' ')).toBe('wait t1 --iid 42')
})

test('rework-mr: a pushed fix kept in git is shown in the diff viewer on a press, never as the recommendation', async ($, on) => {
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'reply', { pushed: true, fix_kept: true })])])
  await openRework($, w, reworkView('t1'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['“Fixed” + resolve', '“Fixed”', 'Reply…', 'Show fix in hunk', 'Next', 'Show sidebar'])
  await ui.press({ key: 'hunk-pushed' })
  await ui.unmount()
  expect(w.ran.map(r => r.argv.slice(2).join(' '))).toContain('hunk-show t1 --pushed --iid 42')
  expect(w.prompts).toEqual([])
})

test('rework-mr: a reply drafted after the push keeps the pushed fix to look at', async ($, on) => {
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'reply', { draft: 'v3', pushed: true, fix_kept: true })])])
  await openRework($, w, reworkView('t1', 'v3'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toContain('Show fix in hunk')
  await ui.unmount()
})

test('rework-mr: a pushed topic that waits on its reviewer offers its fix to look at, and nothing else', async ($, on) => {
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'none', { status: 'waiting', pushed: true, fix_kept: true })], { in_round: false })])
  await openRework($, w, reworkView('t1'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Show fix in hunk', 'Show sidebar'])
  await ui.unmount()
})

test('rework-mr: no way on to a next topic while no round runs', async ($, on) => {
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'reply', { pushed: true }), reworkTopic('t2', 'plan')], { in_round: false, round: ['t1', 't2'] })])
  await openRework($, w, reworkView('t1'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['“Fixed” + resolve', '“Fixed”', 'Reply…', 'Show sidebar'])
  await ui.unmount()
})

test('rework-mr: after a push that differs from the plan, the reply comes first', async ($, on) => {
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'reply', { pushed: true, differs: true })])])
  await openRework($, w, reworkView('t1'))
  const ui = await mountStep($, 'terminal')
  // What the reply has to say is known, so one press drafts it: nothing to type.
  expect((await labels(ui))[0]).toBe('Reply')
  await ui.press({ key: 'draft' })
  await ui.unmount()
  expect(w.prompts[0]).toMatch(/differs from the plan agreed.*set t1 --reply -/)
})

test('rework-mr: after the push of a plan that answers the reviewer, with nothing said yet, the reply comes first', async ($, on) => {
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'reply', { pushed: true, answer_now: true })])])
  await openRework($, w, reworkView('t1'))
  const ui = await mountStep($, 'terminal')
  expect((await labels(ui))[0]).toBe('Reply…')
  await ui.unmount()
})

test('rework-mr: a reply drafted after the fix never resolves; one with no fix may', async ($, on) => {
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'reply', { draft: 'v3', pushed: true })])])
  await openRework($, w, reworkView('t1', 'v3'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Post', 'Check my post', 'Next', 'Show sidebar'])
  await ui.unmount()
})

test('rework-mr: a topic opened from the sidebar, closed or not, gets a card of its own', async ($, on) => {
  const closed = reworkTopic('t3', 'none', { status: 'done', state: 'done' })
  const w = world(on, [reworkState('t2', [reworkTopic('t2', 'reply', { draft: 'v1' }), closed], { in_round: true }),
    reworkState('t3', [reworkTopic('t2', 'reply', { draft: 'v1' }), closed], { in_round: true })], { quote: { stdout: reworkView('t3') } })
  await openRework($, w, reworkView('t2', 'v1'))
  const before = w.placed.length
  const pane = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  await pane.press({ key: 'group-closed' })                     // Closed starts folded
  await pane.press({ key: 'row-t3' })
  await pane.unmount()
  expect(w.ran.map(r => r.argv.slice(2).join(' '))).toContain('quote t3 --focus --iid 42')
  expect(w.placed.length).toBe(before + 1)
})

test('rework-mr: between rounds, a closed topic opened from the sidebar gets a card too', async ($, on) => {
  const closed = reworkTopic('t3', 'none', { status: 'waiting', state: 'waiting' })
  const w = world(on, [reworkState(null, [reworkTopic('t2', 'none', { status: 'waiting', state: 'waiting' }), closed], { in_round: false }),
    reworkState('t3', [reworkTopic('t2', 'none', { status: 'waiting', state: 'waiting' }), closed], { in_round: true })],
    { quote: { stdout: reworkView('t3') } })
  await openRework($, w, reworkView('t2'))
  const before = w.placed.length
  const pane = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  await pane.press({ key: 'row-t3' })
  await pane.unmount()
  expect(w.placed.length).toBe(before + 1)
})

test('the last topic of a round offers no way on; its card closes with a ✕ instead, back to the overview', async ($, on) => {
  const head = '\u{f0182} **t1** **summary of t1** · `src/cache.ts:17`'
  const out = `${head}\n\nthe analysis\n\n<!-- paste-gate:critical\n` + JSON.stringify({ first: head, critical: [], topic: 't1', focus: 't1', review: REVIEW }) + '\n-->'
  const w = world(on, [stateOf('t1', [topic('t1', 'needs_ack')], { in_round: true, round: ['t1'] }), stateOf(null, [topic('t1', 'needs_ack')])])
  await openReview($, w, out)
  const card = await $.ui.mount(cardRow('tu-open', out))
  expect(await labels(card)).not.toContain('Next')
  await card.press({ key: 'card-close' })
  await card.unmount()
  expect(w.ran.map(r => r.argv.slice(2).join(' '))).toContain('leave --iid 42')
})

test("a card's ✕ keeps a column of space from its title, so a long title wraps before it", async ($, on) => {
  const head = '\u{f0182} **t1** **summary of t1** · `src/cache.ts:17`'
  const out = `${head}\n\nthe analysis\n\n<!-- paste-gate:critical\n` + JSON.stringify({ first: head, critical: [], topic: 't1', focus: 't1', review: REVIEW }) + '\n-->'
  const w = world(on, [stateOf('t1', [topic('t1', 'needs_ack')], { in_round: true, round: ['t1'] })])
  await openReview($, w, out)
  const card = await $.ui.mount(cardRow('tu-open', out))
  const gap = await card.find({ type: 'Box', key: 'card-close-gap' })
  expect(gap?.props.marginLeft).toBe(1)
  expect(gap?.props.flexShrink).toBe(0)
  expect(JSON.stringify(gap?.children)).toContain('card-close')
  await card.unmount()
})

test('a posted comment waiting on its author: its card offers to keep waiting, follow up or accept, and closes with its ✕', async ($, on) => {
  const head = '\u{f0182} **t1** **summary of t1** · `src/cache.ts:17`'
  const out = `${head}\n\nthe analysis\n\n<!-- paste-gate:critical\n` + JSON.stringify({ first: head, critical: [], topic: 't1', focus: 't1', review: REVIEW }) + '\n-->'
  const w = world(on, [stateOf('t1', [topic('t1', 'open')], { in_round: true, round: [] })])
  await openReview($, w, out)
  const card = await $.ui.mount(cardRow('tu-open', out))
  expect((await card.findAll({ type: 'Button' })).map(b => b.key)).toEqual(['card-close', 'keep-waiting', 'follow', 'resolve'])
  expect(await labels(card)).toEqual(['\u{F0156}', 'Keep waiting', 'Follow up', 'Resolve'])
  await card.press({ key: 'keep-waiting' })
  await card.unmount()
  expect(w.ran.map(r => r.argv.slice(2).join(' '))).toContain('leave --iid 42')
  // Keep waiting only closes its card: no event, so no line "You chose to wait" on it.
  const again = await $.ui.mount(cardRow('tu-open', out))
  expect(JSON.stringify(await again.findAll({ type: 'Text' }))).not.toContain('chose to wait')
  await again.unmount()
})

test('an error in the band goes once another card comes up', async ($, on) => {
  const w = world(on, [stateOf('t1', [topic('t1', 'open'), topic('t2', 'open')], { in_round: true })], { leave: { exitCode: 1, stdout: '', stderr: 'nope\n' } })
  await openReview($, w, view('t1'))
  const band = await mountStep($, 'terminal')
  await band.press({ key: 'keep-waiting' })                 // fails: the band says why
  expect(await band.find({ type: 'Text', text: /nope/i })).toBeDefined()
  await band.unmount()
  const pane = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  await pane.press({ key: 'row-t2' })                      // another card comes up
  await pane.unmount()
  const again = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await again.find({ type: 'Text', text: /nope/i })).toBeUndefined()
  await again.unmount()
})

test('a card drawn with a draft that is gone since shows no draft and no Copy', async ($, on) => {
  const head = '\u{f0182} **t1** **summary of t1** · `src/cache.ts:17`'
  const body = `${head}\n\n**\u{f05e1} Resolved on GitLab by Jonas**\n\nThat is their toggle.\n\n**\u{f12c4} Draft follow-up**\n_As a reply in the thread:_\n\n> Did you forget?`
  const out = body + '\n\n<!-- paste-gate:critical\n' + JSON.stringify({ first: head, critical: [], topic: 't1', focus: 't1', review: REVIEW }) + '\n-->'
  const w = world(on, [stateOf('t1', [topic('t1', 'open')], { in_round: true })])
  await openReview($, w, out)
  const card = await $.ui.mount(cardRow('tu-open', out))
  const md = (await card.findAll({ type: 'Markdown' })).map(m => String(m.props.text)).join('\n')
  expect(md).not.toContain('Follow up')                      // the heading, not the button
  expect(md).not.toContain('Did you forget?')
  expect(md).toContain('Resolved on GitLab by Jonas')
  expect((await card.findAll({ type: 'Button' })).some(b => String(b.key).startsWith('copy-'))).toBe(false)
  await card.unmount()
})

test("rework-mr: a redrafted reply's card has its Copy, and the card of the older version keeps neither draft nor Copy", async ($, on) => {
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'reply', { draft: 'v4' })])])
  const head = '\u{f0182} **t1** **x**'
  const older = `${head}\n\n**\u{f12c4} Draft reply**\n\n> Danke, ist umgesetzt, und noch viel mehr.\n\n<!-- paste-gate:critical\n` +
    JSON.stringify({ first: head, critical: [], topic: 't1', focus: 't1', review: REWORK, draft: 'v3' }) + '\n-->'
  // `reply-view --refine`: the new draft alone, no title line.
  const draftHead = '**\u{f12c4} Draft reply**'
  const newer = `${draftHead}\n\n> Danke, ist umgesetzt.\n\n<!-- paste-gate:critical\n` +
    JSON.stringify({ first: draftHead, critical: [], topic: 't1', focus: 't1', review: REWORK, draft: 'v4' }) + '\n-->'
  await openRework($, w, older)
  const old = await $.ui.mount(cardRow('tu-open', older))
  const md = (await old.findAll({ type: 'Markdown' })).map(m => String(m.props.text)).join('\n')
  expect(md).not.toContain('noch viel mehr')
  expect((await old.findAll({ type: 'Button' })).some(b => String(b.key).startsWith('copy-'))).toBe(false)
  await old.unmount()
  const card = await $.ui.mount(cardRow('tu-new', newer))
  expect((await card.findAll({ type: 'Button' })).filter(b => b.props.label === 'Copy').map(b => b.key)).toEqual(['copy-t1'])
  await card.unmount()
  // A card that was an older draft alone has nothing left: no empty frame.
  const oldest = newer.replace('"draft":"v4"', '"draft":"v2"')
  const gone = await $.ui.mount(cardRow('tu-oldest', oldest))
  expect(await gone.find({ type: 'Box', key: undefined } as never)).toBeDefined()
  expect((await gone.findAll({ type: 'Markdown' })).length).toBe(0)
  expect(JSON.stringify(await gone.findAll({ type: 'Box' }))).not.toContain('round')
  await gone.unmount()
})

test('a posted comment waiting on its author, with a follow-up drafted, offers to post it', async ($, on) => {
  const w = world(on, [stateOf('t1', [topic('t1', 'open', 'v1')], { in_round: true })])
  await openReview($, w, view('t1', 'v1'))
  const ui = await mountStep($, 'terminal')
  expect((await labels(ui)).slice(0, 4)).toEqual(['Post', 'Post + resolve', 'Check my post', 'Keep waiting'])
  await ui.unmount()
})

// GitLab's own "Reply and resolve thread": "post that a follow-up MR is fine, then resolve"
// once took the agent's "I can't post or resolve", a Post, and a walk back to the topic.
test('a drafted follow-up can be posted and its thread resolved in one press', async ($, on) => {
  const w = world(on, [stateOf('t1', [topic('t1', 'open', 'v1')], { in_round: true })])
  await openReview($, w, view('t1', 'v1'))
  const ui = await mountStep($, 'terminal')
  await ui.press({ key: 'post-resolve' })
  await ui.unmount()
  const post = w.ran.find(r => r.argv[2] === 'post')
  expect(post?.argv.slice(2).join(' ')).toBe('post t1 --expect v1 --resolve --iid 42')
  expect(await isPress(w, post?.env)).toBe(true)
})

test('rework-mr: a card whose fix is not pushed has no ✕: the topic is finished first', async ($, on) => {
  const head = '\u{f0182} **t1** **summary of t1** · `src/cache.ts:17`'
  const out = `${head}\n\nthe fix\n\n<!-- paste-gate:critical\n` + JSON.stringify({ first: head, critical: [], topic: 't1', focus: 't1', review: REWORK }) + '\n-->'
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'fixed', { checks: 'passed' })])])
  await openRework($, w, out)
  const card = await $.ui.mount(cardRow('tu-open', out))
  expect((await card.findAll({ type: 'Button' })).some(b => b.key === 'card-close')).toBe(false)
  await card.unmount()
})

test("rework-mr: a fix not pushed has no ✕ on the sidebar's file either, nor closes by its row", async ($, on) => {
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'fixed', { checks: 'passed' }), reworkTopic('t2', 'fix')], { in_round: true })],
    { code: { stdout: JSON.stringify(codeOf(50, [17, 17])) } })
  await openRework($, w, reworkView('t1'))
  const pane = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  expect(await pane.find({ type: 'Text', text: /cache\.ts/ })).toBeDefined()     // the file is shown
  expect(await pane.find({ type: 'Button', key: 'leave' })).toBeUndefined()
  await pane.press({ key: 'row-t1' })
  await pane.unmount()
  expect(w.ran.map(r => r.argv[2])).not.toContain('leave')
})

test('rework-mr: a fix not pushed holds its topic: another one is refused with a notice', async ($, on) => {
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'fixed', { checks: 'passed' }), reworkTopic('t2', 'plan')], { in_round: true })])
  await openRework($, w, reworkView('t1'))
  const pane = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  await pane.press({ key: 'row-t2' })
  await pane.unmount()
  expect(w.ran.map(r => r.argv.slice(2).join(' '))).not.toContain('quote t2 --focus --iid 42')
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(JSON.stringify(await band.findAll({ type: 'Text' }))).toContain("t1's fix isn't pushed yet: push it first")
  await band.unmount()
})

test('rework-mr: a push the script cannot finish goes to the model in its words', async ($, on) => {
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'fixed', { checks: 'passed' })])],
    { push: { exitCode: 3, stdout: 'Files changed since the checks ran on t1\'s fix: run `threads.py verify t1` and show the fix again.\n' } })
  await openRework($, w, reworkView('t1'))
  const ui = await mountStep($, 'terminal')
  await ui.press({ key: 'push' })
  await ui.unmount()
  expect(w.prompts).toEqual(['Files changed since the checks ran on t1\'s fix: run `threads.py verify t1` and show the fix again.'])
})

test('rework-mr: a drafted reply offers Post first, Post posts the version its card showed', async ($, on) => {
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'reply', { draft: 'v3' })])])
  await openRework($, w, reworkView('t1', 'v3'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Post', 'Post + resolve', 'Check my post', 'Next', 'Show sidebar'])
  await ui.press({ key: 'post-resolve' })
  await ui.unmount()
  const post = w.ran.find(r => r.argv[2] === 'post')
  expect(post?.argv.slice(2).join(' ')).toBe('post t1 --expect v3 --resolve --iid 42')
  expect(await isPress(w, post?.env)).toBe(true)
})

test("rework-mr: Post says what it did in the author's words, not the reviewer's", async ($, on) => {
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'reply', { draft: 'v3' })]), reworkState(null, [reworkTopic('t1', 'none')])])
  await openRework($, w, reworkView('t1', 'v3'))
  const ui = await mountStep($, 'terminal')
  await ui.press({ key: 'post' })
  await ui.unmount()
  const note = JSON.stringify(w.rows.appended())
  expect(note).toContain('t1: posted the reply in its thread')
  expect(note).not.toContain('linked to the posted comment')
})

test('rework-mr: a reply posted by hand is checked by the agent against the draft, as in review-mr', async ($, on) => {
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'reply', { draft: 'v3' })])])
  await openRework($, w, reworkView('t1', 'v3'))
  const ui = await mountStep($, 'terminal')
  await ui.press({ key: 'posted-check' })
  await ui.unmount()
  expect(w.prompts).toEqual([expect.stringMatching(/posted t1`.*posted t1 --ok/)])
})

test("rework-mr: a draft's Copy sits beside its heading, reads Copied! a moment, and the notice says once where to paste it", async ($, on) => {
  on('ui.copy' as never, (() => ({ value: { isCopied: true } })) as never)
  // The moment Copied! shows, held until the test lets it go.
  let release = () => {}
  on('clock.sleep' as never, (() => new Promise(done => { release = () => done({ value: undefined }) })) as never)
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'reply', { draft: 'v3' })],
    { thread: [{ url: 'https://gl/g/p/-/merge_requests/42#note_5', notes: [] }] })], { reply: { stdout: 'Danke, ist umgesetzt.\n' } })
  const out = '\u{f0182} **t1** **x**\n\n**\u{f12c4} Draft reply**\n\n> Danke, ist umgesetzt.\n\n<!-- paste-gate:critical\n' +
    JSON.stringify({ first: '\u{f0182} **t1** **x**', critical: [], topic: 't1', focus: 't1', review: REWORK, draft: 'v3' }) + '\n-->'
  await openRework($, w, out)
  const card = await $.ui.mount(cardRow('tu-open', out))
  // Not in the row of buttons: the one Copy is the heading's.
  expect((await card.findAll({ type: 'Button' })).filter(b => b.props.label === 'Copy').map(b => b.key)).toEqual(['copy-t1'])
  const pressed = card.press({ key: 'copy-t1' })
  await new Promise<void>(done => setTimeout(() => done(), 20))
  expect(JSON.stringify(await card.findAll({ type: 'Text' }))).toContain('Copied!')
  expect((await card.findAll({ type: 'Button' })).some(b => b.key === 'copy-t1')).toBe(false)   // not pressable meanwhile
  release()
  await pressed
  expect((await card.findAll({ type: 'Button' })).some(b => b.key === 'copy-t1')).toBe(true)    // Copy again
  await card.unmount()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect((await band.find({ type: 'Link' }))?.props).toMatchObject({ href: 'https://gl/g/p/-/merge_requests/42#note_5', label: 'thread \u{f03cc}' })
  await band.unmount()
})

test('a follow-up posted by hand is checked against its draft, as a comment is', async ($, on) => {
  const w = world(on, [stateOf('t1', [topic('t1', 'needs_ack', 'v2')])])
  await openReview($, w, view('t1', 'v2'))
  const ui = await mountStep($, 'terminal')
  await ui.press({ key: 'link' })
  await ui.unmount()
  expect(w.prompts[0]).toMatch(/^I posted the comment for t1 myself.*posted t1`.*--differs/)
  expect(w.ran.some(r => r.argv[2] === 'sync')).toBe(false)
})

test('a comment posted by hand that no longer holds: Apply edit first, as a press; the same card in both skills', async ($, on) => {
  const w = world(on, [stateOf('t3', [Object.assign(topic('t3', 'open'), { post_check: true, post_differs: true })]), stateOf(null, [topic('t3', 'open')])],
    { posted: { stdout: 'edited the posted comment of t3: https://gl/g/p/-/merge_requests/42#note_7\n' } })
  await openReview($, w, view('t3'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Apply edit', 'Check again', 'Keep as posted', 'Next', 'Show sidebar'])
  await ui.press({ key: 'apply-edit' })
  await ui.unmount()
  const ran = w.ran.find(r => r.argv[2] === 'posted')
  expect(ran?.argv.slice(2).join(' ')).toBe('posted t3 --apply --iid 42')
  expect(await isPress(w, ran?.env)).toBe(true)
})

test('rework-mr: a reply posted by hand that no longer holds offers the same card', async ($, on) => {
  const w = world(on, [reworkState('t2', [reworkTopic('t2', 'reply', { draft: 'v3', post_differs: true })])])
  await openRework($, w, reworkView('t2', 'v3'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Apply edit', 'Check again', 'Keep as posted', 'Next', 'Show sidebar'])
  await ui.press({ key: 'check-again' })
  await ui.unmount()
  expect(w.prompts[0]).toMatch(/^I edited the comment for t2 on GitLab\. .*posted t2 --differs/)
})

test('rework-mr: a reply ahead of the fix posted by hand that no longer holds gets the same card, not its step\'s', async ($, on) => {
  const w = world(on, [reworkState('t2', [reworkTopic('t2', 'fix', { early: true, draft: 'v3', post_differs: true })], { pass: 'plan' })])
  await openRework($, w, reworkView('t2', 'v3'))
  const ui = await mountStep($, 'terminal')
  expect((await labels(ui)).slice(0, 3)).toEqual(['Apply edit', 'Check again', 'Keep as posted'])
  expect(await labels(ui)).not.toContain('Post')
  await ui.unmount()
})

test('rework-mr: the model may not post on its own', async ($, on) => {
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'reply', { draft: 'v3' })])])
  await openRework($, w, reworkView('t1'))
  const got = await $.tool.call({ tool: 'Bash', command: 'python3 $SD/threads.py post t1 --iid 42' } as never)
  expect((got as { deny?: string }).deny).toContain('press')
})

// The Merged box was drawn like any other, and went from "approved" straight to "merged".
test('a merged MR looks like success: its box and its merge line in green, the pipeline that passed in its story', async ($, on) => {
  const mr = { state: 'merged', merged_at: '2026-11-09T15:20:00Z', merged_by: 'You', auto_merge: false, approved_by: ['Miriam'], pipeline: { status: 'passed' } }
  const w = world(on, [reworkState(null, [reworkTopic('t1', 'none')], {
    mr, in_round: false, inbox: inbox({ mr: [{ kind: 'approved', at: '2026-11-09T15:00:00Z', by: 'Miriam' }, { kind: 'merged', at: '2026-11-09T15:20:00Z', by: 'You' }] }),
  })])
  await openRework($, w, reworkView('t1'))
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  const drawn = JSON.stringify(await panel.findAll({ type: 'Box' }))
  expect(drawn).toContain('"borderColor":"success"')
  expect((await panel.find({ type: 'Text', text: 'Merged' }))?.props.color).toBe('success')
  expect((await panel.find({ type: 'Text', text: 'You merged the MR' }))?.props.color).toBe('success')
  // The merged icon is green everywhere: the heading's too.
  expect((await panel.findAll({ type: 'Text' })).filter(t => t.text === '\u{f062d}').map(t => t.props.color)).toEqual(['success', 'success'])
  const story = (await panel.findAll({ type: 'Text' })).map(t => String(t.text ?? '')).filter(t => /approved|pipeline|merged the MR/.test(t))
  expect(story).toEqual(['Miriam approved the MR', 'The pipeline passed', 'You merged the MR'])
  await panel.unmount()
})

test('the pipeline that passed has its time, and its place in the story by it', async ($, on) => {
  const mr = { state: 'merged', merged_at: '2026-11-09T15:20:00Z', merged_by: 'You', auto_merge: false, approved_by: ['Miriam'],
    pipeline: { status: 'passed', at: '2026-11-09T15:10:00Z' } }
  const r = JSON.parse(reworkState(null, [reworkTopic('t1', 'none')], {
    mr, in_round: false, inbox: inbox({ mr: [{ kind: 'approved', at: '2026-11-09T15:15:00Z', by: 'Miriam' }, { kind: 'merged', at: '2026-11-09T15:20:00Z', by: 'You' }] }),
  }))
  const lines = inboxOf(r)?.lines.filter(l => /approved|pipeline|merged the MR/.test(l.what)).map(l => [l.at, l.what])
  expect(lines).toEqual([['2026-11-09T15:10:00Z', 'The pipeline passed'], ['2026-11-09T15:15:00Z', 'Miriam approved the MR'],
    ['2026-11-09T15:20:00Z', 'You merged the MR']])
})

test('news that is no good end keeps the box as it was: no green', async ($, on) => {
  const w = world(on, [reworkState(null, [{ ...reworkTopic('t1', 'none'), state: 'waiting', status: 'waiting' }], {
    mr: { state: 'opened' }, in_round: false, inbox: inbox({ waiting: 1 }) })])
  await openRework($, w, reworkView('t1'))
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  expect(JSON.stringify(await panel.findAll({ type: 'Box' }))).not.toContain('"borderColor"')
  expect((await panel.find({ type: 'Text', text: 'Done for now' }))?.props.color).toBeUndefined()
  await panel.unmount()
})

test('a merged MR says so at the top and in its box, with what still matters and the drafts nobody saw', async ($, on) => {
  const mr = { state: 'merged', merged_at: '2026-11-09T15:20:00Z', merged_by: 'Jan', auto_merge: false, approved_by: ['Jan'] }
  const merged = { left: ['t1'], drafts: ['t4'], quiet: 2 }
  const push = { at: '2026-11-09T15:10:00Z', stat: '\u{f4d2} 1 file +2 −0', topics: [], rebase: false, url: 'https://gl/g/r/-/merge_requests/42/diffs?diff_id=3' }
  const w = world(on, [stateOf(null, [Object.assign(topic('t1', 'open'), { left: true })], {
    mr, merged, in_round: false, author: 'Jan',
    inbox: inbox({ pushes: [push], mr: [{ kind: 'merged', at: '2026-11-09T15:20:00Z', by: 'Jan' }] }),
  })])
  await openReview($, w, view('t1'))
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  const texts = (await panel.findAll({ type: 'Text' })).map(t => t.text ?? '')
  // The heading says it is merged; who did it, and when, is the box's last step, said once.
  expect(texts).toContain('Merged')
  expect(texts.filter(t => t.includes('Jan merged'))).toEqual(['Jan merged the MR'])
  expect(texts.some(t => / by Jan$/.test(t))).toBe(false)
  expect(texts).toContain('\u{f062d}')
  const md = (await panel.findAll({ type: 'Markdown' })).map(m => String(m.props.text))
  expect(md.some(m => m.startsWith('[Jan pushed') && m.endsWith(`](${push.url})`))).toBe(true)
  expect(texts).toContain('Jan left it open as the MR closed')
  expect(texts).toContain('its draft was never posted')
  // GitLab is not asked again: no word of checks, no key for one.
  expect(await panel.find({ type: 'Button', key: 'check-now' })).toBeUndefined()
  expect(texts.some(t => /^(checked|next check) /i.test(t))).toBe(false)
  await panel.unmount()
})

// A plain push said "2 files" first and a rebase ended in a diffstat: two forms for one
// thing, where the box is the overview and the topics' cards show what changed.
test('every push is a dim line of the box in one form: who, a rebase or not, its topics; auto-merge goes once the MR merged', () => {
  const r = JSON.parse(stateOf(null, [topic('t1', 'open')], { author: 'Jan', inbox: inbox({
    pushes: [
      { at: '2026-11-09T15:10:00Z', stat: '\u{f4d2} 0 files +0 −0', topics: [], rebase: false, same: true, url: 'u0' },
      { at: '2026-11-09T15:12:00Z', stat: '\u{f4d2} 1 file +2 −0', topics: ['t1'], rebase: false, url: 'u1' },
      { at: '2026-11-09T15:13:00Z', stat: '\u{f4d2} 40 files +900 −100', topics: [], rebase: true, own: 0, url: 'u2' },
      { at: '2026-11-09T15:15:00Z', stat: '\u{f4d2} 42 files +905 −100', topics: ['t2', 't4'], rebase: true, own: 2, onto: 'main', url: 'u3' },
    ],
    mr: [{ kind: 'auto-merge', at: '2026-11-09T15:11:00Z', by: null }, { kind: 'merged', at: '2026-11-09T15:20:00Z', by: 'Jan' }],
  }) }))
  const lines = inboxOf(r)?.lines.map(l => `${l.at?.slice(11, 16)} ${l.tone} ${l.kind} ${l.what}${l.url ? ` -> ${l.url}` : ''}`)
  expect(lines).toEqual([
    // One that left the code as it was links to nothing: there is nothing to see.
    '15:10 info push Jan pushed',
    '15:12 info push Jan pushed · t1 -> u1',
    '15:13 info push Jan rebased and pushed',
    '15:15 info push Jan rebased and pushed · t2, t4 -> u3',
  ])
})

test('the box gives one line a topic with the verdict, resolved-but-not-fixed said as such, and +n more past five', () => {
  const ts = ['t1', 't2', 't3', 't4', 't5', 't6', 't7'].map(id => ({ ...topic(id, 'needs_ack'), inbox: 'new', verdict: id === 't2' ? 'more' : id === 't1' ? 'settled' : null }))
  const r = JSON.parse(stateOf(null, ts, { author: 'Jonas', inbox: inbox({ replies: ['t1', 't3', 't4', 't5', 't6', 't7'], resolved: ['t2'], first: 't1' }) }))
  const box = inboxOf(r, { topics: ['t3'], failed: [] })
  expect(box?.lines.slice(0, 3).map(l => [l.topic, l.what, l.tone])).toEqual([
    ['t1', 'Jonas answered: looks fixed', 'needs'], ['t3', 'Jonas answered: judging…', 'busy'], ['t4', 'Jonas answered', 'needs']])
  expect(box?.lines.find(l => l.topic === 't2')).toBeUndefined()          // past five
  expect(box?.more).toBe(2)
  const one = inboxOf(JSON.parse(stateOf(null, ts, { author: 'Jonas', inbox: inbox({ resolved: ['t2'], first: 't2' }) })))
  expect(one?.lines.map(l => l.what)).toEqual(['Jonas resolved, not fixed'])
  // The button opens the first topic flagged new, in the list's order, and is named after it.
  expect(one?.button).toBe('Open t1')
  expect(one?.firstNew).toBe('t1')
})

test("the box says an answer's verdict in its card's words: partly fixed where the card says so", () => {
  const t1 = { ...topic('t1', 'needs_ack'), inbox: 'new', verdict: 'more', verdict_case: 'partly-fixed' }
  const t2 = { ...topic('t2', 'needs_ack'), inbox: 'new', verdict: 'more', verdict_case: 'fix-wrong' }
  const r = JSON.parse(stateOf(null, [t1, t2], { author: 'Alex', inbox: inbox({ replies: ['t1'], resolved: ['t2'], first: 't1' }) }))
  expect(inboxOf(r)?.lines.map(l => l.what)).toEqual(['Alex answered: partly fixed', 'Alex resolved, fix is wrong'])
})

test("a merged MR's box offers to check an answer not accepted yet, not to open a round", async ($, on) => {
  const mr = { state: 'merged', merged_at: '2026-11-09T15:20:00Z', merged_by: 'Jan' }
  const w = world(on, [stateOf(null, [Object.assign(topic('t1', 'needs_ack'), { inbox: 'yours', news: ['resolved'] })], {
    mr, merged: { left: [], drafts: [], quiet: 0 }, in_round: false, inbox: inbox({ resolved: ['t1'], first: 't1' }),
  })])
  await openReview($, w, view('t1'))
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  expect((await panel.find({ type: 'Button', key: 'open-round' }))?.props.label).toBe('Check the answers')
  await panel.unmount()
})

test("a merged MR's topic shown by its thread alone, with no file, closes by the thread's ✕", async ($, on) => {
  const t1 = Object.assign(topic('t1', 'needs_ack'), { verdict: 'settled', resolved: true })
  const thread = [{ url: 'http://gitlab.test/x#note_1', file: 'src/cache.ts', line: 17, notes: [{ author: 'Ana', body: 'rename it' }] }]
  const w = world(on, [stateOf('t1', [t1], { mr: { state: 'merged' }, thread, round: ['t1'] })])
  await openReview($, w, view('t1'))
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  expect(await panel.find({ type: 'Text', text: 'Thread' })).toBeDefined()
  await panel.press({ key: 'leave' })
  await panel.unmount()
  expect(w.ran.map(r => r.argv[2])).toContain('leave')
})

test('a thread with no file is the thread box, always in full, scrolling on its own', async ($, on) => {
  const long = Array.from({ length: 40 }, (_, i) => `Paragraph ${i + 1}, about the README and the flags drifting apart.`).join(' ')
  const thread = [{ url: 'http://gitlab.test/x#note_1', notes: [{ author: 'Ana', body: long }, { author: 'You', body: 'will check', mine: true }] }]
  const w = world(on, [stateOf('t1', [topic('t1', 'open')], { thread })])
  await openReview($, w, view('t1'))
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  expect(await panel.find({ type: 'Text', text: 'Thread' })).toBeDefined()    // the area's line
  const box = (await panel.findAll({ type: 'Box' })).find(b => b.props.borderStyle === 'round' && b.props.borderDimColor)
  expect(box).toBeDefined()
  expect(await panel.find({ type: 'Button', key: 'thread-open' })).toBeUndefined()   // never folded
  // Taller than its area: its own scrollbar, its rows a window of the thread, the reply
  // further down coming into view as it scrolls.
  expect(await bar(panel, 'box')).not.toBeNull()
  const md = async () => (await panel.findAll({ type: 'Markdown' })).map(m => String(m.props.text))
  // At its end, on the newest comment; the thread's start comes into view as it scrolls up.
  expect(await md()).toContain('will check')
  expect((await md()).some(t => t.includes('Paragraph 1,'))).toBe(false)
  for (let i = 0; i < 40 && (await bar(panel, 'box'))?.[0] !== 'thumb'; i += 1) await page(panel, 'box', 'up')
  expect(await md()).not.toContain('will check')
  expect((await md()).some(t => t.includes('Paragraph 1,'))).toBe(true)
  await panel.unmount()
})

test('a thread with no file: the second comment under a thin rule, its code block open with no key to fold it', async ($, on) => {
  const mine = { author: 'You', body: 'like:\n\n```ts\nexport const FLAGS = {}\n```', mine: true }
  const w2 = world(on, [stateOf('t1', [topic('t1', 'open')], { thread: [{ url: 'http://gitlab.test/x#note_1', notes: [{ author: 'Ana', body: 'docs?' }, mine] }] })])
  await openReview($, w2, view('t1'))
  const short = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  expect((await short.findAll({ type: 'Text' })).some(t => /^─{10,}$/.test(String(t.text ?? '')))).toBe(true)
  expect((await short.findAll({ type: 'Code' })).map(c => String(c.props.source))).toContain('export const FLAGS = {}')
  expect((await short.findAll({ type: 'Button' })).some(b => String(b.props.key ?? '').includes('fold-'))).toBe(false)
  await short.unmount()
})

// A thread and the comments on the MR, as the sidebar shows them for each look the person
// meets: one comment or several, from different people, short and long, with a list, a code
// block and a suggestion; the comments on the MR folded, open, scrolled and read. What the
// terminal paints is the engine's; these pin what the mod hands it.
type Drawn = Awaited<ReturnType<Awaited<ReturnType<Engine['ui']['mount']>>['find']>>
const note = (author: string, n: number, body: string, extra: object = {}) => ({ author, body, url: `http://gl/x#note_${n}`, ...extra })
const sentences = (what: string, n: number) => Array.from({ length: n }, (_, i) => `${what} ${i + 1} says a little more about it.`).join(' ')
type Sidebar = { find: (q: { type: string; key?: string; text?: string | RegExp }) => Promise<Drawn>; findAll: (q: { type: string; key?: string; text?: string | RegExp }) => Promise<NonNullable<Drawn>[]> }
const textsOf = async (ui: Sidebar) => (await ui.findAll({ type: 'Text' })).map(t => String(t.text ?? ''))
// A writer's name as drawn, in their colour: the Text that is the name alone, not its line.
const colourOf = async (ui: Sidebar, name: string) => (await ui.findAll({ type: 'Text', text: name })).filter(t => t.text === name).map(t => t.props.color)[0]
const hrefsOf = async (ui: Sidebar) => (await ui.findAll({ type: 'Link' })).map(l => String(l.props.href))
// The rows the person sees of each area, its line and borders included: the thread's (its
// line, the box's two borders, its rows) and the comments' on the MR (their line, the window).
const threadHeight = async (ui: Sidebar) => 3 + ((await ui.find({ type: 'Box', key: 'box-text' }))?.children.length ?? 0)
const mrHeight = async (ui: Sidebar) => 1 + Number((await ui.find({ type: 'Box', key: 'mr-window' }))?.props.height ?? 0)
const newMarks = async (ui: Sidebar) => (await textsOf(ui)).filter(t => t === ' new').length
// The update check's clock, held: each `tick` lets one period run, as the 30 seconds passing.
const heldClock = (on: Parameters<typeof world>[0]) => {
  let release = () => {}
  on('clock.every' as never, (() => new Promise(done => { release = () => done({ value: undefined }) })) as never)
  return async () => {
    release()
    await new Promise<void>(done => setTimeout(() => done(), 30))
  }
}
// Pages an area down until its scrollbar's thumb reaches the end of its track.
const toEnd = async (ui: Scrolls, tag: string) => {
  for (let i = 0; i < 40 && (await bar(ui, tag))?.at(-1) !== 'thumb'; i += 1) await page(ui, tag, 'down')
}

test('a thread with no file from several people: one box, a thin rule between comments, each its link and its writer in colour', async ($, on) => {
  const notes = [
    note('Ana', 1, 'The README and the flags drift apart.\n\nA second paragraph, which says why that matters.', { at: '2026-11-05T09:00:00' }),
    note('Kim', 2, 'Agreed. Two ideas:\n\n- generate the table\n- check it in CI'),
    note('You', 3, 'like this:\n\n```ts\nexport const FLAGS = { cache: true }\n```', { mine: true }),
    note('Lee', 4, '```suggestion:-0+0\nexport const FLAGS = { cache: false }\n```'),
  ]
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'plan')], { thread: [{ url: 'http://gl/x#note_1', notes }], people: ['Ana', 'Kim', 'Lee'] })])
  await openRework($, w, reworkView('t1'))
  const ui = await $.ui.mount({ ...PANEL, props: { ...PANEL.props, scroll: { offset: 0, bodyRows: 60 } }, surface: 'terminal' })
  // One box for the thread, its comments under thin rules: three rules for four comments.
  expect((await ui.findAll({ type: 'Box' })).filter(b => b.props.borderStyle === 'round').length).toBe(1)
  expect((await textsOf(ui)).filter(t => /^─{10,}$/.test(t)).length).toBe(3)
  // Each comment's line: its link, its writer, each writer a colour of their own.
  expect(await hrefsOf(ui)).toEqual(expect.arrayContaining(notes.map(n => n.url)))
  const colours = await Promise.all(['Ana', 'Kim', 'Lee', 'You'].map(a => colourOf(ui, a)))
  expect(new Set(colours).size).toBe(4)
  expect(colours).not.toContain(undefined)
  // Paragraphs stay apart, a row between them, as GitLab shows them; a list keeps its items.
  const rows = (await ui.find({ type: 'Box', key: 'box-text' }))?.children as NonNullable<Drawn>[]
  const textOf = (n: unknown): string => typeof n === 'string' ? n
    : [String((n as Drawn)?.props?.text ?? ''), ...((n as Drawn)?.children ?? []).map(textOf)].join('')
  const rowOf = (s: string) => rows.findIndex(r => textOf(r).includes(s))
  expect(rowOf('A second paragraph') - rowOf('drift apart')).toBe(2)
  expect(rowOf('check it in CI') - rowOf('generate the table')).toBe(1)
  // The code block and the suggestion open, highlighted in the block's language, nothing to fold.
  const code = await ui.findAll({ type: 'Code' })
  expect(code.find(c => String(c.props.source).includes('cache: true'))?.props.language).toBe('ts')
  expect(code.some(c => String(c.props.source).includes('cache: false'))).toBe(true)
  expect((await ui.findAll({ type: 'Button' })).some(b => /fold-|thread-open/.test(String(b.key ?? '')))).toBe(false)
  // With no comment on the MR itself there is no line for them at all.
  expect(await textsOf(ui)).not.toContain('On the MR')
  await ui.unmount()
})

test('a thread under the code puts the same thin rule between its comments', async ($, on) => {
  const thread = [{ url: 'u', file: 'src/cache.ts', line: 17, notes: [note('Ana', 1, 'odd'), note('Kim', 2, 'agreed')] }]
  const w = world(on, [reworkState('t1', [reworkTopic('t1', 'plan')], { thread })], { code: { stdout: JSON.stringify(codeOf(50, [17, 17])) } })
  await openRework($, w, reworkView('t1'))
  const ui = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  expect((await textsOf(ui)).filter(t => /^─{10,}$/.test(t)).length).toBe(1)
  await ui.unmount()
})

// The comments on the MR beside a thread with no file: Sam's short one, then Jo's and Sam's
// longer ones, the last with a code block.
const MR_COMMENTS = [
  note('Sam', 7, 'Looks good overall.', { at: '2026-11-05T10:00:00' }),
  note('Jo', 8, sentences('Point', 4)),
  note('Sam', 9, `${sentences('Detail', 3)}\n\n\`\`\`sh\nmake docs\n\`\`\``),
]
const withComments = (thread: object[], comments: object[]) =>
  reworkState('t1', [reworkTopic('t1', 'plan')], { thread: [{ url: 'http://gl/x#note_1', notes: thread }], mr_comments: comments, people: ['Ana', 'Sam', 'Jo'] })

test('the comments on the MR: folded to their line at first, open a box each, half the room each with a long thread, read once whole on screen', async ($, on) => {
  const tick = heldClock(on)
  on('tool.register' as never, (() => ({ value: undefined })) as never)
  on('session.start' as never, ((_: unknown, e: object) => e) as never)
  // What the mod keeps past the session, as it last wrote it.
  const kept: Record<string, unknown> = {}
  const w = world(on, [withComments([note('Ana', 1, sentences('Paragraph', 40))], MR_COMMENTS)], {}, kept)
  await (($ as unknown as { session: { start: (e: object) => Promise<unknown> } }).session.start({ source: 'startup', cwd: '/wt' }))
  await openRework($, w, reworkView('t1'))
  const ui = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  // Folded: its line alone, how many and how many are new.
  expect(await textsOf(ui)).toEqual(expect.arrayContaining(['On the MR', ' · 3 comments', ' · 3 new']))
  expect(await ui.find({ type: 'Box', key: 'mr-window' })).toBeUndefined()
  // Open: a box each, its link and its writer in their colour.
  await ui.press({ key: 'mr-fold' })
  for (const i of [0, 1, 2]) expect(await ui.find({ type: 'Box', key: `mr-box-${i}` })).toBeDefined()
  expect(await hrefsOf(ui)).toEqual(expect.arrayContaining(MR_COMMENTS.map(c => c.url)))
  const sam = await colourOf(ui, 'Sam')
  const jo = await colourOf(ui, 'Jo')
  expect(sam).toBeDefined()
  expect(jo).toBeDefined()
  expect(sam).not.toBe(jo)
  // Both need more than half the room: half each, each scrolling on its own.
  expect(await bar(ui, 'box')).not.toBeNull()
  expect(await bar(ui, 'mr')).not.toBeNull()
  expect(Math.abs((await threadHeight(ui)) - (await mrHeight(ui)))).toBeLessThanOrEqual(1)
  // Every comment new until the update check says what was whole on screen: the first two.
  expect(await newMarks(ui)).toBe(3)
  await tick()
  expect(await textsOf(ui)).toContain(' · 1 new')
  expect(await newMarks(ui)).toBe(1)
  // Scrolled to the last, it is read at the next check, and the line says nothing is new.
  await toEnd(ui, 'mr')
  await tick()
  expect((await textsOf(ui)).some(t => / new$/.test(t))).toBe(false)
  // Kept for the MR past the session: open, and what was read.
  expect(kept['mr-comments-1:!42']).toEqual({ open: true, seen: MR_COMMENTS.map(c => c.url) })
  // Minimized: back to its line, still counting the comments.
  await ui.press({ key: 'mr-fold' })
  expect(await ui.find({ type: 'Box', key: 'mr-window' })).toBeUndefined()
  expect(await textsOf(ui)).toContain(' · 3 comments')
  expect(kept['mr-comments-1:!42']).toMatchObject({ open: false })
  await ui.unmount()
})

test('the comments on the MR come back as the person left them for that MR: open, the ones read not new', async ($, on) => {
  const store = { 'mr-comments-1:!42': { open: true, seen: ['http://gl/x#note_7'] } }
  const w = world(on, [withComments([note('Ana', 1, 'docs?')], MR_COMMENTS)], {}, store)
  await openRework($, w, reworkView('t1'))
  const ui = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  expect(await ui.find({ type: 'Box', key: 'mr-window' })).toBeDefined()
  expect(await textsOf(ui)).toContain(' · 2 new')
  await ui.unmount()
})

test('one short comment on the MR takes only its rows, a long thread the rest', async ($, on) => {
  const w = world(on, [withComments([note('Ana', 1, sentences('Paragraph', 40))], MR_COMMENTS.slice(0, 1))], {}, { 'mr-comments-1:!42': { open: true } })
  await openRework($, w, reworkView('t1'))
  const ui = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  // Its line and its box: two borders, the head and one line of text.
  expect(await mrHeight(ui)).toBe(1 + 4)
  expect(await bar(ui, 'mr')).toBeNull()
  expect(await threadHeight(ui)).toBeGreaterThan(2 * (await mrHeight(ui)))
  await ui.unmount()
})

test('a short thread leaves its spare room to the comments on the MR', async ($, on) => {
  const long = [0, 1, 2, 3].map(i => note(i % 2 ? 'Jo' : 'Sam', 10 + i, sentences('Point', 8)))
  const w = world(on, [withComments([note('Ana', 1, 'docs?')], long)], {}, { 'mr-comments-1:!42': { open: true } })
  await openRework($, w, reworkView('t1'))
  const ui = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  // The thread is its line and a box of one comment's two rows; the comments take the rest.
  expect(await threadHeight(ui)).toBe(3 + 2)
  expect(await mrHeight(ui)).toBeGreaterThan(2 * (await threadHeight(ui)))
  expect(await bar(ui, 'mr')).not.toBeNull()
  await ui.unmount()
})

test("a press on a merged MR whose review worktree is gone runs from the session's folder", async ($, on) => {
  const gone = { ...REVIEW, cwd: '/wt.reviews/mr-42' }
  const head = `\u{f0182} **t1** \`src/cache.ts:17\``
  const out = `${head}\n\nsome view\n\n<!-- paste-gate:critical\n${JSON.stringify({ first: head, critical: [], topic: 't1', focus: 't1', review: gone })}\n-->`
  const t1 = Object.assign(topic('t1', 'needs_ack'), { verdict: 'settled', verdict_text: 'Holds.', resolved: true })
  const w = world(on, [stateOf('t1', [t1], { mr: { state: 'merged' } }), stateOf(null, [{ ...t1, status: 'acked', state: 'done' }], { mr: { state: 'merged' } })])
  await openReview($, w, out)
  const ui = await mountStep($, 'terminal')
  await ui.press({ key: 'ack' })
  await ui.unmount()
  const ack = w.ran.find(r => r.argv.slice(2, 4).join(' ') === 'set t1')
  expect(ack?.cwd).toBe('/wt')
})

test('a topic a merge left: Summarize, or Drop it', async ($, on) => {
  const w = world(on, [stateOf('t1', [Object.assign(topic('t1', 'open'), { left: true })], { mr: { state: 'merged' } })])
  await openReview($, w, view('t1'))
  const ui = await mountStep($, 'terminal')
  expect(await labels(ui)).toEqual(['Summarize', 'Drop', 'Next', 'Show sidebar'])
  await ui.press({ key: 'summarize' })
  await ui.unmount()
  expect(w.prompts[0]).toMatch(/^!42 is merged, and t1 was not settled\. Summarize/)
})

test("the MR's own news: icons in the heading, the same icon and colour with its words in the News box", async ($, on) => {
  const mr = { state: 'opened', auto_merge: true, approved_by: ['Jan', 'You'] }
  const w = world(on, [stateOf('t1', [topic('t1', 'draft', 'abc')], { mr })])
  await openReview($, w, view('t1'))
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  const all = await panel.findAll({ type: 'Text' })
  // Auto-merge set is a wait, no warning: no colour. Approved is green.
  expect(all.find(t => t.text === '\u{f051f}')?.props.color).toBeUndefined()
  expect(all.find(t => t.text === '\u{f0514}')?.props.color).toBe('success')
  expect(all.some(t => /auto-merge on|Jan, You/.test(t.text ?? ''))).toBe(false)
  await panel.unmount()
})

test("the MR's head pipeline is an icon beside the approval: running, passed, failed, none without one", async ($, on) => {
  // Running is a wait, no warning: no colour.
  const icons = { running: ['\u{f0996}', undefined], passed: ['\u{f0995}', 'success'], failed: ['\u{f110a}', 'error'] }
  const w = world(on, [
    ...Object.keys(icons).map(status => stateOf('t1', [topic('t1', 'draft', 'abc')], { mr: { state: 'opened', approved_by: ['Jan'], pipeline: { status } } })),
    stateOf('t1', [topic('t1', 'draft', 'abc')], { mr: { state: 'opened', approved_by: ['Jan'], pipeline: null } }),
  ])
  await openReview($, w, view('t1'))
  const all = Object.values(icons).map(([icon]) => icon as string)
  for (const [icon, color] of Object.values(icons)) {
    const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
    const texts = await panel.findAll({ type: 'Text' })
    expect(texts.find(t => t.text === icon)?.props.color).toBe(color)
    expect(texts.find(t => t.text === '\u{f0514}')).toBeDefined()
    await panel.unmount()
    await $.tool.call({ tool: 'Bash', command: 'python3 $SD/findings.py drop t9 --iid 42' } as never)
  }
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  expect((await panel.findAll({ type: 'Text' })).some(t => all.includes(String(t.text ?? '')))).toBe(false)
  await panel.unmount()
})

test("between rounds the box lists the MR's own news as dim lines: an approval, auto-merge set", async ($, on) => {
  const mr = { state: 'opened', auto_merge: true, approved_by: ['Jan'] }
  const news = [{ kind: 'auto-merge', at: '2026-11-09T15:00:00Z', by: null }, { kind: 'approved', at: '2026-11-09T15:05:00Z', by: 'Jan' }]
  const w = world(on, [stateOf(null, [topic('t1', 'open')], { mr, in_round: false, inbox: inbox({ mr: news }) })])
  await openReview($, w, view('t1'))
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  const all = await panel.findAll({ type: 'Text' })
  expect(all.find(t => t.text === 'Auto-merge set')?.props.dimColor).toBe(true)
  expect(all.find(t => t.text === 'Jan approved the MR')?.props.dimColor).toBe(true)
  // A box whose news carries no time says News alone, never "since –".
  expect(all.some(t => (t.text ?? '').includes('since –'))).toBe(false)
  // Each led by its kind's icon, plain and dim: the colour is the heading's marks'.
  const lead = all.filter(t => ['\u{f051f}', '\u{f0514}'].includes(String(t.text)) && t.props.dimColor === true)
  expect(lead.map(t => [t.text, t.props.color])).toEqual([['\u{f051f}', undefined], ['\u{f0514}', undefined]])
  // The legend names the heading's marks, and nothing for a line only to know of.
  await panel.press({ key: 'legend' })
  const words = (await panel.findAll({ type: 'Text' })).map(t => String(t.text ?? ''))
  for (const word of [' auto-merge set', ' approved']) expect(words.some(t => t.endsWith(word))).toBe(true)
  expect(words.some(t => t.includes('to know of'))).toBe(false)
  await panel.unmount()
})

test('a merged MR: Accept closes the topic, and any topic it left can be closed from the sidebar', async ($, on) => {
  const mr = { state: 'merged', merged_at: '2026-11-09T15:20:00Z', merged_by: 'Jan', auto_merge: true, approved_by: ['Jan'] }
  const t1 = Object.assign(topic('t1', 'needs_ack'), { verdict: 'settled', verdict_text: 'Holds.', resolved: true, inbox: 'yours' })
  const extra = { mr, merged: { left: [], drafts: [], quiet: 0 }, in_round: true, round: ['t1'] }
  const w = world(on, [stateOf('t1', [t1], extra), stateOf(null, [{ ...t1, status: 'acked', state: 'done' }], extra)], { code: { stdout: JSON.stringify(codeOf(50, [17, 17])) } })
  await openReview($, w, view('t1'))
  w.panes.push('review-ui-topics')
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  expect(await panel.find({ type: 'Button', key: 'leave' })).toBeDefined()
  await panel.unmount()
  const ui = await mountStep($, 'terminal')
  await ui.press({ key: 'ack' })
  await ui.unmount()
  expect(w.ran.map(r => r.argv.slice(2).join(' '))).toContain('set t1 --state acked --iid 42')
  expect(JSON.stringify(w.rows.appended())).toContain('No topic needs the user now')
})

test('every step button has its icon, one per action in both skills', () => {
  // Every label a step offers, in either skill: a label missing here draws without an icon.
  const labels = ['Agree plan', 'Agree and reply', 'Reply…', 'Reply instead…', 'Reply now', 'Reply', 'Comment',
    'Follow up', 'Follow up', 'Fix it', 'Fix instead…', 'Change plan…', 'Propose a plan', 'Explain',
    'Explain further', 'Analyse more', 'Post', 'Send follow-up', 'Post + resolve',
    '“Fixed” + resolve', 'Check my post', 'Check again', 'Keep as posted', 'Review in hunk',
    'Redo…', 'Run the checks', 'Fix the checks', 'Push', 'Push and resolve', 'Drop', 'Summarize', 'Merge into t3',
    'Keep separate', 'Link as t3\'s comment', 'Judge', 'Ack', 'Accept', 'Accept + resolve', 'Resolve',
    'Not enough…', 'Wait for author', 'Nothing to add', 'Seen', 'Generate explainer', 'Skip explainer',
    'My review is done', 'Next', 'Close', 'Analyse', 'Apply edit', 'Approve MR']
  expect(labels.filter(l => !iconOf(l))).toEqual([])
  // The same action, the same icon.
  expect(iconOf('Reply now')).toBe(iconOf('Reply…'))
  expect(iconOf('“Fixed” + resolve')).toBe(iconOf('Post + resolve'))
})


// The end of the MR: the Done box's last line, and the one press it offers.
const ended = (skill: 'rework-mr' | 'review-mr', mr: object) =>
  ({ skill, iid: 3, title: 'x', current: null, topics: [], mr: { state: 'opened', approved_by: ['Robin'], approvals_left: 0, merge_status: 'mergeable', can_merge: true, ...mr } }) as never
const endText = (skill: 'rework-mr' | 'review-mr', mr: object, settled = true) => {
  const e = endOf(ended(skill, mr), settled)
  return e ? [e.text, e.button?.label ?? null] : undefined
}

test('rework-mr: the end of the MR offers Merge only once it is approved and GitLab can merge it', () => {
  expect(endText('rework-mr', {})).toEqual(['', 'Merge'])
  expect(endText('rework-mr', { pipeline: { status: 'running' } })).toEqual(['', 'Set to auto-merge'])
  expect(endText('rework-mr', { pipeline: { status: 'passed' } })).toEqual(['', 'Merge'])
  // A project that requires no approval still needs one from the session.
  expect(endText('rework-mr', { approved_by: [], approvals_left: null })).toEqual(['Waits for approval', null])
  expect(endText('rework-mr', { approvals_left: 1 })).toEqual(['Waits for approval', null])
  expect(endText('rework-mr', { approved_by: [], pipeline: { status: 'running' } })).toEqual(['Waits for approval', null])
  expect(endText('rework-mr', { merge_status: 'conflict' })).toEqual(['Has conflicts', null])
  expect(endText('rework-mr', { merge_status: 'some_new_status' })).toEqual(['Some new status', null])
  expect(endText('rework-mr', { can_merge: false })).toEqual(['You may not merge it', null])
  // Nothing about the merge while a topic still waits.
  expect(endText('rework-mr', {}, false)).toBeUndefined()
})

test('rework-mr: the merge pressed is followed to its end, and a failure offers its analysis', () => {
  const at = '2026-11-14T12:00:00Z'
  expect(endText('rework-mr', { merge: { how: 'merge', at } })).toEqual(['Merging…', null])
  expect(endText('rework-mr', { merge: { how: 'auto', at }, auto_merge: true, pipeline: { status: 'running' } })).toEqual(['Auto-merge on · merges when the pipeline passes', null])
  expect(endText('rework-mr', { merge: { how: 'merge', at, error: 'Branch cannot be merged' } })).toEqual(['Merge failed: Branch cannot be merged', 'Analyze failed merge'])
  // Who merged it is the box's step line: the end line is the press alone.
  expect(endText('rework-mr', { state: 'merged' })).toEqual(['', 'Clean up locally'])
  // A failed pipeline is said, and its analysis offered, while topics still wait too.
  const failed = endOf(ended('rework-mr', { pipeline: { status: 'failed' } }), false)
  expect([failed?.text, failed?.button?.label]).toEqual(['Pipeline failed', 'Analyze CI failure'])
  expect(failed?.button?.press).toEqual({ kind: 'ask', prompt: CI_PROMPT })
  // The fix for a pipeline goes into the commit that broke it: no "fix e2e" commits.
  expect(CI_PROMPT).toMatch(/fixup into the commit of this MR that broke it/)
  expect(CI_PROMPT).not.toMatch(/commit of its own/)
  expect(endText('rework-mr', { state: 'closed' })).toBeUndefined()
})

test('the update check follows a merge pressed, and stops once GitLab merged it or refused', () => {
  const at = '2026-11-14T12:00:00Z'
  expect(isMergeWatched(ended('rework-mr', { merge: { how: 'merge', at } }))).toBe(true)
  expect(isMergeWatched(ended('rework-mr', { merge: { how: 'auto', at }, auto_merge: true }))).toBe(true)
  expect(isMergeWatched(ended('rework-mr', {}))).toBe(false)
  expect(isMergeWatched(ended('rework-mr', { merge: { how: 'merge', at, error: 'no' } }))).toBe(false)
  expect(isMergeWatched(ended('rework-mr', { state: 'merged', merge: { how: 'merge', at } }))).toBe(false)
})

test("review-mr: the end of someone else's MR is Approve, never a merge, and says once you approved", () => {
  expect(endText('review-mr', { approved_by: [] })).toEqual(['', 'Approve MR'])
  expect(endOf(ended('review-mr', { approved_by: [] }), true)?.button?.isGood).toBe(true)   // drawn green
  expect(endText('review-mr', { approved_by: ['You'] })).toEqual(['Approved by you', null])
  expect(endText('review-mr', { approved_by: [] }, false)).toBeUndefined()
  expect(endText('review-mr', { state: 'merged' })).toBeUndefined()
})

test('review-mr: Approve MR waits for every topic to be settled here, whatever GitLab would allow', () => {
  // Nothing waits on the author, but t2 is still to do: its thread is open on GitLab, which a
  // project may allow approving with. The reviewer is not through.
  const mr = { state: 'opened', approved_by: [], approvals_left: 0, merge_status: 'mergeable', can_merge: true }
  const of = (t2: string) => ({ skill: 'review-mr', iid: 3, title: 'x', current: null, mr,
    topics: [{ ...topic('t1', 'acked'), state: 'done' }, { ...topic('t2', 'needs_ack'), state: t2 }] }) as never
  expect(endOf(of('answered'), true)).toBeUndefined()
  expect(endOf(of('new'), true)).toBeUndefined()
  expect(endOf(of('dropped'), true)?.button?.label).toBe('Approve MR')
  expect(endOf(of('wontfix'), true)?.button?.label).toBe('Approve MR')
})

test('review-mr: every topic settled, approvable or approved by you: a dark green border, as the Approve button has', async ($, on) => {
  const mr = { state: 'opened', approved_by: [], approvals_left: 0 }
  const settled = (more: object) => stateOf(null, [{ ...topic('t1', 'acked'), state: 'done' }], { in_round: false, inbox: inbox(), mr: { ...mr, ...more } })
  const w = world(on, [settled({}), settled({ approved_by: ['You'] })], { 'approve-mr': { stdout: settled({ approved_by: ['You'] }) } })
  await openReview($, w, view('t1'))
  let panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  let drawn = JSON.stringify(await panel.findAll({ type: 'Box' }))
  expect(drawn).toContain('"borderColor":"diffAdded"')
  expect(drawn).not.toContain('"borderColor":"success"')
  expect((await panel.find({ type: 'Text', text: 'Done' }))?.props.color).toBeUndefined()
  const chip = JSON.stringify(await panel.find({ type: 'Button', key: 'approve-mr' }))
  // The button in the border's dark green, its light green text the fill under the pointer.
  expect(chip).toContain('"backgroundColor":"#3b6b45"')
  expect(chip).toContain('"color":"#c3e88d"')
  expect(chip).toContain('Approve MR')
  await panel.press({ key: 'approve-mr' })
  await panel.unmount()
  panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  // Approved by you is the same step short of merged: the dark green border stays, the words
  // say it in green; the bright green box is a merged MR's alone.
  drawn = JSON.stringify(await panel.findAll({ type: 'Box' }))
  expect(drawn).toContain('"borderColor":"diffAdded"')
  expect((await panel.find({ type: 'Text', text: 'Done' }))?.props.color).toBeUndefined()
  expect((await panel.find({ type: 'Text', text: 'Approved by you' }))?.props.color).toBe('success')
  await panel.unmount()
})

// One colour rule for the box in both skills: approved (or as good as) a dark green border,
// merged the whole box green, failed red, and a wait no colour at all.
test('the box looks as the MR stands, the same rule in both skills', () => {
  const look = (skill: 'rework-mr' | 'review-mr', mr: object, settled = true, topics: object[] = []) => {
    const r = { ...(ended(skill, mr) as object), topics } as never
    return boxLookOf(r, endOf(r, settled), settled ? 0 : 1)
  }
  expect(look('rework-mr', {})).toBe('approved')                                       // Merge offered
  expect(look('rework-mr', { pipeline: { status: 'running' } })).toBe('approved')      // Set to auto-merge
  expect(look('rework-mr', { auto_merge: true, merge: { how: 'auto', at: 'x' } })).toBe('approved')
  expect(look('rework-mr', { approved_by: [] })).toBe('approved')                      // settled, the approval to come
  expect(look('rework-mr', { approved_by: [] }, true, [{ id: 't1', state: 'answered' }])).toBe('plain')
  expect(look('rework-mr', {}, false)).toBe('plain')                                   // a topic waits
  expect(look('rework-mr', { pipeline: { status: 'failed' } }, false)).toBe('failed')
  expect(look('rework-mr', { merge: { how: 'merge', at: 'x', error: 'no' } })).toBe('failed')
  expect(look('rework-mr', { state: 'merged' })).toBe('merged')
  expect(look('review-mr', { approved_by: [] })).toBe('approved')                      // Approve MR offered
  expect(look('review-mr', { approved_by: ['You'] })).toBe('approved')
  expect(look('review-mr', { state: 'merged' })).toBe('merged')
  expect(look('review-mr', {}, true, [{ id: 't1', state: 'answered' }])).toBe('plain')
  expect(look('rework-mr', { state: 'closed' })).toBe('plain')
})

test('rework-mr: an approved MR\'s box and its merge press are dark green', async ($, on) => {
  const mr = { state: 'opened', approved_by: ['Robin'], approvals_left: 0, merge_status: 'mergeable', can_merge: true }
  const idle = (more: object) => reworkState(null, [reworkTopic('t1', 'none', { state: 'done' })], { in_round: false, mr: { ...mr, ...more }, inbox: inbox() })
  const w = world(on, [idle({ pipeline: { status: 'running' } })])
  await openRework($, w, reworkView('t1'))
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  expect(JSON.stringify(await panel.findAll({ type: 'Box' }))).toContain('"borderColor":"diffAdded"')
  const chip = JSON.stringify(await panel.find({ type: 'Button', key: 'merge-mr' }))
  expect(chip).toContain('Set to auto-merge')
  expect(chip).toContain('"backgroundColor":"#3b6b45"')
  expect(chip).toContain('"color":"#c3e88d"')
  await panel.unmount()
})

test('rework-mr: set to auto-merge, the box says so in plain words, led by the running pipeline\'s icon', async ($, on) => {
  const mr = { state: 'opened', approved_by: ['Robin'], approvals_left: 0, merge_status: 'mergeable', can_merge: true,
    auto_merge: true, merge: { how: 'auto', at: '2026-11-14T12:00:00Z' }, pipeline: { status: 'running' } }
  const w = world(on, [reworkState(null, [reworkTopic('t1', 'none', { state: 'done' })], { in_round: false, mr, inbox: inbox() })])
  await openRework($, w, reworkView('t1'))
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  const line = await panel.find({ type: 'Text', text: /Auto-merge on/ })
  // The icon the title line has for a running pipeline, not the dots of a call under way.
  expect(line?.text).toBe('\u{f0996} Auto-merge on · merges when the pipeline passes')
  // A wait, no warning: no colour, and not dim either.
  expect([line?.props.color, line?.props.dimColor]).toEqual([undefined, undefined])
  await panel.unmount()
})

test('rework-mr: a failed pipeline is red, the border and the words', async ($, on) => {
  const mr = { state: 'opened', approved_by: ['Robin'], approvals_left: 0, merge_status: 'mergeable', can_merge: true, pipeline: { status: 'failed' } }
  const w = world(on, [reworkState(null, [reworkTopic('t1', 'none', { state: 'done' })], { in_round: false, mr, inbox: inbox() })])
  await openRework($, w, reworkView('t1'))
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  expect(JSON.stringify(await panel.findAll({ type: 'Box' }))).toContain('"borderColor":"error"')
  expect((await panel.find({ type: 'Text', text: 'Pipeline failed' }))?.props.color).toBe('error')
  await panel.unmount()
})

test('rework-mr: Merge in the box runs the script with no model turn; Clean up locally ends the review', async ($, on) => {
  const mr = { state: 'opened', approved_by: ['Robin'], approvals_left: 0, merge_status: 'mergeable', can_merge: true }
  const idle = reworkState(null, [reworkTopic('t1', 'none', { state: 'done' })], { in_round: false, mr, inbox: inbox() })
  const merged = reworkState(null, [reworkTopic('t1', 'none', { state: 'done' })], { in_round: false, mr: { ...mr, state: 'merged' }, inbox: inbox() })
  const w = world(on, [idle], { 'merge-mr': { stdout: merged }, cleanup: { stdout: 'Removed the branch fix/x.\nAll cleaned up. You can close this session.' } })
  await openRework($, w, reworkView('t1'))
  let panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  expect((await panel.find({ type: 'Button', key: 'merge-mr' }))?.props.label).toBe('Merge')
  await panel.press({ key: 'merge-mr' })
  await panel.unmount()
  expect(w.ran.map(r => r.argv.slice(2).join(' '))).toContain('merge-mr --iid 42')
  const merge = w.ran.find(r => r.argv[2] === 'merge-mr')
  expect(await isPress(w, merge?.env)).toBe(true)
  expect(w.prompts).toEqual([])
  panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  expect((await panel.findAll({ type: 'Text' })).map(t => String(t.text ?? ''))).toContain('Merged')
  expect(await panel.find({ type: 'Button', key: 'cleanup' })).toBeDefined()
  await panel.press({ key: 'cleanup' })
  await panel.unmount()
  expect(w.ran.map(r => r.argv.slice(2).join(' '))).toContain('cleanup --iid 42')
  // Each press its own secret: one spent proves nothing for the next.
  const cleanup = w.ran.find(r => r.argv[2] === 'cleanup')
  expect(await isPress(w, cleanup?.env)).toBe(true)
  expect(cleanup?.env?.REVIEW_UI_PRESS).not.toBe(merge?.env?.REVIEW_UI_PRESS)
  panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  expect((await panel.findAll({ type: 'Text' })).map(t => String(t.text ?? '')).join('\n')).toContain('All cleaned up. You can close this session.')
  expect(await panel.find({ type: 'Button', key: 'cleanup' })).toBeUndefined()
  await panel.unmount()
})


test('the box is one timeline of the news, oldest first, with nothing said apart', () => {
  // "During your round" headed news that had come before the round started: gone. The box
  // lists what is new, in the order it happened, topics among pushes and the MR's news.
  const t1 = { ...topic('t1', 'needs_ack'), inbox: 'new', news: ['reply'], news_at: '2026-11-09T12:10:00Z' }
  const r = JSON.parse(stateOf(null, [t1], { author: 'Jan', inbox: inbox({
    replies: ['t1'],
    pushes: [
      { at: '2026-11-09T11:30:00Z', stat: '\u{f4d2} 3 files +9 −1', topics: ['t1'], rebase: false },
      { at: '2026-11-09T12:30:00Z', stat: '\u{f4d2} 1 file +1 −1', topics: ['t1'], rebase: false },
    ],
    mr: [{ kind: 'approved', at: '2026-11-09T11:45:00Z', by: 'Robin' }],
  }) }))
  const box = inboxOf(r)
  expect(box?.lines.map(l => [l.at, l.what])).toEqual([
    ['2026-11-09T11:30:00Z', 'Jan pushed · t1'], ['2026-11-09T11:45:00Z', 'Robin approved the MR'],
    ['2026-11-09T12:10:00Z', 'Jan answered'], ['2026-11-09T12:30:00Z', 'Jan pushed · t1']])
  expect(box?.hasNews).toBe(true)
  expect(Object.keys(box ?? {})).not.toContain('during')
})

test("review-mr: an answer that leaves the next move to the author is a dim line and a dim mark, never a round's stop", () => {
  const waits = { ...topic('t2', 'open'), inbox: 'fyi', news: ['reply'], news_at: '2026-11-09T12:10:00Z' }
  const r = JSON.parse(stateOf(null, [topic('t1', 'open'), waits], { author: 'Jonas', inbox: inbox({ replies: ['t2'] }) }))
  const box = inboxOf(r)
  expect(box?.lines.map(l => [l.topic, l.what, l.tone, l.kind, l.at])).toEqual([['t2', 'Jonas answered: fix to come', 'info', 'waits', '2026-11-09T12:10:00Z']])
  expect(box?.count).toBe(0)
  expect(box?.button).toBeNull()
  // Flagged new as any news is: one mark, one word, whatever the topic's group.
  expect(inboxMark(waits as never, [], [])).toEqual({ key: 'new', icon: '\u{f09de}', color: 'yellow' })
})


test('show_topic called without its topic says how to call it, and runs nothing', async ($, on) => {
  const w = world(on, [stateOf('t1', [topic('t1', 'draft')])])
  await openReview($, w, view('t1'))
  const before = w.ran.length
  const got = await $.tool.call({ tool: 'mcp__review-ui__show_topic' } as never) as { deny?: string }
  expect(got.deny).toContain('{"topic": "t3"}')
  expect(w.ran.slice(before).some(r => r.argv.includes('quote'))).toBe(false)
})

// A session started again on a conversation that had a review: `claude --resume` in a new
// process, with none of the mod's state, and the transcript bringing back every card under
// the view call that printed it.
const resumed = (on: On, w: World, review: object, calls: { id: string; out: string; after?: string }[]) => {
  on('tool.register' as never, (() => ({ value: undefined })) as never)
  on('session.start' as never, ((_: unknown, e: object) => e) as never)
  w.files['/home/me/.claude/review-ui/sessions/s-1'] = JSON.stringify({ review, topic: 't1', focus: 't1', draft: null })
  w.turns.n = 3
  const skill = (review as { skill: string }).skill
  const script = skill === 'rework-mr' ? 'threads.py' : 'findings.py'
  on('session.messages', () => ({ value: [
    { role: 'user', text: `<command-name>/${skill}</command-name><command-args>!42</command-args>`, toolUses: [] },
    ...calls.flatMap(c => [
      { role: 'assistant', text: '', toolUses: [{ tool_use_id: c.id, tool: 'Bash', input: { command: `python3 $SD/${script} quote t1 --iid 42` }, result: { stdout: c.out, stderr: '', interrupted: false }, text: c.out }] },
      { role: 'user', text: '', toolUses: [], toolResults: [{ tool_use_id: c.id, text: c.out }] },
      ...(c.after ? [{ role: 'assistant', text: c.after, toolUses: [] }] : []),
    ]),
  ] as never }))
}
const startSession = ($: Engine) => (($ as unknown as { session: { start: (e: object) => Promise<unknown> } }).session.start({ source: 'resume', cwd: '/wt' }))

test('back after a restart, the newest card of the transcript hosts the buttons again, and the older one of its topic is its title line', async ($, on) => {
  const head = '\u{f0182} **t1** **summary of t1** · `src/cache.ts:17`'
  const out = `${head}\n\nsome view\n\n<!-- paste-gate:critical\n` + JSON.stringify({ first: head, critical: [], topic: 't1', focus: 't1', review: REVIEW }) + '\n-->'
  const w = world(on, [stateOf('t1', [topic('t1', 'draft')])])
  heldClock(on)
  resumed(on, w, REVIEW, [{ id: 'tu-first', out }, { id: 'tu-last', out, after: 'I recommend a comment.' }])
  await startSession($)
  const newest = await $.ui.mount(cardRow('tu-last', out))
  expect((await labels(newest)).filter(l => /\w/.test(String(l)))).toEqual(['Comment', 'Drop', 'Explain', 'Next'])
  expect(JSON.stringify(await newest.findAll({ type: 'Markdown' }))).toContain('some view')
  await newest.unmount()
  const older = await $.ui.mount(cardRow('tu-first', out))
  expect(await older.find({ type: 'Button' })).toBeUndefined()
  expect(JSON.stringify(await older.findAll({ type: 'Markdown' }))).not.toContain('some view')
  await older.unmount()
  // The band keeps the rest, as before the restart: not the card's buttons a second time.
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await labels(band)).toEqual(['Show sidebar'])
  await band.unmount()
})

test("a session's start clears what only a running process held: a turn, a press and a check cut off leave the update check free", async ($, on) => {
  const tick = heldClock(on)
  on('tool.register' as never, (() => ({ value: undefined })) as never)
  on('session.start' as never, ((_: unknown, e: object) => e) as never)
  on('turn.start', ($, e) => ({ turnId: e.turnId }))
  const done = stateOf(null, [topic('t1', 'open'), topic('t2', 'open')])
  // GitLab never answers the sync pressed: the process ends in the middle of it.
  const w = world(on, [done], { refresh: { stdout: done, hang: true } })
  await startSession($)
  await openReview($, w, view('t1'))
  await $.turn.start({ text: 'what is left?', turnId: 'turn-1' })
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  void panel.press({ key: 'check-now' })
  await settle()
  expect(await panel.find({ type: 'Text', text: /Checking GitLab/ })).toBeDefined()
  await panel.unmount()
  const asked = () => w.ran.filter(r => r.argv[2] === 'refresh').length
  await tick()
  expect(asked()).toBe(1)                              // the turn and the press stand in its way
  // Started again where the mod's state outlived them (a reload keeps it).
  const again = startSession($)
  await settle()
  const after = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  expect(await after.find({ type: 'Text', text: /Checking GitLab/ })).toBeUndefined()
  expect(await after.find({ type: 'Button', key: 'check-now' })).toBeDefined()
  await after.unmount()
  await again
  await tick()
  expect(asked()).toBe(2)
})

test('a fresh session opening a review that has state already asks GitLab at the first tick, its topics as they were', async ($, on) => {
  const tick = heldClock(on)
  on('tool.register' as never, (() => ({ value: undefined })) as never)
  on('session.start' as never, ((_: unknown, e: object) => e) as never)
  const story = reworkView('t1').replace('some view', 'some view\n\n09:12 \u{F012C} Miriam commented')
  const kept = reworkState(null, [reworkTopic('t1', 'none', { state: 'waiting' }), reworkTopic('t2', 'none', { state: 'waiting' })], { in_round: false, inbox: inbox() })
  const w = world(on, [kept], { refresh: { stdout: kept } })
  await startSession($)
  // Nothing ran before the person started the review: a fresh session takes up no old one.
  expect(w.ran).toEqual([])
  await openRework($, w, story)
  await tick()
  expect(w.ran.filter(r => r.argv[2] === 'refresh').length).toBe(1)
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  const texts = (await panel.findAll({ type: 'Text' })).map(t => String(t.text ?? '')).join('\n')
  expect(texts).toContain('summary of t1')
  expect(texts).toContain('summary of t2')
  await panel.unmount()
  const card = await $.ui.mount(cardRow('tu-open', story))
  expect(JSON.stringify([...await card.findAll({ type: 'Text' }), ...await card.findAll({ type: 'Markdown' })])).toContain('Miriam')
  await card.unmount()
})

test("back days later with the MR merged meanwhile: the scripts run from the session's folder, and the box says merged", async ($, on) => {
  const tick = heldClock(on)
  const gone = { ...REWORK, cwd: '/wt.reviews/mr-42' }
  const mr = { state: 'opened', approved_by: ['Robin'], approvals_left: 0, merge_status: 'mergeable', can_merge: true }
  const left = (more: object) => reworkState(null, [reworkTopic('t1', 'none', { state: 'done' })], { in_round: false, mr: { ...mr, ...more }, inbox: inbox() })
  // The state file as the session left it; GitLab says it was merged since.
  const w = world(on, [left({})], { refresh: { stdout: left({ state: 'merged', merged_at: '2026-11-08T09:00:00Z', merged_by: 'Robin' }) } })
  w.clock.now = Date.parse('2026-11-09T08:00:00Z')
  const head = `\u{f0182} **t1** \`src/cache.ts:17\``
  const out = `${head}\n\nsome view\n\n<!-- paste-gate:critical\n${JSON.stringify({ first: head, critical: [], topic: 't1', focus: 't1', review: gone })}\n-->`
  resumed(on, w, gone, [{ id: 'tu-last', out }])
  await startSession($)
  await tick()
  const refreshed = w.ran.find(r => r.argv[2] === 'refresh')
  expect(refreshed?.cwd).toBe('/wt')
  const panel = await $.ui.mount({ ...PANEL, surface: 'terminal' })
  expect((await panel.findAll({ type: 'Text' })).map(t => String(t.text ?? ''))).toContain('Merged')
  expect(await panel.find({ type: 'Button', key: 'cleanup' })).toBeDefined()
  await panel.unmount()
  // Merged, GitLab is not asked again.
  await tick()
  expect(w.ran.filter(r => r.argv[2] === 'refresh').length).toBe(1)
})
