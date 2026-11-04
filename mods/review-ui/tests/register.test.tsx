import type { On } from 'claude-code'
import { expect, test } from 'claude-code/testing'

import { NOTE } from '../hooks/views.ts'

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

// The engine's own row, standing beneath the mod.
const engineRow = (on: On) =>
  on('ui.render', ($, e) => {
    const { Text } = $.ui.resolve(e)
    return <Text>engine row</Text>
  })

test('a view is drawn as markdown under its row, marker gone, on every surface', async ($, on) => {
  engineRow(on)
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({
      ...row('cd /repo && SD=x python3 $SD/findings.py present --iid 42', VIEW + manifest({})),
      surface,
    })
    const md = await ui.find({ type: 'Markdown' })
    expect(md?.text).toContain('| draft | t1 |')
    expect(md?.text).toContain('```suggestion')
    expect(md?.text).not.toContain('paste-gate')
    expect(await ui.find({ type: 'Text', text: /engine row/ })).toBeDefined()
    await ui.unmount()
  }
})

test('every view command of both skills is drawn', async ($, on) => {
  engineRow(on)
  for (const command of [
    'python3 $SD/findings.py resume --iid 1', 'python3 $SD/findings.py todo --iid 1',
    'python3 $SD/findings.py quote t1 --iid 1', 'python3 $SD/findings.py updates --iid 1',
    'python3 $SD/findings.py set t1 --draft "x" --iid 1', 'python3 $SD/findings.py diff t1 --show',
    'python3 $SD/threads.py present --iid 3', 'python3 $SD/threads.py todo --iid 3',
    'python3 $SD/threads.py quote t2', 'python3 $SD/threads.py reply-view t2 --iid 3',
    'python3 $SD/threads.py change-view t1', '$SD/diff-view.sh t2', 'sh $SD/change-preview.sh t1',
    'python3 "$SD/threads.py" present', "python3 '$SD/findings.py' quote t1", '"$SD/diff-view.sh" t2',
  ]) {
    const ui = await $.ui.mount({ ...row(command, VIEW), surface: 'terminal' })
    expect(await ui.find({ type: 'Markdown' })).toBeDefined()
    await ui.unmount()
  }
})

test('other commands keep the engine row alone', async ($, on) => {
  engineRow(on)
  for (const command of [
    'python3 findings.py sync', 'ls -la', 'python3 findings.py diff t1',
    "cat > t.tsx <<'X'\n  row('findings.py quote t1', VIEW)\nX",
    'echo done # findings.py present',
    "grep -n 'findings.py present' SKILL.md",
    'python3 drive.py verify --prompt "findings.py present"',
  ]) {
    const ui = await $.ui.mount({ ...row(command, VIEW), surface: 'terminal' })
    expect(await ui.find({ type: 'Markdown' })).toBeUndefined()
    await ui.unmount()
  }
})

test('a running, failed or empty call is not drawn', async ($, on) => {
  engineRow(on)
  for (const extra of [
    { isRunning: true, output: undefined },
    { isErrored: true },
    { output: { stdout: '  \n', stderr: '', interrupted: false } },
  ]) {
    const ui = await $.ui.mount({ ...row('python3 findings.py present', VIEW, extra), surface: 'terminal' })
    expect(await ui.find({ type: 'Markdown' })).toBeUndefined()
    await ui.unmount()
  }
})

test('a view of a topic that is not the current one is research, not a card', async ($, on) => {
  engineRow(on)
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
  const ui = await $.ui.mount({
    ...row('npm run lint && python3 $SD/findings.py present', 'lint: 0 problems\n\n' + VIEW + manifest({})),
    surface: 'terminal',
  })
  const md = await ui.find({ type: 'Markdown' })
  expect(md?.text?.startsWith('**MR !42**')).toBe(true)
  await ui.unmount()
})

test('the row above the card no longer prints the raw output', async ($, on) => {
  let stdout: unknown
  on('ui.render', ($, e) => {
    stdout = (e.props as { output?: { stdout?: unknown } }).output?.stdout
    const { Text } = $.ui.resolve(e)
    return <Text>engine row</Text>
  })
  const ui = await $.ui.mount({ ...row('python3 findings.py quote t1', VIEW), surface: 'terminal' })
  expect(stdout).toBe('view below')
  await ui.unmount()
})

test('a group holding a view unfolds; one holding research or none stays folded', async ($, on) => {
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
    call('python3 findings.py present', VIEW),
    call('ls', 'a\nb'),
    call('python3 findings.py quote t3', VIEW + manifest({ topic: 't3', focus: 't1' })),
  ]) {
    const ui = await $.ui.mount({
      plugin: 'review-ui', component: 'ToolGroup', surface: 'terminal',
      props: { calls: [c], isActive: false, isExpanded: false },
    })
    await ui.unmount()
  }
  expect(seen).toEqual([true, false, false])
})

test('the model reads a note after a view it must not paste; nothing else gets one', async ($, on) => {
  let stdout = ''
  on('tool.call', () => ({ result: { stdout, stderr: '', interrupted: false } }))
  const run = async (command: string, out: string) => {
    stdout = out
    const ran = await $.tool.call({ tool: 'Bash', command })
    return (ran as { result?: { stdout?: string } }).result?.stdout ?? ''
  }
  expect(await run('python3 $SD/findings.py present --iid 42', VIEW + manifest({}))).toEndWith(NOTE + '\n')
  expect(await run('ls', 'a\nb')).not.toContain(NOTE)
  expect(await run('python3 $SD/findings.py quote t3', VIEW + manifest({ topic: 't3', focus: 't1' }))).not.toContain(NOTE)
})

test('the card shows the view without the note the model read', async ($, on) => {
  engineRow(on)
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
