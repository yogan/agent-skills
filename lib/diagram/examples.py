"""The reference corpus: one MR scenario drawn six ways.

These are the diagrams the engine decision was measured on — a presence/collaboration
feature added to a document editor (new WebSocket gateway, Redis fan-out, Postgres, k8s).
Each one is the spec form of a diagram whose rendered size and contrast were measured by
hand, so they double as the fixtures that prove a change to the emitter has not moved
anything: `test_reference.py` renders all six and holds them to the gates.

They are also the clearest documentation of the spec format there is, which is why they
live here rather than inside the test file — the `visualize` skill's playbook can point at
a worked example of each kind instead of describing one.

Every one of them observes the content limits: 5 states, 6 messages, tables showing only
the columns the change touches. That is not incidental. d2 cannot compact a diagram after
the fact, so these are what "small enough to render legibly" actually looks like.

They all carry `note` callouts because they describe an MR — marking what it changed is the
case notes exist for. A plain overview should have none; see skills/visualize/SKILL.md.
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
                 "note": "new service", "near": "center-right"},
            ]},
            {"id": "redis", "label": "Redis", "role": "cache", "shape": "cylinder",
             "note": "now fans out presence", "near": "center-right"},
        ]},
        {"id": "pg", "label": "PostgreSQL", "role": "store", "shape": "cylinder"},
        {"id": "idp", "label": "OIDC provider", "role": "ext", "shape": "hexagon"},
    ],
    "edges": [
        {"from": "browser.editor", "to": "k8s.api.pod", "label": "GraphQL"},
        {"from": "browser.wsc", "to": "k8s.presence.pod", "label": "WebSocket"},
        {"from": "k8s.api.pod", "to": "pg"},
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
    "participants": [
        {"id": "editor", "label": "Editor", "role": "client"},
        {"id": "api", "label": "GraphQL API", "role": "svc"},
        {"id": "gw", "label": "Presence Gateway", "role": "svc",
         "note": "new in this MR", "near": "bottom-right"},
        {"id": "redis", "label": "Redis", "role": "cache"},
    ],
    "messages": [
        {"from": "editor", "to": "api", "label": "mutation joinDocument(id: 42)"},
        {"from": "api", "to": "editor", "label": "presenceToken (60s TTL)"},
        {"from": "editor", "to": "gw", "label": "WS upgrade · Bearer …"},
        {"from": "gw", "to": "gw", "label": "authenticate(token)"},
        {"from": "gw", "to": "redis", "label": "SUBSCRIBE doc:42"},
        {"from": "gw", "to": "editor", "label": "presence.update on peer join"},
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
         "note": "new table", "near": "top-right", "columns": [
             {"name": "id", "type": "uuid", "key": "pk"},
             {"name": "document_id", "type": "uuid", "key": "fk"},
             {"name": "user_id", "type": "uuid", "key": "fk"},
             {"name": "socket_id", "type": "text"},
             {"name": "last_seen_at", "type": "timestamptz"},
         ]},
    ],
    "edges": [
        {"from": "documents.owner_id", "to": "users.id", "label": "belongs to"},
        {"from": "presence_sessions.document_id", "to": "documents.id",
         "label": "1 doc : n sessions"},
        {"from": "presence_sessions.user_id", "to": "users.id",
         "label": "1 user : n sessions"},
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
         "note": "new interface", "near": "bottom-left", "members": [
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
        {"id": "connecting", "role": "client"},
        {"id": "authenticating", "role": "client"},
        {"id": "live", "role": "store"},
        {"id": "backoff", "label": "reconnect backoff", "role": "cache",
         "note": "new retry path", "near": "bottom-left"},
        {"id": "closed", "role": "ext"},
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

# Every board is a DIFFERENT topology, which is the only thing that justifies animating:
# what the reader learns is the ORDER of the migration and what is live at each moment.
# One static diagram cannot say "both paths run at once, then the old one is removed".
STEPS = {
    "kind": "steps",
    "title": "Zero-downtime cutover to the gateway",
    "direction": "right",
    "caption": "phase 1 of 4 — today: polling only",
    "nodes": [
        {"id": "editor", "label": "Editor", "role": "client"},
        {"id": "api", "label": "GraphQL API", "role": "svc"},
        {"id": "pg", "label": "PostgreSQL", "role": "store", "shape": "cylinder"},
    ],
    "edges": [
        {"from": "editor", "to": "api", "label": "poll every 5s"},
        {"from": "api", "to": "pg"},
    ],
    "steps": [
        {"emphasize_edges": [{"from": "editor", "to": "api"}]},
        {"caption": "phase 2 of 4 — deploy gateway, no traffic yet",
         "add_nodes": [
             {"id": "gw", "label": "Presence Gateway", "role": "svc", "new": True},
             {"id": "redis", "label": "Redis", "role": "cache", "shape": "cylinder",
              "new": True},
         ],
         "add_edges": [
             {"from": "gw", "to": "redis", "label": "subscribe"},
             {"from": "gw", "to": "pg", "label": "upsert"},
         ]},
        {"caption": "phase 3 of 4 — dual-run, 10% of clients on WebSocket",
         "add_edges": [{"from": "editor", "to": "gw", "label": "WebSocket 10%"}],
         "emphasize_edges": [{"from": "editor", "to": "gw"}]},
        {"caption": "phase 4 of 4 — cutover complete, polling path removed",
         "relabel_edges": [{"from": "editor", "to": "gw", "label": "WebSocket 100%"}],
         "remove_edges": [{"from": "editor", "to": "api"}]},
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
    "animated": STEPS,
}
