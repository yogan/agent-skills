import { expect, test } from 'claude-code/testing'
import { barPaint, cellsOf, centredOn, changeKinds, eighthOf, fromTop, pressOf, previewOf, revealedAt, thumbOf } from '../hooks/sidebar.ts'
import type { BarLook, BarSpec } from '../hooks/sidebar.ts'

// Colours by name, so a drawing reads as words.
const LOOK: BarLook = {
  track: 'track', thumb: 'thumb', lit: 'lit', former: 'former', added: 'added', removed: 'removed', modified: 'modified', topic: 'topic',
}
const spec = (more: Partial<BarSpec> = {}): BarSpec => ({ count: 24, shown: 8, from: 5, rows: 8, look: LOOK, ...more })
const glyphs = (b: BarSpec, at: number | null = null, drag: number | null = null) => barPaint(b, { at, drag }).map(c => c.glyph).join('')

test('the thumb is the share shown, at where the window is', async () => {
  // 8 of 24 rows on a track of 8 cells: a third of 64 eighths.
  expect(thumbOf(24, 8, 0, 8)).toEqual({ top: 0, end: 21 })
  expect(thumbOf(24, 8, 16, 8)).toEqual({ top: 43, end: 64 })      // at the last row: the end
  expect(thumbOf(24, 8, 5, 8)).toEqual({ top: 13, end: 34 })
})

test('a thumb is never shorter than a cell, and fills the track when all shows', async () => {
  expect(thumbOf(1000, 5, 0, 5)).toEqual({ top: 0, end: 8 })
  expect(thumbOf(5, 5, 0, 5)).toEqual({ top: 0, end: 40 })
  expect(thumbOf(3, 5, 0, 5)).toEqual({ top: 0, end: 40 })
})

test("at rest, the thumb's ends are partial blocks, from the bottom where it starts and from the top where it ends", async () => {
  const cells = barPaint(spec())
  expect(cells.map(c => c.glyph).join('')).toBe(' ▃██\u{1FB82}   ')
  expect(cells.map(c => c.at)).toEqual(['above', 'thumb', 'thumb', 'thumb', 'thumb', 'below', 'below', 'below'])
  expect(cells.map(c => c.color)).toEqual(['track', 'thumb', 'thumb', 'thumb', 'thumb', 'track', 'track', 'track'])
  expect(cells.every(c => c.backgroundColor === 'track')).toBe(true)
})

test('a press on the track centres the thumb on the pointer: the top cell is the top, the bottom one the end', async () => {
  // 8 of 80 rows on 8 cells: the thumb a cell long, the window can go 72 rows.
  expect(centredOn(80, 8, 8, eighthOf(0))).toBe(0)
  expect(centredOn(80, 8, 8, eighthOf(7))).toBe(72)
  expect(centredOn(80, 8, 8, eighthOf(4))).toBe(41)        // the middle of cell 4: 32 of 56 eighths
  // Where the terminal reports where in the cell the pointer is, that counts.
  expect(centredOn(80, 8, 8, eighthOf(4, 4.0))).toBe(36)
  // The thumb it puts there has its middle under the pointer.
  const { top, end } = thumbOf(24, 8, centredOn(24, 8, 8, 40), 8)
  expect(Math.abs((top + end) / 2 - 40)).toBeLessThanOrEqual(1)
})

test("a drag keeps the thumb where it was grabbed: its top follows the pointer's move", async () => {
  // 21 eighths of thumb on 64: the window goes 16 rows over 43 eighths of the track.
  expect(fromTop(24, 8, 8, 0)).toBe(0)
  expect(fromTop(24, 8, 8, 43)).toBe(16)
  expect(fromTop(24, 8, 8, 99)).toBe(16)                   // past the foot: the end
  expect(fromTop(24, 8, 8, -9)).toBe(0)
  expect(fromTop(24, 8, 8, 13)).toBe(5)
})

