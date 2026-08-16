"""The reference corpus: one MR scenario drawn five ways.

These are the diagrams the engine decision was measured on — a presence/collaboration
feature added to a document editor (new WebSocket gateway, Redis fan-out, Postgres, k8s).
Each one is the spec form of a diagram whose rendered size and contrast were measured by
hand, so they double as the fixtures that prove a change to the emitter has not moved
anything: `test_reference.py` renders all five and holds them to the gates.

They are also the clearest documentation of the spec format there is, which is why they
live here rather than inside the test file — the `visualize` skill's playbook can point at
a worked example of each kind instead of describing one.

Every one of them observes the content limits: 5 states, 6 messages, tables showing only
the columns the change touches. That is not incidental. d2 cannot compact a diagram after
the fact, so these are what "small enough to render legibly" actually looks like.

They all carry `note` callouts because they describe an MR — marking what it changed is the
case notes exist for. A plain overview should have none; see skills/visualize/SKILL.md.

Every note DOES pin a `near`, and each one is the anchor `place.place` measures for it. That
looks like it contradicts REFERENCE.md, which tells an author to leave `near` out, and it does
not: an author's spec is always rendered through the placement pass, and these are also
rendered WITHOUT it, by every fast test in the suite. The pin is the fallback for that path.

It was removed from all seven and measured, because "a pin the pass overrides documents the
wrong thing" is a good argument that turns out to be answering the wrong question. Unpinned,
three of the five bury text at d2's `top-center` default — the architecture puts a callout
across 59% of `presence deploy ×2` and 88% of `publish`, the class diagram across 165% of
`implements`. Pinned to what the pass picks, all five are clean, and the number in
`test_reference.MEASURED` becomes the geometry that actually ships instead of one no reader
ever sees.

So the rule for this file is not "pin" or "do not pin", it is: **a pin here must be the
anchor the pass measures, and the corpus must render cleanly with no pass at all.** The state
machine is the proof that this needs checking rather than assuming, twice over. It once pinned
`bottom-left`, which lay across 69% of `max attempts` in the PORTRAIT layout it had then, and
`center-left` was the only one of the eight that hid nothing.

That figure is now laid out landscape — `route.straighten` fixed the arrowheads that used to
push it to a wider rung, and the rung was the only thing keeping the wide candidate out (see
`test_reference.MEASURED`). A pin is a statement about a shape, so both anchors changed hands:
`center-left` now lands across `transport error`, and `bottom-left` — the one that used to be
the worst of the eight — is what the pass measures and picks. **A pin here does not survive a
layout change and must be re-derived rather than assumed to still hold.**
"""

ARCHITECTURE = {
    "kind": "architecture",
    "title": "Presence gateway in context",
    "nodes": [
        {"id": "browser", "label": "Browser", "children": [
            {"id": "editor", "label": "Editor", "role": "client"},
            {"id": "wsc", "label": "usePresence()", "role": "client"},
        ]},
        {"id": "k8s", "label": "Kubernetes cluster", "children": [
            {"id": "api", "label": "api deploy ×3", "children": [
                {"id": "pod", "label": "GraphQL API", "role": "svc"},
            ]},
            {"id": "presence", "label": "presence deploy ×2", "children": [
                {"id": "pod", "label": "Presence Gateway", "role": "svc",
                 "note": "new service", "near": "bottom-left"},
            ]},
            {"id": "redis", "label": "Redis", "role": "cache", "shape": "cylinder",
             "note": "now fans out presence", "near": "bottom-left"},
        ]},
        {"id": "pg", "label": "PostgreSQL", "role": "store", "shape": "cylinder"},
        {"id": "idp", "label": "OIDC provider", "role": "ext", "shape": "hexagon"},
    ],
    "edges": [
        {"from": "browser.editor", "to": "k8s.api.pod", "label": "GraphQL"},
        {"from": "browser.wsc", "to": "k8s.presence.pod", "label": "WebSocket"},
        {"from": "k8s.api.pod", "to": "pg", "label": "read · write",
         "bidirectional": True},
        {"from": "k8s.presence.pod", "to": "k8s.redis", "label": "publish"},
        {"from": "k8s.redis", "to": "k8s.presence.pod", "label": "fan-out"},
        {"from": "k8s.presence.pod", "to": "pg", "label": "upsert"},
        {"from": "k8s.api.pod", "to": "idp", "label": "verify JWT"},
    ],
}

# Six messages, not seven: a callout costs ~49px of height, which pushed this diagram over
# the viewport gate at seven, so one message was traded for the annotation. Row compaction
# has since bought that height back -- the sixth message stays as the fixture for a
# self-call sitting under a callout, which is what makes this diagram worth pinning.
SEQUENCE = {
    "kind": "sequence",
    "title": "Joining a document's presence channel",
    # Grouped, not roled: four per-lane roles made four colours that each said what a lane
    # *is*, when what a reader of a flow wants to know is which side of the wire it sits on.
    # Two groups is two colours, and the browser/server split is then visible at a glance.
    "participants": [
        {"id": "editor", "label": "Editor", "group": "browser"},
        {"id": "api", "label": "GraphQL API", "group": "server"},
        {"id": "gw", "label": "Presence Gateway", "group": "server",
         "note": "new in this MR", "near": "bottom-left"},
        {"id": "redis", "label": "Redis", "group": "server"},
    ],
    "messages": [
        {"from": "editor", "to": "api", "label": "mutation joinDocument(id: 42)"},
        {"from": "api", "to": "editor", "label": "presenceToken (60s TTL)"},
        {"from": "editor", "to": "gw", "label": "WS upgrade · Bearer …"},
        {"from": "gw", "to": "gw", "label": "authenticate(token)"},
        {"from": "gw", "to": "redis", "label": "SUBSCRIBE doc:42"},
        # A push: the editor subscribed once and is now told about someone else's arrival,
        # so this is the one message here it did not call for.
        {"from": "gw", "to": "editor", "label": "presence.update on peer join", "push": True},
    ],
}

