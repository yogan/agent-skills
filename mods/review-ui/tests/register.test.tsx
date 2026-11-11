import type { On } from 'claude-code'
import type { Engine } from 'claude-code/testing'
import { expect, test } from 'claude-code/testing'

import { boxRowsOf, plainTitle, reflow, rowOf, sectionsOf, urlLabel } from '../hooks/panel.ts'
import { withEmoji } from '../hooks/views.ts'
import { blocksOf, isPostCommand, NOTE, partsOf } from '../hooks/views.ts'

test('only a command that runs post or resolve is the person\'s press; one that names it is not', () => {
  for (const ran of [
    'python3 $SD/findings.py post t1 --iid 4',
    'cd /wt && python3 "$SD/threads.py" resolve t2',
    'S=$SD/threads.py && python3 $S post t3',
  ]) expect(isPostCommand(ran)).toBe(true)
  for (const named of [
    "cat >> notes.md <<'EOF'\n- the guard blocks heredocs naming threads.py post\nEOF",
    'git commit -m "findings.py post refuses a changed draft"',
    'echo hi # findings.py post',
    'grep -n "threads.py resolve" SKILL.md',
  ]) expect(isPostCommand(named)).toBe(false)
})

// An invented view, shaped like the scripts' output: a table, an excerpt, a thread, a
// suggestion, and the gate's trailing manifest.
const VIEW = [
  '**MR !42** · Add a widget cache',
  '',
  '| State | Topic | Summary |',
  '|---|---|---|',
  '| draft | t1 | Cache key ignores locale |',
  '',
  '◈ **t1** `src/cache.ts:17`',
  '```ts',
  '  const key = id   // ►',
  '```',
  '> **reviewer:** the key should include the locale',
  '',
  '```suggestion',
  'const key = `${id}:${locale}`',
  '```',
].join('\n')

const manifest = (fields: object) =>
  `\n\n<!-- paste-gate:critical\n${JSON.stringify({ first: '**MR !42** · Add a widget cache', critical: [], ...fields })}\n-->`

// A view's opening line, as the scripts name it in the manifest.
const firstOf = (view: string) => view.split('\n').find(l => l.trim())?.trim()

const SURFACES = ['terminal', 'desktop', 'vscode', 'mobile'] as const

const row = (command: string, stdout: string, extra = {}) => ({
  plugin: 'review-ui',
  component: 'ToolUse',
  props: {
    tool_use_id: 'tu1',
    tool: 'Bash',
    input: { command },
    isRunning: false,
    isErrored: false,
    isInterrupted: false,
    output: { stdout, stderr: '', interrupted: false },
    ...extra,
  },
}) as const

// The engine's own row, standing beneath the mod, and the person's prompts.
const engineRow = (on: On) => {
  on('ui.render', ($, e) => {
    const { Text } = $.ui.resolve(e)
    return <Text>engine row</Text>
  })
  on('prompt.submit', ($, e) => ({ text: e.text }))
}

// The review skill in use, as the person typing it puts it: the mod draws for nothing else.
const reviewing = ($: Engine) => $.prompt.submit({ text: '/review-mr !42', command: { name: 'review-mr', args: '!42' } } as never)

test('a view is drawn as markdown in place of its row, marker gone, on every surface', async ($, on) => {
  engineRow(on)
  await reviewing($)
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({
      ...row('cd /repo && SD=x python3 $SD/findings.py present --iid 42', VIEW + manifest({})),
      surface,
    })
    const md = { text: (await ui.findAll({ type: 'Markdown' })).map(m => m.text).join('\n') }
    expect(md.text).toContain('| draft | t1 |')
    expect(md.text).toContain('```suggestion')
    expect(md.text).not.toContain('paste-gate')
    expect(await ui.find({ type: 'Text', text: /engine row/ })).toBeUndefined()
    await ui.unmount()
  }
})

test('every view command of both skills is drawn', async ($, on) => {
  engineRow(on)
  await reviewing($)
  for (const command of [
    'python3 $SD/findings.py resume --iid 1', 'python3 $SD/findings.py todo --iid 1',
    'python3 $SD/findings.py quote t1 --iid 1', 'python3 $SD/findings.py updates --iid 1',
    'python3 $SD/findings.py set t1 --draft "x" --iid 1', 'python3 $SD/findings.py diff t1 --show',
    'python3 $SD/threads.py present --iid 3', 'python3 $SD/threads.py todo --iid 3',
    'python3 $SD/threads.py quote t2', 'python3 $SD/threads.py reply-view t2 --iid 3',
    'python3 $SD/threads.py change-view t1', '$SD/diff-view.sh t2', 'sh $SD/change-preview.sh t1',
    'python3 "$SD/threads.py" present', "python3 '$SD/findings.py' quote t1", '"$SD/diff-view.sh" t2',
    'S=~/.claude/skills/rework-mr/scripts/threads.py && python3 $S propose t1 --why x && python3 $S quote t1 --focus',
    'T="$SD/threads.py"; python3 "${T}" present',
    'python3 $SD/threads.py posted t2 --differs "x" --fix - --iid 3', 'python3 $SD/findings.py posted t1 --differs "x" --fix -',
  ]) {
    const ui = await $.ui.mount({ ...row(command, VIEW + manifest({})), surface: 'terminal' })
    expect(await ui.find({ type: 'Markdown' })).toBeDefined()
    await ui.unmount()
  }
})

