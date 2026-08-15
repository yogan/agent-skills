"""The second corpus: this repository's own diagram engine, drawn five ways.

`examples.py` is one scenario authored by hand while the renderer was being built, which
makes it a fixture the renderer has been tuned against. That is exactly what a regression
corpus should not be on its own: a layout change that improves the reference architecture and
ruins every other architecture diagram passes with the reference alone.

So this is a second real-world case per kind, and its provenance is the point — these five are
the transcription of a `/explain-branch` run on the branch that built the `visualize` skill,
authored by the model with no human steering the geometry. Every flaw the first reader found
in that output is reproducible from here:

  * `ARCHITECTURE` — edge labels landing on the horizontal leg of an orthogonal route,
    masking the whole leg out of the drawing.
  * `ER` — a cardinality centred on a vertical leg 50px from the `nodes` table, so its left
    half runs into the table.
  * `SEQUENCE` — an `outcome` on the one return message, in a flow with no failure path for
    it to be distinguished from.
  * `STATE` — a callout that settles on top of the start marker, which the placement search
    prices at almost nothing.
  * `CLASS` — a second stereotype hand-written as a member row, and ten members where the
    limit is eight.

They are kept AS AUTHORED, including what is wrong with them. Fixing the specs here would
delete the evidence: a renderer that only looks good on specs somebody cleaned up first is
the thing this file exists to catch.
"""

# Two containers and two leaf tools. The interesting geometry is the four edges that leave a
# container and enter another one's child, which is where ELK produces a route with both a
# vertical and a horizontal leg and has to park a label on one of them.
ARCHITECTURE = {
    "kind": "architecture",
    "title": "How a figure gets drawn",
    "nodes": [
        {"id": "skills", "label": "skills", "children": [
            {"id": "explain", "label": "explain-diff / explain-branch", "role": "svc"},
            {"id": "viz", "label": "visualize", "role": "svc"},
        ]},
        {"id": "lib", "label": "lib/diagram", "children": [
            {"id": "fig", "label": "figure.draw()", "role": "svc",
             "note": "the only entry point"},
            {"id": "parts", "label": "spec · d2 · render · place",
             "detail": "vocabulary, emitter, layout, anchors", "role": "svc"},
            {"id": "gates", "label": "gates",
             "detail": "size · contrast · theming · clipping", "role": "svc"},
        ]},
        {"id": "d2", "label": "d2 0.8.1", "role": "ext", "shape": "hexagon"},
        {"id": "chrome", "label": "headless Chrome", "role": "ext", "shape": "hexagon"},
    ],
    "edges": [
        {"from": "skills.explain", "to": "lib.fig", "label": 'draw(target="embed")'},
        {"from": "skills.viz", "to": "lib.fig", "label": 'draw(target="file")'},
        {"from": "lib.fig", "to": "lib.parts", "label": "one drawing, decided once"},
        {"from": "lib.fig", "to": "lib.gates", "label": "the target picks the set"},
        {"from": "lib.parts", "to": "d2", "label": "compile"},
        {"from": "lib.parts", "to": "chrome", "label": "measure every anchor"},
        {"from": "lib.gates", "to": "chrome", "label": "clipping, once per document"},
    ],
}

# The spec format described as if it were a schema. `nodes.parent -> nodes.id` is the case
# worth having: a self-referential foreign key routes out of the bottom of the table, around
# it, and back into the top, which gives the label a long vertical leg that runs close to the
# table it belongs to.
ER = {
    "kind": "er",
    "title": "What a spec holds",
    "tables": [
        {"id": "spec", "role": "store", "columns": [
            {"name": "kind", "type": "enum of 5", "key": "pk"},
            {"name": "title / slug", "type": "text"},
            {"name": "direction", "type": "not accepted"},
        ]},
        {"id": "nodes", "role": "store", "columns": [
            {"name": "id", "type": "text", "key": "pk"},
            {"name": "label", "type": "text"},
            {"name": "role", "type": "enum per kind"},
            {"name": "shape", "type": "enum of 14"},
            {"name": "parent", "type": "id", "key": "fk"},
        ]},
        {"id": "edges", "role": "store", "columns": [
            {"name": "from", "type": "id", "key": "fk"},
            {"name": "to", "type": "id", "key": "fk"},
            {"name": "label", "type": "text"},
            {"name": "dashed", "type": "bool"},
        ]},
        {"id": "note", "role": "store", "columns": [
            {"name": "note", "type": "2–4 words"},
            {"name": "near", "type": "measured, not set"},
            {"name": "new", "type": "bool"},
        ]},
    ],
    "edges": [
        {"from": "nodes", "to": "spec", "label": "n nodes : 1 spec"},
        {"from": "nodes.parent", "to": "nodes.id", "label": "n children : 1 container"},
        {"from": "edges.from", "to": "nodes.id", "label": "n edge ends : 1 node id"},
        {"from": "note", "to": "nodes", "label": "0..1 note : 1 box"},
    ],
}

