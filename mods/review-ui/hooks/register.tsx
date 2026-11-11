import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { ReviewCard, ReviewHost, ReviewOutcome, ReviewJudging, ReviewNote, ReviewNotice, ReviewPlaced, ReviewSession, ReviewState } from '../types'
import {
  argsFor, buttonsFor, checkPrompt, iconOf, isHeldTopic, outcomeOf, earlyPrompt, replyPrompt, clockOf, currentTopic, didText, inboxOf, noteForModel, stepOf, untilOf, waitingLine, waitingOf,
  type BandButton,
} from './review.ts'
import {
  boxRowsOf, fitTitle, plainTitle, INBOX_ICON, MR_COLOR, INBOX_WORD, inboxMark, kindSlot, legendFor, oneLineNote, reflow, sectionsOf, STATE_ICON,
  whenOf, type BoxRow, type Row,
} from './panel.ts'
import { blocksOf, type DiffSize, type SizeRun, withEmoji, withoutDraft, isPostCommand, isViewCommand, partsOf, sizeOf, sizeRunsOf, withoutPasted, withSuggestions, manifestOf, titleOf, viewOf, withNote, withoutExcerpt, withoutExcerptLabel, withoutThread } from './views.ts'
import {
  askedFor, clampOffset, codeArgsOf, codeWindow, currentOwn, firstOffset, phaseOf, stepOwn,
  type CodeView,
} from './sidebar.ts'

const P = 'review-ui'
// Each tag changes when its value's fields do: it declines what an earlier version stored,
// which outlives a reload.
const session = atom({ plugin: 'review-ui', key: 'session' } as const, null as ReviewSession | null, { shape: 'session-2' })
// Whether this session uses a review skill: the person typed /review-mr or /rework-mr, the
// model ran one through the Skill tool, or the session carries on one that did. Only then
// does the mod take a review's views for its own. A session working ON the skills runs the
// same scripts while it builds and tests them, and is not a review.
const inUse = atom({ plugin: 'review-ui', key: 'in-use' } as const, false, { shape: 'in-use-1' })
// How it came to be in use: the person or the model ran a review skill here (`skill`), or the
// mod took it up from the session's mark or the folder's memory (`recovered`). Only the
// second can be wrong, and is checked again at every turn.
const inUseBy = atom({ plugin: 'review-ui', key: 'in-use-by' } as const, null as 'skill' | 'recovered' | null, { shape: 'in-use-by-1' })
const REVIEW_SKILLS = ['review-mr', 'rework-mr']
const review = atom({ plugin: 'review-ui', key: 'review' } as const, null as ReviewState | null, { shape: 'review-1' })
const card = atom({ plugin: 'review-ui', key: 'card' } as const, null as ReviewCard | null, { shape: 'card-1' })
const placed = atom({ plugin: 'review-ui', key: 'placed' } as const, {} as ReviewPlaced, { shape: 'placed-1' })
const placedOf = atom({ plugin: 'review-ui', key: 'placed-of' } as const, {} as Record<string, { topic?: string; draft?: string }>, { shape: 'placed-of-1' })
const notice = atom({ plugin: 'review-ui', key: 'notice' } as const, null as ReviewNotice, { shape: 'notice-1' })
const hostCard = atom({ plugin: 'review-ui', key: 'host' } as const, null as ReviewHost | null, { shape: 'host-1' })
// The topic whose draft was just copied, while its Copy reads "Copied!"; and the topics
// whose notice said where to paste the draft, which it says once.
const copiedDraft = atom({ plugin: 'review-ui', key: 'copied' } as const, null as string | null, { shape: 'copied-1' })
const copyTold = atom({ plugin: 'review-ui', key: 'copy-told' } as const, [] as string[], { shape: 'copy-told-1' })
// What a press did to the card it was pressed on, by the card's id: its last line.
const outcomes = atom({ plugin: 'review-ui', key: 'outcomes' } as const, {} as Record<string, ReviewOutcome>, { shape: 'outcomes-1' })
const busy = atom({ plugin: 'review-ui', key: 'busy' } as const, null as string | null, { shape: 'busy-1' })
const panel = atom({ plugin: 'review-ui', key: 'panel' } as const, false, { shape: 'panel-1' })
// The sidebar's groups the person folded (true) or unfolded (false); one not in it is as
// its group starts.
const folded = atom({ plugin: 'review-ui', key: 'folded' } as const, {} as Record<string, boolean>, { shape: 'folded-1' })

// The sidebar: the own thread the person picked, how far each file is scrolled, the file
// last read for it, and whether the sidebar opened by itself or was closed by the person.
const ownPick = atom({ plugin: 'review-ui', key: 'own-pick' } as const, null as string | null, { shape: 'own-pick-1' })
const codeAt = atom({ plugin: 'review-ui', key: 'code-at' } as const, {} as Record<string, number>, { shape: 'code-at-1' })
const codeSeen = atom({ plugin: 'review-ui', key: 'code-seen' } as const, null as { key: string; stamp: number; view: CodeView | null } | null, { shape: 'code-seen-2' })
const stamp = atom({ plugin: 'review-ui', key: 'stamp' } as const, 0, { shape: 'stamp-1' })
const ticks = atom({ plugin: 'review-ui', key: 'ticks' } as const, 0, { shape: 'ticks-1' })
// When the update check last asked GitLab, and when it asks next (ms since the epoch), for
// the sidebar's waiting box.
const checks = atom({ plugin: 'review-ui', key: 'checks' } as const, { last: null, next: null, failed: null } as { last: number | null; next: number | null; failed?: number | null; running?: boolean }, { shape: 'checks-2' })
// The background agent that judges what the update check found (one at a time), and what
// it could not judge. Its answers are stored with the topics; nothing of it reaches the chat.
const judging = atom({ plugin: 'review-ui', key: 'judging' } as const, { agentId: null, topics: [], own: [], failed: [] } as ReviewJudging, { shape: 'judging-1' })
// What the person was told of already, by the notification, so each piece of news is told once.
const notified = atom({ plugin: 'review-ui', key: 'notified' } as const, [] as string[], { shape: 'notified-1' })
const working = atom({ plugin: 'review-ui', key: 'working' } as const, false, { shape: 'working-1' })
const sidebar = atom({ plugin: 'review-ui', key: 'sidebar' } as const, { isClosedByPerson: false }, { shape: 'sidebar-1' })

const explained = atom({ plugin: 'review-ui', key: 'explained' } as const, [] as string[], { shape: 'explained-1' })
// Whether the legend is open: closed at first, as the icons are learned once and it takes rows
// from the file.
const legendShown = atom({ plugin: 'review-ui', key: 'legend-shown' } as const, false, { shape: 'legend-shown-2' })
// The items whose thread under the code the person folded to one line per comment, or opened;
// every other one shows it as its view does at first (in full, folded under a fix).
const threadOpen = atom({ plugin: 'review-ui', key: 'thread-open' } as const, {} as Record<string, boolean>, { shape: 'thread-open-1' })
// The code blocks in a thread box the person opened (folded by default), and how far each
// box taller than its room is scrolled.
const codeOpen = atom({ plugin: 'review-ui', key: 'code-open' } as const, {} as Record<string, boolean>, { shape: 'code-open-1' })
// How far each of the sidebar's areas that scroll inside themselves is scrolled, and for which
// current item: the list's window follows the current topic until the person moves it.
const areaAt = atom({ plugin: 'review-ui', key: 'area-at' } as const, {} as Record<string, { at: number; on: string | null }>, { shape: 'area-at-1' })
// The fix's other files the person opened past their folded rows, by the code view.
const filesOpen = atom({ plugin: 'review-ui', key: 'files-open' } as const, {} as Record<string, boolean>, { shape: 'files-open-1' })
const boxAt = atom({ plugin: 'review-ui', key: 'box-at' } as const, {} as Record<string, number>, { shape: 'box-at-1' })
// rework-mr: the comments on the MR itself opened in full under the file.
const mrOpen = atom({ plugin: 'review-ui', key: 'mr-open' } as const, false, { shape: 'mr-open-1' })

// The sidebar's file: how far a key moves it, and the background of the lines a comment is
// about (a muted blue, under which every syntax colour still reads on a dark theme).
const SCROLL_STEP = 3
// The columns a scrolling thread box keeps free at its right for its keys: at the top the fold
// key and ▲, at the bottom ▼, each with the space before it.
const BOX_ARROWS = 4
// The columns an area's arrow takes at the end of its rows, with the space before it.
const ARROW = 2
// The rows a folded area (the fix's other files, the comments on the MR) shows at most.
const FOLDED_ROWS = 5
// The columns the fold key takes at the end of a folded box's first line, with its space.
const FOLD_KEY = 2
// The update check: a tick every 30 seconds, GitLab asked every sixth (every tick while the
// person's own review is open, so their comments come in as they post them).
const TICK_MS = 30_000
const CHECK_TICKS = 6
// What the band says while the person's `check now` runs.
const CHECKING = 'checking GitLab'
const DISCUSSED_BG = '#2b3a55'
// The link to a comment on GitLab, as a glyph (Nerd Fonts md-open_in_new). Mirrors
// lib/fences.py's LINK_ICON.
const LINK_ICON = '\u{f03cc}'

// What marks the MR's own title at the head of the sidebar: md-source_pull.
const MR_ICON = '\u{f04c2}'
// Where the MR stands as a whole, as marks at the right of its title: closed, auto-merge set,
// approved. Merged is the title's own icon; once over, neither of the others is news.
const mrMarksOf = (mr: ReviewState['mr']): string[] => {
  const isOver = mr?.state === 'merged' || mr?.state === 'closed'
  return [
    ...(mr?.state === 'closed' ? ['closed'] : []),
    ...(!isOver && mr?.auto_merge ? ['auto-merge'] : []),
    ...(!isOver && (mr?.approved_by?.length ?? 0) > 0 ? ['approved'] : []),
  ]
}
const MR_WORD: Record<string, string> = { closed: 'MR closed', 'auto-merge': 'auto-merge set', approved: 'approved' }

// The sidebar's and the cards' own controls, which only change the view: plain, in the glyphs
// the person picked (Nerd Fonts names beside each), one per control wherever it is drawn.
const GLYPH = {
  close: '\u{F0156}', // md-close
  up: '\u{F0BBD}', // md-pan_up: not a chevron, which is a severity
  down: '\u{F0BB7}', // md-pan_down
  open: '\u{F47C}', // oct-chevron_down: a group, a fold, shown
  shut: '\u{F460}', // oct-chevron_right: folded
  more: '\u{F084F}', // md-arrow_expand_vertical
  less: '\u{F084D}', // md-arrow_collapse_vertical
  legend: '\u{F064E}', // md-information_variant: not the question kind's circled ?
}
const PANE = 'review-ui-topics'


// Marks this session as one whose views the mod draws, so the scripts render for it
// (lib/ui.py): an empty file named after the session id, which every shell the session runs
// carries as CLAUDE_CODE_SESSION_ID. Written again before a review script runs, since a
// /clear starts a new id with no session.start.
//
// Never in the way: without the mark the scripts render as without the mod, which is still a
// working session, while a mark that threw would cost the hook that writes it everything else
// it does.
const markOf = async ($: EngineInterface): Promise<string | undefined> => {
  const home = await $.env.get('HOME')
  return home ? `${home}/.claude/review-ui/sessions/${await $.session.id()}` : undefined
}

const markSession = async ($: EngineInterface) => {
  if (!(await read($, inUse))) return
  try {
    const file = await markOf($)
    if (file && !(await $.fs.exists(file))) await $.fs.write(file, '')
  } catch {
    // the views render as without the mod
  }
}

// The skill in use: typed by the person, or run by the model. A plugin's skill comes with
// its plugin's name in front (`agent-skills:rework-mr`), which is the same skill.
const useSkill = async ($: EngineInterface, name: string | undefined) => {
  name = name?.replace(/^.*:/, '')
  if (!name || !REVIEW_SKILLS.includes(name)) return
  await update($, inUse, () => true)
  await update($, inUseBy, () => 'skill')
  await markSession($)
}

// What the last view left in the mark (lib/ui.py's `record`): which review, which topic,
// the current one, the draft's version. The model never reads it, so it cannot filter it.
type Recorded = { review?: ReviewSession; topic?: string | null; focus?: string | null; draft?: string | null }
const recorded = async ($: EngineInterface): Promise<Recorded | undefined> => {
  try {
    const file = await markOf($)
    const text = file && (await $.fs.exists(file)) ? await $.fs.read(file) : ''
    return typeof text === 'string' && text.trim() ? (JSON.parse(text) as Recorded) : undefined
  } catch {
    return undefined
  }
}

// Whether the topic panel is up now, by the engine's own record; on any doubt, not.
const panelShown = async ($: EngineInterface): Promise<boolean> => {
  try {
    return Boolean((await $.ui.panes()).find(p => p.id === PANE)?.isShown)
  } catch {
    return false
  }
}

const REVIEW_SCRIPT = /(?:findings|threads)\.py|diff-view\.sh|change-preview\.sh/

// The model's tool for showing a topic, as the engine names a plugin's tool.
const SHOW_TOPIC = 'show_topic'
const SHOW_TOPIC_TOOL = "mcp__review-ui__show_topic"

const commandOf = (tool: string, input: unknown): string | undefined => {
  const command = (input as { command?: unknown } | undefined)?.command
  return tool === 'Bash' && isViewCommand(command) ? command : undefined
}

// A call that may print a view: a view command of the scripts, or the show_topic tool.
const isViewCall = (tool: string, input: unknown): boolean => tool === SHOW_TOPIC_TOOL || commandOf(tool, input) !== undefined

// A call of the review's own scripts that prints no view: a sync, a lookup, research.
const isHiddenCall = (tool: string, input: unknown, output: unknown, isRunning: boolean): boolean => {
  const command = (input as { command?: unknown } | undefined)?.command
  if (tool !== 'Bash' || typeof command !== 'string' || !REVIEW_SCRIPT.test(command)) return false
  if (isRunning) return !isViewCommand(command)
  return !(isViewCommand(command) && viewOf(stdoutOf(output)) !== undefined)
}

// A call's printed text: a shell call's stdout, or a tool's text, which an MCP tool such as
// show_topic returns as content blocks.
const stdoutOf = (output: unknown): unknown => {
  if (typeof output === 'string') return output
  const o = output as { stdout?: unknown; content?: unknown } | undefined
  if (o?.stdout !== undefined) return o.stdout
  const blocks = Array.isArray(output) ? output : Array.isArray(o?.content) ? o?.content : undefined
  const texts = (blocks as { type?: string; text?: unknown }[] | undefined)
    ?.filter(b => b?.type === 'text' && typeof b.text === 'string').map(b => b.text as string)
  return texts?.length ? texts.join('\n') : undefined
}

// Runs the review's own script, where the view said it runs, for this MR.
// It carries the session id, as the Bash tool's shells do, so it renders for the cards
// (lib/ui.py) like every view the model runs.
// REVIEW_UI_MOD tells the scripts the run is the mod's, not the model's: a topic the mod
// brings up after a press is not one the chat is working on.
//
// The review's worktree goes once the MR is merged (the scripts prune it): the script then
// runs from the session's folder, the same repository, the MR named by `--iid`. A run that
// cannot start at all answers as a failed one, so every press says why in the band rather
// than ending without a word.
const runScript = async ($: EngineInterface, s: ReviewSession, args: string[], env?: Record<string, string>, timeoutMs = 120_000) => {
  const cwd = (await $.fs.exists(s.cwd).catch(() => false)) ? s.cwd : await $.session.cwd()
  try {
    return await $.process.run(['python3', s.script, ...args, '--iid', String(s.iid)], {
      cwd, env: { CLAUDE_CODE_SESSION_ID: await $.session.id(), REVIEW_UI_MOD: '1', ...env }, timeoutMs,
    })
  } catch (err) {
    return { exitCode: -1, stdout: '', stderr: `the review script did not run: ${err instanceof Error ? err.message : String(err)}`, isStdoutTruncated: false, isStderrTruncated: false }
  }
}

// A push runs the hooks a new record of the repository's checks has not earned skipping yet,
// which can take minutes: the longest a process may run. One cut short is pushed by the next
// press (the script goes on from a fix already committed).
const PUSH_MS = 600_000

// The scripts' state, read again after anything that may have changed it. `refresh` asks
// GitLab first (the update check's); `state` reads the state file alone.
const refresh = async ($: EngineInterface, s: ReviewSession, how: 'state' | 'refresh' = 'state'): Promise<ReviewState | null> => {
  const r = await runScript($, s, [how])
  if (r.exitCode !== 0) return null
  const state = JSON.parse(r.stdout) as ReviewState
  await update($, review, () => state)
  await update($, stamp, n => n + 1)
  await remember($, s)
  await syncCode($, s, state)
  return state
}

const check = async ($: EngineInterface) => {
  try {
    const { s, r } = await known($)
    // Never alongside a turn or a press: both write the state, and a check started next to
    // one would only queue behind it (the scripts take turns on the state file) and draw an
    // answer older than the press. The next period checks.
    if (!s || !r || (await read($, working)) || (await read($, busy))) return
    // A fix open in the diff viewer: the person's notes there make reading them the
    // recommended press, so the state is read again (locally; GitLab is not asked).
    if (r.hunk?.open) await refresh($, s)
    // A merged or closed MR has nothing more to say: GitLab is not asked again.
    if (r.mr?.state === 'merged' || r.mr?.state === 'closed') return
    const n = (await read($, ticks)) + 1
    await update($, ticks, () => n)
    // The first tick of a review checks at once: what changed while it waited (a push made
    // before the review was resumed) belongs in the inbox from the start.
    const isFirst = !(await read($, checks)).last
    const isDue = r.own_review === 'pending' || isFirst || n % CHECK_TICKS === 0
    if (isDue) await checkNow($, s)
    else await startJudging($, s, r)   // one the last check could not start, a turn ran
    const now = await $.clock.now()
    const left = r.own_review === 'pending' || (isFirst && !(await read($, checks)).last) ? 1 : CHECK_TICKS - (n % CHECK_TICKS)
    await update($, checks, c => ({ ...c, next: now + left * TICK_MS }))
  } catch {
    // the next period tries again
  }
}