test('a review skill typed with its plugin\'s name in front is the same skill', async ($, on) => {
  engineRow(on)
  await $.prompt.submit({ text: '/agent-skills:rework-mr !3', command: { name: 'agent-skills:rework-mr', args: '!3' } } as never)
  const ui = await $.ui.mount({ ...row('python3 $SD/threads.py present --iid 3', VIEW + manifest({})), surface: 'terminal' })
  expect(await ui.find({ type: 'Markdown' })).toBeDefined()
  await ui.unmount()
})

test('other commands keep the engine row alone', async ($, on) => {
  engineRow(on)
  await reviewing($)
  for (const command of [
    'python3 findings.py sync', 'ls -la', 'python3 findings.py diff t1',
    "cat > t.tsx <<'X'\n  row('findings.py quote t1', VIEW)\nX",
    'echo done # findings.py present',
    "grep -n 'findings.py present' SKILL.md",
    'python3 drive.py verify --prompt "findings.py present"',
  ]) {
    const ui = await $.ui.mount({ ...row(command, VIEW + manifest({})), surface: 'terminal' })
    expect(await ui.find({ type: 'Markdown' })).toBeUndefined()
    await ui.unmount()
  }
})

test('a view cut at the top, its first line gone, is research and not a card', async ($, on) => {
  engineRow(on)
  await reviewing($)
  const cut = VIEW.split('\n').slice(-3).join('\n') + manifest({})
  const ui = await $.ui.mount({ ...row('python3 $SD/threads.py quote t1 --focus 2>&1 | tail -5', cut), surface: 'terminal' })
  expect(await ui.find({ type: 'Markdown' })).toBeUndefined()
  await ui.unmount()
})

test('a running, failed or empty call is not drawn', async ($, on) => {
  engineRow(on)
  await reviewing($)
  for (const extra of [
    { isRunning: true, output: undefined },
    { isErrored: true },
    { output: { stdout: '  \n', stderr: '', interrupted: false } },
  ]) {
    const ui = await $.ui.mount({ ...row('python3 findings.py present', VIEW + manifest({}), extra), surface: 'terminal' })
    expect(await ui.find({ type: 'Markdown' })).toBeUndefined()
    await ui.unmount()
  }
})

test('a view of a topic that is not the current one is research, not a card', async ($, on) => {
  engineRow(on)
  await reviewing($)
  const cmd = 'python3 $SD/findings.py quote t3 --iid 42'
  let ui = await $.ui.mount({ ...row(cmd, VIEW + manifest({ topic: 't3', focus: 't1' })), surface: 'terminal' })
  expect(await ui.find({ type: 'Markdown' })).toBeUndefined()
  await ui.unmount()
  // The current topic, and a view that cannot know what is current, are drawn.
  for (const fields of [{ topic: 't1', focus: 't1' }, { topic: 't3' }]) {
    ui = await $.ui.mount({ ...row(cmd, VIEW + manifest(fields)), surface: 'terminal' })
    expect(await ui.find({ type: 'Markdown' })).toBeDefined()
    await ui.unmount()
  }
})

test('output ahead of the view (a linter chained in front) is left out', async ($, on) => {
  engineRow(on)
  await reviewing($)
  const ui = await $.ui.mount({
    ...row('npm run lint && python3 $SD/findings.py present', 'lint: 0 problems\n\n' + VIEW + manifest({})),
    surface: 'terminal',
  })
  const md = await ui.find({ type: 'Markdown' })
  expect(md?.text?.startsWith('**MR !42**')).toBe(true)
  await ui.unmount()
})

test('the card stands in place of the row: no command, no raw output', async ($, on) => {
  on('prompt.submit', ($, e) => ({ text: e.text }))
  let drawn = false
  on('ui.render', ($, e) => {
    drawn = true
    const { Text } = $.ui.resolve(e)
    return <Text>engine row</Text>
  })
  await reviewing($)
  const ui = await $.ui.mount({ ...row('python3 findings.py quote t1', VIEW + manifest({})), surface: 'terminal' })
  expect(drawn).toBe(false)
  expect(await ui.find({ type: 'Markdown' })).toBeDefined()
  await ui.unmount()
})

test('a group holding a view unfolds, one holding none stays folded, research is left out', async ($, on) => {
  on('prompt.submit', ($, e) => ({ text: e.text }))
  const seen: boolean[] = []
  on('ui.render', ($, e) => {
    seen.push((e.props as { isExpanded: boolean }).isExpanded)
    const { Text } = $.ui.resolve(e)
    return <Text>group</Text>
  })
  const call = (command: string, stdout: string) => ({
    tool: 'Bash', input: { command }, isRunning: false, isErrored: false, isInterrupted: false,
    output: { stdout, stderr: '', interrupted: false },
  })
  for (const c of [
    call('python3 findings.py present', VIEW + manifest({})),
    call('ls', 'a\nb'),
    call('python3 findings.py quote t3', VIEW + manifest({ topic: 't3', focus: 't1' })),
  ]) {
    await reviewing($)
    const ui = await $.ui.mount({
      plugin: 'review-ui', component: 'ToolGroup', surface: 'terminal',
      props: { calls: [c], isActive: false, isExpanded: false },
    })
    await ui.unmount()
  }
  expect(seen).toEqual([true, false])     // the research group drew nothing at all
})

