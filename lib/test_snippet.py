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


if __name__ == "__main__":
    unittest.main(verbosity=2)