// Asks GitLab now, and notes when; or that it failed, which the inbox says in yellow. What it
// found is judged in the background, and told to a person who is away.
const checkNow = async ($: EngineInterface, s: ReviewSession) => {
  let got: ReviewState | null = null
  // Said at the sidebar's foot while it runs, however it was started.
  await update($, checks, c => ({ ...c, running: true }))
  try {
    got = await refresh($, s, 'refresh')
  } catch {
    // GitLab out of reach, or the script failed: the next period tries again
  }
  const now = await $.clock.now()
  await update($, checks, c => (got ? { ...c, last: now, failed: null, running: false } : { ...c, failed: now, running: false }))
  if (got) {
    await startJudging($, s, got)
    await tell($, got)
  }
  return got
}

// The background agent's task, and the ids of those started. The engine hands a finished
// agent's report back to the main loop as a prompt of its own (a peer's, marked as a
// hand-back from the plugin that started it), and the model would answer it in the chat: the
// mod drops it, since the judgments are in the state and the person meets them on the cards.
const JUDGE_TASK = 'Judge news on MR'
const judges = new Set<string>()
// At `prompt.submit` the origin says only `peer`; the sender's id is in the envelope's text.
type HandBack = { kind?: string; handback?: boolean; plugin?: string; from?: string; senderTaskId?: string }
const isJudgeReport = async ($: EngineInterface, origin: unknown, text = ''): Promise<boolean> => {
  const o = (origin ?? {}) as HandBack
  if (o.kind !== 'peer' && o.kind !== 'task-notification') return false
  if (o.handback === true && o.plugin === P) return true
  const from = o.senderTaskId ?? o.from ?? /<agent-message from="([^"]+)"/.exec(text)?.[1] ?? ''
  // The ids are kept in the session's state too: a reload while an agent runs forgets the set.
  return judges.has(from) || ((await read($, judging)).ids ?? []).includes(from)
}

// A background agent that has said nothing for this long is taken for gone (a restart, a
// crash): what it had is judged again.
const JUDGE_TIMEOUT_MS = 15 * 60_000

// What the update check found is judged by an agent of its own, in the background: each
// answer's verdict, another reviewer's thread's title and view, a match for each comment the
// person posted themselves. By the time the person opens the topic its card carries it.
// One agent at a time; what arrives meanwhile waits for the next.
const startJudging = async ($: EngineInterface, s: ReviewSession, r: ReviewState) => {
  const j = r.judge
  if (!j) return
  const was = await read($, judging)
  const now = await $.clock.now()
  if (was.agentId && now - (was.at ?? 0) < JUDGE_TIMEOUT_MS) return
  // What it could not judge before goes to the chat, when the person opens it; not again here.
  const failed = was.failed.filter(x => j.topics.includes(x) || j.own.includes(x))
  const topics = j.topics.filter(x => !failed.includes(x))
  const own = j.own.filter(x => !failed.includes(x))
  await update($, judging, v => ({ agentId: null, topics: [], own: [], failed, ids: v.ids }))
  if (topics.length === 0 && own.length === 0) return
  try {
    // The brief is of what this agent is tracked as judging: nothing it failed on before.
    const brief = await runScript($, s, ['judge-brief', ...failed.flatMap(x => ['--skip', x])])
    if (brief.exitCode !== 0 || !brief.stdout.trim()) return
    // In the review's worktree, while it is there (see `runScript`).
    const cwd = (await $.fs.exists(s.cwd).catch(() => false)) ? s.cwd : await $.session.cwd()
    const got = await $.agent.spawn({ prompt: brief.stdout, description: `${JUDGE_TASK} !${s.iid}`, cwd })
    if (got.agentId) {
      judges.add(got.agentId)
      await update($, judging, v => ({ ...v, agentId: got.agentId as string, topics, own, at: now, ids: [...(v.ids ?? []), got.agentId as string].slice(-20) }))
    }
  } catch {
    // no agent: the next check tries again
  }
}

// The background agent ended: its judgments are read back, and what it left is the chat's
// to judge, when the person opens it. A card already up for a topic it judged comes up
// again, now with the judgment.
const endJudging = async ($: EngineInterface, agentId: string) => {
  const was = await read($, judging)
  if (was.agentId !== agentId) return
  const s = await read($, session)
  const r = s ? await refresh($, s) : null
  const left = r?.judge ?? { topics: [], own: [] }
  const failed = [...new Set([...was.failed, ...was.topics.filter(x => left.topics.includes(x)), ...was.own.filter(x => left.own.includes(x))])]
  await update($, judging, v => ({ agentId: null, topics: [], own: [], failed, ids: v.ids }))
  if (!s || !r) return
  await fillPending($, s)
  await startJudging($, s, r)
}

// Each card that waited for the judgment gets the topic's card, in its place: the judged one,
// or where the agent could not judge it, what there is (the band then offers Judge).
const fillPending = async ($: EngineInterface, s: ReviewSession) => {
  for (const [id, text] of Object.entries(await read($, placed))) {
    if (!text.startsWith(PENDING)) continue
    const q = await runScript($, s, ['quote', text.slice(PENDING.length)])
    const view = viewOf(q.stdout)
    if (q.exitCode === 0 && view) {
      await update($, placed, m => ({ ...m, [id]: view.text }))
      await update($, placedOf, m => ({ ...m, [id]: { topic: view.topic, draft: view.draft } }))
    }
  }
}

// The terminals a person reads this session in: with one of them in front, the person is
// taken to be here (tmux then says whether it is this window).
const TERMINALS = /ghostty|iterm|terminal|kitty|wezterm|alacritty|warp/i

// Whether the person is away from this session: another app in front, or another tmux
// window. Asked of the system the way the person's own notification hook asks; where it
// cannot be told, they are taken to be away, since a notification too many costs less than
// news missed.
const isAway = async ($: EngineInterface): Promise<boolean> => {
  let front: string | undefined
  try {
    const r = await $.process.run(['osascript', '-e', 'tell application "System Events" to get name of first process whose frontmost is true'], { timeoutMs: 5_000 })
    if (r.exitCode === 0) front = r.stdout.trim()
  } catch {
    // not macOS
  }
  if (front !== undefined && !TERMINALS.test(front)) return true
  const pane = await $.env.get('TMUX_PANE')
  if (pane) {
    try {
      const r = await $.process.run(['tmux', 'display-message', '-p', '-t', pane, '#{window_active}#{session_attached}'], { timeoutMs: 5_000 })
      if (r.exitCode === 0) return !/^1[1-9]/.test(r.stdout.trim())
    } catch {
      // no tmux to ask
    }
  }
  return front === undefined
}

// Tells a person who is away from the session what arrived, once per piece of news, and only
// between rounds: during one they are working through it. Through Claude Code's own
// notification, never anything that leaves the machine.
const SEEDED = '·seeded'
const tell = async ($: EngineInterface, r: ReviewState) => {
  if (!r.inbox) return
  const keys = [
    ...r.topics.filter(t => t.inbox === 'new').map(t => `${t.id}@${t.news_at ?? ''}`),
    ...r.inbox.pushes.map(p => `push@${p.at}`),
    ...(r.inbox.mr ?? []).map(m => `${m.kind}@${m.at}`),
    ...r.inbox.own.map(p => `own@${p.thread}`),
  ]
  const was = await read($, notified)
  const fresh = keys.filter(k => !was.includes(k))
  await update($, notified, v => [SEEDED, ...new Set([...v.filter(k => k !== SEEDED), ...keys])].slice(-200))
  // What was there when the mod first looked is not news to anyone.
  if (!was.includes(SEEDED) || fresh.length === 0 || r.current || r.in_round) return
  if (!(await isAway($))) return
  const box = inboxOf(r)
  const what = box?.lines.map(l => (l.topic ? `${l.topic} ${l.what}` : l.what)).join(' · ')
  try {
    const sent = await $.ui.notify(what || 'News on the review', { title: `Review !${r.iid}${r.title ? ` · ${plainTitle(r.title)}` : ''}` })
    if (!sent.isSent) $.ui.toast(`Review !${r.iid}: ${what}`)
  } catch {
    // no way to tell: the inbox still says it
  }
}

// Whether the sidebar has anything to show: the person's own comments, or the topics.
const hasSidebar = (r: ReviewState) => phaseOf(r) === 'own' || r.topics.length > 0

const paneTitle = (s: ReviewSession) => `${s.skill === 'review-mr' ? 'Review' : 'Rework'} · MR !${s.iid}`

const openSidebar = async ($: EngineInterface, s: ReviewSession, focus: boolean) => {
  // Opening an open id brings that pane forward; a closed one opens anew.
  await $.ui.open({ id: PANE, title: paneTitle(s), ...(focus ? { focus: true as const } : {}) })
  await update($, panel, () => true)
}

// A card the model's call brought up: the newest in the chat, so it hosts the step's buttons.
const shownCard = async ($: EngineInterface, id: string, stdout: string) => {
  const view = viewOf(stdout)
  if (!view) return
  await update($, hostCard, () => ({ id, topic: view.topic ?? null, step: view.step ?? null }))
}

// Any view the script prints, as a row of its own at the transcript's end.
const placeView = async ($: EngineInterface, s: ReviewSession, args: string[]) => {
  const q = await runScript($, s, args)
  return q.exitCode === 0 && placeOutput($, q.stdout)
}

// A view a script run already printed, placed as `placeView` places one.
const placeOutput = async ($: EngineInterface, stdout: string) => {
  const view = viewOf(stdout)
  if (!view) return false
  const id = `card-${Object.keys(await read($, placed)).length + 1}`
  await update($, placed, m => ({ ...m, [id]: view.text }))
  await update($, placedOf, m => ({ ...m, [id]: { topic: view.topic, draft: view.draft } }))
  await update($, hostCard, () => ({ id, topic: view.topic ?? null, step: view.step ?? null }))
  await $.ui.place({ id })
  return true
}

// A topic's card as a row of its own at the transcript's end, for a view a press brought up.
// `focus` makes it the current topic, as a click on its row in the sidebar does.
// A topic whose news the background agent is still judging has no card worth showing yet: its
// thread is the sidebar's already. It gets one that says so, filled in where it stands once
// the judgment is in (`fillPending`), so the chat never shows the topic twice.
const PENDING = '\u0000pending:'

// An error the band says belongs to the press that failed: once another card comes up, or
// one is closed, that moment is over, and an old error would read as one about the new card.
const clearError = ($: EngineInterface) => update($, notice, n => (n?.isError ? null : n))

const placeTopic = async ($: EngineInterface, s: ReviewSession, tid: string, focus = false) => {
  await clearError($)
  const j = await read($, judging)
  if (j.agentId && j.topics.includes(tid)) {
    if (focus) await runScript($, s, ['quote', tid, '--focus'])     // current all the same
    const id = `card-${Object.keys(await read($, placed)).length + 1}`
    await update($, placed, m => ({ ...m, [id]: `${PENDING}${tid}` }))
    await update($, hostCard, () => ({ id, topic: tid }))
    await update($, card, () => ({ topic: tid, draft: null }))
    await $.ui.place({ id })
    if (!(await read($, judging)).agentId) await fillPending($, s)   // it finished meanwhile
    return true
  }
  const q = await runScript($, s, ['quote', tid, ...(focus ? ['--focus'] : [])])
  const view = viewOf(q.stdout)
  if (q.exitCode !== 0 || !view) return false
  const id = `card-${Object.keys(await read($, placed)).length + 1}`
  await update($, placed, m => ({ ...m, [id]: view.text }))
  await update($, placedOf, m => ({ ...m, [id]: { topic: view.topic, draft: view.draft } }))
  await update($, hostCard, () => ({ id, topic: tid, step: view.step ?? null }))
  await update($, card, () => ({ topic: tid, draft: manifestOf(q.stdout)?.draft ?? null }))
  await $.ui.place({ id })
  return true
}

// A click on a topic's row: it becomes the current topic, its card comes up, and the model
// learns of the jump at its next turn. Between rounds the click opens the next round there,
// as the inbox's own key does.
// One move at a time: a double click, or the inbox's key pressed twice, arrives while the
// first is still running, before the state says where it went, and would draw the card twice.
let isMoving = false
const once = async (move: () => Promise<void>) => {
  if (isMoving) return
  isMoving = true
  try { await move() } finally { isMoving = false }
}

const showTopic = ($: EngineInterface, s: ReviewSession, tid: string) => once(() => moveTo($, s, tid))

// rework-mr: a fix made and not pushed holds its topic. Its files are in the working tree, so
// no other topic opens until it is pushed, or undone on the person's word. True where the
// move was refused, with the notice saying why.
const isHeld = async ($: EngineInterface, r: ReviewState | null, tid?: string) => {
  const held = r?.skill === 'rework-mr' ? r.topics.find(t => t.step === 'fixed') : undefined
  if (!held || held.id === tid) return false
  await update($, notice, () => ({ text: `${held.id}'s fix isn't pushed yet: push it first`, isError: false, isWarning: true, topic: held.id }))
  return true
}

const moveTo = async ($: EngineInterface, s: ReviewSession, tid: string) => {
  const r = await read($, review)
  const before = r?.current ?? null
  if (before === tid) return
  if (await isHeld($, r, tid)) return
  if (!before && !r?.in_round) {
    await startRound($, s, tid)
    await $.session.append({
      message: { type: 'user', content: [{ type: 'text', text: noteForModel(`opened ${tid} from the sidebar`, before, tid) }] },
    })
    return
  }
  if (!(await placeTopic($, s, tid, true))) return
  await refresh($, s)
  await $.session.append({
    message: { type: 'user', content: [{ type: 'text', text: noteForModel(`opened ${tid} from the sidebar`, before, tid) }] },
  })
}

// Opens a round, at `tid` or where the inbox says: the review worktree moves to the MR's
// newest push first (so the sidebar's file is the code the news is about), then the topic's
// card comes up, or the card of a comment of the person's own whose match waits.
const openRound = ($: EngineInterface, s: ReviewSession, tid?: string) => once(() => startRound($, s, tid))

const startRound = async ($: EngineInterface, s: ReviewSession, tid?: string) => {
  await update($, busy, () => 'opening')
  try {
    const ran = await runScript($, s, ['open', ...(tid ? [tid] : [])])
    if (ran.exitCode !== 0) {
      await update($, notice, () => ({ text: lastLine(ran.stderr) || 'Open failed', isError: true }))
      return
    }
    await update($, codeSeen, () => null)
    const now = await refresh($, s)
    await showStep($, s, now)
  } finally {
    await update($, busy, () => null)
  }
}

// What a round is at, on screen: the current topic's card, or the card of the first comment
// of the person's own whose match waits.
const showStep = async ($: EngineInterface, s: ReviewSession, r: ReviewState | null) => {
  if (r?.current) await placeTopic($, s, r.current)
  else if (r?.in_round && r.proposals?.length) await pickOwn($, s, (r.proposals[0] as { thread: string }).thread, true)
}

// The current item's file, read again when the item or the review changed: after every
// state read and every pick in the sidebar. Drawing is pure, so the sidebar draws what this
// left and never runs the script itself.
const syncCode = async ($: EngineInterface, s: ReviewSession, r: ReviewState) => {
  const want = codeArgsOf(r, currentOwn(r, await read($, ownPick)))
  if (!want) return
  const now = await read($, stamp)
  const seen = await read($, codeSeen)
  if (seen?.key === want.key && seen.stamp === now) return
  let view: CodeView | null = null
  try {
    const ran = await runScript($, s, want.args)
    view = ran.exitCode === 0 ? (JSON.parse(ran.stdout) as CodeView | null) : null
  } catch {
    view = null
  }
  await update($, codeSeen, () => ({ key: want.key, stamp: now, view }))
}

// A turn that moved the review on without showing where it went (the model finishing a
// press it was handed: commits made, the push run, the thread resolved) leaves the current
// topic with no card, or with one a step behind. It gets one, as after a press the mod ran
// itself; a fix card left behind by a push the model finished says so, as one the mod's
// own push finished does.
const cardForCurrent = async ($: EngineInterface) => {
  const s = await read($, session)
  if (!s || !(await read($, inUse)) || (await read($, busy))) return
  const r = await refresh($, s)
  if (!r?.current) return
  const host = await read($, hostCard)
  const now = currentTopic(r)
  if (host?.topic === r.current && (!host.step || host.step === now?.step)) return
  if (host?.topic === r.current && host.step === 'fixed' && now?.pushed && !(await read($, outcomes))[host.id]) {
    const done = outcomeOf({ kind: 'push' }, now.diff_url ?? '')
    if (done) await update($, outcomes, m => ({ ...m, [host.id]: done }))
  }
  await showStep($, s, r)
}

// Closes the topic the person opened to look at; the next that needs them comes up with its
// card, or none does and the sidebar shows the review waiting.
const leaveTopic = async ($: EngineInterface, s: ReviewSession) => {
  await clearError($)
  const ran = await runScript($, s, ['leave'])
  if (ran.exitCode !== 0) return
  const now = await refresh($, s)
  if (now?.current) await placeTopic($, s, now.current)
}

// A comment of the person's own made current, in the sidebar or with Previous and Next. One
// whose match waits for an answer brings its card up, so what the band asks is on screen.
const pickOwn = async ($: EngineInterface, s: ReviewSession, id: string, isStep = false) => {
  const before = await read($, ownPick)
  await update($, ownPick, () => id)
  const r = await read($, review)
  if (!r) return
  await syncCode($, s, r)
  if ((isStep || before !== id) && r.own_review !== 'pending' && r.proposals?.some(p => p.thread === id)) {
    await placeView($, s, ['proposal', id])
  }
}

const codeFor = async ($: EngineInterface, r: ReviewState, own: ReturnType<typeof currentOwn>) => {
  const want = codeArgsOf(r, own)
  const seen = await read($, codeSeen)
  return want && seen?.key === want.key && seen.view ? { key: want.key, view: seen.view } : undefined
}

// Sessions whose sidebar opened by itself once: kept here, not in the mod's state, because
// it opens from a drawing, which may not write state. A reload forgets it, and the sidebar
// then opens by itself once more.
const autoOpened = new Set<string>()