test('the model reads a note after a view it must not paste; nothing else gets one', async ($, on) => {
  on('prompt.submit', ($, e) => ({ text: e.text }))
  let stdout = ''
  on('tool.call', () => ({ result: { stdout, stderr: '', interrupted: false } }))
  const run = async (command: string, out: string) => {
    stdout = out
    await reviewing($)
    const ran = await $.tool.call({ tool: 'Bash', command })
    return (ran as { result?: { stdout?: string } }).result?.stdout ?? ''
  }
  expect(await run('python3 $SD/findings.py present --iid 42', VIEW + manifest({}))).toEndWith(NOTE + '\n')
  expect(await run('ls', 'a\nb')).not.toContain(NOTE)
  expect(await run('python3 $SD/findings.py quote t3', VIEW + manifest({ topic: 't3', focus: 't1' }))).not.toContain(NOTE)
})

test('the card shows the view without the note the model read', async ($, on) => {
  engineRow(on)
  await reviewing($)
  const ui = await $.ui.mount({
    ...row('python3 $SD/findings.py present', VIEW + manifest({}) + '\n\n' + NOTE + '\n'),
    surface: 'terminal',
  })
  const md = await ui.find({ type: 'Markdown' })
  expect(md?.text).toContain('| draft | t1 |')
  expect(md?.text).not.toContain('[review-ui]')
  expect(md?.text).not.toContain('paste-gate')
  await ui.unmount()
})

test("a comment's typed line breaks are joined; code, lists and paragraphs keep theirs", () => {
  const body = ['Naming: this one', 'exports queryKeys.', '', '- one', '- two', '```ts', 'a', 'b', '```', 'Can we', 'settle?'].join('\n')
  expect(reflow(body)).toBe(['Naming: this one exports queryKeys.', '', '- one', '- two', '```ts', 'a', 'b', '```', 'Can we settle?'].join('\n'))
})

test('in an unfolded group the calls around a view keep one line', async ($, on) => {
  on('prompt.submit', ($, e) => ({ text: e.text }))
  let stdout: string | undefined
  on('ui.render', ($, e) => {
    stdout = ((e.props as { output?: { stdout?: string } }).output)?.stdout
    const { Text } = $.ui.resolve(e)
    return <Text>row</Text>
  })
  const call = (id: string, command: string, out: string) => ({
    tool_use_id: id, tool: 'Bash', input: { command }, isRunning: false, isErrored: false, isInterrupted: false,
    output: { stdout: out, stderr: '', interrupted: false },
  })
  await reviewing($)
  const group = await $.ui.mount({
    plugin: 'review-ui', component: 'ToolGroup', surface: 'terminal',
    props: { calls: [call('tu9', 'sed -n 1,40p a.ts', 'a\nb\nc\n'), call('tu1', 'python3 findings.py present', VIEW + manifest({}))], isActive: false, isExpanded: false },
  })
  await group.unmount()
  // The engine draws the unfolded rows from each row's own output.
  const ui = await $.ui.mount({ ...row('sed -n 1,40p a.ts', 'a\nb\nc\n', { tool_use_id: 'tu9', isExpanded: true }), surface: 'terminal' })
  await ui.unmount()
  expect(stdout).toBe('a\n… +2 lines')
})

test("show_topic beside another call is the group's card, not a call cut to one line", async ($, on) => {
  const expanded: boolean[] = []
  on('ui.render', ($, e) => {
    if (e.component === 'ToolGroup') expanded.push((e.props as { isExpanded: boolean }).isExpanded)
    const { Text } = $.ui.resolve(e)
    return <Text>engine row</Text>
  })
  on('prompt.submit', ($, e) => ({ text: e.text }))
  await reviewing($)
  const show = {
    tool_use_id: 'tu7', tool: 'mcp__review-ui__show_topic', input: { topic: 't1' }, isRunning: false, isErrored: false, isInterrupted: false,
    output: [{ type: 'text', text: VIEW + manifest({}) }],
  }
  const search = { tool_use_id: 'tu6', tool: 'ToolSearch', input: { query: 'select:mcp__review-ui__show_topic' }, isRunning: false, isErrored: false, isInterrupted: false, output: 'loaded' }
  const group = await $.ui.mount({ plugin: 'review-ui', component: 'ToolGroup', surface: 'terminal', props: { calls: [search, show], isActive: false, isExpanded: false } } as never)
  await group.unmount()
  expect(expanded).toEqual([true])
  const ui = await $.ui.mount({ plugin: 'review-ui', component: 'ToolUse', surface: 'terminal', props: { ...show, isExpanded: true } } as never)
  expect(await ui.find({ type: 'Text', text: 'engine row' })).toBeUndefined()
  expect(await ui.find({ type: 'Markdown' })).toBeDefined()
  await ui.unmount()
})