# Seven messages, which is the limit, and one `outcome` on the return. Three lanes call out to
# two tools, so the messages fan rightward and then one comes all the way back.
SEQUENCE = {
    "kind": "sequence",
    "title": "One call to figure.draw()",
    "participants": [
        {"id": "cli", "label": "visualize.py", "group": "skill"},
        {"id": "fig", "label": "figure.draw()", "group": "library"},
        {"id": "draw", "label": "renderer", "detail": "render.py · place.py",
         "group": "library"},
        {"id": "d2", "label": "d2", "group": "tools"},
        {"id": "chrome", "label": "Chrome", "group": "tools"},
    ],
    "messages": [
        {"from": "cli", "to": "fig", "label": 'draw(specs, "file")'},
        {"from": "fig", "to": "fig", "label": "validate(spec)"},
        {"from": "fig", "to": "draw", "label": "choose_drawing()"},
        {"from": "draw", "to": "d2", "label": "compile each rung"},
        {"from": "draw", "to": "chrome", "label": "measure every anchor"},
        {"from": "fig", "to": "chrome", "label": "clipping, one launch"},
        {"from": "fig", "to": "cli", "label": "Figure(svg, results)", "outcome": "ok"},
    ],
}

# The spacing ladder as a machine. Wide and short — 850x211 as first drawn — which puts the
# start marker, the callout and a long horizontal edge label in the same strip of canvas.
STATE = {
    "kind": "state",
    "title": "The layer-spacing ladder",
    "states": [
        {"id": "tight", "label": "ELK spacing 15", "role": "working", "start": True,
         "note": "no browser: stays here"},
        {"id": "wider", "label": "spacing 30", "role": "transient"},
        {"id": "widest", "label": "spacing 40", "role": "transient"},
        {"id": "ships", "label": "this drawing ships", "role": "steady"},
    ],
    "transitions": [
        {"from": "tight", "to": "ships", "label": "nothing hidden"},
        {"from": "tight", "to": "wider", "label": "a label sits on a border"},
        {"from": "wider", "to": "ships", "label": "nothing hidden"},
        {"from": "wider", "to": "widest", "label": "still hidden"},
        {"from": "widest", "to": "ships", "label": "ladder exhausted"},
    ],
}

# Authored badly on purpose — see the module docstring. `«namedtuple»` is a member row, not a
# stereotype, and `figure` carries ten members against a limit of eight.
CLASS = {
    "kind": "class",
    "title": "What draw() hands back",
    "classes": [
        {"id": "figure", "stereotype": "module", "members": [
            {"name": "+ draw(specs, target)", "type": "[Figure]"},
            {"name": "«namedtuple»"},
            {"name": "name", "type": "str"},
            {"name": "svg", "type": "str"},
            {"name": "results", "type": "[Result]"},
            {"name": "placement", "type": "[str]"},
            {"name": "problems", "type": "[str]"},
            {"name": "advice", "type": "[str]"},
            {"name": "blocked", "type": "[str]"},
            {"name": "+ ok", "type": "bool"},
        ]},
        {"id": "Result", "members": [
            {"name": "name", "type": "str"},
            {"name": "gate", "type": "str"},
            {"name": "problems", "type": "[str]"},
            {"name": "+ ok", "type": "bool"},
        ]},
        {"id": "SpecError", "members": [
            {"name": "+ str(exc)", "type": "what to fix"},
        ]},
        {"id": "GateError", "members": [
            {"name": "+ str(exc)", "type": "why it could not run"},
        ]},
    ],
    "edges": [
        {"from": "figure", "to": "figure", "label": "one per spec"},
        {"from": "figure", "to": "Result", "label": "holds as results"},
        {"from": "figure.+ draw(specs, target)", "to": "SpecError", "label": "raises",
         "dashed": True},
        {"from": "GateError", "to": "figure", "label": "caught → blocked", "dashed": True},
    ],
}

REPO = {
    "arch": ARCHITECTURE,
    "sequence": SEQUENCE,
    "er": ER,
    "class": CLASS,
    "state": STATE,
}
