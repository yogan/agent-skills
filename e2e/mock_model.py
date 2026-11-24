#!/usr/bin/env python3
"""A stand-in for the model: a real Claude Code, its tools, the skills' scripts, the mod and
the local GitLab all run for real, and only the model's replies come from a recording.

    python3 mock_model.py record <session.jsonl | folder> [--out FILE]   # cut a recording
    python3 mock_model.py serve <recording.json> [--port N]               # answer from it

What a run on it proves: the scripts, cards, buttons, hooks and GitLab side work together.
What it cannot prove: that a real model follows a skill's wording, since this one says only
what it was recorded saying. That stays a real-model run's job (`drive.py verify --model`).

**A recording** is cut from a passing real run's transcript: every reply the model gave, in
order, per conversation: the main one and each background agent's. Thinking is left out. It
is committed under `recordings/` and may be trimmed by hand, reply text shortened, as long as
the tool calls stay.

**Answering needs no state.** A request carries the whole conversation so far, so the number
of replies already in it says which reply comes next, and its first message says which
conversation it belongs to. A reply that opens a turn also names the prompt it answered, and
is given only when that prompt is in the request: a scenario's or a button's text that has
changed since the recording is caught there, instead of the old reply being played to it.

**Ids a run makes are followed.** A comment posted, a commit made and a token a button press
makes come out different every run, and a later call names them. So a recording notes the
ids each call's output and each prompt showed, and a replay holds them against this run's:
where the same output shows a different id, later replies use the new one.

**A mod's own model call** (`$.model.complete`: the review-ui mod judges news with one) has
no tools and leaves no trace in the transcript, so the mod keeps each call and its reply in a
file while a real model runs (`REVIEW_UI_CALLS`), and a recording takes them from it
(`calls`). Played, such a request gets the reply of the recorded call whose prompt opens the
same, the nearest whole prompt first; a reply that is a JSON object keyed by item is filled
in from the other calls of that opening for any item the nearest one lacks, since a check
that runs at another moment than it did when recorded can ask about the same items in other
batches.

Whatever cannot be placed gets an answer that says so, "off script", and is noted in `misses`
for the driver to fail the run on. Any other request with no tools is one of Claude Code's
own small side requests, never a conversation, and gets a short neutral answer; so does one
that reuses a conversation for the engine's own ends (`SIDE`).
"""
import argparse
import difflib
import json
import os
import re
import sys
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
HANDBACK = "SubagentHandback"
# The engine's own requests that reuse a conversation, told by how their last prompt opens,
# and what each is answered with: never part of a recording, so never a reply to count.
SIDE = {
    "[SUGGESTION MODE:": "",        # what the person might type next: none
}
# The engine's own nudge after a reply with nothing visible in it (thinking alone): a replay
# never gives such a reply, so the nudge never comes, and a recording leaves it out.
NUDGES = ("[Your previous response had no visible output",)
RECORDINGS = os.path.join(HERE, "recordings")
OFF = "⚠ mock model: off script"
SIG = 200        # how much of a prompt identifies it: enough to tell buttons and topics apart


# The folder the demo checkouts are under in this run, where it is not the one holding this
# checkout: a scenario's own copy of the rig (drive.py's `copy_of`). Set by the driver before
# a recording is played or made.
CHECKOUTS = None
DEMO = "agent-skills-demo"       # what every demo checkout's folder name starts with


def places():
    """(path, placeholder) pairs, longest path first. A recording is committed, so the folders
    it ran under are kept as placeholders and put back when it is played: the paths name
    whoever recorded it, and the replay may run under someone else's. The folder holding this
    checkout holds the demo checkouts too (fixture.py), unless CHECKOUTS names another;
    `~/src` may be a link to it."""
    home = os.path.expanduser("~")
    src = os.path.realpath(os.path.dirname(os.path.dirname(HERE)))
    pairs = [(src, "{SRC}"), (os.path.dirname(os.path.dirname(HERE)), "{SRC}"), (home, "{HOME}")]
    alias = os.path.join(home, "src")
    if os.path.realpath(alias) == src:
        pairs.append((alias, "{SRC}"))
    if CHECKOUTS:
        # The real folder first: a placeholder goes back to the first path that carries it.
        for root in (os.path.realpath(CHECKOUTS), CHECKOUTS):
            pairs.append((os.path.join(root, DEMO), "{SRC}/" + DEMO))
    return sorted(dict.fromkeys(pairs), key=lambda p: -len(p[0]))


