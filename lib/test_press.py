import hashlib
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import press  # noqa: E402


class TestClaimed(unittest.TestCase):
    """A press is a secret whose hash the mod left as a file: only that proves one, and once."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.dir = folder.name
        mock.patch.object(press, "PRESSES", self.dir).start()
        mock.patch.dict(press._claimed, clear=True).start()
        self.addCleanup(mock.patch.stopall)

    def grant(self, secret):
        name = hashlib.sha256(secret.encode()).hexdigest()
        open(os.path.join(self.dir, name), "w").close()
        return name

    def claimed(self, secret):
        press._claimed.clear()                 # each a process of its own
        with mock.patch.dict(os.environ, {press.ENV: secret}):
            return press.claimed()

    def test_a_secret_the_mod_left_a_file_for_is_a_press_and_is_spent(self):
        name = self.grant("9f2c")
        self.assertTrue(self.claimed("9f2c"))
        self.assertFalse(os.path.exists(os.path.join(self.dir, name)))
        self.assertFalse(self.claimed("9f2c"))

    def test_a_value_the_model_could_set_is_no_press(self):
        self.grant("9f2c")
        for value in ("1", "", "9f2", hashlib.sha256(b"9f2c").hexdigest()):
            with self.subTest(value):
                self.assertFalse(self.claimed(value))

    def test_no_variable_is_no_press(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(press.claimed())

    def test_the_answer_holds_for_the_rest_of_the_process(self):
        self.grant("9f2c")
        with mock.patch.dict(os.environ, {press.ENV: "9f2c"}):
            self.assertTrue(press.claimed())
            self.assertTrue(press.claimed())

    def test_a_press_never_claimed_is_cleared_once_stale_and_nothing_else_is(self):
        old = self.grant("old")
        fresh = self.grant("fresh")
        other = os.path.join(self.dir, "notes.txt")
        open(other, "w").close()
        then = time.time() - press.STALE_SECONDS - 60
        os.utime(os.path.join(self.dir, old), (then, then))
        os.utime(other, (then, then))
        press._clear_stale()
        self.assertEqual(sorted(os.listdir(self.dir)), sorted([fresh, "notes.txt"]))


if __name__ == "__main__":
    unittest.main()
