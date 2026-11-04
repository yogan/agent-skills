import type { Register } from 'claude-code'

import { isViewCommand, viewOf, withNote } from './views.ts'

// What the tool row prints in place of the output it would show unfolded: the card under
// it is that output, and a second, raw copy above it would include the gate's marker.
const ROW_LINE = 'view below'

const commandOf = (tool: string, input: unknown): string | undefined => {
  const command = (input as { command?: unknown } | undefined)?.command
  return tool === 'Bash' && isViewCommand(command) ? command : undefined
}

const stdoutOf = (output: unknown): unknown => (output as { stdout?: unknown } | undefined)?.stdout

export const register: Register = on => {
  // A view the person gets as a card carries a note for the model: do not paste it.
  // Research (another topic's view) gets no card, so no note either.
  on('tool.call', { tool: 'Bash' }, async ($, e, next) => {
    const ran = await next(e)
    if (!isViewCommand(e.command) || ran.deny !== undefined || ran.isError) return ran
    const result = ran.result as { stdout?: unknown } | undefined
    if (typeof result?.stdout !== 'string' || !viewOf(result.stdout)) return ran
    return { ...ran, result: { ...result, stdout: withNote(result.stdout) } }
  })

  // The transcript folds a run of shell calls into one count line; a view folded away is a
  // view not shown, so a group holding one stays unfolded.
  on('ui.render', { component: 'ToolGroup' }, ($, e, next) => {
    if (e.props.isExpanded) return next(e)
    const holdsView = e.props.calls.some(c => {
      if (!commandOf(c.tool, c.input)) return false
      // A call still running, or one that printed research, unfolds nothing.
      const out = (c as { output?: unknown }).output
      return out === undefined || viewOf(stdoutOf(out)) !== undefined
    })
    if (!holdsView) return next(e)
    return next({ ...e, props: { ...e.props, isExpanded: true } })
  })

  on('ui.render', { component: 'ToolUse' }, async ($, e, next) => {
    const { tool, input, output, isRunning, isErrored } = e.props
    if (isRunning || isErrored || !commandOf(tool, input)) return next(e)
    const view = viewOf(stdoutOf(output))
    if (!view) return next(e)

    const row = await next({
      ...e,
      props: { ...e.props, output: { ...(output as object), stdout: ROW_LINE } },
    })
    const { Box, Markdown } = $.ui.resolve(e)
    return (
      <Box flexDirection="column">
        {row}
        <Box borderStyle="round" paddingX={1} flexDirection="column">
          <Markdown text={view.text} />
        </Box>
      </Box>
    )
  })
}