// Where the sidebar last drew the file, per session: the rows of its tree the file takes,
// the window it shows. The wheel over those rows moves the file; anywhere else, the sidebar.
// The thread box's rows of it too, where the box scrolls on its own: the wheel over them
// moves the box.
// The other areas that scroll inside themselves (the list, the fix's other files, the comments
// on the MR, a thread with no file) too, each by its own rows.
type CodeArea = {
  top: number; bottom: number; key: string; rows: number; start: number; view?: CodeView
  box?: { top: number; bottom: number; key: string; max: number }
  lists?: { top: number; bottom: number; key: string; max: number; at: number; on: string | null }[]
}
const codeAreas = new Map<string, CodeArea>()

// The review last seen in this folder, kept past the session: Claude Code carries a
// conversation on under a new session id (back from its session overview, say), and this
// mod's state is per session. The folder is what the new session shares with the old one.
// Which review only: its state is read anew, since it may have moved on or been cleared.
type Remembered = { session: ReviewSession }
const memoryKey = async ($: EngineInterface) => `last-review-2:${await $.session.cwd()}`
const remember = async ($: EngineInterface, s: ReviewSession) => {
  try {
    await $.store.set(await memoryKey($), { session: s })
  } catch {
    // only the recovery after a session change is lost
  }
}
const remembered = async ($: EngineInterface): Promise<Remembered | undefined> => {
  try {
    const v = (await $.store.get(await memoryKey($))) as Remembered | undefined
    return v?.session ? { session: v.session } : undefined
  } catch {
    return undefined
  }
}

// The review this folder last had, where this conversation is that review's, carried on under
// a new session id: the person typed its MR (`/review-mr !12`), or a review script ran on it
// (`--iid 12`), or ran on the folder's own branch (no `--iid`: the MR of the branch checked
// out, as `/rework-mr this MR` starts it) while nothing names another MR. A conversation
// that started a review of ANOTHER MR in the same folder, or none yet, gets none: its own
// review arrives with its first script call.
//
// Only in a conversation that used a review skill itself (`usedReviewSkill`), the test that
// lets a stray review go: a command's text names a script without running it as the review
// (a session working on the skills types one into a test session it drives), and a
// review carried on always brings its skill's use along in its transcript. Without it, a
// stray review was let go at each turn's start and taken up again a moment later.
const carriedReview = async ($: EngineInterface): Promise<Remembered | undefined> => {
  const m = await remembered($)
  if (!m || !(await usedReviewSkill($))) return undefined
  const iid = m.session.iid
  try {
    const typed = new RegExp(`<command-args>[^<]*!${iid}\\b`)
    const ran = new RegExp(`--iid[ =]${iid}\\b`)
    const messages = await $.session.messages()
    const typedBy = (x: (typeof messages)[number]) => x.role === 'user' && !(x.toolResults?.length)
    const scripts = messages.flatMap(x => x.toolUses.filter(u => u.tool === 'Bash' && REVIEW_SCRIPT.test(String(u.input.command ?? '')))
      .map(u => String(u.input.command ?? '')))
    const isNamed = messages.some(x => (typedBy(x) && typed.test(x.text))
      || x.toolUses.some(u => u.tool === 'Skill' && new RegExp(`!${iid}\\b`).test(String(u.input.args ?? ''))))
      || scripts.some(c => ran.test(c))
    // Another MR named anywhere the person or a script said one: the branch is not this review's.
    const others = [...messages.filter(typedBy).flatMap(x => [...x.text.matchAll(/<command-args>[^<]*?!(\d+)/g)].map(g => g[1])),
      ...scripts.flatMap(c => [...c.matchAll(/--iid[ =](\d+)/g)].map(g => g[1]))]
    const isOnBranch = scripts.some(c => !/--iid\b/.test(c)) && others.every(n => n === String(iid))
    return isNamed || isOnBranch ? m : undefined
  } catch {
    return undefined
  }
}

// The open review as a drawing reads it: this session's, as its last read of the state file
// left it. Never the folder's memory of a review: that is a copy from an earlier session,
// and the review may have moved on or been cleared since, so drawn it showed old topics until
// the first read replaced them. `recover` takes the memory's review up and reads its state
// before anything draws it.
const known = async ($: EngineInterface): Promise<{ s: ReviewSession | null; r: ReviewState | null }> => {
  // Nothing is drawn in a session that is not a review's, whatever its state still holds.
  if (!(await read($, inUse))) return { s: null, r: null }
  return { s: await read($, session), r: await read($, review) }
}

// Remember what a view showed: the review it belongs to, and the draft on its card.
// Remember what a view showed: the review it belongs to, and the draft on its card. Read
// from the mark first, the view's own manifest second (where the scripts predate the mark).
const noteView = async ($: EngineInterface, stdout: string) => {
  if (!(await read($, inUse))) return
  const m: Recorded | undefined = (await recorded($)) ?? manifestOf(stdout)
  if (!m?.review) return
  const s = m.review
  await update($, session, () => s)
  const isCard = m.topic && (m.focus === undefined || m.focus === m.topic) && viewOf(stdout)
  if (isCard) await update($, card, () => ({ topic: m.topic as string, draft: m.draft ?? null }))
  await refresh($, s)
}

// A call's output cut to its first line and a count of the rest.
const oneLine = (text: string): string => {
  const lines = text.replace(/\n+$/, '').split('\n')
  return lines.length <= 1 ? text : `${lines[0]}\n… +${lines.length - 1} lines`
}

// The model's own calls in a group the mod unfolded for a view in it, by id: their rows are
// cut to one line. Kept outside the mod's state, as a drawing may not write it; a reload
// forgets it, and the next drawing of the group notes them again.
//
// ctrl+o draws these rows one line too: nothing in a row tells the transcript's from the
// chat's, so the full output of such a call is in the session's log only.
const beside = new Set<string>()

// The text of every view drawn as a card in this session, so a reply that pastes one draws
// without it (`withoutPasted`). Kept as `beside` is, and for the same reason; a reload
// forgets it until the cards are drawn again, which they are before the replies under them.
const drawnViews = new Set<string>()

// Whether this conversation used a review skill itself: the person's /review-mr or
// /rework-mr, or the model's Skill call of one. What tells a conversation carried on under a
// new session id from any other in the same folder: having had turns says nothing (a
// session working ON the skills has plenty), and the folder may have had a review in
// another session days ago.
const SKILL_USE = new RegExp(`<command-(?:name|message)>/?(?:[\\w-]+:)?(?:${REVIEW_SKILLS.join('|')})\\b|Base directory for this skill: \\S*/(?:${REVIEW_SKILLS.join('|')})\\b`)
const usedReviewSkill = async ($: EngineInterface): Promise<boolean> => {
  try {
    // What the person typed, not what a tool printed: a session working on the skills reads
    // their transcripts, and their command lines are in that output.
    return (await $.session.messages()).some(m =>
      (m.role === 'user' && !(m.toolResults?.length) && SKILL_USE.test(m.text))
      || m.toolUses.some(u => u.tool === 'Skill' && REVIEW_SKILLS.includes(String(u.input.skill ?? '').replace(/^.*:/, ''))))
  } catch {
    return false
  }
}

// A review the mod took up by itself that nothing bears out any more (no review in the
// session's mark, no review skill in its conversation) is let go: the band and the sidebar
// go with it. One the person started with a review skill is never let go.
const dropStray = async ($: EngineInterface) => {
  if (!(await read($, inUse)) || (await read($, inUseBy)) === 'skill') return
  if ((await recorded($))?.review || (await usedReviewSkill($))) return
  await update($, inUse, () => false)
  await update($, inUseBy, () => null)
  await update($, session, () => null)
  await update($, review, () => null)
  if (await panelShown($)) {
    try { await $.ui.close({ id: PANE }) } catch { /* closed meanwhile */ }
  }
}

// A session that knows no review yet picks it up from its mark: Claude Code can carry a
// conversation on under a new session id (its background sessions do), and this mod's
// state is per session, so without this the band and the panel would be gone until the
// next view.
const recover = async ($: EngineInterface) => {
  if (await read($, session)) return
  // The session's own mark first. The folder's memory is for a conversation carried on
  // under a new id, which has turns already; a session with none is a fresh one, and its
  // review starts when the person starts it.
  const carried = (await carriedReview($))?.session
  const m = (await recorded($))?.review ?? carried
  if (!m) return
  // A review this folder had, carried on: the skill is in use here as it was there.
  await update($, inUse, () => true)
  await update($, inUseBy, v => v ?? 'recovered')
  await markSession($)
  await update($, session, () => m)
  await refresh($, m)
}

// A view's explainer line, split off from the rest of the view.
// The tools whose output a review's chat leaves out (the ToolResult hook).
const FOLDED_RESULTS = ['Bash', 'Skill']

// A view's line above its card: the explainer link, or a line in italics that says how the
// card came up ("No comments of yours to match"), which belongs to no topic either.
const splitLead = (text: string): [string, string] => {
  const m = /^(📖 \*\*Explainer:\*\*[^\n]*|_[^\n]+_)\n+/.exec(text)
  if (!m || (!m[1]?.startsWith('📖') && !titleOf(text.slice(m[0].length)))) return ['', text]
  return [m[1] as string, text.slice(m[0].length)]
}

const lastLine = (text: string) => text.trim().split('\n').pop()?.replace(/^error:\s*/, '') ?? ''

// The explainer line, its link opened by a plain click. In the fullscreen terminal Claude
// Code takes the click itself and shows a `file:` link in Finder, while the person wants the
// page: so the click is answered here, by the system's opener, as the terminal's own
// cmd-click would. Only this link; any other keeps the surface's behaviour.
const explainerLink = (
  $: EngineInterface, Markdown: ReturnType<EngineInterface['ui']['resolve']>['Markdown'], lead: string,
) => {
  const href = /\]\((file:\/\/[^)\s]+)\)/.exec(lead)?.[1]
  if (!href) return <Markdown text={lead} />
  const open = async () => {
    const path = decodeURI(href.slice('file://'.length))
    for (const opener of ['open', 'xdg-open']) {
      try {
        if ((await $.process.run([opener, path])).exitCode === 0) return
      } catch {
        // not this system's opener
      }
    }
  }
  return <Markdown key="explainer" text={lead} pressableLinks={[href]} onLinkPress={() => { void open() }} />
}

// A topic's label, the same in the card's title, the band and the panel: its state icon,
// the handle in the accent colour, and in review-mr the severity (in its colour) or kind.
const ACCENT = 'cyan'
// Inline code where the mod draws text itself rather than as markdown: the colour the
// engine's markdown gives a code span.
const CODE_COLOR = 'permission'
type LabelOf = { state: string; id: string; kind?: { icon: string; color?: string; isDim?: boolean } }
const labelOf = (t: ReviewState['topics'][number], isReview: boolean): LabelOf =>
  ({ state: STATE_ICON[t.state] ?? '', id: t.id, kind: isReview ? kindSlot(t) : undefined })
const drawLabel = (
  Text: ReturnType<EngineInterface['ui']['resolve']>['Text'], l: LabelOf, props: { bold?: boolean; dimColor?: boolean } = {},
) => (
  <Text {...props}>
    {`${l.state} `}<Text color={props.dimColor ? undefined : ACCENT}>{l.id}</Text>
    {l.kind && <Text color={l.kind.color} dimColor={l.kind.isDim || undefined}>{` ${l.kind.icon}`}</Text>}
  </Text>
)

// The colour the person's own name takes wherever a comment is signed: one word, "You",
// in one colour, so their own comments stand out from everyone else's.
const YOU = 'You'
const YOU_COLOR = 'magenta'
const author = (Text: ReturnType<EngineInterface['ui']['resolve']>['Text'], name: string | null | undefined) => (
  <Text bold color={name === YOU ? YOU_COLOR : undefined}>{name ?? 'someone'}</Text>
)

// A diff's size as lib/diffsize.py writes it, the counts in the colours a diff has. `pad`, for
// sizes one under another: the widths its file count and added count are padded to, so
// the columns line up.
const filesOf = (d: DiffSize) => `${d.files} ${d.files === 1 ? 'file' : 'files'}`
const drawSize = (Text: ReturnType<EngineInterface['ui']['resolve']>['Text'], d: DiffSize, isDim?: boolean, pad?: { files: number; added: number }) => (
  <Text dimColor={isDim || undefined}>
    {`\u{f4d2} ${filesOf(d).padEnd(pad?.files ?? 0)} `}
    <Text color={isDim ? undefined : 'green'}>{`+${d.added}`.padEnd(pad?.added ?? 0)}</Text>
    {' '}
    <Text color={isDim ? undefined : 'red'}>{`−${d.removed}`}</Text>
  </Text>
)

// A card's text, block by block: the asides dim, each comment with its author apart and,
// for the person's own, in their colour; a bar at the left as a quote has.
// Text that may hold code in diff form: the markdown as markdown, each diff as the sidebar
// draws its file (`partsOf`).
// A card part's heading, as the scripts write one: bold, alone on its line.
const HEADING_MD = /^\*\*[^*]+\*\*$/

// A run of a card's text, with the blank line kept that stood before it in the text: the
// runs are drawn one under the other, so without it the parts would close up.
const spaced = (Box: ReturnType<EngineInterface['ui']['resolve']>['Box'], r: SizeRun, key: string, el: JSX.Element) =>
  r.gap ? <Box key={`gap-${key}`} marginTop={1}>{el}</Box> : el

const drawParts = ($: EngineInterface, e: Parameters<EngineInterface['ui']['resolve']>[0], text: string, at: string, isDim?: boolean, copy?: JSX.Element | null) => {
  const { Box, Code, Markdown, Text } = $.ui.resolve(e)
  return partsOf(text).map((p, j) => 'md' in p
    ? (
      <Box key={`md-${at}-${j}`} flexDirection="column">
        {sizeRunsOf(p.md).map((r, k) => spaced(Box, r, `${at}-${j}-${k}`, 'md' in r
          ? <Markdown key={`md-${at}-${j}-${k}`} text={withEmoji(r.md)} dimColor={isDim || undefined} />
          : 'draftHead' in r
            // A draft's heading, its Copy beside it where the card can copy it.
            ? (
              <Box key={`draft-${at}-${j}-${k}`} flexDirection="row" columnGap={2}>
                <Markdown text={r.draftHead} dimColor={isDim || undefined} />
                {!isDim && copy}
              </Box>
            )
          : 'words' in r
            // A proposed edit: what is taken out struck through, what is put in marked. On
            // the diff's line colours, not its word colours: a word colour is bright, made
            // to mark a few words inside a line already coloured, and running text on it was
            // hard to read. A code span on a mark keeps the text's own colour, as the accent
            // on green or red was the hardest of all to read; unmarked it keeps the accent.
            ? (
              <Text key={`words-${at}-${j}-${k}`} dimColor={isDim || undefined}>
                {r.words.map((w, n) => (w.kind === 'same' && !w.isCode ? w.text
                  : <Text key={`w-${n}`} strikethrough={w.kind === 'removed' || undefined}
                    color={w.isCode && !isDim && w.kind === 'same' ? CODE_COLOR : undefined}
                    backgroundColor={isDim || w.kind === 'same' ? undefined : w.kind === 'removed' ? 'diffRemoved' : 'diffAdded'}>{w.text}</Text>))}
              </Text>
            )
          : 'commit' in r
            // A commit: the hash as code, the subject dim and italic, apart from the text.
            ? (
              <Box key={`commit-${at}-${j}-${k}`} flexDirection="row" gap={1}>
                <Text dimColor={isDim || undefined}>{r.commit.icon}</Text>
                {r.commit.sha && <Markdown text={`\`${r.commit.sha}\``} dimColor={isDim || undefined} />}
                <Text dimColor italic wrap="truncate-end">{r.commit.subject}</Text>
              </Box>
            )
            : (
              <Box key={`size-${at}-${j}-${k}`} flexDirection="row" gap={1}>
                {r.before.trim() && <Markdown text={r.before.trimEnd()} dimColor={isDim || undefined} />}
                {/* A size beside a heading (the fix's Changes) is said with it, so drawn dim;
                    one on a line of its own keeps a diff's colours. */}
                {drawSize(Text, r.size, isDim || HEADING_MD.test(r.before.trim()))}
                {/* What the size is said with, in brackets (how many commits), drawn dim. */}
                {r.after.trim() && (r.after.trim().startsWith('(')
                  ? <Text dimColor>{r.after.trim()}</Text>
                  : <Markdown text={r.after.trimStart()} dimColor={isDim || undefined} />)}
              </Box>
            )))}
      </Box>
    )
    : isDim
      // A diff in colour cannot be dimmed: on a card that is not the current one it is plain
      // text, so the colours on screen are the current topic's alone.
      ? (
        <Text key={`diff-${at}-${j}`} dimColor wrap="truncate-end">
          {[...(p.loc ? [p.loc] : []), ...p.hunks.flatMap(hunk => hunk.split('\n').slice(1)).map(l => `${p.isQuoted ? '▎ ' : ''}${l}`)].join('\n')}
        </Text>
      )
      : (
      <Box key={`diff-${at}-${j}`} flexDirection="row">
        {/* A quote's bar, as Markdown draws one, a row per line of the diff; and one for
            the blank line that parts it from the quote's text above. */}
        {p.isQuoted && (
          <Text dimColor>{[...(j > 0 ? [''] : []), ...p.hunks.flatMap(hunk => hunk.split('\n').slice(1))].map(() => '▎ ').join('\n')}</Text>
        )}
        <Box flexDirection="column" flexGrow={1} marginTop={j > 0 ? 1 : 0}>
          {p.loc && <Text bold color={ACCENT} wrap="truncate-end">{p.loc}</Text>}
          {p.hunks.map((hunk, k) => (
            <Box key={`hunk-${at}-${j}-${k}`} backgroundColor={k + 1 === p.at ? DISCUSSED_BG : undefined}>
              <Code source={hunk} path={p.path} format="diff" wrap="truncate-end" />
            </Box>
          ))}
        </Box>
      </Box>
    ))
}

