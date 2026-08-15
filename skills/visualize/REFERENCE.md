# The diagram spec

JSON. One spec is one diagram. See [SKILL.md](SKILL.md) for *which* kind to use and how much
to put in one — this file is only the field list.

The output is a standalone image, so there is no page furniture: no heading, no caption. What
the diagram says has to be inside the diagram. The file is named from `title`/`slug`, which is
how you find it again later.

Validation is strict and the errors are specific, so the fastest way to check a spec is to
render it. Two things it will refuse, both of which otherwise produce a diagram that looks
fine and is wrong:

- an edge pointing at an id that does not exist — D2 invents an empty node rather than
  failing, so the typo ships as a stray blank box;
- `near` without a `note`, which is almost always a misspelt `note` key and means the callout
  you wanted is silently missing.

## Common to every kind

| field | | |
|---|---|---|
| `kind` | required | `architecture` · `sequence` · `er` · `class` · `state` |
| `title` | optional | names the output file, and the ids inside the SVG |
| `slug` | optional | same, and wins over `title` when both are set |
| `direction` | **not accepted** | The renderer decides. For an embedded figure it draws the diagram both ways, measures each, and keeps the one that stays legible with less height — wrapping long edge labels if that is what makes the wider layout fit. A spec carrying one is rejected, because pinning it also switches off the spacing escalation that keeps text readable, and the diagram comes out quietly worse: the reference ER pinned to its own measured direction renders a cardinality on top of a table. A standalone image is laid out wide by default, having no column to fit into. (`sequence` ignores it entirely — d2's sequence engine has its own layout.) |

### Roles

Every box takes a `role`, which is what it *is*, not what colour you want:
`client` · `svc` · `store` · `cache` · `ext` · `neutral` (the default).

**A `state` has its own set**, because a state is not a datastore or a cache and tagging it as
one just picks a colour: `working` · `steady` · `transient` · `stuck` · `terminal` · `neutral`.
These are rejected on any other kind, and the architectural roles are rejected on a state.
`stuck` is for a state something ended up in that it should not be in, and which is not the end
— the one whose colour (purple) has no convention behind it, so its **label** has to name the
problem. Use it when two states would otherwise share a role the reader must tell apart.

### Notes

Any box, table, class or sequence lane may carry:

| field | | |
|---|---|---|
| `note` | | 2–4 words, becomes a permanently visible callout |
| `near` | optional | one of `top-left` `top-center` `top-right` `center-left` `center-right` `bottom-left` `bottom-center` `bottom-right` |
| `new` | optional | `true` marks the box a change **added**: an accent border, or an accent fill on a table, which has no border of its own to colour. Requires a `note` — the accent has no legend, so it cannot say what is new on its own, and with one the note shrinks to a word: `"new"`, `"added"`. Not allowed on a container, where it would be silently ignored. |

**Most diagrams should have no notes at all** — see [SKILL.md](SKILL.md) on when one earns its
place. Note that no example in this file uses one, deliberately: a callout is the exception, not
part of the normal shape of a spec.

A note must point at something the reader would otherwise **miss or misread**. It must not
describe what a box already is:

```json
{"id": "document_claims", "role": "cache", "note": "leased, expiring"}   // NO — a caption
{"id": "email_documents", "role": "svc", "note": "join table"}           // NO — the FKs say so
{"id": "documents", "role": "store", "note": "gains a revision column"}  // yes — a change
{"id": "sessions", "role": "store", "note": "no index on user_id"}       // yes — a flaw
```

If the fact is intrinsic to the thing, it belongs in the label or is already visible in the
columns. If it is a change, a flaw, or the answer to the question that was asked, it is a note.

**Leave `near` out.** The renderer measures all eight positions in a browser and picks the
one that is not clipped and covers least. A hand-picked anchor is a starting point for that
search, not an instruction — and hand-picked anchors have measurably lost to it before.

## `architecture`

```json
{
  "kind": "architecture",
  "title": "Presence gateway in context",
  "nodes": [
    {"id": "browser", "label": "Browser", "children": [
      {"id": "editor", "label": "Editor", "role": "client"}
    ]},
    {"id": "pg", "label": "PostgreSQL", "role": "store", "shape": "cylinder"},
    {"id": "idp", "label": "OIDC provider", "role": "ext", "shape": "hexagon"}
  ],
  "edges": [
    {"from": "browser.editor", "to": "pg", "label": "GraphQL"}
  ]
}
```

- **`children` makes a container.** A container is styled as a group and must not also carry a
  `role`.
- **Nested ids are addressed by dotted path** — `browser.editor`, `k8s.api.pod`. The bare name
  is not addressable, which is deliberate: it is how two containers can each hold a `pod`.
- `shape` (optional, leaf nodes): `rectangle` (default) · `cylinder` · `hexagon` · `queue` ·
  `document` · `stored_data` · `person` · `diamond` · `oval` · `circle` · `package` · `step` ·
  `page` · `cloud`. Anything else is rejected; `code` in particular carries its own colour
  scheme that the theming cannot follow.
- `edges`: `from`, `to`, optional `label`, optional `dashed`.
- **Label every edge.** An unlabelled arrow only repeats what the line already says. Name what
  crosses it — a protocol, a call, a direction of data. The renderer warns when one has none.
- `bidirectional` (optional, architecture only): draws an arrowhead at both ends, for traffic
  that genuinely crosses both ways — `read · write` against a database, a request and its
  reply. Not for "they talk to each other" in general: if one side initiates, one arrow is the
  truer picture. A transition, a message and a foreign key all go one way by definition, so
  this is rejected on the other kinds.
- `detail` (optional, any box): smaller muted lines under the name. On an architecture box it
  names what is inside an area that has been collapsed to one node — see `--overview` in
  SKILL.md, which fills it in for you, most-connected member first and `+N more` for the rest.

## `sequence`

```json
{
  "kind": "sequence",
  "participants": [
    {"id": "editor", "label": "Editor", "group": "browser"},
    {"id": "routing", "label": "FE routing", "group": "browser",
     "detail": "AppRoutes / react-router"},
    {"id": "gw", "label": "Presence Gateway", "group": "server"}
  ],
  "messages": [
    {"from": "editor", "to": "gw", "label": "WS upgrade"},
    {"from": "gw", "to": "gw", "label": "authenticate(token)"},
    {"from": "gw", "to": "editor", "label": "on peer join", "push": true}
  ]
}
```

- **Participants become columns, in the order you write them.** Put them in the order the
  reader should scan, usually outside-in.
- `group` (optional, sequence only): which side of the wire this lane is on — `browser` /
  `server`, `cli` / `daemon`, whatever the system's own division is. Lanes in a group share one
  colour, assigned in order of first appearance, so you choose which side gets which by ordering
  your lanes. **Use this instead of `role` here**: per-lane roles make one colour per kind of
  thing, which is six colours saying nothing a reader of a flow asked about. A lane carrying both
  is rejected. Keeping a group's lanes adjacent usually reads best; the renderer warns when one is
  split, but a long arrow can be the worse trade — see SKILL.md. **The renderer names the groups
  under the diagram**, each in its own colour over the lanes it covers, so the reader is never
  left decoding a hue — which means the group name is read by a human and should be a word they
  know (`browser`, `server`, `worker`), not an internal one.
- `detail` (optional): a smaller second line under the label. For a lane that is a *subsystem*
  rather than one module — name the lane at the altitude of the question, and put the real
  module names here so the reader can still grep for them. Every participant box grows to the
  taller size when any one of them has a `detail`, so the row stays even. See SKILL.md on when
  to raise a lane's altitude, and on the two things never to merge into one.
- A message from a participant to itself is fine and renders as a self-call.
- `push` (optional, sequence only): the receiver never asked for this one — a server push, an
  event, a subscription firing. Renders dashed with an open arrowhead, and its label should say
  what triggered it. An ordinary call you are waiting on is not a push.
- `outcome` (optional, sequence only): `error` draws the message red, `ok` draws it green. For a
  flow whose *point* is that it can end two ways — the 409 that turns a request back against the
  200 that does not. Mark the two or three messages that are the fork, not every message: if
  everything is coloured, nothing is. A `push` may carry one too; the dash says nobody asked for
  it, the colour says how it went.
- **An arrow is a call.** If a participant only *reacts* to state changing elsewhere, put that
  state in the diagram as a participant — then the write and the read are both real calls. See
  SKILL.md; this is the most common way a sequence diagram ends up asserting something false.
- Participants cannot nest.
- ≤7 messages, and this one is a judgement rather than a size limit: the renderer re-stacks
  the rows after D2 has laid them out, so height is not what a long sequence costs you — the
  reader having to hold eight steps in their head is.

## `er`

```json
{
  "kind": "er",
  "tables": [
    {"id": "users", "role": "store", "columns": [
      {"name": "id", "type": "uuid", "key": "pk"},
      {"name": "email", "type": "text"}
    ]},
    {"id": "documents", "role": "store",
     "columns": [{"name": "owner_id", "type": "uuid", "key": "fk"}]}
  ],
  "edges": [
    {"from": "documents.owner_id", "to": "users.id", "label": "n docs : 1 owner"}
  ]
}
```

- `key` (optional): `pk` · `fk` · `unique`.
- **An edge can point at a single column** (`documents.owner_id`) or at the whole table
  (`documents`). Column-level is usually what you want for a foreign key, and it shows: the
  arrow leaves that column's row. For a long time it was silently drawn table-to-table, because
  the layout engine of the day discarded the column without a word — worth knowing if you are
  reading an older figure and wondering why it never matched its spec.
- **Label every edge with a cardinality that names its entities.** A bare ratio is not enough:
  `n : 1` makes the reader work out which end is which and then map it back to the table names.
  Spell it out so the label reads on its own.

  | | |
  |---|---|
  | `"n : 1"`, `"1 : 1"`, `"1 : n"` | **no.** Ambiguous without tracing the arrow. |
  | `"n edits : 1 doc"` | yes |
  | `"0..1 claim : 1 doc"` | yes — and says the 1:1 is optional |
  | `"n : m via email_documents"` | yes — names the join table |

  The renderer warns you when a label is a bare ratio.
- **Write it in the direction the arrow goes.** The table the edge LEAVES goes on the left of
  the colon, the table it points at on the right — `presence_sessions -> documents` is
  `"n sessions : 1 doc"`, not `"1 doc : n sessions"`. Both are true; only one of them agrees
  with the picture. Written backwards, the reader has to reconcile a label that reads
  right-to-left against an arrow that flies left-to-right, which is work the label exists to
  save them. The renderer warns you when it can tell a label is reversed.
- `edges` is optional — a single table with no relationships is a legitimate diagram.
- Show the columns the question is about. Not all of them.

## `class`

```json
{
  "kind": "class",
  "classes": [
    {"id": "Broadcaster", "role": "ext", "stereotype": "interface",
     "members": [{"name": "+ publish()", "type": "void"}]},
    {"id": "RedisFanout", "role": "cache",
     "members": [{"name": "+ publish()", "type": "void"}]}
  ],
  "edges": [
    {"from": "RedisFanout", "to": "Broadcaster", "label": "implements", "dashed": true}
  ]
}
```

- `members`: `name` is free text — write the signature as a reader expects it
  (`+ handleUpgrade()`, `- authenticate()`); `type` is the return or field type.
- `stereotype` (optional, e.g. `interface`) adds a «guillemet» row and a dashed outline.
- Use `dashed: true` for "implements" / "uses" and a solid edge for ownership.
- **An edge may start at one member**, written `ClassName.member name` exactly as the member
  appears: `"from": "SupplierLookupService.+ lookup(id, data?)"`. Use it when the whole box is
  not the answer — `raises` leaving a class with four methods does not say which one raises,
  and that was a real complaint about a real figure. `builds` and `returns` are already
  traceable through the member types, so they rarely need it.
  A member whose name contains a `.` cannot be addressed (the path is split on dots).

## `state`

```json
{
  "kind": "state",
  "states": [
    {"id": "live", "role": "steady", "start": true},
    {"id": "backoff", "label": "reconnect backoff", "role": "transient"},
    {"id": "closed", "role": "terminal"}
  ],
  "transitions": [
    {"from": "live", "to": "backoff", "label": "transport error"},
    {"from": "backoff", "to": "closed", "label": "max attempts"}
  ]
}
```

- **`start: true` on the state the machine begins in**, which the renderer draws as UML's
  filled dot with an arrow into that state. Mark one — nothing else says where to start
  reading: being written first, or being drawn at the top, is an accident of layout, and a
  state with no incoming transition looks like every other state. At most one per diagram
  (two is a hard error); none is an advisory, not a refusal. The dot goes in the margin
  *beside* the state and normally costs no canvas at all.
- Label every transition with what causes it. An unlabelled state machine is a shape.
- ≤6 states. If there are more, the diagram is answering more than one question — split it.
- **Keep transitions to about two per state.** Past that the same trigger is usually drawn
  several times, and repeated labels are what make a state diagram unreadable — not the number
  of states. If one state is reachable from everywhere on the same trigger, that is *one* fact:
  draw the path that matters and put "from any state" in its note.
- States cannot nest.