ER = {
    "kind": "er",
    "title": "Presence sessions alongside the existing tables",
    "tables": [
        {"id": "users", "role": "store", "columns": [
            {"name": "id", "type": "uuid", "key": "pk"},
            {"name": "email", "type": "text"},
            {"name": "display_name", "type": "text"},
        ]},
        {"id": "documents", "role": "store",
         "note": "gains a revision column", "near": "top-left", "columns": [
             {"name": "id", "type": "uuid", "key": "pk"},
             {"name": "owner_id", "type": "uuid", "key": "fk"},
             {"name": "title", "type": "text"},
             {"name": "revision", "type": "bigint"},
         ]},
        {"id": "presence_sessions", "role": "svc",
         "note": "new table", "near": "top-left", "columns": [
             {"name": "id", "type": "uuid", "key": "pk"},
             {"name": "document_id", "type": "uuid", "key": "fk"},
             {"name": "user_id", "type": "uuid", "key": "fk"},
             {"name": "socket_id", "type": "text"},
             {"name": "last_seen_at", "type": "timestamptz"},
         ]},
    ],
    # A cardinality reads in the direction its arrow goes: the table the arrow LEAVES on the
    # left of the colon, the one it points at on the right. These said "1 doc : n sessions"
    # while being drawn `presence_sessions -> documents`, so the label and the picture
    # disagreed about which end was which and the reader had to reconcile them.
    "edges": [
        {"from": "documents.owner_id", "to": "users.id", "label": "belongs to"},
        {"from": "presence_sessions.document_id", "to": "documents.id",
         "label": "n sessions : 1 doc"},
        {"from": "presence_sessions.user_id", "to": "users.id",
         "label": "n sessions : 1 user"},
    ],
}

CLASS = {
    "kind": "class",
    "title": "How the gateway's types relate",
    "classes": [
        {"id": "PresenceGateway", "role": "svc", "members": [
            {"name": "+ handleUpgrade()", "type": "Socket"},
            {"name": "+ onMessage()", "type": "void"},
            {"name": "- authenticate()", "type": "Session"},
        ]},
        {"id": "SessionRegistry", "role": "svc", "members": [
            {"name": "+ add()", "type": "void"},
            {"name": "+ dropStale()", "type": "int"},
            {"name": "+ forDocument()", "type": "Session[]"},
        ]},
        {"id": "Broadcaster", "role": "ext", "stereotype": "interface",
         "note": "new interface", "near": "center-right", "members": [
             {"name": "+ publish()", "type": "void"},
             {"name": "+ subscribe()", "type": "void"},
         ]},
        {"id": "RedisFanout", "role": "cache", "members": [
            {"name": "+ publish()", "type": "void"},
        ]},
        {"id": "PresenceEvent", "role": "store", "members": [
            {"name": "userId", "type": "uuid"},
            {"name": "kind", "type": "join|move|leave"},
        ]},
    ],
    "edges": [
        {"from": "PresenceGateway", "to": "SessionRegistry", "label": "owns"},
        {"from": "PresenceGateway", "to": "Broadcaster", "label": "uses", "dashed": True},
        {"from": "RedisFanout", "to": "Broadcaster", "label": "implements", "dashed": True},
        {"from": "SessionRegistry", "to": "PresenceEvent", "label": "emits"},
    ],
}

STATE = {
    "kind": "state",
    "title": "The client socket's lifecycle",
    "states": [
        {"id": "connecting", "role": "working", "start": True},
        {"id": "authenticating", "role": "working"},
        {"id": "live", "role": "steady"},
        {"id": "backoff", "label": "reconnect backoff", "role": "transient",
         "note": "new retry path", "near": "bottom-left"},
        {"id": "closed", "role": "terminal"},
    ],
    "transitions": [
        {"from": "connecting", "to": "authenticating", "label": "socket open"},
        {"from": "authenticating", "to": "live", "label": "token ok"},
        {"from": "authenticating", "to": "closed", "label": "401 invalid"},
        {"from": "live", "to": "backoff", "label": "transport error"},
        {"from": "backoff", "to": "connecting", "label": "retry (max 30s)"},
        {"from": "backoff", "to": "closed", "label": "max attempts"},
        {"from": "live", "to": "closed", "label": "user leaves"},
    ],
}

# Keyed by the name the prototype measured them under, so the numbers in
# test_reference.py can be traced back to prototypes/diagram-stacks/.
REFERENCE = {
    "arch": ARCHITECTURE,
    "sequence": SEQUENCE,
    "er": ER,
    "class": CLASS,
    "state": STATE,
}
