"""Diagram rendering shared by the explainer skills and the `visualize` skill.

The public surface is deliberately small: describe a diagram as a spec (`spec.py`),
turn it into an SVG (`render.py`), and check the result against the gates
(`gates/`). Nothing here knows about any skill, and no skill needs another skill
installed to use it — see CLAUDE.md on `lib/`.

D2 is the rendering engine. `d2.py` carries the measured recipe for driving it; the
gates are what make that recipe safe to depend on, since several of the behaviours it
relies on are undocumented and could change on a d2 upgrade.
"""