// A comment's head, wherever one is drawn: its link (the glyph), who wrote it, and when, on
// one line of text with the link inline. Drawn as one line, not a row of parts: a link drawn
// as a block of markdown beside dim text set the dim parts flickering as the pointer moved.
const LINK_MD = /^\[[^\]]*\]\(([^)]*)\)$/
const hrefOf = (md: string | undefined): string | undefined => (md ? LINK_MD.exec(md)?.[1] : undefined)
const noteHeadLine = (
  $: EngineInterface, e: Parameters<EngineInterface['ui']['resolve']>[0],
  head: { url?: string | null; author?: string | null; when?: string | null; isDim?: boolean; hasBar?: boolean },
) => {
  const { Link, Text } = $.ui.resolve(e) as ReturnType<EngineInterface['ui']['resolve']> & { Link: (p: { href: string; label?: string }) => JSX.Element }
  return (
    <Text>
      {head.hasBar && <Text dimColor>{'▎ '}</Text>}
      {head.url && <Link href={head.url} label={LINK_ICON} />}
      {head.url && ' '}
      {head.isDim ? <Text bold dimColor>{head.author ?? 'someone'}</Text> : author(Text, head.author)}
      {head.when && ' '}
      {head.when && <Text dimColor>{head.when}</Text>}
    </Text>
  )
}

// A card's text, block by block: the asides dim, each comment with its link and author
// apart and, for the person's own, the author in their colour; a bar at the left as a quote
// has.
const drawBlocks = ($: EngineInterface, e: Parameters<EngineInterface['ui']['resolve']>[0], text: string, isDim?: boolean, copy?: JSX.Element | null) => {
  const { Box, Text } = $.ui.resolve(e)
  return blocksOf(text).map((b, i) => b.author
    ? (
      // The comment's head on a line of its own, its text below it at the card's whole
      // width, a quote, whose bar runs down every line however it wraps.
      <Box key={`block-${i}`} flexDirection="column" marginTop={b.isApart ? 1 : 0}>
        {noteHeadLine($, e, { url: hrefOf(b.link), author: b.author, when: b.when, isDim, hasBar: true })}
        {drawParts($, e, b.text, `n${i}`, isDim, copy)}
      </Box>
    )
    : (
      <Box key={`block-${i}`} flexDirection="column" marginTop={b.isApart ? 1 : 0}>
        {drawParts($, e, b.text, `b${i}`, b.isAside || isDim, copy)}
      </Box>
    ))
}

// A step's button drawn as a chip: its icon and label on a filled background between
// rounded caps, as the person's tmux tabs are. The recommended one in the accent fill, the
// rest in a muted one. The colours are the ones the icons were picked against; they are
// fixed, not the terminal theme's.
//
// The caps stand outside the Button: under the pointer a Button inverts everything in it,
// and a cap inverted is a block with the cap's shape cut out of it. Outside, they take the
// inverted label's colour while the pointer is over the chip, so it inverts whole.
const CHIP_CAPS = ['\u{E0B6}', '\u{E0B4}']
const CHIP_FILL = { primary: '#7aa2f7', muted: '#3b4261' }
const CHIP_TEXT = { primary: '#1a1b26', muted: '#c0caf5' }
const drawChip = (
  ui: { Box: ReturnType<EngineInterface['ui']['resolve']>['Box']; Button: ReturnType<EngineInterface['ui']['resolve']>['Button']; Text: ReturnType<EngineInterface['ui']['resolve']>['Text'] },
  b: BandButton, onPress: (ev: { surface?: string }) => void,
) => {
  const { Box, Button, Text } = ui
  const tone = b.isPrimary ? 'primary' : 'muted'
  const icon = iconOf(b.label)
  const cap = (glyph: string, key: string) => (
    <Text key={key} color={CHIP_FILL[tone]} hover={{ color: CHIP_TEXT[tone] }}>{glyph}</Text>
  )
  // No hotkey: the engine draws one ahead of its Button's label (`1: `), inside the chip
  // between its left cap and its fill. A chip is pressed with the pointer.
  return (
    <Box key={`chip-${b.key}`} flexDirection="row">
      {cap(CHIP_CAPS[0] as string, 'l')}
      <Button key={b.key} plain label={b.label} onPress={onPress}>
        <Text color={CHIP_TEXT[tone]} backgroundColor={CHIP_FILL[tone]}>{` ${icon ? `${icon} ` : ''}${b.label} `}</Text>
      </Button>
      {cap(CHIP_CAPS[1] as string, 'r')}
    </Box>
  )
}

// A draft's Copy, beside its heading on the card: pressed, it reads "Copied!" for a moment,
// in which it cannot be pressed again, as copy buttons do; where to paste it is said once.
const COPIED_MS = 2000
const copyChip = async ($: EngineInterface, e: Parameters<EngineInterface['ui']['resolve']>[0], tid: string | null) => {
  const { s, r } = await known($)
  if (!s || !r || !tid) return null
  const els = $.ui.resolve(e)
  const isCopied = (await read($, copiedDraft)) === tid
  const b: BandButton = { key: `copy-${tid}`, label: isCopied ? 'Copied!' : 'Copy', press: { kind: 'copy' } }
  if (isCopied) {
    // The same chip, filled as Copy's was, but no button for the moment it shows.
    const { Box, Text } = els
    return (
      <Box key={`copied-${tid}`} flexDirection="row">
        <Text color={CHIP_FILL.muted}>{CHIP_CAPS[0]}</Text>
        <Text color={CHIP_TEXT.muted} backgroundColor={CHIP_FILL.muted}>{' \u{F012C} Copied! '}</Text>
        <Text color={CHIP_FILL.muted}>{CHIP_CAPS[1]}</Text>
      </Box>
    )
  }
  return drawChip(els, b, ev => { void copyDraft($, s, r, tid, ev.surface) })
}

const copyDraft = async ($: EngineInterface, s: ReviewSession, r: ReviewState, t: string, surface?: string) => {
  const body = await runScript($, s, [s.skill === 'rework-mr' ? 'reply' : 'draft', t])
  const handles = body.stdout.match(/\bt[0-9]+\b/g)
  if (body.exitCode !== 0 || handles) {
    const why = handles ? `the draft names ${handles.join(', ')}, which mean nothing on GitLab` : lastLine(body.stderr)
    await update($, notice, () => ({ text: `Not copied: ${why}`, isError: true }))
    return
  }
  const copied = await $.ui.copy({ text: body.stdout.replace(/\n$/, ''), surface: surface as never })
  if (!copied.isCopied) {
    await update($, notice, () => ({ text: `Not copied: ${copied.reason}`, isError: true }))
    return
  }
  await update($, copiedDraft, () => t)
  // rework-mr's reply goes into a reviewer's thread: where to paste it is said once a topic.
  const where = s.skill === 'rework-mr' && t === r.current ? r.thread?.[0]?.url : undefined
  if (where && !(await read($, copyTold)).includes(t)) {
    await update($, copyTold, v => [...v, t])
    await update($, notice, () => ({ text: `${t}: copied its draft · paste it in`, url: where, label: 'its thread', isError: false, topic: t }))
  }
  try {
    await $.clock.sleep(COPIED_MS)
  } finally {
    // Back to Copy however the wait ended, or it would read Copied! for good.
    await update($, copiedDraft, v => (v === t ? null : v))
  }
}

// The current step's buttons, on the card that hosts them (`stepControls`): none while a
// turn or a press runs, as in the band. A click is the only way to press them here: a key
// reaches the band and the sidebar alone, never a row of the chat.
const cardButtons = async ($: EngineInterface, e: Parameters<EngineInterface['ui']['resolve']>[0], id: string) => {
  const host = await read($, hostCard)
  if (host?.id !== id) return null
  const { s, r } = await known($)
  if (!s || !r) return null
  const { buttons, press, isHosted } = await stepControls($, s, r, await read($, working))
  if (!isHosted || buttons.length === 0) return null
  const els = $.ui.resolve(e)
  const { Box } = els
  // The way on is a different kind of press from the decisions: it stands apart, at the
  // right end of the row, and never gives way to them: the decisions wrap instead.
  const way = buttons.filter(b => b.key === 'next')
  return (
    <Box flexDirection="row" marginTop={1} width="100%">
      <Box flexDirection="row" flexWrap="wrap" columnGap={1} flexGrow={1}>
        {buttons.filter(b => b.key !== 'next').map(b => drawChip(els, b, press(b)))}
      </Box>
      <Box flexDirection="row" flexShrink={0}>{way.map(b => drawChip(els, b, press(b)))}</Box>
    </Box>
  )
}

// A view in its frame. A topic's title line is drawn in colour, as the panel draws the
// topic's row: the severity icon in the severity's colour, the handle in the accent colour.
const drawCard = async ($: EngineInterface, e: Parameters<EngineInterface['ui']['resolve']>[0], view: string, id: string, of: ViewOf = {}) => {
  const { Box, Button, Markdown, Text } = $.ui.resolve(e)
  const buttons = await cardButtons($, e, id)
  // The sidebar beside the chat shows the current item's whole file; a card for that item
  // then leaves its excerpt out. Every other card, and every card where the sidebar is
  // closed or sits above the prompt, keeps it.
  const { s, r } = await known($)
  const vp = (e as { viewport?: { isFullscreen?: boolean } }).viewport
  // Docked beside the chat wherever the screen is fullscreen, at any width: the 110 columns
  // the engine wants before it opens a sidebar by itself are the terminal's, while `columns`
  // here is the chat's own, which a wider sidebar makes narrower.
  const isBeside = vp?.isFullscreen === true && (await panelShown($))
  // A topic's code is the sidebar's to show. A card leaves it out but where it is the one
  // place to read it: the current topic's card with no sidebar beside the chat. A card of
  // another topic never needs it; what it says stands, and the sidebar shows its file once
  // the topic is current again.
  //
  // Its thread goes the same way while the sidebar is beside the chat, and stays gone once
  // the topic is no longer current: a card keeps the shape it had, rather than growing its
  // thread back when the sidebar moves on to another topic.
  const shown = titleOf(view)
  const isCurrentCard = !!r && shown?.id === r.current
  const isThreadBeside = isBeside && !!r && (!isCurrentCard || (r.thread ?? []).some(th => th.notes.length > 0))
  const text = !shown ? view
    // No state read yet: nothing says another topic is current, and no sidebar shows it.
    : (!r || shown.id === r.current) && !isBeside ? withoutExcerptLabel(view)
      // The sidebar shows the topic's thread under its code: the card keeps what it adds.
      : isThreadBeside ? withoutThread(withoutExcerpt(view))
      : withoutExcerpt(view)
  // The draft it was drawn with is no longer the topic's (posted, by a press or by hand, or
  // redrafted, the newer version on a card below): its part goes too, with its Copy, which
  // would copy another text than the one it stands beside. Copy is the newest version's.
  const tid = shown?.id ?? of.topic
  const cardTopic = tid && r ? r.topics.find(t => t.id === tid) : undefined
  const isDraftOld = !!cardTopic && (!cardTopic.draft || (!!of.draft && of.draft !== cardTopic.draft))
  const shownText = isDraftOld ? withoutDraft(text) : text
  const title = titleOf(shownText)
  const done = (await read($, outcomes))[id]
  if (!title) {
    // A card that was a draft alone, and the draft is no longer the topic's: nothing is left
    // to show, and an empty frame reads as a broken card.
    if (!shownText.trim()) return <Box />
    // A card with no title line (a redrafted reply): its topic is the manifest's.
    return (
      <Box borderStyle="round" paddingX={1} flexDirection="column" width="100%">
        {drawBlocks($, e, shownText, false, await copyChip($, e, of.topic ?? null))}
        {done && <Box marginTop={1}>{outcomeLine($, e, done)}</Box>}
        {buttons}
      </Box>
    )
  }
  const topic = r?.topics.find(t => t.id === title.id)
  // A card that only held what the sidebar shows (the code and the thread) has nothing left
  // to say. An open topic's draws as nothing, as its proposal is on its way; a closed or
  // waiting topic's, opened to look at, says where it stands under its title, so the chat
  // ends on what is looked at.
  const isBare = blocksOf(title.rest).length === 0 && !buttons
  const where = isBare ? standing(r, topic) : null
  if (isBare && !where) return <Box />
  // The cards of the other topics step back, so the eye finds the one being decided.
  const isDim = !!r && title.id !== r.current
  // The ✕ is the current topic's newest card's, with buttons or none (a topic waiting on
  // someone else has none): one card closes the topic, not every one it ever had.
  const isNewest = (await read($, hostCard))?.id === id
  // The icons as the view printed them; the colour from the topic's record.
  const label: LabelOf = {
    state: title.state, id: title.id,
    kind: title.kind ? { ...(topic && !isDim ? kindSlot(topic) : { isDim: true }), icon: title.kind } : undefined,
  }
  return (
    <Box borderStyle="round" borderDimColor={isDim || undefined} paddingX={1} flexDirection="column" width="100%">
      <Box flexDirection="column" marginBottom={1}>
        <Box flexDirection="row" width="100%">
          <Box flexGrow={1}>
            <Text bold dimColor={isDim || undefined}>
              {drawLabel(Text, label, { bold: true, ...(isDim ? { dimColor: true } : {}) })}{title.summary ? ` ${plainTitle(title.summary)}` : ''}
            </Text>
          </Box>
          {/* The step's card closes: back to the sidebar's overview, the topic as it is.
              Not while the topic must be finished first. */}
          {isNewest && s && !isDim && !isHeldTopic(topic) && (
            <Button key="card-close" plain dimColor label={GLYPH.close} onPress={() => { void leaveTopic($, s) }} />
          )}
        </Box>
        {title.tail && <Markdown text={title.tail} dimColor />}
      </Box>
      {isBare
        ? <Text dimColor>{where}</Text>
        : drawBlocks($, e, title.rest, isDim, await copyChip($, e, title.id))}
      {done && <Box marginTop={1}>{outcomeLine($, e, done)}</Box>}
      {buttons}
    </Box>
  )
}

// Where a topic stands, in a few words, for a card that has nothing else to say: the rest
// of it (its code, its thread) is in the sidebar.
const standing = (r: ReviewState | null, t: ReviewState['topics'][number] | undefined): string | null => {
  const other = r?.skill === 'rework-mr' ? 'the reviewers' : r?.author ?? 'the author'
  const word = !t ? null : t.state === 'waiting' || t.status === 'waiting' ? `Waiting for ${other}`
    : ['done', 'acked', 'wontfix'].includes(t.state ?? '') || t.status === 'done' ? 'Closed'
    : t.state === 'dropped' ? 'Dropped'
    : null
  return word && `${word}: its code and its thread are in the sidebar.`
}

// A line that says what happened and, where there is one, links to it: a short label and
// an arrow, inline, as a comment's head links. A card's last line, and a notice.
const outcomeLine = ($: EngineInterface, e: Parameters<EngineInterface['ui']['resolve']>[0], o: ReviewOutcome) => {
  const { Link, Text } = $.ui.resolve(e) as ReturnType<EngineInterface['ui']['resolve']> & { Link: (p: { href: string; label?: string }) => JSX.Element }
  return (
    <Text dimColor>
      {o.icon ? `${o.icon} ` : ''}{o.text}
      {o.url && ' '}
      {o.url && <Link href={o.url} label={`${o.label ?? 'open'} ↗`} />}
      {o.url && o.after ? ` ${o.after}` : ''}
    </Text>
  )
}

// The card of a topic whose news is still being judged: its title, and that it waits.
const drawPending = async ($: EngineInterface, e: Parameters<EngineInterface['ui']['resolve']>[0], tid: string) => {
  const { Box, Text } = $.ui.resolve(e)
  const { r } = await known($)
  const topic = r?.topics.find(t => t.id === tid)
  const label: LabelOf = { state: STATE_ICON[topic?.state ?? ''] ?? ' ', id: tid, kind: topic ? kindSlot(topic) : undefined }
  const what = r?.skill === 'rework-mr' ? 'this thread and propose a plan' : topic?.peer ? 'this thread' : `${r?.author ?? 'the author'}'s answer`
  return (
    <Box borderStyle="round" borderDimColor paddingX={1} flexDirection="column" width="100%">
      <Text bold>{drawLabel(Text, label, { bold: true })}{topic?.summary ? ` ${plainTitle(topic.summary)}` : ''}</Text>
      <Text dimColor>{`Waiting for the background agent to judge ${what}…`}</Text>
    </Box>
  )
}

// A view as the chat shows it. The explainer link heads the opener; it is about the whole MR,
// not the topic below it, so it stands on its own line above the topic's card.
// `of`: the topic and the draft version the view was printed with (its manifest's).
type ViewOf = { topic?: string; draft?: string }
const drawView = async ($: EngineInterface, e: Parameters<EngineInterface['ui']['resolve']>[0], text: string, id: string, of: ViewOf = {}) => {
  const { Box, Markdown } = $.ui.resolve(e)
  const [lead, body] = splitLead(text)
  return (
    <Box flexDirection="column">
      {lead && (lead.startsWith('📖')
        ? <Box paddingX={1}>{explainerLink($, Markdown, lead)}</Box>
        // How the card came up, set off from it: the card is what the person reads next.
        : <Box paddingX={1} marginBottom={2}><Markdown text={lead} /></Box>)}
      {body && (await drawCard($, e, body, id, of))}
    </Box>
  )
}

