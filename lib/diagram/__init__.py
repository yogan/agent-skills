"""Diagram rendering shared by the explainer skills and the `visualize` skill.

The public surface is deliberately small: describe a diagram as a spec (`spec.py`),
turn it into an SVG (`render.py`), and check the result against the gates
(`gates/`). Nothing here knows about any skill, and no skill needs another skill
installed to use it — see CLAUDE.md on `lib/`.

D2 is the rendering engine. `d2.py` carries the measured recipe for driving it; the
gates are what make that recipe safe to depend on, since several of the behaviours it
relies on are undocumented and could change on a d2 upgrade.

Why D2, decided by prototype against measurable gates rather than by preference:
Mermaid lost on weight and speed (405 MB plus its own 565 MB Chromium, 3x D2's render
time), not on capability. Graphviz needs no post-processing and themes for free, but
`dot` reorders lifeline columns to minimise edge crossings, so it cannot draw a sequence
diagram at all — participants come out in the wrong order with sloped arrows. It also has
no annotation callout, no native table or class shape, and no animation. D2 costs a 35 MB
binary and real post-processing (baked-in colours, no intrinsic size, three visual roles
of a table coupled to two properties) — that cost is what the gates pay for. Graphviz ran
alongside D2 for one milestone and was then removed: two engines means two visual
languages and two theming maps, and a document mixing them looks mixed.
"""