# A run's own plugin folder of this repo's skills (drive.py's `skills_plugin`), whose `skills`
# is a link to the repo's: a command through it names the run, and the run's folder may be
# gone when the recording is played. Kept as the repo's own skills folder, which it is: beside
# the run's folder in this checkout, or wherever `--out` put the run (a scratchpad, say).
RUN_SKILLS = re.compile(r"e2e/\.runs/[^/\s'\"]+/skills-plugin/skills/")
OUT_SKILLS = re.compile(r"(?:\{\w+\}|~|/)[^\s'\"=:;]*/skills-plugin/skills/")


def _repo_skills():
    """This repo's skills folder as a recording keeps it, its folders as placeholders."""
    value = os.path.join(os.path.dirname(HERE), "skills") + "/"
    for path, mark in places():
        value = value.replace(path, mark)
    return value

# The scratchpad Claude Code gives a session, under the system's temp folder and named after
# the session. Kept as a placeholder, and every play of a recording gets a folder of its own:
# plays of one recording that run at the same time would otherwise write the same file into
# each other's commands, and the recorded session's folder is gone after a restart.
SCRATCHPAD = re.compile(r"(?:/private)?/tmp/claude-\d+/[^/\s'\"]+/[0-9a-f-]{36}/scratchpad")
SCRATCH = None      # this play's own; drive.py sets it to the run's folder


def scratch():
    """This play's scratchpad, made on first use: a temp folder where no run names one."""
    global SCRATCH
    if SCRATCH is None:
        SCRATCH = tempfile.mkdtemp(prefix="mock-scratchpad-")
    os.makedirs(SCRATCH, exist_ok=True)
    return SCRATCH


def _runless(value):
    if isinstance(value, str):
        value = OUT_SKILLS.sub(lambda _: _repo_skills(), RUN_SKILLS.sub("skills/", value))
        return SCRATCHPAD.sub("{SCRATCHPAD}", value)
    if isinstance(value, list):
        return [_runless(v) for v in value]
    if isinstance(value, dict):
        return {k: _runless(v) for k, v in value.items()}
    return value


def to_placeholders(value):
    for path, mark in places():
        value = _swap(value, path, mark)
    return _runless(value)


def from_placeholders(value):
    seen = set()
    for path, mark in places():
        if mark not in seen:       # a placeholder goes back to its real folder, not an alias
            seen.add(mark)
            value = _swap(value, mark, os.path.realpath(path))
    if "{SCRATCHPAD}" in json.dumps(value):
        value = _swap(value, "{SCRATCHPAD}", scratch())
    return value


# ── text of a message ────────────────────────────────────────────────────────

def _swap(value, here, there):
    """`value` with `here` changed to `there` in every string inside it."""
    if isinstance(value, str):
        return value.replace(here, there)
    if isinstance(value, list):
        return [_swap(v, here, there) for v in value]
    if isinstance(value, dict):
        return {k: _swap(v, here, there) for k, v in value.items()}
    return value