test('a view call still streaming in when its group is drawn becomes its card once it ran', async ($, on) => {
  engineRow(on)
  await reviewing($)
  const call = (id: string, input: object, output?: object) => ({
    tool_use_id: id, tool: 'Bash', input, isRunning: output === undefined, isErrored: false, isInterrupted: false, output,
  })
  const done = { stdout: VIEW + manifest({}), stderr: '', interrupted: false }
  // Drawn while the second call's command has not arrived yet: it is not research.
  const group = await $.ui.mount({
    plugin: 'review-ui', component: 'ToolGroup', surface: 'terminal',
    props: { calls: [call('tu1', { command: 'python3 findings.py quote t1' }, done), call('tu2', {})], isActive: true, isExpanded: false },
  })
  await group.unmount()
  const ui = await $.ui.mount({ ...row("python3 findings.py set t1 --draft 'x'", VIEW + manifest({}), { tool_use_id: 'tu2' }), surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: 'engine row' })).toBeUndefined()
  await ui.unmount()
})

test('a view the show_topic tool returns as content blocks is drawn as a card', async ($, on) => {
  engineRow(on)
  await reviewing($)
  const ui = await $.ui.mount({
    plugin: 'review-ui', component: 'ToolUse', surface: 'terminal',
    props: { tool_use_id: 'tu7', tool: 'mcp__review-ui__show_topic', input: { topic: 't1' }, isRunning: false, isErrored: false, isInterrupted: false,
      output: [{ type: 'text', text: VIEW + manifest({}) }] },
  } as never)
  expect(await ui.find({ type: 'Text', text: 'engine row' })).toBeUndefined()
  await ui.unmount()
})

test("under show_topic's card the engine's own result block is left out; ctrl+o keeps it", async ($, on) => {
  engineRow(on)
  await reviewing($)
  const result = (isExpanded: boolean) => ({
    plugin: 'review-ui', component: 'ToolResult', surface: 'terminal',
    props: { tool_use_id: 'tu7', tool: 'mcp__review-ui__show_topic', isErrored: false, isExpanded, output: [{ type: 'text', text: VIEW + manifest({}) }] },
  }) as never
  const ui = await $.ui.mount(result(false))
  expect(await ui.find({ type: 'Text', text: 'engine row' })).toBeUndefined()
  await ui.unmount()
  const full = await $.ui.mount(result(true))
  expect(await full.find({ type: 'Text', text: 'engine row' })).toBeDefined()
  await full.unmount()
})

test("in a review, a shell command's and a skill's output are left out; an error and ctrl+o keep it", async ($, on) => {
  engineRow(on)
  const result = (tool: string, isExpanded = false, isErrored = false) => ({
    plugin: 'review-ui', component: 'ToolResult', surface: 'terminal',
    props: { tool_use_id: 'tu9', tool, isErrored, isExpanded, output: { stdout: 'diff --git a/x b/x', stderr: '', interrupted: false } },
  }) as never
  const outside = await $.ui.mount(result('Bash'))
  expect(await outside.find({ type: 'Text', text: 'engine row' })).toBeDefined()    // no review: as ever
  await outside.unmount()
  await reviewing($)
  for (const [tool, isExpanded, isErrored, shown] of [['Bash', false, false, false], ['Skill', false, false, false], ['Bash', true, false, true], ['Bash', false, true, true], ['Read', false, false, true]] as const) {
    const ui = await $.ui.mount(result(tool, isExpanded, isErrored))
    expect([tool, isExpanded, isErrored, !!(await ui.find({ type: 'Text', text: 'engine row' }))]).toEqual([tool, isExpanded, isErrored, shown])
    await ui.unmount()
  }
})

test("a reply that pastes a card draws without it; the model's own words stay", async ($, on) => {
  const drawn: string[] = []
  on('ui.render', ($, e) => {
    if (e.component === 'AssistantMessage') drawn.push((e.props as { text: string }).text)
    const { Text } = $.ui.resolve(e)
    return <Text>engine row</Text>
  })
  on('prompt.submit', ($, e) => ({ text: e.text }))
  await reviewing($)
  const opener = '📖 **Explainer:** [e.html](file:///e.html)\n\n**Agent review done.** Your own review first.'
  const card = await $.ui.mount({ ...row('python3 $SD/findings.py present --iid 42', opener + manifest({ first: '📖 **Explainer:** [e.html](file:///e.html)' })), surface: 'terminal' })
  await card.unmount()
  const reply = (text: string) => ({ plugin: 'review-ui', component: 'AssistantMessage', surface: 'terminal', props: { text, isFirstOfReply: true } }) as never
  const pasted = await $.ui.mount(reply('📖 **Explainer:** [e.html](file:///e.html)\n\n**Agent review done.**  Your own\nreview first.'))
  expect(await pasted.find({ type: 'Text', text: 'engine row' })).toBeUndefined()
  await pasted.unmount()
  const mixed = await $.ui.mount(reply('**Agent review done.** Your own review first.\n\nI recommend t1 first.'))
  await mixed.unmount()
  expect(drawn).toEqual(['I recommend t1 first.'])
})

