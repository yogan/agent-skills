#!/usr/bin/env python3
"""Runs every mod's own checks as part of the repo's suite: its tests (`claude plugin test`),
its type check, and the validation of it and of the repo's marketplace.

Run: `python3 mods/test_mods.py`. A mod's tests are TypeScript, run by the engine itself on
every surface with no model; this file is the bridge that lets `run_tests.py`, and so the
commit hook, see them. The engine's tests load a file with a type error and fail only where
it breaks at runtime, so the type check is its own case. A check whose tool is missing says
so as a skip rather than passing.
"""
import json
import os
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MODS = sorted(d for d in os.listdir(HERE)
              if os.path.isfile(os.path.join(HERE, d, ".claude-plugin", "plugin.json")))

# One TypeScript for every machine, as the commit hook pins ruff. Bump it on its own commit.
TYPESCRIPT = "5.6.3"

# Marketplace names an install refused as reserved for Anthropic's own marketplaces.
# `claude plugin validate` passes them, so the first to hear of it was the person installing.
RESERVED = ("agent-skills",)


def _run(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=REPO)


class ModTests(unittest.TestCase):
    def test_there_is_a_mod(self):
        self.assertTrue(MODS)

    def test_each_mods_own_tests_pass(self):
        if not shutil.which("claude"):
            self.skipTest("claude is not installed: the mods' tests cannot run here")
        for mod in MODS:
            with self.subTest(mod=mod):
                r = _run("claude", "plugin", "test", os.path.join(HERE, mod))
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_each_mod_type_checks(self):
        """The engine's types are copied in from the plugin-authoring skill and kept out of
        git, so a fresh checkout or worktree has none until one is copied there."""
        if not shutil.which("npx"):
            self.skipTest("npx is not installed: the mods cannot be type-checked here")
        for mod in MODS:
            with self.subTest(mod=mod):
                types = os.path.join(HERE, mod, ".claude-plugin", "types", "tsconfig.json")
                if not os.path.isfile(types):
                    self.skipTest(f"{mod} has no engine types in .claude-plugin/types: copy them "
                                  "from a checkout that has them (or load the plugin-authoring "
                                  "skill) to type-check it")
                r = _run("npx", "-y", "-p", f"typescript@{TYPESCRIPT}", "tsc", "--noEmit", "-p",
                         os.path.join(HERE, mod))
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_each_mod_and_the_marketplace_validate(self):
        if not shutil.which("claude"):
            self.skipTest("claude is not installed: nothing can validate the mods here")
        targets = [os.path.join(HERE, m) for m in MODS]
        market = os.path.join(REPO, ".claude-plugin", "marketplace.json")
        if os.path.isfile(market):
            targets.append(market)
        for target in targets:
            with self.subTest(target=os.path.relpath(target, REPO)):
                r = _run("claude", "plugin", "validate", target)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_the_marketplace_name_is_not_reserved(self):
        market = os.path.join(REPO, ".claude-plugin", "marketplace.json")
        if not os.path.isfile(market):
            self.skipTest("this repo has no marketplace")
        with open(market, encoding="utf-8") as fh:
            name = json.load(fh).get("name")
        self.assertNotIn(name, RESERVED)


if __name__ == "__main__":
    unittest.main()