def _texts(content):
    if isinstance(content, str):
        return [content]
    return [b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text"]


def human(content):
    """A user message's own words: its text blocks without the engine's reminders, spaces
    collapsed. The same prompt reads the same in a transcript and in a request this way."""
    text = "\n".join(_texts(content))
    text = re.sub(r"<system-reminder>.*?</system-reminder>", " ", text, flags=re.S)
    return to_placeholders(" ".join(text.split()))


def is_tool_results(content):
    return isinstance(content, list) and bool(content) and all(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in content)


# ── recording ────────────────────────────────────────────────────────────────

def _keep(block):
    block = to_placeholders(block)
    if block.get("type") == "text":
        return {"type": "text", "text": block["text"]}
    if block.get("type") == "tool_use":
        return {"type": "tool_use", "id": block["id"], "name": block["name"], "input": block["input"]}
    return None          # thinking, and anything else a replay has no use for


def conversation(lines):
    """One transcript's replies, in order, each with the prompts it answered when it opens a
    turn: [{"prompts": [...], "content": [...]}, {"content": [...]}, ...]."""
    replies, waiting, ids, hexes, last_id = [], [], [], [], None
    for d in lines:
        kind, msg = d.get("type"), d.get("message") or {}
        if kind == "user":
            c = msg.get("content")
            if not is_tool_results(c) and human(c) and not human(c).startswith(NUDGES):
                waiting.append(human(c)[:SIG])
                ids += ID.findall(human(c))
                hexes += HEX.findall(human(c))
        elif kind == "assistant" and msg.get("model") != "<synthetic>":
            blocks = [b for b in map(_keep, msg.get("content") or []) if b]
            if msg.get("id") == last_id and replies:     # one reply, logged block by block
                replies[-1]["content"] += blocks
                continue
            last_id = msg.get("id")
            reply = {"content": blocks}
            if waiting:
                reply["prompts"] = waiting
            if ids:
                reply["prompt_ids"] = ids       # a token a press made, held against this run's
            if hexes:
                reply["prompt_hexes"] = hexes   # every hex token, as `hexes` are for outputs
            waiting, ids, hexes = [], [], []
            replies.append(reply)
    return [r for r in replies if r["content"]]


# An id a run makes: a git commit, a GitLab thread. Hex with a letter and a digit in it, so
# words ("added", "facade") and plain numbers (counts, line numbers) are not taken for one.
ID = re.compile(r"\b(?=[0-9a-f]*[a-f])(?=[0-9a-f]*[0-9])[0-9a-f]{7,40}\b")


def _result_text(block):
    """A call's output, its folders as placeholders: a home folder's name can look like an id,
    and is nothing a recording may keep."""
    c = block.get("content")
    return to_placeholders(c if isinstance(c, str) else "\n".join(_texts(c)))


def seen_ids(lines, pattern=None):
    """tool_use id -> the ids its output showed, in order: what a replay's outputs are held
    against to learn the ids this run made instead (see `Mock.renamed`). `pattern`: HEX for
    every hex token, as `hexes` records them."""
    seen = {}
    for d in lines:
        c = (d.get("message") or {}).get("content")
        if d.get("type") == "user" and isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    found = (pattern or ID).findall(_result_text(b))
                    if found:
                        seen[b.get("tool_use_id")] = found
    return seen


# Every hex token an id could be, digits alone included: a commit's short hash is all digits
# now and then ("7998180"), which ID does not take for one, and the run's output and the
# recording's then hold a different number of ids. Held side by side as these, they line up.
HEX = re.compile(r"\b[0-9a-f]{7,40}\b")
# A commit's full hash: what tells a short hash of all digits from a number (a count, a size),
# which no full hash starts with.
FULL_SHA = re.compile(r"\b[0-9a-f]{40}\b")


def commits_in(*texts):
    """Every full commit hash in `texts`, for `is_commit`."""
    return {sha for t in texts for sha in FULL_SHA.findall(t)}


def is_commit(token, shas):
    """Whether `token` is a short hash of one of `shas`: a commit, all digits or not."""
    return any(sha.startswith(token) for sha in shas)


def _jsonl(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def opening(lines):
    """A transcript's first prompt whole, numbers as `#`: what tells apart two conversations
    whose first `SIG` characters are the same (two background agents briefed alike, each on
    other topics). Numbers aside, since a run makes its own (GitLab's note ids)."""
    for d in lines:
        c = (d.get("message") or {}).get("content")
        if d.get("type") == "user" and not is_tool_results(c) and human(c):
            return _numbers(human(c))
    return ""


def _numbers(text):
    return re.sub(r"\d+", "#", ID.sub("#", text))


def record(session, calls=None):
    """A recording of one session: its main conversation, each background agent's, and the
    mod's own model calls, from the file `calls` the mod kept them in (a list of `{prompt,
    reply}`)."""
    lines = _jsonl(session)
    main = conversation(lines)
    streams = [{"name": "main", "first": main[0]["prompts"][0] if main else "", "replies": main,
                "ids": seen_ids(lines), "hexes": seen_ids(lines, HEX)}]
    agents = os.path.join(session[:-len(".jsonl")], "subagents")
    for f in sorted(os.listdir(agents)) if os.path.isdir(agents) else []:
        if not f.endswith(".jsonl"):
            continue
        meta = {}
        if os.path.isfile(os.path.join(agents, f[:-6] + ".meta.json")):
            with open(os.path.join(agents, f[:-6] + ".meta.json"), encoding="utf-8") as fh:
                meta = json.load(fh)
        agent = _jsonl(os.path.join(agents, f))
        replies = conversation(agent)
        if replies and replies[0].get("prompts"):
            streams.append({"name": f"agent: {meta.get('description') or f[:-6]}",
                            "first": replies[0]["prompts"][0], "opening": opening(agent),
                            "replies": replies, "ids": seen_ids(agent),
                            "hexes": seen_ids(agent, HEX)})
    model = next((d["message"].get("model") for d in lines if d.get("type") == "assistant"
                  and d["message"].get("model") != "<synthetic>"), None)
    rec = {"recorded_from": {"model": model, "session": os.path.basename(session)},
           "streams": streams}
    if calls and os.path.isfile(calls):
        rec["calls"] = [{"prompt": _flat(to_placeholders(c["prompt"])), "reply": to_placeholders(c["reply"])}
                        for c in load(calls)]
    return rec


def _flat(text):
    """A prompt as `human` reads one in a request: its whitespace collapsed."""
    return " ".join(text.split())


# ── answering ────────────────────────────────────────────────────────────────

def first_prompt(messages):
    for m in messages:
        if m.get("role") == "user" and not is_tool_results(m.get("content")) and human(m.get("content")):
            return human(m.get("content"))
    return ""


def same(recorded, asked):
    """Whether the recorded prompt is in what is asked now, ids aside: a press makes a token
    of its own each time, and a run its own commits and threads. Every hex token counts as
    one, as a short hash can come out all digits; a prompt that differs only in a number of
    seven digits or more passes for the same, which none of the skills' prompts has."""
    return HEX.sub("#", recorded) in HEX.sub("#", asked)


def prompts_before(messages):
    """The user's words before each reply in the request, and after the last one: one string
    per reply, in order, the last for the reply being asked for."""
    out, words = [], []
    for m in messages:
        if m.get("role") == "assistant":
            out.append(" ".join(words))
            words = []
        elif m.get("role") == "user" and not is_tool_results(m.get("content")):
            words.append(human(m.get("content")))
    return out + [" ".join(words)]


def since_last_reply(messages):
    """The user's words after the last reply in the request: the prompt being answered."""
    words = []
    for m in reversed(messages):
        if m.get("role") == "assistant":
            break
        if not is_tool_results(m.get("content")):
            words.append(human(m.get("content")))
    return " ".join(reversed(words))


class Mock:
    def __init__(self, rec, log=None):
        self.streams = rec["streams"]
        self.calls = rec.get("calls") or []
        self.misses = []
        self.lock = threading.Lock()
        self.log = log            # a path: one line per request, what it was answered with

    def note(self, line):
        if self.log:
            with self.lock, open(self.log, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def miss(self, why):
        with self.lock:
            self.misses.append(why)
        return [{"type": "text", "text": f"{OFF}: {why}"}]

    def answer(self, body):
        """The reply's content blocks for one request."""
        blocks = self._answer(body)
        msgs = body.get("messages") or []
        self.note(f"{len(msgs)} messages, {sum(m.get('role') == 'assistant' for m in msgs)} replies, "
                  f"last {msgs[-1].get('role') if msgs else '-'} "
                  f"{json.dumps(msgs[-1].get('content'))[:160] if msgs else ''} -> "
                  + " ".join(b.get("name") or repr(b.get("text", ""))[:60] for b in blocks))
        return blocks

    def _answer(self, body):
        if not body.get("tools"):
            reply = self.call_reply(first_prompt(body.get("messages") or []))
            return [{"type": "text", "text": "ok" if reply is None else reply}]
        messages = body.get("messages") or []
        asked = since_last_reply(messages)
        side = next((k for k in SIDE if asked.startswith(k)), None)
        if side is not None:
            return [{"type": "text", "text": SIDE[side]}]
        first = first_prompt(messages)
        alike = [s for s in self.streams if s["first"] and same(s["first"], first)]
        # Several open alike: the one whose whole first prompt is nearest this one's.
        whole = _numbers(first) if len(alike) > 1 else ""
        stream = max(alike, key=lambda s: difflib.SequenceMatcher(
            None, s.get("opening") or "", whole, autojunk=False).ratio(), default=None) if whole else (
            alike[0] if alike else None)
        if stream is None:
            return self.miss(f"a conversation the recording does not have, opening {first[:80]!r}")
        n = sum(1 for m in messages if m.get("role") == "assistant")
        if n >= len(stream["replies"]):
            return self.miss(f"{stream['name']}: reply {n + 1} asked for, "
                             f"{len(stream['replies'])} recorded")
        reply = stream["replies"][n]
        if reply.get("prompts") and not any(same(p, asked) for p in reply["prompts"]):
            return self.miss(f"{stream['name']}: reply {n + 1} answered {reply['prompts'][0][:80]!r}, "
                             f"this time the prompt is {asked[:80]!r}")
        content = from_placeholders(reply["content"])
        for old, new in self.renamed(stream, messages).items():
            content = _swap(content, old, new)
        return self.offered(content, body)

    def call_reply(self, asked):
        """The reply to a mod's own model call that asks `asked`, or None where no recorded
        call opens the same. Ids the run made (a comment's discussion id) are followed from
        the recorded prompt to this one, as they line up in order."""
        alike = [c for c in self.calls if same(c["prompt"][:SIG], asked)]
        if not alike:
            return None
        whole = _numbers(asked)
        alike.sort(key=lambda c: -difflib.SequenceMatcher(None, _numbers(c["prompt"]), whole,
                                                          autojunk=False).ratio())
        replies = []
        for c in alike:
            reply = from_placeholders(c["reply"])
            then, now = HEX.findall(c["prompt"]), HEX.findall(asked)
            if len(then) == len(now):
                for old, new in zip(then, now):
                    if old != new:
                        reply = reply.replace(old, new)
            replies.append(reply)
        merged = {}
        for reply in replies:
            try:
                got = json.loads(reply)
            except ValueError:
                got = None
            if not isinstance(got, dict):
                break
            for k, v in got.items():
                merged.setdefault(k, v)
        else:
            return json.dumps(merged)
        return replies[0]

    def renamed(self, stream, messages):
        """recorded id -> the id this run made in its place. The same call's output, recorded
        and now, shows its ids in the same order; where the two differ, the run made a new
        one (a comment posted, a commit made) and every later reply has to use it."""
        recorded, renamed = stream.get("ids") or {}, {}
        hexes = stream.get("hexes")
        # A pair of hex tokens counts where either side is an id, so a number that differs (a
        # count, a size) is never taken for one; or where either side is a commit, whose short
        # hash comes out all digits now and then (on both sides at once, one run in ~25), and
        # is known for one by the full hash it is cut from, in the recording or in this run.
        shas_then = commits_in(json.dumps(stream))
        shas_now = commits_in(json.dumps(messages))

        def pairs(a, z):
            return a != z and bool(ID.fullmatch(a) or ID.fullmatch(z)
                                   or is_commit(a, shas_then) or is_commit(z, shas_now))
        for reply, asked in zip(stream["replies"], prompts_before(messages)):
            if reply.get("prompt_hexes") is not None:
                # Every hex token on both sides, as for a call's output below: a commit's short
                # hash that came out all digits on either side does not shift the others.
                then, now = reply["prompt_hexes"], HEX.findall(asked)
                if len(then) == len(now):
                    renamed.update((a, z) for a, z in zip(then, now) if pairs(a, z))
                continue
            then, now = reply.get("prompt_ids"), ID.findall(asked)
            if then and len(now) < len(then):
                # A short hash this run made can come out all digits, which ID does not take
                # (a press's hand-off names the commits beside its token): every hex token
                # here may line up instead, as for a call's output below.
                now = HEX.findall(asked)
            if then and len(then) == len(now):
                renamed.update((a, z) for a, z in zip(then, now) if a != z)
        for m in messages:
            if m.get("role") != "user" or not isinstance(m.get("content"), list):
                continue
            for b in m["content"]:
                if not (isinstance(b, dict) and b.get("type") == "tool_result"):
                    continue
                if hexes is not None:
                    # Every hex token on both sides, paired as `pairs` says.
                    then, now = hexes.get(b.get("tool_use_id")), HEX.findall(_result_text(b))
                    if then and len(then) == len(now):
                        renamed.update((a, z) for a, z in zip(then, now) if pairs(a, z))
                    continue
                text = _result_text(b)
                then, now = recorded.get(b.get("tool_use_id")), ID.findall(text)
                if then and len(now) < len(then):
                    # A recording from before `hexes`: an id this run made can come out all
                    # digits, which ID does not take; every hex token here may line up instead.
                    now = HEX.findall(text)
                if then and len(then) == len(now):
                    renamed.update((a, z) for a, z in zip(then, now) if a != z)
        return renamed

    def offered(self, content, body):
        """`content` fitted to the tools the request offers. An agent hands its report back
        through a tool the engine offers some accounts only; where it is not offered, the
        report is the agent's last words, as it was before that tool. Any other call goes as
        recorded: a tool not offered yet (a mod's, registered as the session starts) gets the
        engine's own answer, as it would for a model."""
        tools = {t.get("name") for t in body.get("tools") or []}
        out = []
        for b in content:
            if b["type"] == "tool_use" and b["name"] == HANDBACK and HANDBACK not in tools:
                out.append({"type": "text", "text": b["input"].get("message", "")})
            else:
                out.append(b)
        return out


# ── the wire ─────────────────────────────────────────────────────────────────

def _id():
    """A reply's own id. Claude Code joins replies that share one into a single message, so
    two with the same id read as one reply and the next request asks for it again."""
    return f"msg_mock_{uuid.uuid4().hex[:16]}"


def events(blocks, model):
    """The reply as the streamed events the Messages API sends."""
    yield "message_start", {"type": "message_start", "message": {
        "id": _id(), "type": "message", "role": "assistant", "model": model, "content": [],
        "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}}
    for i, b in enumerate(blocks):
        if b["type"] == "text":
            start, delta = {"type": "text", "text": ""}, {"type": "text_delta", "text": b["text"]}
        else:
            start = {"type": "tool_use", "id": b["id"], "name": b["name"], "input": {}}
            delta = {"type": "input_json_delta", "partial_json": json.dumps(b["input"])}
        yield "content_block_start", {"type": "content_block_start", "index": i, "content_block": start}
        yield "content_block_delta", {"type": "content_block_delta", "index": i, "delta": delta}
        yield "content_block_stop", {"type": "content_block_stop", "index": i}
    stop = "tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn"
    yield "message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                            "usage": {"output_tokens": 1}}
    yield "message_stop", {"type": "message_stop"}


def message(blocks, model):
    """The same reply, not streamed."""
    stop = "tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn"
    return {"id": _id(), "type": "message", "role": "assistant", "model": model,
            "content": blocks, "stop_reason": stop, "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1}}


def serve(mock, port=0):
    """Start answering on 127.0.0.1 in a thread; returns the server (its port in .server_port)."""
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, data, status=200):
            out = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def do_GET(self):
            self._json({"type": "error", "error": {"type": "not_found_error", "message": "mock"}}, 404)

        def do_POST(self):
            size = int(self.headers.get("content-length") or 0)
            body = json.loads(self.rfile.read(size) or b"{}")
            if "count_tokens" in self.path:
                return self._json({"input_tokens": 1})
            if not self.path.startswith("/v1/messages"):
                return self.do_GET()
            model = body.get("model") or "mock"
            blocks = mock.answer(body)
            if not body.get("stream"):
                return self._json(message(blocks, model))
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            for name, data in events(blocks, model):
                self.wfile.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def environment(port):
    """What a claude needs to talk to the mock on `port` instead of the real API. Its other
    traffic stays on: the engine's feature settings come with it, and without them it runs
    another interface (no fullscreen), so the screens would not be the recorded ones."""
    return {"ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}", "ANTHROPIC_AUTH_TOKEN": "mock"}


def recording_path(scenario):
    return os.path.join(RECORDINGS, f"{scenario}.json")


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save(rec, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=1, ensure_ascii=False)
        fh.write("\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="mode", required=True)
    r = sub.add_parser("record")
    r.add_argument("session", help="a transcript .jsonl, or the folder a session ran in (newest)")
    r.add_argument("--out", help="default: print it")
    s = sub.add_parser("serve")
    s.add_argument("recording")
    s.add_argument("--port", type=int, default=0)
    s.add_argument("--log", help="a file to note each request's answer in")
    a = ap.parse_args(argv)
    if a.mode == "record":
        sys.path.insert(0, HERE)
        import transcript
        session = transcript.session_file(a.session)
        if not session:
            raise SystemExit(f"no session ran in {a.session}")
        rec = record(session)
        if a.out:
            save(rec, a.out)
            print(f"{a.out}: " + ", ".join(f"{s['name']} {len(s['replies'])} replies"
                                           for s in rec["streams"]))
        else:
            json.dump(rec, sys.stdout, indent=1, ensure_ascii=False)
        return 0
    mock = Mock(load(a.recording), log=a.log)
    server = serve(mock, a.port)
    print(" ".join(f"{k}={v}" for k, v in environment(server.server_port).items()), flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    for m in mock.misses:
        print(f"off script: {m}", file=sys.stderr)
    return 1 if mock.misses else 0


if __name__ == "__main__":
    sys.exit(main())