test("a review script's quiet call in a card's group stays out of the chat", async ($, on) => {
  engineRow(on)
  const call = (id: string, command: string, out: string) => ({
    tool_use_id: id, tool: 'Bash', input: { command }, isRunning: false, isErrored: false, isInterrupted: false,
    output: { stdout: out, stderr: '', interrupted: false },
  })
  await reviewing($)
  const group = await $.ui.mount({
    plugin: 'review-ui', component: 'ToolGroup', surface: 'terminal',
    props: { calls: [call('tu8', 'python3 findings.py set-head --iid 4', 'baseline set\nmore'), call('tu1', 'python3 findings.py present', VIEW + manifest({}))], isActive: false, isExpanded: false },
  })
  await group.unmount()
  const ui = await $.ui.mount({ ...row('python3 findings.py set-head --iid 4', 'baseline set\nmore', { tool_use_id: 'tu8', isExpanded: true }), surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: 'engine row' })).toBeUndefined()
  await ui.unmount()
})

test('the explainer line stands above the card, not in it', async ($, on) => {
  engineRow(on)
  await reviewing($)
  const ui = await $.ui.mount({
    ...row('python3 $SD/findings.py present', '📖 **Explainer:** file:///x.html\n\n' + VIEW + manifest({ first: '📖 **Explainer:** file:///x.html' })),
    surface: 'terminal',
  })
  const md = await ui.findAll({ type: 'Markdown' })
  expect(md.slice(0, 2).map(m => (m.props.text as string).split('\n')[0])).toEqual(['📖 **Explainer:** file:///x.html', '**MR !42** · Add a widget cache'])
  await ui.unmount()
})


test('a line saying how a topic came up stands above its card; one before no topic stays in the card', async ($, on) => {
  engineRow(on)
  await reviewing($)
  const lead = '_No comments of yours on !42 to match: on to the agent\'s topics._'
  const card = '\u{f0182} **t2** \u{f013f} **loadDb returns null on a read error**\n\n_Code currently in MR — `db.ts:59`:_'
  const ui = await $.ui.mount({ ...row('python3 $SD/findings.py own-review done --iid 42', `${lead}\n\n${card}` + manifest({ first: lead })), surface: 'terminal' })
  const md = await ui.findAll({ type: 'Markdown' })
  expect(md.map(m => m.props.text as string)).toEqual([lead, '_Code currently in MR — `db.ts:59`:_'])
  expect((await ui.findAll({ type: 'Box' })).some(b => b.props.marginBottom === 2)).toBe(true)   // two blank lines under it
  expect((await ui.findAll({ type: 'Text' })).find(t => t.props.color === 'cyan')?.text).toBe('t2')
  await ui.unmount()
  const other = await $.ui.mount({ ...row('python3 $SD/findings.py present', `_Not a topic's lead._\n\n${VIEW}` + manifest({ first: "_Not a topic's lead._" })), surface: 'terminal' })
  expect(((await other.findAll({ type: 'Markdown' }))[0]?.props.text as string).split('\n')[0]).toBe("_Not a topic's lead._")
  expect(await other.find({ type: 'Text', text: 'engine row' })).toBeUndefined()
  await other.unmount()
})


test("a topic card's title is drawn in colour, the rest as markdown", async ($, on) => {
  engineRow(on)
  await reviewing($)
  const card = '\u{f0182} **t2** \u{f013f} **loadDb returns null on a read error**\n\n_Code currently in MR — `db.ts:59`:_'
  const ui = await $.ui.mount({ ...row('python3 $SD/findings.py quote t2 --focus', card + manifest({ first: firstOf(card) })), surface: 'terminal' })
  const texts = await ui.findAll({ type: 'Text' })
  expect(texts.find(t => t.props.color === 'cyan')?.text).toBe('t2')
  expect(texts.some(t => /loadDb returns null on a read error/.test(t.text))).toBe(true)
  const md = await ui.findAll({ type: 'Markdown' })
  expect(md.map(m => m.props.text as string)).toEqual(['_Code currently in MR — `db.ts:59`:_'])
  await ui.unmount()
})

test('a title drawn as plain text shows its code spans without their backticks', async ($, on) => {
  expect(plainTitle('Rename `loadDb` to ``readDb``, keep `x')).toBe('Rename loadDb to readDb, keep x')
  const r = { skill: 'rework-mr', iid: 1, title: null, current: null, topics: [] } as never
  expect(rowOf(r, { id: 't1', state: 'new', summary: 'Rename `loadDb`' } as never).title).toBe('Rename loadDb')
  engineRow(on)
  await reviewing($)
  const card = '\u{f0182} **t2** \u{f013f} **`loadDb` returns null**\n\nthe analysis'
  const ui = await $.ui.mount({ ...row('python3 $SD/findings.py quote t2 --focus', card + manifest({ first: firstOf(card) })), surface: 'terminal' })
  const texts = (await ui.findAll({ type: 'Text' })).map(t => t.text ?? '')
  expect(texts.some(t => t.includes('loadDb returns null'))).toBe(true)
  expect(texts.some(t => t.includes('`'))).toBe(false)
  await ui.unmount()
})

