#!/usr/bin/env python3
"""Nerd Font glyphs by name, from the font's own table, never from memory.

    python3 lib/glyphs.py md-check md-wrench   # each name's codepoint and glyph
    python3 lib/glyphs.py --search wrench      # every name containing it, drawn

The table is Nerd Fonts' `glyphnames.json` at the version `lib/ui.py`'s icons were taken
from, fetched once and kept in the user's cache (half a megabyte, not worth carrying in the
repo). The owner's terminal is known to draw the Material Design set (`md-*`); offer those
first.

Codepoints guessed from memory were wrong often enough that sessions went looking for this
file on the whole disk, and once read it from another session's scratchpad.
"""
import json
import os
import sys
import urllib.request

VERSION = "3.5.1"
URL = f"https://raw.githubusercontent.com/ryanoasis/nerd-fonts/v{VERSION}/glyphnames.json"
CACHE = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
                     "agent-skills", f"glyphnames-{VERSION}.json")


def table():
    """name -> {"char", "code"}, fetched into the cache the first time."""
    if not os.path.isfile(CACHE):
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        with urllib.request.urlopen(URL, timeout=30) as r:
            data = r.read()
        json.loads(data)                       # a broken download is never cached
        tmp = CACHE + ".part"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, CACHE)
    with open(CACHE, encoding="utf-8") as fh:
        return {k: v for k, v in json.load(fh).items() if k != "METADATA"}


def line(name, glyph):
    return f"{glyph['char']}  {name}  \\u{{{glyph['code'].upper()}}}"


def main(argv):
    if not argv:
        print(__doc__.strip().splitlines()[2:4][0].strip())
        return 2
    glyphs = table()
    if argv[0] == "--search":
        hits = sorted(n for n in glyphs if any(w in n for w in argv[1:]))
        for n in hits:
            print(line(n, glyphs[n]))
        return 0 if hits else 1
    missing = 0
    for name in argv:
        if name in glyphs:
            print(line(name, glyphs[name]))
        else:
            print(f"no glyph named {name} in Nerd Fonts {VERSION}", file=sys.stderr)
            missing += 1
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