// The current step's buttons and what a press on one does, for the band and for the card
// that hosts them (`hostOf`): one set of buttons, wherever it is drawn.
const stepControls = async ($: EngineInterface, s: ReviewSession, r: ReviewState, isWorking: boolean) => {
  const own = currentOwn(r, await read($, ownPick))
  const topic = currentTopic(r)
  const doing = await read($, busy)

  // Runs one press to its end: the script, the state read back, the next topic's card,
  // and a note so the model knows at its next turn what the person decided.
  // `isStaying`: the press goes on with a model turn on the same topic (Agree and reply), so
  // the next topic's card would only come up to be left at once.
  // The card the step's buttons are on, which a press finishes: what the press did is its
  // last line.
  const finish = async (pressed: BandButton['press'], stdout: string) => {
    const at = await read($, hostCard)
    const done = outcomeOf(pressed, stdout)
    if (at && done && at.topic === (topic?.id ?? null)) await update($, outcomes, m => ({ ...m, [at.id]: done }))
  }

  const act = async (label: string, args: string[], did: string, env?: Record<string, string>, isStaying = false, pressed?: BandButton['press']) => {
    await update($, busy, () => label)
    try {
      const before = r.current
      const ran = await runScript($, s, args, env)
      if (ran.exitCode !== 0) {
        await update($, notice, () => ({ text: lastLine(ran.stderr) || `${label} failed`, isError: true }))
        return false
      }
      if (pressed) await finish(pressed, ran.stdout)
      const now = await refresh($, s)
      const after = now?.current ?? null
      await update($, notice, () => ({ text: did, isError: false, topic: before }))
      // The round moves on: the next topic's card, or a comment of the person's own whose
      // match waits.
      const isMoved = after !== before
      if (isMoved && !isStaying) await showStep($, s, now)
      await $.session.append({
        message: { type: 'user', content: [{ type: 'text', text: noteForModel(did, before, after) }] },
      })
      return true
    } finally {
      await update($, busy, () => null)
    }
  }

  // A press that leaves its topic's card where it is (the press moved the topic a step on
  // in place) leaves that card the step's host: only a step moved behind its back (by the
  // model's turn) needs a new card (`cardForCurrent`).
  const press = (b: BandButton) => async (ev: { surface?: string }) => {
    await pressOn(b, ev)
    const now = currentTopic(await read($, review))
    await update($, hostCard, h => (h?.step && now && h.topic === now.id ? { ...h, step: now.step ?? null } : h))
  }

  const pressOn = async (b: BandButton, ev: { surface?: string }) => {
    if (!topic && !['sync', 'own-done', 'settle', 'ask', 'fill', 'own-step', 'explainer'].includes(b.press.kind)) return
    const t = topic?.id ?? ''
    switch (b.press.kind) {
      case 'own-step': {
        const to = b.press.to ?? stepOwn(r, own?.id, b.press.by)
        if (to) await pickOwn($, s, to, true)
        return
      }
      case 'ask':
        await update($, notice, () => null)
        {
          // What was explained: the topic, or the comment of the person's whose match the
          // band asks about.
          const what = phaseOf(r) === 'own' ? currentOwn(r, await read($, ownPick))?.id : t
          if (b.key === 'explain' && what) await update($, explained, v => (v.includes(what) ? v : [...v, what]))
        }
        await $.prompt.submit({ text: b.press.prompt })
        return
      case 'fill':
        await update($, notice, () => null)
        await $.prompt.fill({ text: b.press.text })
        return
      case 'push': {
        // The script's alone: the checks ran when the fix was made. What it cannot finish
        // (files changed since, notes in the diff viewer, a conflict) goes to the model,
        // in the script's words (exit 3).
        await update($, busy, () => b.label)
        try {
          const ran = await runScript($, s, argsFor(b.press, t, null) ?? [], { REVIEW_UI_PRESS: '1' }, PUSH_MS)
          if (ran.exitCode === 3) {
            await update($, notice, () => null)
            await $.prompt.submit({ text: ran.stdout.trim() })
            return
          }
          if (ran.exitCode !== 0) {
            await update($, notice, () => ({ text: lastLine(ran.stderr) || `${b.label} failed`, isError: true }))
            return
          }
          await finish(b.press, ran.stdout)
          const before = r.current
          const now = await refresh($, s)
          const did = didText(b.press, t)
          await update($, notice, () => ({ text: did, isError: false, topic: t }))
          // What goes into the thread is the next card's: a "Fixed", or a reply that says more.
          await showStep($, s, now)
          await $.session.append({
            message: { type: 'user', content: [{ type: 'text', text: noteForModel(did, before, now?.current ?? null) }] },
          })
        } finally {
          await update($, busy, () => null)
        }
        return
      }
      case 'link': {
        // "I posted my own": the comment is looked for among the person's own on the
        // topic's file. None there yet is no failure, only early; several is the model's
        // to sort out, which can read them.
        if (await act(b.label, ['link', t], didText(b.press, t))) {
          // Often reworded by hand: the agent checks what went up before the round moves on.
          await $.prompt.submit({ text: checkPrompt(t) })
          return
        }
        const why = (await read($, notice))?.text ?? ''
        if (/^no comment of yours/.test(why)) {
          await update($, notice, () => ({
            text: `${why[0]?.toUpperCase()}${why.slice(1)}. Post it there and press “${b.label}” again, or press Post to post it from here.`,
            isError: false, isWarning: true, topic: t,
          }))
        } else if (/^\d+ comments of yours/.test(why)) {
          await update($, notice, () => null)
          await $.prompt.submit({ text: `I posted my own comment for ${t}, and ${why.split(' — ')[0]}: link the right one to ${t}.` })
        }
        return
      }
      case 'copy':
        await copyDraft($, s, r, t, ev.surface)
        return
      case 'own-done': {
        // The script syncs and, with no comment of the person's own to match, opens the first
        // topic and prints its card: no turn of the model's. With comments, it prints them
        // for the agent to match, which is the model's turn.
        await update($, busy, () => b.label)
        try {
          const ran = await runScript($, s, argsFor(b.press, t, null) ?? [])
          if (ran.exitCode !== 0) {
            await update($, notice, () => ({ text: lastLine(ran.stderr) || `${b.label} failed`, isError: true }))
            return
          }
          const now = await refresh($, s)
          if (viewOf(ran.stdout)) {
            await placeOutput($, ran.stdout)
            await $.session.append({
              message: { type: 'user', content: [{ type: 'text', text: noteForModel(didText(b.press, t), null, now?.current ?? null) }] },
            })
          } else {
            await $.prompt.submit({ text: 'My own review in GitLab is done: pull in my comments.' })
          }
        } finally {
          await update($, busy, () => null)
        }
        return
      }
      default: {
        const args = argsFor(b.press, t, await read($, card))
        if (!args) return
        const env = ['post', 'resolve', 'apply-edit'].includes(b.press.kind) ? { REVIEW_UI_PRESS: '1' } : undefined
        // An agreed plan the model goes on with on the same topic: a reply ahead of the fix,
        // or the reply a plan of replying is.
        const follow = b.press.kind === 'agree' ? (b.press.reply ? earlyPrompt(t) : b.press.draft ? replyPrompt(t) : null) : null
        const ok = await act(b.label, args, didText(b.press, t), env, !!follow, b.press)
        if (ok && follow) {
          await $.prompt.submit({ text: follow })
          return
        }
        const left = (await read($, review))?.proposals ?? []
        if (ok && b.press.kind === 'settle' && left.length > 0) {
          // The next comment that waits comes up with its card.
          await pickOwn($, s, (left[0] as { thread: string }).thread, true)
        }
        if (ok && b.press.kind === 'explainer') {
          // The review waits on this answer: the agent goes on from it, with the
          // explainer spawned in the background where the person chose one.
          await $.prompt.submit({ text: b.press.how === 'generate'
            ? 'Generate the explainer for this MR in the background, then carry on with the review.'
            : 'Skip the explainer for this MR and carry on with the review.' })
        }
        if (ok && b.press.kind === 'settle' && b.press.how === 'merge') {
          // The topic is the user's comment now: what the agent's own analysis adds to
          // it (a better fix, a proven alternative) needs the model to say.
          await $.prompt.submit({ text: `I merged my comment into ${b.press.into}. What does your own analysis add to it?` })
        }
      }
    }
  }

  // The proposal the band answers is the current comment's: its card is in the chat. A
  // comment already settled has none, and the band then leads back to one that waits.
  const asked = askedFor(r, own)
  const isSettledPick = phaseOf(r) === 'own' && r.own_review !== 'pending'
    && (asked.proposals?.length ?? 0) === 0 && (r.proposals?.length ?? 0) > 0
  const buttons = isWorking || doing ? []
    : isSettledPick ? [
      { key: 'own-back', label: 'Next undecided comment', press: { kind: 'own-step', by: 1, to: r.proposals?.[0]?.thread } },
    ] as BandButton[]
    : buttonsFor({ ...asked, explained: await read($, explained), unjudged: (await read($, judging)).failed })
  // The newest card in the chat is the current step's: the buttons are drawn on it, and the
  // band keeps the feedback alone. A step with no card of its own keeps them in the band.
  const host = await read($, hostCard)
  const isHosted = !isSettledPick && !!host && host.topic === (r.current ?? null)
  return { buttons, press, isHosted, host }
}