test("a diff's size is drawn apart, its counts in a diff's colours; one in a fence or a quote is left alone", async ($, on) => {
  engineRow(on)
  await reviewing($)
  const card = '\u{f0182} **t2** \u{f013f} **x**\n\n**Diff (t2)** — \u{f4d2} 2 files  +3  −1 · tmux window 9\n\n```yaml\na.ts:  +2  −1\n```\n\n> \u{f4d2} 1 file  +9  −9'
  const ui = await $.ui.mount({ ...row('python3 $SD/findings.py quote t2 --focus', card + manifest({ first: firstOf(card) })), surface: 'terminal' })
  const texts = await ui.findAll({ type: 'Text' })
  expect(texts.find(t => t.text === '+3')?.props.color).toBe('green')
  expect(texts.find(t => t.text === '−1')?.props.color).toBe('red')
  expect(texts.some(t => t.text === '+9')).toBe(false)
  const md = (await ui.findAll({ type: 'Markdown' })).map(m => m.props.text as string)
  expect(md).toEqual(['**Diff (t2)** —', '· tmux window 9', '```yaml\na.ts:  +2  −1\n```\n\n> \u{f4d2} 1 file  +9  −9'])
  await ui.unmount()
})

test("a fix's Changes heading carries its size and, dim, its commit counts; each commit is drawn apart", async ($, on) => {
  engineRow(on)
  await reviewing($)
  const card = '\u{f0182} **t1** **x**\n\n**\u{f11a7} Agreed plan**\n\nRename it.\n\n**\u{f189b} Changes**  \u{f4d2} 4 files +15 −15  (1 new commit, 1 commit rewritten)\n\nRenamed it.\n\n' +
    '\u{f071b} `455d841` extract keys\n\u{f071c} test: cover the keys'
  const ui = await $.ui.mount({ ...row('python3 $SD/findings.py quote t1 --focus', card + manifest({ first: firstOf(card) })), surface: 'terminal' })
  const texts = await ui.findAll({ type: 'Text' })
  expect(texts.find(t => t.text === '(1 new commit, 1 commit rewritten)')?.props.dimColor).toBe(true)
  const subject = texts.find(t => t.text === 'extract keys')
  expect([subject?.props.dimColor, subject?.props.italic]).toEqual([true, true])
  expect(texts.find(t => t.text === 'test: cover the keys')?.props.italic).toBe(true)
  // The size beside the heading is said with it: dim, without a diff's colours.
  expect(texts.find(t => t.text === '+15')?.props.color).toBeUndefined()
  // Each blank line of the text stays between the parts: above Changes and above the first
  // commit; not under the Changes heading, which sits on what it heads, nor between commits.
  const gaps = (await ui.findAll({ type: 'Box' })).filter(b => b.props.marginTop === 1 && String(b.key ?? '').startsWith('gap-'))
  expect(gaps.length).toBe(2)
  const md = (await ui.findAll({ type: 'Markdown' })).map(m => m.props.text as string)
  expect(md).toContain('`455d841`')
  expect(md.some(m => m.includes('455d841 extract'))).toBe(false)
  await ui.unmount()
})

test('a proposed edit of a posted comment is drawn word by word: struck where taken out, marked where put in, readably', async ($, on) => {
  engineRow(on)
  await reviewing($)
  const card = '\u{f0182} **t2** **x**\n\n**\u{f1a7d} Proposed edit**\n\n```worddiff\nWe take option [-A-]{+B+}, `keys.detail(id)` {+again, `as before`+}.\n```'
  const ui = await $.ui.mount({ ...row('python3 $SD/threads.py posted t2 --differs "x" --fix -', card + manifest({ first: firstOf(card) })), surface: 'terminal' })
  const texts = await ui.findAll({ type: 'Text' })
  const removed = texts.find(t => t.text === 'A')
  // On the diff's line colours: a word colour is too bright to read running text on.
  expect([removed?.props.strikethrough, removed?.props.backgroundColor]).toEqual([true, 'diffRemoved'])
  expect(texts.find(t => t.text === 'B')?.props.backgroundColor).toBe('diffAdded')
  // Code spans are drawn as code, without their backticks, also where a change runs into one.
  expect(texts.find(t => t.text === 'keys.detail(id)')?.props.color).toBe('permission')
  const inside = texts.find(t => t.text === 'as before')
  // On a mark, a code span takes the text's own colour: the accent on green is hard to read.
  expect([inside?.props.color, inside?.props.backgroundColor]).toEqual([undefined, 'diffAdded'])
  expect(texts.some(t => String(t.text ?? '').includes('`'))).toBe(false)
  expect((await ui.findAll({ type: 'Markdown' })).some(m => String(m.props.text).includes('worddiff'))).toBe(false)
  await ui.unmount()
})

test('a session working on the skills, not with them, keeps the engine row for a view', async ($, on) => {
  engineRow(on)
  const ui = await $.ui.mount({ ...row('python3 $SD/findings.py present --iid 42', VIEW + manifest({})), surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: 'engine row' })).toBeDefined()
  expect(await ui.find({ type: 'Markdown' })).toBeUndefined()
  await ui.unmount()
})

