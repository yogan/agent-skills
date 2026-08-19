# Shared vocabulary

One agreed word per concept, so that a report, a commit message or an explanation means the
same thing to both of us. **The code may keep its own names; anything written for a person
uses the words in this file.**

The rule this exists to serve is in [CLAUDE.md](CLAUDE.md) under "Working agreement". Short
version: an internal name is fine where it is defined and useless anywhere else, so a sentence
addressed to a human never carries one.

This file grows a section at a time. Only the diagram engine is defined so far; the merge-request
skills and the explainers still speak in their own terms and are listed at the bottom as owed.

---

## The diagram engine

The shared code under `lib/diagram/` that turns a description of something into a finished,
quality-checked picture. `/visualize` and the explainer skills all use it — so it is never
called "the visualize renderer", because a change to it changes every skill that draws.

| say this | not this | what it means |
|---|---|---|
| **diagram engine** | the renderer, lib/diagram, the pipeline | The whole thing: description in, checked picture out. |
| **diagram** | figure, drawing, graphic | One finished picture. Ten of them exist as test material. |
| **diagram kind** | type | Which of the five shapes a diagram is: architecture, sequence, ER, class, state. |
| **description** | spec | What the diagram is meant to show, before any drawing happens. Written by a skill, not by a person. |
| **note** | callout, annotation | The small labelled box pinned to something in a diagram. The description already calls this field `note`. |
| **note placement** | anchor search, the placement pass | Choosing where a note sits so it covers as little as possible. Expensive: it tries every candidate position and measures each. |
| **layout search** | the ladders, escalation, picking a candidate | Trying several arrangements of the same diagram and keeping the one that reads best. |
| **spacing step** | rung, ladder rung | One step in the run of spacing values the layout search walks. **Never say "rung".** Give the number and its unit — "25px of space between an arrow and the box it points at". |
| **quality check** | gate | An automatic check on the finished diagram — text too small, text hidden, text cut off, contrast too low. A diagram that fails one is reported, not silently shipped. |
| **sample set** | corpus, corpora | One of two fixed collections of five diagrams, one per kind, used to test every change. There are two on purpose: one the engine was tuned against, one taken from a real unsteered run. |

### Words for how it is measured

These appear in the performance report and nowhere a person reads otherwise.

| say this | not this | what it means |
|---|---|---|
| **layout candidate** | d2 compile, draw attempt | One differently arranged version of a picture, produced while searching for the best one. Most are discarded. A single diagram costs many, because both the arrangement search and note placement try alternatives. |
| **inspection** | measurement, page, job, harness page | Opening a finished drawing and reading back where its text and boxes actually landed. |
| **browser start** | launch, spawning node/Chrome | Starting a fresh headless browser to carry out inspections. Costs about 0.74s whatever it is then asked to do, so the count of these matters more than what happens inside them. |
| **core usage** | idle share, cores idle | How much of the machine a job kept busy: the average number of things running at once, as a share of the cores available. Low means speed is still available, though not always reachable. **Higher is better** — which is why it is never stated as "idle". |

**A layout candidate costs no browser; an inspection is the thing that needs one.** That split is
the single most useful fact about these three numbers, and it is why an inspection is expensive
and a candidate is not. Keep "browser" out of the *term* — it is how the work is done, not what
the work is — but say it plainly wherever the cost is being explained.

Neither of the first two is a share of the other. Many candidates are never inspected (the
arrangement search compares them on size and shape alone and only opens the one it keeps), and
some inspections are of diagrams laid out earlier (the legibility checks produce no candidates at
all). Either total can be the larger, and on a full run they land within one of each other purely
by coincidence.

### Why a browser is involved at all

Worth knowing, because it is most of the cost and looks like an odd choice. The engine has to
know where text actually landed — how wide a word came out, whether a note covers a label,
whether a shadow is cut off at the edge. Nothing outside a real browser can answer that for
the notes, because a note is real HTML laid out with the page's own stylesheet. So the engine
draws a candidate, opens it, measures it, and decides. That is why "measurements" and "browser
starts" are the numbers that move everything else.

---

## Still owed

Sections to add when those areas are next worked on, so the terms get agreed against something
real rather than in the abstract:

- **the merge-request skills** (`review-mr`, `rework-mr`) — finding, topic, thread, seeding,
  curation, and the two different status vocabularies that deliberately do not match.
- **the explainer skills** (`explain-diff`, `explain-branch`) — chapter, quiz, figure, and how
  a "figure" there relates to a "diagram" here.
