#!/usr/bin/env python3
"""Tests for open_construct — see snippet.py's module docstring for the bug class this
guards against (a fixed-line-count code window starting mid multi-line string/comment).

Run: `python3 lib/test_snippet.py` (stdlib only).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.snippet import open_construct


class TestOpenConstruct(unittest.TestCase):
    def test_python_docstring_open_is_detected(self):
        lines = ['def f():', '    """doc', '    more', '    """', '    pass']
        self.assertEqual(open_construct(lines, 3), ('"""', 2))

    def test_python_docstring_closed_before_window_is_not_flagged(self):
        lines = ['def f():', '    """doc', '    more', '    """', '    pass']
        self.assertIsNone(open_construct(lines, 5))

    def test_single_quote_triple_string_is_tracked_too(self):
        lines = ["x = '''", "still open", "'''", "y = 1"]
        self.assertEqual(open_construct(lines, 2), ("'''", 1))

    def test_js_template_literal_open_is_detected(self):
        lines = ['function build() {', '  const msg = `line one',
                 '  line two', '  line three`;', '  return msg;']
        self.assertEqual(open_construct(lines, 3), ('`', 2))

    def test_inline_backtick_closed_same_line_does_not_leak(self):
        lines = ['const a = `hello`;', 'const b = 1;', 'const c = 2;']
        self.assertIsNone(open_construct(lines, 3))

    def test_block_comment_open_is_detected(self):
        lines = ['/* start', 'still inside', '*/', 'const x = 1;']
        self.assertEqual(open_construct(lines, 2), ('/*', 1))

    def test_block_comment_closed_before_window_is_not_flagged(self):
        lines = ['/* start', 'still inside', '*/', 'const x = 1;']
        self.assertIsNone(open_construct(lines, 4))

    def test_jsdoc_style_comment_is_detected(self):
        """`/**` starts with `/*`, so a JSDoc block is just a block comment to this
        scanner — the extra `*` is inert, already inside the open state."""
        lines = ['/**', ' * JSDoc comment', ' * @param x thing', ' */', 'function f(x) {}']
        self.assertEqual(open_construct(lines, 3), ('/*', 1))
        self.assertIsNone(open_construct(lines, 5))

    def test_no_construct_open_at_top_of_file(self):
        lines = ['x = 1', 'y = 2']
        self.assertIsNone(open_construct(lines, 1))
        self.assertIsNone(open_construct(lines, 2))

    def test_escaped_backtick_does_not_close_the_template_literal(self):
        """An escaped backtick inside a JS/TS template literal is a literal character,
        not the real closer — this used to be misread as the close, which then made the
        REAL closing backtick on the next real-close line look like a brand new opener."""
        lines = ['function build() {', r'  const msg = `before \` after',
                 '  still more template', '  end`;', '  return msg;', '}']
        self.assertEqual(open_construct(lines, 4), ('`', 2))
        self.assertIsNone(open_construct(lines, 5))

    def test_escaped_backslash_before_a_real_closer_still_closes(self):
        """Two backslashes are one literal escaped backslash, not an escape of the
        backtick that follows — the closer right after it is real."""
        lines = [r'const a = `text\\`;', 'const b = 1;']
        self.assertIsNone(open_construct(lines, 2))

    def test_escaped_backtick_with_no_real_close_on_that_line(self):
        lines = [r'const a = `text\`', 'still open', 'end`;']
        self.assertEqual(open_construct(lines, 2), ('`', 1))
        self.assertEqual(open_construct(lines, 3), ('`', 1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