test("a comment's author stands apart in the card, the person's own in their colour", async ($, on) => {
  engineRow(on)
  await reviewing($)
  const card = '\u{f0182} **t1** \u{f013f} **x** · `a.ts:3`\n\n> [\u{f03cc}](http://gl/n1) **You** _Nov 5, 12:09_\n> seems bad\n\n> [\u{f03cc}](http://gl/n2) **Kim** _Nov 5, 12:30_\n> agreed'
  const ui = await $.ui.mount({ ...row('python3 $SD/findings.py quote t1 --focus', card + manifest({ first: firstOf(card) })), surface: 'terminal' })
  const texts = await ui.findAll({ type: 'Text' })
  expect(texts.find(t => t.text === 'You')?.props.color).toBe('magenta')
  expect(texts.find(t => t.text === 'Kim')?.props.color).toBeUndefined()
  const md = (await ui.findAll({ type: 'Markdown' })).map(m => m.props.text as string)
  // The head on a line of its own (link, author, time), the text under it, still a quote,
  // so its bar runs down every line.
  expect(md.slice(1, 3)).toEqual(['> seems bad', '> agreed'])
  // The link inline in the head's one line of text, no block of markdown of its own.
  expect((await ui.findAll({ type: 'Link' })).map(l => l.props.href)).toEqual(['http://gl/n1', 'http://gl/n2'])
  expect(texts.find(t => t.text === 'Nov 5, 12:09')?.props.dimColor).toBe(true)
  await ui.unmount()
})

test('a card lays out by one rule: a label sits on what it labels, a blank line sets parts apart', () => {
  const card = [
    '> [\u{f03cc}](u) **You** this is not secure!', '',
    '**\u{f13b8} Analysis**', '', 'It breaks.', '', '**\u{f06e9} Recommendation**', '', 'Raise it.', '',
    '**\u{f12c4} Draft of follow-up reply to post (en)**', '_As a reply in the thread on `a.ts:17`:_', '',
    '> **Wichtig:** It also breaks the e2e run.',
  ].join('\n')
  const b = blocksOf(card)
  expect(b.map(x => [x.author ?? (x.isLabel ? 'label' : 'text'), Boolean(x.isApart)])).toEqual([
    ['You', false], ['label', true], ['text', false], ['label', true], ['text', false],
    ['label', true], ['label', false], ['text', false],
  ])
  expect(b[2]?.text).toBe('It breaks.')
  expect(b[4]?.text).toBe('Raise it.')
  expect(b[5]?.isAside).toBe(false)              // the draft's heading
  expect(b[6]?.isAside).toBe(true)               // where it goes, dim under it
})

test("the review scripts' quiet calls are left out of a review's chat", async ($, on) => {
  engineRow(on)
  await reviewing($)
  const quiet = await $.ui.mount({ ...row('python3 $SD/findings.py own-review done --iid 1', 'd1  `a.ts:3`  odd'), surface: 'terminal' })
  expect(await quiet.find({ type: 'Text', text: 'engine row' })).toBeUndefined()
  await quiet.unmount()
  const other = await $.ui.mount({ ...row('git status', 'clean'), surface: 'terminal' })
  expect(await other.find({ type: 'Text', text: 'engine row' })).toBeDefined()
  await other.unmount()
})

test('a diff block is drawn apart from the markdown around it, quoted or not', () => {
  const card = [
    '```diff path=a.ts at=2', '@@ -1,1 +1,1 @@', ' a', '@@ -2,1 +2,0 @@', '-b', '```',
    '> Use this:', '>', '> ```diff path=a.ts', '> @@ -2,1 +2,1 @@', '> -b', '> +B', '> ```',
  ].join('\n')
  expect(partsOf(card)).toEqual([
    { path: 'a.ts', at: 2, hunks: ['@@ -1,1 +1,1 @@\n a', '@@ -2,1 +2,0 @@\n-b'], isQuoted: false },
    { md: '> Use this:' },
    { path: 'a.ts', at: undefined, hunks: ['@@ -2,1 +2,1 @@\n-b\n+B'], isQuoted: true },
  ])
})

test('a card with code in diff form, in a quote or not, draws each hunk as a diff', async ($, on) => {
  engineRow(on)
  await reviewing($)
  const text = [
    '\u{f12c4} **t3** \u{f013f} **x** · `a.ts:2`', '',
    '_Code currently in MR — `a.ts:2`:_', '',
    '```diff path=a.ts at=2', '@@ -1,1 +1,1 @@', ' a', '@@ -2,1 +2,1 @@', ' b', '```', '',
    '_Draft of comment to post (en) — thread on `a.ts:2`:_', '',
    '> Use this:', '>', '> ```diff path=a.ts', '> @@ -2,1 +2,1 @@', '> -b', '> +B', '> ```',
  ].join('\n')
  const ui = await $.ui.mount({ ...row('python3 $SD/findings.py quote t3 --focus', text + manifest({ first: firstOf(text) })), surface: 'terminal' })
  const codes = await ui.findAll({ type: 'Code' })
  expect(codes.map(c => c.props.format)).toEqual(['diff', 'diff', 'diff'])
  await ui.unmount()
})