export const register: Register = on => {
  // The main loop's turns only: the background agent's run beside them changes nothing the
  // person works on, and its end is read back by itself.
  on('turn.start', async ($, e, next) => {
    if ((e as { agentId?: string }).agentId) return next(e)
    await update($, working, () => true)
    await dropStray($)
    await recover($)
    return next(e)
  })
  on('turn.complete', async ($, e, next) => {
    const agentId = (e as { agentId?: string }).agentId
    if (agentId) {
      const out = await next(e)
      await endJudging($, agentId)
      return out
    }
    await update($, working, () => false)
    const out = await next(e)
    await cardForCurrent($)
    return out
  })
  on('turn.abort', async ($, e, next) => {
    const agentId = (e as { agentId?: string }).agentId
    if (agentId) {
      const out = await next(e)
      await endJudging($, agentId)
      return out
    }
    await update($, working, () => false)
    return next(e)
  })

  // The background agent's report, handed back to the main loop: dropped, so the model does
  // not answer it in the chat.
  on('prompt.submit', async ($, e, next) => {
    if (await isJudgeReport($, e.origin, e.text)) return { drop: 'review-ui: the news was judged in the background; the sidebar has it' }
    await useSkill($, e.command?.name)
    return next(e)
  })
  on('tool.call', { tool: 'Skill' }, async ($, e, next) => {
    await useSkill($, String((e as { skill?: unknown }).skill ?? ''))
    return next(e)
  })

  on('session.start', async ($, e, next) => {
    await dropStray($)
    await markSession($)
    await recover($)
    // The update check: GitLab asked again, with no model and no tokens. Every 30 seconds
    // while the person's own review is open (their comments show in the sidebar as they
    // post them), every 3 minutes otherwise; never while a turn runs, since the model's
    // own commands write the same state file.
    $.clock.every(TICK_MS, () => { void check($) })
    await $.tool.register({
      name: SHOW_TOPIC,
      description:
        'Shows one topic of the open review-mr review to the user as a card, and makes it the ' +
        'current topic. Use it wherever the skill says `findings.py quote <t> --focus`. ' +
        'Returns the view, which the user already sees.',
      inputSchema: {
        type: 'object',
        properties: { topic: { type: 'string', description: 'The topic, like t3' } },
        required: ['topic'],
      },
    })
    return next(e)
  })

  on('tool.call', { tool: 'mcp__review-ui__show_topic' }, async ($, e) => {
    const s = await read($, session)
    if (!s) return { deny: 'No review is open in this session yet: run the opener first.' }
    const topic = String((e as { topic?: unknown }).topic ?? '')
    const r = await runScript($, s, ['quote', topic, '--focus'])
    if (r.exitCode !== 0) return { deny: lastLine(r.stderr) || `quote ${topic} failed` }
    await noteView($, r.stdout)
    await shownCard($, e.tool_use_id, r.stdout)
    return { result: withNote(r.stdout) }
  })

  // A view the person gets as a card carries a line for the model, which the skills read
  // as "already shown". Research (another topic's view) gets no card, so no line either.
  // Any call of the review's scripts may change it, so the band's state is read again after it.
  on('tool.call', { tool: 'Bash' }, async ($, e, next) => {
    if (isPostCommand(e.command)) {
      return { deny: 'Posting and resolving are the user\'s press: the Post or Resolve thread button on the card. Leave it to them.' }
    }
    if (REVIEW_SCRIPT.test(e.command)) await markSession($)
    // The background agent's own calls: no card, no note; its end reads the state back.
    if ((e as { agentId?: string }).agentId) return next(e)
    const ran = await next(e)
    if (ran.deny !== undefined || ran.isError) return ran
    const result = ran.result as { stdout?: unknown } | undefined
    const stdout = typeof result?.stdout === 'string' ? result.stdout : undefined
    if (stdout !== undefined && isViewCommand(e.command)) await noteView($, stdout)
    else if (REVIEW_SCRIPT.test(e.command)) {
      const s = await read($, session)
      if (s) await refresh($, s)
    }
    if (stdout === undefined || !isViewCommand(e.command) || !viewOf(stdout)) return ran
    if (!(await read($, inUse))) return ran        // no review here: the output as printed
    await shownCard($, e.tool_use_id, stdout)
    return { ...ran, result: { ...result, stdout: withNote(stdout) } }
  })

  // The transcript folds a run of shell calls into one count line; a view folded away is a
  // view not shown, so a group holding one stays unfolded.
  on('ui.render', { component: 'ToolGroup' }, async ($, e, next) => {
    if (!(await read($, inUse))) return next(e)
    // A run of nothing but the review scripts' quiet calls is left out whole.
    if (e.props.calls.length > 0 && e.props.calls.every(c => isHiddenCall(c.tool, c.input, c.output, c.output === undefined))) {
      const { Box } = $.ui.resolve(e)
      return <Box />
    }
    if (e.props.isExpanded) return next(e)
    const holdsView = e.props.calls.some(c => {
      if (!isViewCall(c.tool, c.input)) return false
      // A call still running, or one that printed research, unfolds nothing.
      return c.output === undefined || viewOf(stdoutOf(c.output)) !== undefined
    })
    if (!holdsView) return next(e)
    // Unfolding the group unfolds every call in it, and an unfolded call prints its whole
    // output: the model's research around a view (a file it read, a grep) would fill the
    // screen. Those rows keep one line, cut where each is drawn: the group's own list of
    // calls is not what the engine draws them from.
    // Only a call that has finished: while one streams in, its command is still empty, and
    // a view's call taken for research then never became its card.
    for (const c of e.props.calls) {
      if (c.output !== undefined && !isViewCall(c.tool, c.input) && c.tool_use_id) beside.add(c.tool_use_id)
    }
    return next({ ...e, props: { ...e.props, isExpanded: true } })
  })

  on('ui.render', { component: 'ToolUse' }, async ($, e, next) => {
    const { tool, input, output, isRunning, isErrored } = e.props
    // The review scripts' own calls that print no view (a sync, a lookup, the model's
    // research into the state) say nothing the person acts on: in a review they are left
    // out of the chat, as the views are drawn as cards. ctrl+o still shows them all. First,
    // before the rule for calls beside a view: a quiet call in a view's group is still quiet.
    if (isHiddenCall(tool, input, output, isRunning) && (await read($, inUse))) {
      const { Box } = $.ui.resolve(e)
      return <Box />
    }
    // A call of the model's own unfolded beside a view: its first line, and a count of the
    // rest, as its folded group would have said it.
    if (beside.has(e.props.tool_use_id) && !isRunning && !isViewCall(tool, input)) {
      const out = stdoutOf(output)
      if (typeof out === 'string' && out.includes('\n') && typeof output === 'object' && output !== null) {
        return next({ ...e, props: { ...e.props, output: { ...(output as object), stdout: oneLine(out) } } })
      }
      return next(e)
    }
    if (isRunning || isErrored || !isViewCall(tool, input)) return next(e)
    const view = viewOf(stdoutOf(output))
    if (!view || !(await read($, inUse))) {
      // A view command whose row stays a row: why, in the debug log, as the screen cannot say.
      $.ui.log(`review-ui: ${e.props.tool_use_id} not drawn as a card: ${!view ? `no view in its output (${typeof output}${typeof stdoutOf(output) === 'string' ? `, ${String(stdoutOf(output)).length} chars` : ''})` : 'no review in use'}`, { to: 'debug' })
      return next(e)
    }

    // The card stands in place of the whole row: the command that printed the view says
    // nothing the card does not, and the model's call itself is unchanged.
    drawnViews.add(view.text)
    return drawView($, e, view.text, e.props.tool_use_id, view)
  })

  // The result the engine draws under a standalone call's row. Where the row is a card (an
  // MCP tool's, such as show_topic), the card already is the result. Under a shell command
  // or a skill's load in a review, it is the agent's research, a few lines each that bury the
  // cards between them: the row says what ran, and the output is left out. Only the drawing:
  // the model keeps the whole result, ctrl+o shows it, and an error is always drawn.
  on('ui.render', { component: 'ToolResult' }, async ($, e, next) => {
    if (e.props.isExpanded || e.props.isErrored || !(await read($, inUse))) return next(e)
    if (!FOLDED_RESULTS.includes(e.props.tool) && !viewOf(stdoutOf(e.props.output))) return next(e)
    const { Box } = $.ui.resolve(e)
    return <Box />
  })

  // A reply's text, without what it pasted of a card above it.
  on('ui.render', { component: 'AssistantMessage' }, async ($, e, next) => {
    if (e.props.isSummary || !(await read($, inUse))) return next(e)
    if (drawnViews.size === 0) return next(e)
    const text = withoutPasted(e.props.text, drawnViews)
    if (text === e.props.text.trim()) return next(e)
    if (!text) {
      const { Box } = $.ui.resolve(e)
      return <Box />
    }
    return next({ ...e, props: { ...e.props, text } })
  })

  // A view a press brought up, such as the next topic after a Drop: there is no tool row to
  // hang it under, so it is a row of its own at the transcript's end.
  on('ui.render', { component: 'Placed' }, async ($, e, next) => {
    if (e.props.plugin !== P) return next(e)
    const text = (await read($, placed))[e.props.id]
    if (!text) return next(e)
    if (text.startsWith(PENDING)) return drawPending($, e, text.slice(PENDING.length))
    drawnViews.add(text)
    // As a model's view: a line saying how the card came up stands above it.
    return drawView($, e, text, e.props.id, (await read($, placedOf))[e.props.id])
  })

  // The wheel, or a trackpad's two-finger swipe, moves what is under the pointer, a line per
  // tick: the file, its thread box, or another area that scrolls inside itself. Elsewhere the
  // engine's own scrolling, which has nothing to move while the sidebar fits its pane. The
  // chat is a site of its own and never comes here.
  on('ui.scroll', { requestId: PANE }, async ($, e, next) => {
    const area = codeAreas.get(await $.session.id())
    if (!area || !e.pointer || e.origin.kind !== 'person') return next(e)
    // The row under the pointer in the sidebar's tree: the window's own offset (where it
    // stands before this move) plus the pointer's row in it.
    const last = Math.max(0, e.contentRows - e.bodyRows)
    const row = e.pointer.row + Math.min(last, Math.max(0, e.offset - e.by))
    const list = area.lists?.find(x => row >= x.top && row < x.bottom)
    if (list) {
      await update($, areaAt, m => ({ ...m, [list.key]: { at: Math.min(list.max, Math.max(0, list.at + e.by)), on: list.on } }))
      return {}
    }
    const box = area.box
    if (box && row >= box.top && row < box.bottom) {
      await update($, boxAt, m => ({ ...m, [box.key]: Math.min(box.max, Math.max(0, (m[box.key] ?? 0) + e.by)) }))
      return {}
    }
    const view = area.view
    if (!view || row < area.top || row >= area.bottom) return next(e)
    await update($, codeAt, m => ({ ...m, [area.key]: clampOffset(view, area.rows, area.start + e.by) }))
    return {}
  })

  // Closing the panel by its ✕ or ctrl+x x flips the band's toggle back.
  on('ui.close', async ($, e, next) => {
    const out = await next(e)
    if (e.id === PANE) {
      await update($, panel, () => false)
      if (e.origin.kind === 'person') await update($, sidebar, v => ({ ...v, isClosedByPerson: true }))
    }
    return out
  })

  // The sidebar: the list (the person's own threads while their review is open, the topics
  // after), the current one marked; in rework-mr the current topic's whole thread; then the
  // current item's file, a window of it that fills the rest of the height; and at the foot
  // what the icons mean.
  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Button, Code, Markdown, Text } = $.ui.resolve(e)
    const { s, r } = await known($)
    if (!s || !r) return <Text dimColor>No review open in this session yet.</Text>
    const phase = phaseOf(r)
    const isReview = r.skill === 'review-mr'
    const isDocked = e.props.placement === 'dock'
    const cols = e.props.bodyColumns
    const rowsAll = e.props.scroll.bodyRows
    const own = currentOwn(r, await read($, ownPick))
    // A topic opened only to look at (nothing to decide, or another reviewer's thread to know
    // of) closes again from the file's ✕, or a second click on its row.
    // Once the MR is merged or closed, nothing waits on the person any more: any topic closes.
    const isOverMr = r.mr?.state === 'merged' || r.mr?.state === 'closed'
    // A fix made and not pushed holds its topic: no way out of it but its push.
    const isLeavable = phase === 'topics' && r.current !== null && !isHeldTopic(currentTopic(r))
      && (isOverMr || ['none', 'watch', 'waiting'].includes(stepOf(currentTopic(r))))
    const leave = () => leaveTopic($, s)
    const sid = await $.session.id()
    const scrollAt = await read($, areaAt)
    // Where each area that scrolls inside itself sits in the sidebar's tree, for the wheel.
    const lists: NonNullable<CodeArea['lists']> = []

    // An area's line: its name and what it says (`segs`), a rule to the right edge, and its
    // own controls at the right (`right`, `width` cells). Where the line is too narrow, the
    // first part gives way from its start (a path keeps its file's name), then the last parts.
    type Seg = { text: string; color?: string; isBold?: boolean; isDim?: boolean }
    const cells = (x: string) => [...x].length
    const rule = (key: string, segs: Seg[], right?: { el: JSX.Element; width: number }) => {
      const tail = right ? right.width + 3 : 1
      const room = Math.max(4, cols - 4 - tail - 1)
      let parts = segs.filter(x => x.text)
      const total = () => parts.reduce((n, x) => n + cells(x.text), 0)
      const head = parts[0]
      if (head && total() > room) {
        // Cut from its start, down to its last path part (a file's name) and the "…" before it.
        const chars = [...head.text]
        const least = cells(head.text.split('/').pop() ?? '') + 1
        const keep = Math.max(least, chars.length - (total() - room))
        if (keep < chars.length) parts = [{ ...head, text: `…${chars.slice(chars.length - keep + 1).join('')}` }, ...parts.slice(1)]
      }
      while (parts.length > 1 && total() > room) parts = parts.slice(0, -1)
      // A line with nothing to say is the rule alone, unbroken.
      if (parts.length === 0 && !right) return <Text key={key} dimColor>{'─'.repeat(Math.max(1, cols))}</Text>
      const dashes = Math.max(1, cols - 4 - total() - tail)
      return (
        <Box key={key} flexDirection="row" flexShrink={0}>
          <Text dimColor>{'── '}</Text>
          {parts.map((x, i) => (
            <Text key={`${key}-${i}`} color={x.color} bold={x.isBold || undefined} dimColor={x.isDim || undefined}>{x.text}</Text>
          ))}
          <Text dimColor>{` ${'─'.repeat(dashes)}`}</Text>
          {right && <Text> </Text>}
          {right?.el}
          <Text dimColor>{right ? ' ─' : '─'}</Text>
        </Box>
      )
    }
    // An area's rows, one line each, in at most `cap` lines: past that a window of them that
    // scrolls inside the area, by its arrows (the up one at the end of its first line, the
    // down one at the end of its last, dim where it cannot move further) and by the wheel over
    // it. The fold key, where the area has one, stands left of the up arrow. `at` is where the
    // window starts unless the person moved it for this `item` (the current one). `rows` is
    // the lines themselves, or how many there are and a drawing of a run of them (a thread's,
    // whose code block is one element over several lines).
    const areaRows = (key: string, rows: JSX.Element[] | { count: number; draw: (from: number, n: number) => JSX.Element[] },
      cap: number, top: number, at: number, item: string | null, fold?: JSX.Element | false, news: number[] = []) => {
      const count = Array.isArray(rows) ? rows.length : rows.count
      const n = Math.min(count, cap)
      const tag = key.split(':')[0]       // the area's name, which its keys are named by
      const max = count - n
      const moved = scrollAt[key]
      const from = Math.min(max, Math.max(0, moved && moved.on === item ? moved.at : at))
      const isScrolled = max > 0
      if (isScrolled) lists.push({ top, bottom: top + n, key, max, at: from, on: item })
      const move = (to: number) => update($, areaAt, m => ({ ...m, [key]: { at: Math.min(max, Math.max(0, to)), on: item } }))
      // An arrow past which rows with news lie is in the colour of news.
      const isNewsAbove = news.some(i => i < from)
      const isNewsBelow = news.some(i => i >= from + n)
      const up = isScrolled && (
        <Button key={`${tag}-up`} plain dimColor={(from === 0 && !isNewsAbove) || undefined} onPress={() => move(from - SCROLL_STEP)}>
          <Text color={isNewsAbove ? 'yellow' : undefined}>{GLYPH.up}</Text>
        </Button>
      )
      const shown = Array.isArray(rows) ? rows.slice(from, from + n) : rows.draw(from, n)
      const keys = (fold || up) && <Box key={`${tag}-keys`} flexDirection="row" gap={1} flexShrink={0}>{fold}{up}</Box>
      return (
        <Box key={`${tag}-rows`} flexDirection="column" position="relative" flexShrink={0}>
          {keys
            ? (
              <Box key={`${tag}-first`} flexDirection="row" gap={1}>
                <Box flexGrow={1} flexShrink={1} flexDirection="column">{shown[0]}</Box>
                {keys}
              </Box>
            )
            : shown[0]}
          {shown.slice(1)}
          {isScrolled && (
            <Box key={`${tag}-down-at`} position="absolute" bottom={0} right={0} flexDirection="row">
              <Text> </Text>
              <Button key={`${tag}-down`} plain dimColor={(from >= max && !isNewsBelow) || undefined} onPress={() => move(from + SCROLL_STEP)}>
                <Text color={isNewsBelow ? 'yellow' : undefined}>{GLYPH.down}</Text>
              </Button>
            </Box>
          )}
        </Box>
      )
    }

    // The sidebar is areas under the heading, each under a line of its own that carries its
    // name and its controls: the list, then the current item's file (or its thread, or the
    // inbox between rounds), then what belongs to the file. Every area but the file's has a
    // height it grows to and scrolls inside itself past; the file takes what is left, so the
    // sidebar fits its pane and never scrolls as a whole.
    const H = isDocked ? rowsAll : 24
    // The list: up to a quarter of the height, past that a window that keeps the current
    // item in view until the person scrolls it.
    const listCap = Math.max(3, Math.floor(H / 4))
    const isFolded = await read($, folded)
    const sections = phase === 'own' ? [] : sectionsOf(r)
    const listCount = phase === 'own'
      ? Math.max(1, (r.own_threads ?? []).length)
      : sections.reduce((n, g) => n + 1 + ((isFolded[g.key] ?? g.key === 'closed') ? 0 : g.rows.length), 0)
    // A list that scrolls leaves its arrows their two cells at the right.
    const listReserve = listCount > listCap ? ARROW : 0

    // The list, as rows of one line each.
    const listRows: JSX.Element[] = []
    let currentRow = 0
    // The rows with news, so a count of news always has its marks in view: on the row, on a
    // folded group's header, or on the arrow past which a scrolled list holds them.
    const newsRows: number[] = []
    const judged = await read($, judging)
    const shownMarks = new Set<string>()
    if (phase === 'own') {
      const threads = r.own_threads ?? []
      const whereWidth = Math.min(28, Math.max(4, ...threads.map(x => x.where.length)))
      for (const x of threads) {
        const isCurrent = x.id === own?.id
        if (isCurrent) currentRow = listRows.length
        const base = { bold: isCurrent || undefined, dimColor: !isCurrent || undefined }
        // What became of it: on a topic, or proposed for one; nothing while it waits.
        const fate = x.topic ? ` on ${x.topic}` : x.proposal ? ` → ${x.proposal}?` : ''
        const room = Math.max(8, cols - 4 - whereWidth - fate.length - listReserve)
        listRows.push(
          <Box key={`own-${x.id}`} flexDirection="row">
            <Text color={ACCENT}>{isCurrent ? '▌' : ' '}</Text>
            <Button key={`own-${x.id}`} plain onPress={() => pickOwn($, s, x.id)}>
              <Text {...base}>{`${STATE_ICON[x.topic ? 'done' : 'new']} `}</Text>
              <Text {...base} color={ACCENT}>{x.where.slice(0, whereWidth).padEnd(whereWidth)}</Text>
              <Text {...base}>{` ${fitTitle(x.text.replace(/\s+/g, ' '), room).padEnd(room)}`}</Text>
              <Text {...base} dimColor>{fate}</Text>
            </Button>
          </Box>,
        )
      }
      if (threads.length === 0) {
        listRows.push(<Text key="own-none" dimColor>Your comments on GitLab show here as you post them.</Text>)
      }
    } else {
      // The fixed slots, one space between each: the bar, state icon, handle (padded to the
      // longest), severity or kind, and the source at the end. The title takes the rest, cut
      // to it, so a row is always one line and the source column lines up.
      const idWidth = Math.max(2, ...r.topics.map(t => t.id.length))
      // review-mr: bar, mark, handle, severity; rework-mr: bar, mark, state, handle.
      const room = Math.max(12, cols - 6 - idWidth - listReserve)
      const row = (x: Row) => {
        // Where the topic stands in the inbox (news, being judged, put off), in a slot of
        // its own after the bar: minimal, but in colour where it needs the person.
        const topic = r.topics.find(t => t.id === x.id)
        const mark = topic ? inboxMark(topic, judged.failed, judged.agentId ? judged.topics : []) : undefined
        if (mark) shownMarks.add(mark.key)
        // The current row: a bar in the accent colour in its first slot, and bold; every
        // other row dimmed a little. Set on every part, so no part's own colour resets it.
        const base = { bold: x.isCurrent || undefined, dimColor: !x.isCurrent || undefined }
        // The bar stands outside the row's button: under the pointer the button inverts,
        // and a bar inside it would turn into a block on every row the pointer crosses.
        // The current row, pressed again, is closed where it is only being looked at.
        return (
          <Box key={`line-${x.id}`} flexDirection="row">
            <Text color={ACCENT}>{x.isCurrent ? '▌' : ' '}</Text>
            <Text color={mark?.color} dimColor={mark?.isDim || undefined}>{mark ? `${mark.icon} ` : '  '}</Text>
            {/* The severity stands outside the row's button, as the bar and the mark do:
                under the pointer the button inverts, and a coloured glyph inside it would
                turn into a block of its colour. Its colour is what the list is scanned by,
                so it keeps it on every open row (a dim colour is drawn plain grey). */}
            {x.kind ? <Text {...base} color={x.isClosed ? undefined : x.kind.color} dimColor={(x.isClosed && !x.isCurrent) || x.kind.isDim || undefined}>{`${x.kind.icon} `}</Text> : ''}
            <Button key={`row-${x.id}`} plain onPress={() => (x.isCurrent && isLeavable ? leave() : showTopic($, s, x.id))}>
              {!isReview && <Text {...base}>{`${x.icon} `}</Text>}
              <Text {...base} color={ACCENT}>{x.id.padEnd(idWidth)}</Text>
              {/* A dropped topic is struck through: closed, and not raised after all. */}
              <Text {...base} strikethrough={x.state === 'dropped' || undefined}>{` ${fitTitle(x.title, room)}`}</Text>
            </Button>
          </Box>
        )
      }
      // Each group under a header of one look, a click folding or unfolding it; no blank
      // line between groups, as the file below needs the rows. The chevron stands in the
      // bar's column, so a group's topics sit one column in from its name. The closed ones
      // start folded. A folded group says how many it holds, as its rows no longer do.
      for (const g of sections) {
        const isShut = isFolded[g.key] ?? g.key === 'closed'
        const hasNews = (x: Row) => r.topics.find(t => t.id === x.id)?.inbox === 'new'
        // Folded, a group with news says so on its header: its rows' marks are out of sight.
        const isHiding = isShut && g.rows.some(hasNews)
        if (isHiding) newsRows.push(listRows.length)
        listRows.push(
          <Box key={`group-${g.key}`} flexDirection="row">
            <Button key={`group-${g.key}`} plain dimColor label={isShut ? `${GLYPH.shut} ${g.title} (${g.rows.length})` : `${GLYPH.open} ${g.title}`}
              onPress={() => update($, folded, m => ({ ...m, [g.key]: !isShut }))} />
            {isHiding && <Text color="yellow">{` ${INBOX_ICON.new}`}</Text>}
          </Box>,
        )
        if (isShut) continue
        for (const x of g.rows) {
          if (x.isCurrent) currentRow = listRows.length
          if (hasNews(x)) newsRows.push(listRows.length)
          listRows.push(row(x))
        }
      }
    }

    // The current item's thread: the topic's (both skills), or the own comment's.
    const threads = (phase === 'own' ? own?.thread : r.thread) ?? []
    const notes = threads.flatMap(th => th.notes)
    const code = await codeFor($, r, own)
    const openKey = code?.key ?? (phase === 'own' ? `thread:${own?.id}` : `topic:${r.current}`)

    // A comment as the sidebar reads it: the author leads it, its link a glyph after the
    // name.
    const link = (n: ReviewNote) => (n.url ? `[${LINK_ICON}](${n.url}) ` : '')
    // A comment's own line, wherever it is shown in full: its link, who wrote it, and when.
    const noteHead = (n: ReviewNote) => noteHeadLine($, e, { url: n.url, author: n.author, when: whenOf(n.at) })
    // A thread with no file to sit under, in full, as an opened thread box draws it.
    // The first one carries the ✕ a file's heading would (`isClosable`), as it stands in its place.
    const fullNotes = (title: string, list: ReviewNote[], isClosable = false) =>
      list.length > 0 && (
        <Box key={`notes-${title}`} flexDirection="column" marginTop={1}>
          <Box flexDirection="row" justifyContent="space-between">
            <Text bold>{title}</Text>
            {isClosable && <Button key="leave" plain dimColor label={GLYPH.close} onPress={leave} />}
          </Box>
          {list.map((n, i) => (
            <Box key={`${title}-${i}`} flexDirection="column" marginTop={i ? 1 : 0}>
              {noteHead(n)}
              {drawParts($, e, reflow(n.body ?? ''), `${title}-${i}`)}
            </Box>
          ))}
        </Box>
      )
    // How tall a text draws, guessed from its length: Markdown's own wrapping is not known
    // here. A guess off by a few rows only moves where the file ends.
    const rowsOf = (text: string, width: number) =>
      text.split('\n').reduce((k, ln) => k + Math.max(1, Math.ceil(ln.length / Math.max(1, width))), 0)
    const fullRows = (list: ReviewNote[]) => 2 + list.reduce((m, x) => m + 2 + rowsOf(reflow(withEmoji(x.body ?? '')), cols), 0)

    // The thread under the code, in full (its code blocks and every word) until its fold key
    // folds it to one line per comment, with no blank line between them, and opens it again.
    // The key, an icon alone, stands at the end of the box's first line, left of the box's up
    // arrow where it scrolls. The box sits two columns in and has a border and a column of
    // padding on each side.
    // Open while the thread is what the person reads, so planning has the reviewer's words in
    // full; folded under the person's own fix, where the code is what they look at. Either
    // way as the person last left it on that view.
    const isOpen = (await read($, threadOpen))[openKey] ?? code?.view.mode !== 'fix'
    const inner = cols - 6
    // The first line leaves the fold key its two cells (a space and the icon).
    const short = notes.map((n, i) => oneLineNote(withEmoji(n.body ?? ''),
      Math.max(8, inner - (n.author ?? 'someone').length - (n.url ? 3 : 1) - (i === 0 ? FOLD_KEY : 0))))
    const isShortened = short.some(x => x.isCut)
    const boxText = notes.map(n => reflow(withEmoji(n.body ?? ''))).join('\n\n')
    // A topic with no thread yet stands in its own box under its lines, as one line: its
    // severity (or kind) and its title. On code the MR added, the lines' own colour hides
    // the background that marks them, so the box is what says where the topic is.
    const finding = phase === 'topics' && notes.length === 0 ? currentTopic(r) : undefined
    const findingRow = finding && (() => {
      const slot = kindSlot(finding)
      const title = oneLineNote(finding.summary ?? '', Math.max(8, inner - 2)).text
      return (
        <Box key="finding" flexDirection="row" gap={1}>
          {isReview && <Text color={slot.color} dimColor={slot.isDim || undefined}>{slot.icon}</Text>}
          <Markdown text={title} />
        </Box>
      )
    })()
    // Opened, a suggestion in a comment is a diff against the lines it replaces, counted from
    // the line the thread is on, in the file the sidebar holds.
    const fileLines = code ? code.view.rows.filter(row => row[2] !== null).map(row => row[3]) : null
    const anchor = code?.view.side === 'new' ? code.view.line : null
    // Opened, the box is rows of one line each (panel.ts's `boxRowsOf`): each comment's own
    // line for who wrote it and when, its text wrapped to the box, its code blocks folded.
    const opened = await read($, codeOpen)
    const ext = (code?.view.file ?? '').split('.').pop() ?? ''
    const rowsAt = (width: number): BoxRow[] => isOpen && !findingRow && notes.length > 0
      ? boxRowsOf(notes.map(n => withEmoji(n.body ?? '')), Math.max(8, width), (i, b) => `${openKey}:${i}:${b}`,
        k => opened[k] === true, blk => withSuggestions(blk, code?.view.file, anchor, fileLines), ext)
      : []
    let openRows = rowsAt(inner - 1)
    const drawBoxRows = (rows: BoxRow[], noteOf: (i: number) => ReviewNote = i => notes[i] as ReviewNote) => {
      const out: JSX.Element[] = []
      for (let i = 0; i < rows.length; i += 1) {
        const row = rows[i] as BoxRow
        if (row.kind === 'code') {
          // A block's lines in the window, drawn as one: a suggestion's as the diff it makes.
          let j = i
          while (j + 1 < rows.length && (rows[j + 1] as BoxRow).kind === 'code' && (rows[j + 1] as { key: string }).key === row.key) j += 1
          const run = rows.slice(i, j + 1) as Extract<BoxRow, { kind: 'code' }>[]
          const lines = run.map(x => x.line)
          const source = row.diff
            ? [`@@ -${row.diff.old},${lines.filter(l => !l.startsWith('+')).length} +${row.diff.new},${lines.filter(l => !l.startsWith('-')).length} @@`, ...lines].join('\n')
            : lines.join('\n')
          out.push(row.diff
            ? <Code key={`box-${i}`} source={source} path={code?.view.file} format="diff" wrap="truncate-end" />
            : <Code key={`box-${i}`} source={source} language={row.lang || undefined} wrap="truncate-end" />)
          i = j
        } else if (row.kind === 'head') {
          out.push(<Box key={`box-${i}`}>{noteHead(noteOf(row.note))}</Box>)
        } else if (row.kind === 'md') {
          out.push(<Markdown key={`box-${i}`} text={row.text} />)
        } else if (row.kind === 'fold') {
          const key = row.key
          out.push(<Button key={`fold-${key.split(':').slice(-2).join('-')}`} plain dimColor label={row.label}
            onPress={() => update($, codeOpen, m => ({ ...m, [key]: !row.isOpen }))} />)
        } else {
          out.push(<Text key={`box-${i}`}> </Text>)
        }
      }
      return out
    }
    const closedRows = notes.map((n, i) => (
        <Box key={`note-${i}`} flexDirection="row" gap={1}>
          {n.url && <Markdown text={link(n).trim()} />}
          {author(Text, n.author)}
          <Markdown text={short[i]?.text ?? ''} />
        </Box>
      ))

    // rework-mr: the comments on the MR itself, an area under the file's: one line each up
    // to five lines, and opened, in full up to a third of the height.
    const mrComments = isReview ? [] : (r.mr_comments ?? [])
    const isMrOpen = await read($, mrOpen)
    const filesCap = (isOpened: boolean) => (isOpened ? Math.max(FOLDED_ROWS, Math.floor(H / 3)) : FOLDED_ROWS)
    const mrCap = filesCap(isMrOpen)
    const mrShort = mrComments.map(n => oneLineNote(withEmoji(n.body ?? ''),
      Math.max(8, cols - (n.author ?? 'someone').length - (n.url ? 3 : 1) - BOX_ARROWS)))
    const mrFull = isMrOpen && mrComments.length > 0
      ? boxRowsOf(mrComments.map(n => withEmoji(n.body ?? '')), Math.max(8, cols - BOX_ARROWS), (i, b) => `mr:${i}:${b}`,
        k => opened[k] === true, blk => blk, '')
      : []
    const mrCount = isMrOpen ? mrFull.length : mrComments.length
    const mrShown = Math.min(mrCount, mrCap)
    const mrRows = mrComments.length === 0 ? 0 : 1 + mrShown
    // Folding offered only where it leaves something out: a comment cut, or more than fit.
    const isMrFoldable = mrComments.length > FOLDED_ROWS || mrShort.some(x => x.isCut)

    // The legend: what the marks in the list mean, only those it shows, one line per slot
    // (more where a narrow pane wraps one). One toggle, folded and open as a group's header
    // (▸ ▾), at the right of the row under the list; open, the box's top line is that row,
    // the toggle in it where it was, so the pointer opens and closes it without moving.
    const isLegendShown = await read($, legendShown)
    const markGroup: { icon: string; word: string; color?: string }[] = [...shownMarks].map(k => ({ icon: INBOX_ICON[k] as string, word: INBOX_WORD[k] as string, color: k === 'new' || k === 'unjudged' ? 'yellow' : undefined }))
    // The box's marks too, where it is drawn: what needs the person and what is being judged.
    // A line only to know of says what it is in words, beside an icon of its kind.
    const boxTones = new Set((!code && waitingOf(r) ? inboxOf(r)?.lines ?? [] : []).map(l => l.tone))
    const boxGroup = [
      ...(boxTones.has('needs') ? [{ icon: INBOX_ICON.new as string, word: 'needs you', color: 'yellow' }] : []),
      ...(boxTones.has('busy') ? [{ icon: INBOX_ICON.judging as string, word: 'being judged' }] : []),
    ]
    // The MR's own marks at the right of its title, which say nothing in words there.
    const mrMarks = mrMarksOf(r.mr)
    const mrGroup = mrMarks.map(k => ({ icon: INBOX_ICON[k] as string, word: MR_WORD[k] as string, color: MR_COLOR[k] }))
    const allGroups = [...legendFor(r, phase === 'own'), ...(markGroup.length ? [markGroup] : []), ...(boxGroup.length ? [boxGroup] : []),
      ...(mrGroup.length ? [mrGroup] : [])]
    const groups = isLegendShown ? allGroups : []
    // Each slot's items packed into lines of the box's inner width, two spaces apart.
    const legendWidth = Math.max(10, cols - 4)
    const legendLines = groups.flatMap(g => g.reduce<(typeof g)[]>((lines, item) => {
      const last = lines[lines.length - 1]
      const width = (xs: typeof g) => xs.reduce((w, x) => w + x.word.length + 2, 0) + 2 * Math.max(0, xs.length - 1)
      if (last && width([...last, item]) <= legendWidth) last.push(item)
      else lines.push([item])
      return lines
    }, []))
    const legendRows = groups.length === 0 ? 0 : 2 + legendLines.length

    // The other areas' heights first: the list's (with the legend, open), the fix's other
    // files', the comments' on the MR, and the foot's between rounds. The file's area takes
    // the rest, its line included.
    const listShown = Math.min(listRows.length, listCap)
    const listArea = 1 + listShown + legendRows
    const others = code?.view.mode === 'fix' ? code.view.others ?? [] : []
    const isFilesOpen = code ? (await read($, filesOpen))[code.key] === true : false
    const filesShown = Math.min(others.length, filesCap(isFilesOpen))
    const filesArea = others.length ? 1 + filesShown : 0
    const footRows = !code && waitingOf(r) ? 1 : 0
    const rest = Math.max(4, H - 1 - listArea - filesArea - mrRows - footRows)
    // The thread box's room: what leaves the lines it is on, and up to five lines of the file
    // above and below them (fewer where the file ends first). Taller, it scrolls on its own.
    const at = code?.view.at ?? null
    const boxCap = code && at && openRows.length > 0
      ? Math.max(3, rest - 1 - 1
        - Math.min(10, at[1] - at[0] + 1) - Math.min(5, at[0] - 1) - Math.min(5, code.view.rows.length - at[1]) - 2)
      : Infinity
    // A box that scrolls keeps its arrows at its right corners, as the file does, with the fold
    // key left of the up one: its text gives them the room on every line.
    if (openRows.length > boxCap) openRows = rowsAt(inner - 1 - BOX_ARROWS)
    const isBoxScrolled = openRows.length > boxCap
    const boxBody = isOpen && !findingRow ? Math.min(openRows.length, boxCap) : notes.length
    const boxFrom = Math.min(Math.max(0, openRows.length - boxBody), (await read($, boxAt))[openKey] ?? 0)
    const hasFoot = isShortened
    const boxRows = findingRow ? 3 : notes.length === 0 ? 0 : 2 + boxBody + (code?.view.mode === 'since' ? 1 : 0)
    // Folding offered only where it leaves something out.
    const foldKey = !findingRow && hasFoot && (
      <Button key="thread-open" plain dimColor label={isOpen ? GLYPH.less : GLYPH.more}
        onPress={() => update($, threadOpen, m => ({ ...m, [openKey]: !isOpen }))} />
    )
    // As the file's: the arrows a box that scrolls on its own moves by, besides the wheel
    // over it, the up one at the end of its first line and the down one at the end of its
    // last; one that cannot move further is dim.
    const boxUp = isBoxScrolled && (
      <Button key="box-up" plain dimColor={boxFrom === 0 || undefined} label={GLYPH.up}
        onPress={() => update($, boxAt, m => ({ ...m, [openKey]: Math.max(0, boxFrom - SCROLL_STEP) }))} />
    )
    const boxDown = isBoxScrolled && (
      <Box key="box-down-at" position="absolute" bottom={0} right={0} flexDirection="row">
        <Text> </Text>
        <Button key="box-down" plain dimColor={boxFrom >= openRows.length - boxBody || undefined} label={GLYPH.down}
          onPress={() => update($, boxAt, m => ({ ...m, [openKey]: Math.min(openRows.length - boxBody, boxFrom + SCROLL_STEP) }))} />
      </Box>
    )
    const shownRows = isOpen && !findingRow ? drawBoxRows(openRows.slice(boxFrom, boxFrom + boxBody)) : closedRows
    const firstKeys = (foldKey || boxUp) && (
      <Box key="box-keys" flexDirection="row" gap={1} flexShrink={0}>{foldKey}{boxUp}</Box>
    )
    const noteRows = shownRows.length > 0
      ? (
        <Box key="box-rows" flexDirection="column" position="relative">
          {firstKeys
            ? (
              <Box key="box-first" flexDirection="row" gap={1}>
                <Box flexGrow={1} flexShrink={1} flexDirection="column">{shownRows[0]}</Box>
                {firstKeys}
              </Box>
            )
            : shownRows[0]}
          {shownRows.slice(1)}
          {boxDown}
        </Box>
      )
      : null
    // The file's lines: the area's rest once its line and the thread box have theirs.
    const room = Math.max(3, rest - 1 - boxRows)

    let codePart: JSX.Element | null = null
    if (code) {
      const v = code.view
      const offsets = await read($, codeAt)
      // A thread whose lines are out of the window stays at the edge they went past, with a
      // line saying where they are, which takes a row of the file's.
      const isPinnedAt = (rows: number) => {
        const w0 = codeWindow(v, rows, offsets[code.key] ?? firstOffset(v, rows))
        return boxRows > 0 && v.at !== null && (v.at[1] < w0.start || v.at[0] > w0.end)
      }
      const isPinned = isPinnedAt(room)
      const codeRows = isPinned ? room - 1 : room
      const offset = offsets[code.key] ?? firstOffset(v, codeRows)
      const w = codeWindow(v, codeRows, offset)
      const move = (by: number) => update($, codeAt, m => ({ ...m, [code.key]: clampOffset(v, codeRows, w.start + by) }))
      const back = () => update($, codeAt, m => ({ ...m, [code.key]: firstOffset(v, room) }))
      const lines = v.line ? `${v.start && v.start !== v.line ? `lines ${v.start}–` : 'line '}${v.line}` : 'its lines'
      const isAbove = v.at !== null && v.at[1] < w.start
      const threadBox = boxRows > 0 && (
        <Box marginLeft={2} paddingX={1} borderStyle="round" borderDimColor flexDirection="column">
          {isPinned && (
            <Button key="thread-back" plain dimColor label={`${isAbove ? '↑' : '↓'} on ${lines}`} onPress={back} />
          )}
          {v.mode === 'since' && !findingRow && <Text dimColor>written on the code before the push; above, what changed since</Text>}
          {findingRow ?? noteRows}
        </Box>
      )
      // The runs of the window, the discussed lines on a background, the thread set in under
      // the last of them.
      const runs = w.runs.map(run => {
        const marks = w.changed.slice(run.from - w.start, run.to - w.start + 1)
        const body = (
          <Box key={`run-${run.from}`} flexDirection="row">
            <Box flexDirection="column">
              {marks.map((m, i) => <Text key={`mark-${run.from + i}`} color="green">{m ? '▎' : ' '}</Text>)}
            </Box>
            <Box flexGrow={1} backgroundColor={run.isDiscussed ? DISCUSSED_BG : undefined}>
              <Code source={run.source} path={v.file} format="diff" wrap="truncate-end" />
            </Box>
          </Box>
        )
        if (!(run.isDiscussed && threadBox)) return body
        return (
          <Box key={`run-${run.from}-with`} flexDirection="column">
            {body}
            {threadBox}
          </Box>
        )
      })
      if (isPinned && threadBox) {
        // A column, as the runs are: in a row the box would shrink to its text, narrower
        // than the same box under its lines.
        if (isAbove) runs.unshift(<Box key="thread-above" flexDirection="column">{threadBox}</Box>)
        else runs.push(<Box key="thread-below" flexDirection="column">{threadBox}</Box>)
      }
      // Where the file's lines sit in the sidebar's tree, for the wheel: the heading, the
      // list's area and the file's own line above them. Kept outside the mod's state, since a
      // drawing may not write it.
      const top = 1 + listArea + 1
      // The box's own rows, where it scrolls: right under the lines it is on, or at the edge
      // of the window they went past.
      const boxTop = isPinned ? (isAbove ? top : top + codeRows) : top + ((at?.[1] ?? w.start) - w.start + 1)
      codeAreas.set(sid, {
        top, bottom: top + codeRows + boxRows, key: code.key, rows: codeRows, start: w.start, view: v,
        ...(isBoxScrolled ? { box: { top: boxTop, bottom: boxTop + boxRows, key: openKey, max: openRows.length - boxBody } } : {}),
        lists,
      })
      // The keys stand at the right: the thread's, then the ones that move the file; one
      // that cannot move further is dim, and none shows where the whole file fits.
      const isTop = w.start <= 1
      const isBottom = w.end >= v.rows.length
      // What the line says of the file, past its name: a word each, and only where the code
      // would otherwise look unexpected (or is the person's own, not pushed yet).
      const where = `${v.file}${v.line ? `:${v.start && v.start !== v.line ? `${v.start}–` : ''}${v.line}` : ''}`
      const said: Seg[] = []
      if (v.whole === 'added') said.push({ text: 'new file', color: 'green' })
      if (v.whole === 'removed') said.push({ text: 'deleted file', color: 'red' })
      if (v.side === 'old' && v.at && !v.whole) said.push({ text: 'removed', isDim: true })
      if (v.moved) said.push({ text: 'moved', isDim: true })
      if (v.mode === 'since') said.push({ text: 'changed', color: 'green' })
      if (v.mode === 'fix') said.push({ text: 'unpushed', color: 'yellow' })
      if (r.inbox?.newer_push) said.push({ text: 'newer push', isDim: true })
      const badges = said.flatMap(x => [{ text: ' · ', isDim: true }, x])
      codePart = (
        <Box key="code" flexDirection="column">
          {rule('code-line', [{ text: where, color: ACCENT, isBold: true }, ...badges],
            isLeavable ? { el: <Button key="leave" plain dimColor label={GLYPH.close} onPress={leave} />, width: 1 } : undefined)}
          {/* The arrows that scroll the file stand at the content's own corners, the up one at
              the end of its first line and the down one at the end of its last, over those
              lines' last two cells (a space and the arrow). Shown only where the file does not
              fit; one that cannot move further is dim. */}
          <Box flexDirection="column" position="relative">
            {runs}
            {!(isTop && isBottom) && (
              <Box key="code-up-at" position="absolute" top={0} right={0} flexDirection="row">
                <Text> </Text>
                <Button key="code-up" plain dimColor={isTop || undefined} label={GLYPH.up} onPress={() => move(-SCROLL_STEP)} />
              </Box>
            )}
            {!(isTop && isBottom) && (
              <Box key="code-down-at" position="absolute" bottom={0} right={0} flexDirection="row">
                <Text> </Text>
                <Button key="code-down" plain dimColor={isBottom || undefined} label={GLYPH.down} onPress={() => move(SCROLL_STEP)} />
              </Box>
            )}
          </Box>
        </Box>
      )
    }
    // The rows the file's area takes, its line included: what the areas under it start after.
    const codeArea = code ? 1 + room + boxRows : rest
    // The fix's other files, an area under the file's: each with its size, in columns, folded
    // to five rows and opened to a third of the height. Reading them is the diff viewer's, so
    // its key, the card's own, is in the area's line.
    const filesTop = 1 + listArea + codeArea + 1
    const pathCut = (f: string, width: number) => (cells(f) <= width ? f : `…${[...f].slice(cells(f) - width + 1).join('')}`)
    let filesPart: JSX.Element | null = null
    if (code && others.length > 0) {
      const addedWidth = Math.max(...others.map(([, a]) => `+${a}`.length))
      const sizeWidth = addedWidth + 1 + Math.max(...others.map(([, , d]) => `−${d}`.length))
      // Whole paths where they fit: two of a fix's files often share a name (`query-keys.ts` per feature).
      const nameWidth = Math.max(8, Math.min(Math.max(...others.map(([f]) => cells(f))), cols - 2 - 2 - sizeWidth - BOX_ARROWS))
      const fileRows = others.map(([f, a, d]) => (
        <Text key={`other-${f}`} wrap="truncate-end">
          {`  ${pathCut(f, nameWidth).padEnd(nameWidth)}  `}
          <Text color="green">{`+${a}`.padEnd(addedWidth)}</Text>
          {' '}
          <Text color="red">{`−${d}`}</Text>
        </Text>
      ))
      const fold = others.length > FOLDED_ROWS && (
        <Button key="files-open" plain dimColor label={isFilesOpen ? GLYPH.less : GLYPH.more}
          onPress={() => update($, filesOpen, m => ({ ...m, [code.key]: !isFilesOpen }))} />
      )
      const { buttons, press } = await stepControls($, s, r, await read($, working))
      const hunk = buttons.find(b => b.key === 'hunk' || b.key === 'hunk-notes')
      const hunkLabel = hunk ? `${iconOf(hunk.label) ?? ''} ${hunk.label}`.trim() : ''
      filesPart = (
        <Box key="files" flexDirection="column">
          {rule('files-line', [{ text: 'Also changed', isBold: true }, { text: ` · ${others.length}`, isDim: true }],
            hunk ? { el: <Button key="files-hunk" plain label={hunkLabel} onPress={() => { void press(hunk)({ surface: 'terminal' }) }} />, width: cells(hunkLabel) } : undefined)}
          {areaRows(`files:${code.key}`, fileRows, filesShown, filesTop, 0, null, fold)}
        </Box>
      )
    }
    // The comments on the MR itself, under the file's area and the fix's files: one line each,
    // and opened, in full, as a thread box draws them.
    const mrTop = filesTop + filesArea
    const mrLines = isMrOpen
      ? { count: mrFull.length, draw: (from: number, n: number) => drawBoxRows(mrFull.slice(from, from + n), i => mrComments[i] as ReviewNote) }
      : mrComments.map((n, i) => (
        <Box key={`mr-note-${i}`} flexDirection="row" gap={1}>
          {n.url && <Markdown text={link(n).trim()} />}
          {author(Text, n.author)}
          <Markdown text={mrShort[i]?.text ?? ''} />
        </Box>
      ))
    const mrPart = mrComments.length > 0 && (
      <Box key="mr-comments" flexDirection="column">
        {rule('mr-line', [{ text: 'On the MR', isBold: true }, { text: ` · ${mrComments.length}`, isDim: true }])}
        {areaRows('mr', mrLines, mrShown, mrTop, 0, null, isMrFoldable && (
          <Button key="mr-open" plain dimColor label={isMrOpen ? GLYPH.less : GLYPH.more} onPress={() => update($, mrOpen, x => !x)} />
        ))}
      </Box>
    )
    // The legend: its key in the list's line, and opened, a box under the list saying what
    // every mark on screen means.
    const legendBox = isLegendShown && legendLines.length > 0 && (
      <Box key="legend-box" borderStyle="round" borderDimColor flexDirection="column" alignItems="center" flexShrink={0}>
        {legendLines.map((line, n) => (
          <Text key={`legend-${n}`} dimColor>
            {line.map((item, k) => (
              <Text key={`legend-${n}-${item.word}`}>
                {/* Plain: the list colours an icon only on an open topic, so colour here
                    would teach a meaning the icon does not always carry. */}
                {k ? '  ' : ''}<Text>{item.icon}</Text>{` ${item.word}`}
              </Text>
            ))}
          </Text>
        ))}
      </Box>
    )
    // The MR's icon, its number as a link to it on GitLab, and its title: the same in every
    // phase, so it always says which MR this is.
    // During a round, what arrived meanwhile is counted after it, in the colour of news.
    const fresh = r.current ? r.topics.filter(t => t.inbox === 'new' && t.id !== r.current).length : 0
    // Where the MR stands as a whole, at the right of its title, which gives way first:
    // merged or closed, auto-merge set (a warning while topics are open), who approved it.
    const mr = r.mr
    const isOver = mr?.state === 'merged' || mr?.state === 'closed'
    // Icons only, each in its kind's colour, right after the title; the legend says what they
    // stand for. A merged MR's icon is the heading's first. The whole line is centred over the
    // areas under it.
    const marks = mrMarks
    // Centred by a margin of whole cells worked out here: centred by the layout, an odd
    // leftover puts the line on half a cell, each part is rounded on its own, and the gap
    // after the icon comes out two cells wide. A line too long for the pane is not centred;
    // its title gives way.
    const headWidth = 1 + 1 + cells(`!${r.iid}`) + (r.title ? 1 + cells(plainTitle(r.title)) : 0)
      + (fresh > 0 ? 1 + cells(`· ${fresh} new`) : 0) + (marks.length > 0 ? 2 + 2 * marks.length : 0)
    const headIndent = Math.max(0, Math.floor((cols - headWidth) / 2))
    const heading = (
      <Box flexDirection="row" gap={1} flexShrink={1} paddingLeft={headIndent}>
        {/* GitLab's mark: a merged MR's icon is the merge one. */}
        <Text bold color={mr?.state === 'merged' ? MR_COLOR.merged : undefined}>{mr?.state === 'merged' ? INBOX_ICON.merged : MR_ICON}</Text>
        {/* The number never gives way; the title does. */}
        <Box flexShrink={0}>{r.url ? <Markdown text={`**[!${r.iid}](${r.url})**`} /> : <Text bold>{`!${r.iid}`}</Text>}</Box>
        {r.title && <Box flexShrink={1}><Text bold wrap="truncate-end">{plainTitle(r.title)}</Text></Box>}
        {fresh > 0 && <Box flexShrink={0}><Text dimColor>· </Text><Text color="yellow">{`${fresh} new`}</Text></Box>}
        {/* A space after the last: Ghostty draws a Nerd Font icon two cells wide where a space
            follows it, one where nothing does, so at a line's end it came out smaller. */}
        {marks.length > 0 && <Box flexShrink={0} gap={1}>{marks.map(k => <Text key={`mark-${k}`} color={MR_COLOR[k]}>{INBOX_ICON[k]}</Text>)}<Text> </Text></Box>}
      </Box>
    )
    // An item with no file (a comment on the MR as a whole): its thread in full in the file's
    // place, as a thread box draws it, scrolling inside its area where it is taller.
    const freeRows = !code && notes.length > 0
      ? boxRowsOf(notes.map(n => withEmoji(n.body ?? '')), Math.max(8, cols - BOX_ARROWS), (i, b) => `${openKey}:${i}:${b}`,
        k => opened[k] === true, blk => blk, '')
      : []
    const threadPart = freeRows.length > 0 && (
      <Box key="thread" flexDirection="column">
        {rule('thread-line', [{ text: 'Thread', isBold: true }],
          isLeavable ? { el: <Button key="leave" plain dimColor label={GLYPH.close} onPress={leave} />, width: 1 } : undefined)}
        {areaRows(`thread:${openKey}`, { count: freeRows.length, draw: (from, n) => drawBoxRows(freeRows.slice(from, from + n)) },
          Math.max(1, rest - 1), 1 + listArea + 1, 0, null)}
      </Box>
    )
    // Nothing left to decide: in the file's place, a box in the middle of what is left. With
    // news it is the inbox, what arrived since by kind and the key that opens a round on it;
    // without, it says so. Both say when GitLab was last asked and when next, and have a key
    // to ask now, for when the person knows the other side just answered.
    const waiting = !code ? waitingOf(r) : undefined
    const judgedBox = await read($, judging)
    const box = waiting ? inboxOf(r, { topics: judgedBox.agentId ? r.judge?.topics ?? [] : [], failed: judgedBox.failed }) : undefined
    const when = await read($, checks)
    const busyCheck = (await read($, busy)) === CHECKING || (await read($, checks)).running === true
    const clock = (ms: number | null | undefined) => (ms ? new Date(ms).toTimeString().slice(0, 5) : '–')
    // Read only where the box says it: a drawing with no box needs no clock.
    const now = waiting && !isOver ? await $.clock.now() : 0
    // Only what the person may act on: when GitLab is asked next (none is scheduled before
    // the first tick), or that the last ask failed. When it last answered tells nothing.
    // A check that is due is about to run: no "next check now", which says nothing. One that
    // runs says so, whether the person asked for it or its time came.
    const due = when.next ? untilOf(when.next, now) : null
    const next = due === 'now' ? null : due
    const checkText = busyCheck ? ['checking GitLab…']
      : when.failed
        ? [`check failed ${clock(when.failed)}`, next && `retry ${next}`]
        : [next && `next check ${next}`]
    const checkWords = checkText.filter(Boolean).join(' · ')
    const checkLine = checkWords && (when.failed
      ? <Text color="yellow">{checkWords}</Text>
      : <Text dimColor>{checkWords}</Text>)
    const opener = box?.first ?? (r.inbox?.own.length ? 'yours' : null)
    const openerLabel = isOver ? 'Check the answers' : box?.button ?? (opener === 'yours' ? 'Open your comment' : 'Start the round')
    // When GitLab is asked next, and a small press to ask it now, at the sidebar's foot while
    // the box is drawn: rarely pressed, so out of the box and out of the way. Merged or
    // closed, GitLab is not asked again: no word of checks, no press for one.
    const syncPart = waiting && (
      <Box key="foot" flexDirection="column">
        {/* One column off each edge of the sidebar, so the chips do not touch its borders. */}
        <Box flexDirection="row" alignItems="center" gap={2} paddingX={1}>
          {!isOver && !busyCheck && drawChip({ Box, Button, Text }, { key: 'check-now', label: 'sync', press: { kind: 'sync' } }, () => {
            void (async () => {
              await update($, busy, () => CHECKING)
              try { await checkNow($, s) } finally { await update($, busy, () => null) }
            })()
          })}
          {!isOver && checkLine}
        </Box>
      </Box>
    )
    const waitPart = waiting && (
      <Box key="waiting" flexGrow={1} flexDirection="column" justifyContent="center" alignItems="center">
        <Box borderStyle="round" borderDimColor paddingX={2} flexDirection="column" alignItems="center">
          <Text bold>
            {r.mr?.state === 'merged' || r.mr?.state === 'closed'
              ? `${r.mr.state === 'merged' ? 'Merged' : 'Closed'}${(r.mr.merged_at ?? r.mr.closed_at) ? ` ${whenOf(r.mr.merged_at ?? r.mr.closed_at)}` : ''}${(r.mr.merged_by ?? r.mr.closed_by) ? ` by ${r.mr.merged_by ?? r.mr.closed_by}` : ''}`
              : box?.hasNews
                ? (clockOf(box.since) ? `Since ${clockOf(box.since)}` : 'News')
                // The line under it says what waits, and on whom: the title does not say it again.
                : waiting.count > 0 ? 'Done for now' : 'Done'}
          </Text>
          {/* One line a topic: who did what, and the agent's verdict where it judged. The mark
              says whether it needs the person (yellow), is being judged, or is only to know of
              (dim); a push carries its size and links to what it changed. */}
          {(box?.lines.length ?? 0) > 0 && (
            <Box flexDirection="column" marginTop={1}>
              {box?.lines.map((l, i) => (
                <Box key={`inbox-${i}`} flexDirection="row" gap={1}>
                  {/* What needs the person in yellow; what is only to know of by its kind's
                      icon, plain and dim: it reads with the words beside it, no legend needed. */}
                  <Text color={l.tone === 'needs' ? 'yellow' : undefined} dimColor={l.tone !== 'needs' || undefined}>
                    {l.tone === 'needs' ? INBOX_ICON.new : l.tone === 'busy' ? INBOX_ICON.judging : (l.kind && INBOX_ICON[l.kind]) || '·'}
                  </Text>
                  {l.topic && <Text color={ACCENT} dimColor={l.tone === 'info' || undefined}>{l.topic}</Text>}
                  {/* A topic's line links to its thread on GitLab; a push's, to what it changed. */}
                  {l.url
                    ? <Markdown text={`[${l.what}](${l.url})`} dimColor={l.tone !== 'needs' || undefined} />
                    : <Text dimColor={l.tone !== 'needs' || undefined} wrap="wrap">{l.what}</Text>}
                  {l.size && sizeOf(l.size) && <Text>{drawSize(Text, (sizeOf(l.size) as { size: DiffSize }).size, true)}</Text>}
                </Box>
              ))}
              {(box?.more ?? 0) > 0 && <Text dimColor>{`+${box?.more} more, in the list above`}</Text>}
            </Box>
          )}
          {/* What waits, and on whom; merged or closed, the title and the lines say it all,
              as they do where nothing waits and the lines say what arrived. */}
          {!r.merged && (waiting.count > 0 || !box?.hasNews) && (
            <Box marginTop={1}>
              <Text dimColor>
                {waiting.count > 0
                  ? `${waiting.count} topic${waiting.count === 1 ? '' : 's'} wait${waiting.count === 1 ? 's' : ''} on ${waiting.who}`
                  : 'every topic is settled'}
              </Text>
            </Box>
          )}
          {/* The way into the topics, as a step's button is: named for what it starts. */}
          {opener && (
            <Box flexDirection="row" justifyContent="center" marginTop={1}>
              {drawChip({ Box, Button, Text }, { key: 'open-round', label: openerLabel, press: { kind: 'next' }, isPrimary: true },
                () => { void (async () => { if (!(await isHeld($, r, opener === 'yours' ? undefined : opener))) await openRound($, s) })() })}
            </Box>
          )}
        </Box>
      </Box>
    )
    // The list's area: its line with the legend's key, the list, and the legend opened.
    const focusId = phase === 'own' ? own?.id ?? null : r.current
    const listPart = (
      <Box key="list" flexDirection="column">
        {rule('list-line', [{ text: phase === 'own' ? 'Your comments' : 'Topics', isBold: true }],
          allGroups.length > 0 ? { el: <Button key="legend" plain dimColor label={GLYPH.legend} onPress={() => update($, legendShown, x => !x)} />, width: 1 } : undefined)}
        {areaRows('list', listRows, listCap, 2, currentRow - Math.floor(listCap / 2), focusId, false, newsRows)}
        {legendBox}
      </Box>
    )
    // Areas that scroll with no file in the sidebar still need the wheel.
    if (!code) codeAreas.set(sid, { top: -1, bottom: -1, key: '', rows: 0, start: 0, lists })
    return (
      <Box flexDirection="column" justifyContent="space-between" minHeight={isDocked ? rowsAll : undefined}>
        <Box flexDirection="column" flexGrow={waitPart ? 1 : undefined}>
          {heading}
          {listPart}
          {threadPart}
          {codePart}
          {filesPart}
          {mrPart}
          {waitPart && rule('inbox-line', [])}
          {waitPart}
        </Box>
        {syncPart}
      </Box>
    )
  })

  // The buttons for the current topic, above the prompt.
  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.props.hasSurvey) return next(e)
    const { s, r } = await known($)
    if (!s || !r) return next(e)
    const { Box, Button, Text } = $.ui.resolve(e)
    // Whether the panel is up is the engine's answer, never a value kept here: a pane can be
    // closed, or open and not shown, in ways no event of this mod hears about, and a toggle
    // trusting its own note then says "Hide" over nothing and does nothing when pressed.
    // The note is still written, so a change redraws the band.
    await read($, panel)
    const isShown = await panelShown($)
    // Only while the sidebar is closed: open, it closes by its own ✕, and the band has no
    // other way back to it.
    const toggle = !isShown && drawChip({ Box, Button, Text }, { key: 'topics', label: 'Show sidebar', press: { kind: 'sync' } },
      () => {
        void (async () => {
          await openSidebar($, s, true)
          await update($, sidebar, v => ({ ...v, isClosedByPerson: false }))
        })()
      })
    // The sidebar opens by itself the first time it has something to show, where it docks
    // beside the chat (fullscreen, from 110 columns); never again once the person closed it.
    const sb = await read($, sidebar)
    const docks = e.viewport?.isFullscreen === true && (e.viewport?.columns ?? 0) >= 110
    const sid = await $.session.id()
    if (!isShown && docks && !autoOpened.has(sid) && !sb.isClosedByPerson && hasSidebar(r)) {
      autoOpened.add(sid)
      void $.ui.open({ id: PANE, title: paneTitle(s) })
    }
    const topic = currentTopic(r)
    // Which topic the buttons act on; where it sits among the others is the panel's to say.
    // What the last press did, under the buttons. A success is said only while its topic is
    // current: once the next topic's card is up, that card is what says the press went
    // through, and the line only repeats what the chat above already told. A failure stays.
    const heard = await read($, notice)
    const said = heard && ((heard.isError && !heard.isWarning) || heard.topic === undefined || heard.topic === r.current) ? heard : null
    const doing = await read($, busy)


    const { buttons, press, isHosted } = await stepControls($, s, r, e.props.isWorking)
    // Between rounds the sidebar's inbox says what arrived and what waits. With the sidebar
    // open the band has nothing to add, short of a failure; closed, one line says it, and the
    // way to see more.
    const waiting = !doing && !e.props.isWorking ? waitingOf(r) : undefined
    const box = waiting ? inboxOf(r) : undefined
    const failed = said?.isError || said?.isWarning ? said : null
    if (waiting && !failed && isShown) return next(e)
    // The current topic's news still being judged in the background, or not judged at all.
    const judgedNow = await read($, judging)
    const isJudging = !!topic && (r.judge?.topics ?? []).includes(topic.id)
    const isUnjudged = isJudging && judgedNow.failed.includes(topic?.id ?? '')
    // One column off each edge, as the sidebar's foot keeps its chips and a card its content:
    // a chip against the screen's edge reads as cut off.
    return (
      <Box flexDirection="column" paddingX={1}>
        {waiting && (box?.count
          ? <Text color="yellow">{`${box.count} topic${box.count === 1 ? ' has' : 's have'} news`}</Text>
          : <Text dimColor>{waitingLine(waiting)}</Text>)}
        {isJudging && !e.props.isWorking && (
          <Text dimColor>{isUnjudged ? `${topic?.id}: the news could not be judged in the background` : `${topic?.id}: judging the news…`}</Text>
        )}
        {/* Wraps onto a second line where the band is narrower than its buttons. */}
        <Box flexDirection="row" width="100%">
          <Box flexDirection="row" flexWrap="wrap" columnGap={2} flexGrow={1}>
            {!isHosted && buttons.map(b => (b.key === 'next' ? null : drawChip({ Box, Button, Text }, b, press(b))))}
            {doing && <Text dimColor>{doing}…</Text>}
          </Box>
          {/* The way on stands apart from the decisions, at the right, as on a card. */}
          <Box flexDirection="row" columnGap={2} flexShrink={0}>
            {!isHosted && buttons.map(b => (b.key === 'next' ? drawChip({ Box, Button, Text }, b, press(b)) : null))}
            {toggle}
          </Box>
        </Box>
        {said && !doing && (!waiting || failed) && (said.url
          ? outcomeLine($, e, { text: said.text, url: said.url, label: said.label })
          : <Text color={said.isWarning ? 'yellow' : said.isError ? 'red' : undefined} dimColor={!said.isError && !said.isWarning}>{said.text}</Text>
        )}
      </Box>
    )
  })
}