test('a press on the thumb grabs it, on the topic\'s mark goes to the topic, elsewhere centres there', async () => {
  const b = spec({ topic: { cells: [7], to: 12 } })
  expect(pressOf(b, 20)).toBeNull()                        // the thumb: eighths 13 to 33
  expect(pressOf(b, 60)).toBe(12)                          // the mark, in cell 7
  expect(pressOf(b, 4)).toBe(0)
  // Under the thumb the mark is the thumb's: a press there grabs it.
  expect(pressOf(spec({ topic: { cells: [2], to: 12 } }), 20)).toBeNull()
})

test('hovering the track shows the thumb where a press would put it, at its full length, the thumb at the window faint', async () => {
  const b = spec()
  const at = eighthOf(7)
  const to = previewOf(b, { at, drag: null })
  expect(to).toBe(16)                                      // the foot: the end
  const cells = barPaint(b, { at, drag: null })
  // The preview: the thumb at the end, lit, as long as the thumb (21 eighths: the ▅ at its top
  // and two full cells); the thumb at the window, faint, as it was.
  expect(glyphs(b, at)).toBe(' ▃██\u{1FB82}▅██')
  expect(cells.map(c => c.color)).toEqual(['track', 'former', 'former', 'former', 'former', 'lit', 'lit', 'lit'])
  // A press there puts the thumb exactly where the preview was.
  expect([...glyphs({ ...b, from: to as number })].slice(5)).toEqual([...glyphs(b, at)].slice(5))
  // Where the faint thumb ends and the preview starts in one cell, with track between, the
  // preview is the glyph and what covers most of the rest behind it.
  const near = barPaint(b, { at: eighthOf(5), drag: null })
  expect(near[4]).toMatchObject({ glyph: '▅', color: 'lit', backgroundColor: 'former' })
})

test('the pointer on the thumb, or a drag, lights it and shows no preview', async () => {
  const b = spec()
  expect(previewOf(b, { at: 20, drag: null })).toBeNull()
  expect(barPaint(b, { at: 20, drag: null }).filter(c => c.at === 'thumb').every(c => c.color === 'lit')).toBe(true)
  // Dragged, the thumb is drawn where the drag holds the window, ahead of the sidebar.
  const dragged = barPaint(b, { at: 60, drag: 16 })
  expect(dragged.map(c => c.glyph).join('')).toBe('     ▅██')
  expect(dragged.filter(c => c.glyph !== ' ').every(c => c.color === 'lit')).toBe(true)
})

test("a file's changes stand on the cells for their rows, in the right half of a cell, a replaced line in a colour of its own", async () => {
  // 24 rows on 8 cells, three to a cell: a line added in row 7 (cell 2), one replaced in rows
  // 9-10 (cell 3), one removed at the end (cell 7); the thumb over cells 1 to 4.
  const signs = Array.from({ length: 24 }, () => ' ')
  signs[7] = '+'
  signs[9] = '-'
  signs[10] = '+'
  signs[23] = '-'
  const changes = changeKinds(24, 8, signs)
  expect(changes).toEqual([[2, 'added'], [3, 'modified'], [7, 'removed']])
  const cells = barPaint(spec({ changes }))
  // On the thumb and off it alike: the half in the change's colour, the rest what is under it.
  expect(cells[2]).toMatchObject({ glyph: '▐', color: 'added', backgroundColor: 'thumb' })
  expect(cells[3]).toMatchObject({ glyph: '▐', color: 'modified', backgroundColor: 'thumb' })
  expect(cells[7]).toMatchObject({ glyph: '▐', color: 'removed', backgroundColor: 'track' })
  // A cell the thumb's end covers less than half of is the track's behind a change.
  expect(barPaint(spec({ changes: [[1, 'added']] }))[1]).toMatchObject({ glyph: '▐', backgroundColor: 'track' })
})