test("a proposal's change is headed by its file and the lines it spans, as the sidebar heads a file", async ($, on) => {
  engineRow(on)
  await reviewing($)
  const text = [
    '\u{f12c4} **t3** **x** · `a.ts:2`', '',
    '```diff path=src/features/x.ts loc', '@@ -60,3 +60,4 @@', ' a', '-b', '+B', '+C', '@@ -70,1 +71,1 @@', '-d', '+D', '```',
  ].join('\n')
  expect(partsOf(text.split('\n').slice(2).join('\n'))[0]).toMatchObject({ path: 'src/features/x.ts', loc: 'src/features/x.ts:60–71' })
  const ui = await $.ui.mount({ ...row('python3 $SD/threads.py quote t3 --focus', text + manifest({ first: firstOf(text) })), surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: 'src/features/x.ts:60–71' })).toBeDefined()
  expect((await ui.findAll({ type: 'Markdown' })).map(m => m.props.text as string).join('\n')).not.toContain('@@')
  expect((await ui.findAll({ type: 'Code' })).length).toBe(2)
  await ui.unmount()
})

test('a view cut short by the command around it is not a card; one with output after it is, without that output', async ($, on) => {
  engineRow(on)
  await reviewing($)
  const whole = VIEW + manifest({})
  const cut = await $.ui.mount({ ...row('python3 $SD/findings.py present | head -20', whole.slice(0, whole.length - 8)), surface: 'terminal' })
  expect(await cut.find({ type: 'Markdown' })).toBeUndefined()
  await cut.unmount()
  const after = await $.ui.mount({ ...row('python3 $SD/findings.py present; sed -n 1,3p a.ts', whole + '\nline one\nline two\n'), surface: 'terminal' })
  const md = (await after.findAll({ type: 'Markdown' })).map(m => String(m.props.text)).join('\n')
  expect(md).toContain('**MR !42**')
  expect(md).not.toContain('line one')
  await after.unmount()
})

test('rework-mr\'s sidebar groups as review-mr\'s: to do in the topics\' own order, waiting for reviewers, closed', () => {
  const t = (id: string, state: string, inbox: string | null = null) => ({ id, status: 'open', state, summary: id, file: null, posted: true, draft: null, inbox })
  const r = { skill: 'rework-mr', iid: 1, title: 'x', current: 't3', round: ['t3', 't1'],
    topics: [t('t1', 'new'), t('t2', 'waiting'), t('t3', 'answered'), t('t5', 'done')] }
  expect(sectionsOf(r as never).map(x => [x.title, x.rows.map(row => row.id)])).toEqual([
    ['To plan', ['t1', 't3']], ['Waiting for reviewers', ['t2']], ['Closed', ['t5']],
  ])
})

test('rework-mr: the round in groups by what is left: to plan or planned, then to fix or to reply', () => {
  const t = (id: string, step: string) => ({ id, status: 'open', state: 'new', summary: id, file: null, posted: true, draft: null, inbox: null, step })
  const r = { skill: 'rework-mr', iid: 1, title: 'x', current: 't3', round: ['t3', 't1', 't2'], pass: 'plan',
    topics: [t('t1', 'fix'), t('t2', 'reply'), t('t3', 'plan')] }
  expect(sectionsOf(r as never).map(x => [x.title, x.rows.map(row => row.id)])).toEqual([['To plan', ['t3']], ['Planned', ['t1', 't2']]])
  // The fix pass: a fix to make or push, or only the reply left.
  expect(sectionsOf({ ...r, pass: 'fix' } as never).map(x => [x.title, x.rows.map(row => row.id)])).toEqual([['To fix', ['t1', 't3']], ['To reply', ['t2']]])
})

test('a bare URL in a thread box is a short link, never broken across rows', () => {
  const url = 'https://gitlab.example/group/app/-/merge_requests/1017/diffs?commit_id=a03a1cd234cea42120e51a851f3e844137e839a3'
  expect(urlLabel(url)).toBe('!1017 · commit a03a1cd')
  expect(urlLabel('https://gitlab.example/group/app/-/merge_requests/1017#note_99')).toBe('!1017 · comment')
  expect(urlLabel('https://gitlab.example/group/app/-/issues/7')).toBe('#7')
  expect(urlLabel('https://example.org/docs/a-rather-long-path/that-goes/on-and-on/and-on')).toBe('example.org/docs/a-rather-long-path/tha…')
  const rows = boxRowsOf([`see ${url} for the commit, and ${'more words '.repeat(6)}`], 30, (i, b) => `k:${i}:${b}`, () => false, b => b, 'ts')
  const md = rows.filter(r => r.kind === 'md').map(r => (r as { text: string }).text)
  expect(md.filter(t => t.includes(url))).toHaveLength(1)
  expect(md.join('\n')).toContain(`[!1017 · commit a03a1cd](${url})`)
  // Inline code and links already written as such stay as they are.
  const kept = boxRowsOf(['`https://x.example/a` and [docs](https://x.example/b)'], 80, (i, b) => `k:${i}:${b}`, () => false, b => b, 'ts')
  expect((kept[1] as { text: string }).text).toBe('`https://x.example/a` and [docs](https://x.example/b)')
})

test("a comment's GitLab emoji shortcodes are drawn as the emoji; code and unknown ones are left as written", () => {
  expect(withEmoji('good catch :ok_hand: :+1:')).toBe('good catch 👌 👍')
  expect(withEmoji('keep `:ok_hand:` and :no_such_one:')).toBe('keep `:ok_hand:` and :no_such_one:')
  expect(withEmoji('```\n:tada:\n```\n:tada:')).toBe('```\n:tada:\n```\n🎉')
})
