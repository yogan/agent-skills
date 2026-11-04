// Which tool calls print a view the person must see, and what of their output is the view.
//
// Pure, no `$`: the hooks module draws, this decides. Kept in step with the gate specs
// (skills/*/scripts/paste-gates.json): a command listed there is a view here.

// The subcommands that print a view, by script. `Q` is the quote a script's path may carry
// (`python3 "$SD/threads.py" present` is as common as the bare form).
const Q = `["']?`
const VIEW_COMMANDS = [
  String.raw`findings\.py${Q}\s+(?:resume|present|todo|quote|updates)\b`,
  String.raw`findings\.py${Q}\s+set\b[^\n]*--draft`,
  String.raw`findings\.py${Q}\s+diff\b[^\n]*--show`,
  String.raw`threads\.py${Q}\s+(?:present|todo|quote|reply-view|change-view)\b`,
  String.raw`(?:diff-view|change-preview)\.sh\b`,
].join('|')

// Only where the script is the program a segment of the command runs (`python3
// $SD/findings.py present`, optionally after `cd x &&` or an assignment), never where its
// name merely appears in an argument, a heredoc or a comment: a heredoc writing a test
// file once contained `findings.py quote`, and its output was drawn as a view.
const VIEW = new RegExp(
  String.raw`(?:^|[;&|]|\n)\s*(?:\w+=\S*\s+)*(?:(?:python3?|sh|bash)\s+)?${Q}[^\s'"#]*?(?:${VIEW_COMMANDS})`,
)

// The trailing payload the scripts append for the paste gate (lib/critical_manifest.py).
const MANIFEST = /\n*<!-- paste-gate:critical\n([\s\S]*?)\n-->\s*$/

export type Manifest = { first?: string; topic?: string; focus?: string | null }

export type View = { text: string; topic?: string }

export const isViewCommand = (command: unknown): command is string =>
  typeof command === 'string' && VIEW.test(command)

const parseManifest = (stdout: string): { body: string; manifest?: Manifest } => {
  const m = MANIFEST.exec(stdout)
  if (!m) return { body: stdout }
  let manifest: Manifest | undefined
  try {
    manifest = JSON.parse(m[1] ?? '') as Manifest
  } catch {
    manifest = undefined
  }
  return { body: stdout.slice(0, m.index), manifest }
}

// The view a view command's stdout holds, or undefined when there is none to show:
// nothing printed, or a view of a topic that is not the current one. That one is research
// (lib/focus.py): the model reads it, the person is not shown it.
//
// `first` names the view's opening line. Output ahead of it is not the view: the model
// chains a formatter or a linter in front of a view command, and `2>&1` folds stderr in.
export const viewOf = (stdout: unknown): View | undefined => {
  if (typeof stdout !== 'string') return undefined
  const { body, manifest } = parseManifest(withoutNote(stdout))
  if (manifest?.topic && manifest.focus !== undefined && manifest.topic !== manifest.focus) {
    return undefined
  }
  let text = body
  if (manifest?.first) {
    const lines = body.split('\n')
    const at = lines.findIndex(line => line.trim() === manifest.first)
    if (at > 0) text = lines.slice(at).join('\n')
  }
  text = text.trim()
  if (!text) return undefined
  return manifest?.topic ? { text, topic: manifest.topic } : { text }
}

// The line the model reads at the end of a view command's output once the person has the
// view as a card. It states a fact and gives no order: instructions inside a tool result are
// rightly ignored by the model, so what to DO with the line is the skills' own rule ("A card
// is a paste already made", in both SKILL.md files), which a mod cannot add to a skill's text
// either: an organization's plugin may sit above this one and pass over `skill.prompt` and
// `prompt.compose`, as one did on the machine this was built on.
//
// hooks/paste-gate.py stands down for a session where a tool result ENDS with this line.
// Change it in all four places at once.
export const NOTE = '[review-ui] Shown to the user as a card.'

// The output with the note after it, as the model reads it.
export const withNote = (stdout: string): string => stdout.replace(/\s*$/, '') + '\n\n' + NOTE + '\n'

// The output as the script printed it, the note taken off again: the row and the card are
// drawn from the result the model read.
export const withoutNote = (stdout: string): string => {
  const at = stdout.lastIndexOf(NOTE)
  return at < 0 ? stdout : stdout.slice(0, at).replace(/\s*$/, '\n')
}