test("a replaced line is one change wherever its rows fall, never a removal beside an addition", () => {
  // A file of 63 lines with six lines replaced (each a removed row, then its added row): 69
  // rows on 25 cells, under three to a cell, so most pairs fall across two cells.
  const replaced = [9, 34, 37, 40, 48, 53]
  const signs: string[] = []
  for (let line = 1; line <= 63; line++) {
    if (replaced.includes(line)) signs.push('-')
    signs.push(replaced.includes(line) ? '+' : ' ')
  }
  const changes = changeKinds(69, 25, signs)
  expect(changes.length).toBeGreaterThan(6)
  expect(changes.every(([, kind]) => kind === 'modified')).toBe(true)
  // A line added and another removed, rows apart but on one cell: neither is a replacement.
  // The removed one shows, as the one no longer to be seen in the file.
  expect(changeKinds(8, 2, [' ', '+', ' ', '-', ' ', ' ', ' ', ' '])).toEqual([[0, 'removed']])
  expect(changeKinds(8, 2, [' ', '+', '+', ' ', '-', ' ', ' ', ' '])).toEqual([[0, 'added'], [1, 'removed']])
  // Of a replaced line and an added one in one cell, the replaced one shows; of more added
  // rows than replaced ones, the added.
  expect(changeKinds(8, 1, ['-', '+', ' ', '+', ' ', ' ', ' ', ' '])).toEqual([[0, 'modified']])
  expect(changeKinds(8, 1, ['-', '+', ' ', '+', '+', '+', ' ', ' '])).toEqual([[0, 'added']])
  // A stretch of several removed rows and one added row is one replacement, over every cell.
  expect(changeKinds(8, 4, [' ', '-', '-', '-', '+', ' ', ' ', ' '])).toEqual([[0, 'modified'], [1, 'modified'], [2, 'modified']])
  // A box set in between the two halves of a replaced line ('' rows) leaves it one.
  expect(changeKinds(8, 4, [' ', '-', '', '', '', '+', ' ', ' '])).toEqual([[0, 'modified'], [2, 'modified']])
})

test("the topic's mark is the left half of its cells, over the thumb, the track or a change", async () => {
  // Rows 6 to 14 of 24: cells 2 to 4.
  expect(cellsOf(24, 8, 6, 14)).toEqual([2, 3, 4])
  expect(cellsOf(24, 8, 23, 23)).toEqual([7])
  const cells = barPaint(spec({ topic: { cells: [3, 5], to: 12 }, changes: [[5, 'added']] }))
  expect(cells[3]).toMatchObject({ glyph: '▌', color: 'topic', backgroundColor: 'thumb', isTopic: true })
  expect(cells[5]).toMatchObject({ glyph: '▌', color: 'topic', backgroundColor: 'added', isTopic: true })
  // Hovered, the mark shows where a press on it goes.
  expect(previewOf(spec({ topic: { cells: [7], to: 12 } }), { at: 60, drag: null })).toBe(12)
})

// What a press opened, brought into view: a window of 10 rows starting at row 20 (it shows
// rows 20 to 29), the row pressed at 27.
test('what opened past the foot moves the window down the least that shows its last row', async () => {
  // Opened to row 32: its last row at the window's foot, the window from 23.
  expect(revealedAt(20, 32 - 10 + 1, 27, 100)).toBe(23)
})

test('what opened taller than the window shows from the row pressed, at the top', async () => {
  // Opened to row 45: its foot would want the window at 36, past the row pressed.
  expect(revealedAt(20, 45 - 10 + 1, 27, 100)).toBe(27)
})

test('what opened and already shows moves nothing', async () => {
  expect(revealedAt(20, 29 - 10 + 1, 27, 100)).toBe(20)
  expect(revealedAt(20, 25 - 10 + 1, 21, 100)).toBe(20)
})

test('the window never goes past its last start, nor up, nor anywhere where it cannot scroll', async () => {
  expect(revealedAt(20, 36, 27, 22)).toBe(22)
  // The row pressed above the window (cut at its top): the window does not go up to it.
  expect(revealedAt(20, 25, 18, 100)).toBe(20)
  // An area that fits: its last start is its first.
  expect(revealedAt(0, 4, 2, 0)).toBe(0)
})
