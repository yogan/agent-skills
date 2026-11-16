import { expect, test } from 'claude-code/testing'
import { barCells, changeCells, jumpOf, markedCells, thumbOf } from '../hooks/sidebar.ts'

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

test("the thumb's ends are partial blocks, from the bottom where it starts and from the top where it ends", async () => {
  const cells = barCells(24, 8, 5, 8)
  expect(cells.map(c => c.glyph).join('')).toBe(' ▃██\u{1FB82}   ')
  expect(cells.map(c => c.at)).toEqual(['above', 'thumb', 'thumb', 'thumb', 'thumb', 'below', 'below', 'below'])
})

test('a mark stands on the cell for its row, only where the row is out of the window', async () => {
  expect([...markedCells(24, 8, 5, 8, [0, 6, 23])]).toEqual([0, 7])
})

test("a file's changes stand on the cells for their rows, a removed and an added row in one cell both", async () => {
  // 24 rows on 8 cells, three to a cell: a change in row 4 (cell 1), one replaced in rows 9-10
  // (cell 3), one added at the end (cell 7).
  const signs = Array.from({ length: 24 }, () => ' ')
  signs[4] = '+'
  signs[9] = '-'
  signs[10] = '+'
  signs[23] = '+'
  expect([...changeCells(24, 8, signs)]).toEqual([
    [1, { isRemoved: false, isAdded: true }],
    [3, { isRemoved: true, isAdded: true }],
    [7, { isRemoved: false, isAdded: true }],
  ])
})

test('a press on the track jumps there: the top cell to the top, the bottom one to the end, between to its part', async () => {
  // 8 of 80 rows on a track of 8 cells: the window can go 72 rows.
  expect(jumpOf(80, 8, 8, 0)).toBe(0)
  expect(jumpOf(80, 8, 8, 7)).toBe(72)
  expect(jumpOf(80, 8, 8, 4)).toBe(41)        // four sevenths of the way the window can go
})
