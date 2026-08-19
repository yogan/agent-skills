# Shared vocabulary

One agreed word per concept, so that a report, a commit message or an explanation means the
same thing to both of us. **The code may keep its own names; anything written for a person
uses the words in this file.**

The rule this exists to serve is in [CLAUDE.md](CLAUDE.md) under "Working agreement". Short
version: an internal name is fine where it is defined and useless anywhere else, so a sentence
addressed to a human never carries one.

Only terms that have actually caused trouble are here. A word that reads the same to both of us
does not need an entry, and a glossary nobody finishes reading protects nothing.

---

## The diagram engine

The shared code under `lib/diagram/` that turns a description of something into a finished,
quality-checked picture. `/visualize` and the explainer skills all use it — so it is never
called "the visualize renderer", because a change to it changes every skill that draws.

| say this | not this | what it means |
|---|---|---|
| **diagram engine** | the renderer, the pipeline, the visualize renderer | The whole thing: description in, checked picture out. |
| **diagram** | figure, drawing, graphic | One finished picture. Ten of them exist as test material. |
| **diagram kind** | type | Which of the five shapes a diagram is: architecture, sequence, ER, class, state. |
| **description** | spec | What the diagram is meant to show, before anything is drawn. Written by a skill, not by a person. |
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
| **inspection** | harness page, measuring | Opening a laid-out picture and reading back where its text and boxes actually landed. "Measurement" on its own means a whole recorded benchmark run, so it is not a synonym for this. |
| **browser start** | launch, spawning node/Chrome | Starting a fresh headless browser to carry out inspections. Costs about 0.74s whatever it is then asked to do, so the count of these matters more than what happens inside them. |
| **job** | scenario, case | One of the four things the performance report times — a whole piece of work the engine does, not a step inside one. |
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
draws a candidate, opens it, measures it, and decides. That is why inspections and browser
starts are the numbers that move everything else.

---

## The merge-request skills

`review-mr` reviews someone else's merge request; `rework-mr` works through the feedback on your
own. They are mirrors of each other, and these words mean the same thing on both sides.

| say this | not this | what it means |
|---|---|---|
| **topic** | finding, item, comment | One point being worked through — `t1`, `t2`. The unit both skills work in: raised, agreed and closed one at a time, never several at once. Two threads making the same point are merged into one topic. |
| **thread** | discussion | The exchange on the merge request that a topic corresponds to. Whether it exists and whether it is resolved is GitLab's answer, never the skill's — both skills only read. GitLab calls the individual comments inside one "notes"; we do not, because a note is a thing on a diagram. |

**The two skills use different status words, and that is deliberate.** Reviewing, a topic is
*draft, open, needs-ack, acked* or *wontfix*; reworking, it is *reply-pending, open, waiting* or
*done*. Only "open" appears in both, and even there it means "waiting on the other person" —
which is a different person in each. Never carry a status word from one skill into a sentence
about the other.

---

## The explainer skills

`explain-diff` and `explain-branch` generate a self-contained page that teaches a change.

| say this | not this | what it means |
|---|---|---|
| **explainer** | the doc, the article, the output | The generated page. Self-contained: it carries its own styling, diagrams and quizzes, so it can be sent to somebody with nothing else. |
| **section** | block, part | One stretch of an explainer — background, intuition, code walkthrough. |
| **chapter** | part | One commit's section in an `explain-branch` explainer. Every chapter is a section; the intro and the summary are sections that are not chapters. A commit too small to deserve one is folded into its neighbour instead. `explain-diff` is flat and has none. |
| **quiz** | questions, the test | The short self-check under a chapter, or at the end of a flat explainer. |

A picture inside an explainer is a **diagram**, exactly as above — never a "figure". It is drawn
by the same diagram engine, so everything in that section applies unchanged.
