// A scrollbar's own drawing: one column the sidebar hands its geometry to (`BarSpec`), drawn
// again here, where the pointer is, at every move over it, so the preview of a press follows
// the pointer with no round trip to the sidebar. What moves the window goes back as a post,
// `{ to }`, the row the window then starts at: a press on the track, on the topic's mark, or
// each step of a drag.

import type { ClientModule } from 'claude-code'
import { barPaint, eighthOf, fromTop, isOnTopic, pressOf, thumbOf } from './sidebar.ts'
import type { BarPointer, BarSpec } from './sidebar.ts'

// The pointer as last seen, and while a button is down on the thumb, how far below the
// thumb's top it was grabbed (eighths) and where the drag holds the window.
type State = BarPointer & { grab: number | null }

const ScrollBar: ClientModule<BarSpec, State> = (b, surface) => {
  const { Box, Text } = surface.elements
  const s: State = surface.state ?? { at: null, drag: null, grab: null }
  surface.onPointer(e => {
    const was: State = surface.state ?? { at: null, drag: null, grab: null }
    const at = eighthOf(e.y, e.fine?.y)
    if (e.type === 'leave') {
      // A drag goes on past the bar's edges until the button comes up.
      if (was.grab === null && was.at !== null) surface.setState({ ...was, at: null })
      return
    }
    if (e.type === 'down' && e.button === 'left') {
      const to = pressOf(b, at)
      if (to !== null) surface.post({ to })
      // The topic's mark only takes the window to the topic: the thumb lands where it lands,
      // away from the pointer, so there is nothing to drag.
      if (isOnTopic(b, at)) {
        surface.setState({ at, drag: null, grab: null })
        return
      }
      // On the thumb it is grabbed where it was pressed; elsewhere it goes there first, and a
      // drag goes on from its middle, which is where the pointer then is.
      const { top, end } = thumbOf(b.count, b.shown, to ?? b.from, b.rows)
      surface.setState({ at, drag: to ?? b.from, grab: to === null ? at - top : Math.floor((end - top) / 2) })
      return
    }
    if (e.type === 'up') {
      if (was.grab !== null) surface.setState({ at, drag: null, grab: null })
      return
    }
    if (e.type === 'move' && was.grab !== null && e.button) {
      const to = fromTop(b.count, b.shown, b.rows, at - was.grab)
      if (to !== was.drag) surface.post({ to })
      surface.setState({ ...was, at, drag: to })
      return
    }
    // A hover: draw again only where what it shows changes, a cell (or an eighth, where the
    // terminal reports them) at a time.
    if (at !== was.at) surface.setState({ ...was, at, drag: null, grab: null })
  })
  // While the button is down the window is where the drag holds it, ahead of the sidebar.
  const cells = barPaint(b, { at: s.at, drag: s.grab !== null ? s.drag : null })
  return (
    <Box flexDirection="column" width={1}>
      {cells.map((c, i) => (
        <Text key={`${c.isTopic ? 'topic' : c.at}-${i}`} color={c.color} backgroundColor={c.backgroundColor}>{c.glyph}</Text>
      ))}
    </Box>
  )
}

export default ScrollBar
