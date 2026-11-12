// What the band offers for the current topic, and what each press does. Pure: the state
// comes from `findings.py state`, and register.tsx runs what a press names.

import type { ReviewCard, ReviewOutcome, ReviewState, ReviewTopic } from '../types'
import { sizeOf } from './views.ts'

// What a press does. `ask` hands the model a turn, with a prompt the person sees as theirs;
// every other kind runs the review's own script, with no model turn at all.
export type Press =
  | { kind: 'ask'; prompt: string }
  // Puts the start of a prompt in the box for the person to finish.
  | { kind: 'fill'; text: string }
  | { kind: 'resolve' }
  // A comment the person posted by hand, edited on GitLab to the corrected version stored.
  | { kind: 'apply-edit' }
  // The previous or next of the person's own comments, in the sidebar.
  | { kind: 'own-step'; by: 1 | -1; to?: string }
  // rework-mr: `resolve` resolves the thread with the reply; `fixed` posts `Fixed: <link>`;
  // `reply`, the author's reply in a reviewer's thread (review-mr posts the person's comment).
  // `early`: a reply ahead of the fix, which leaves the plan standing.
  | { kind: 'post'; resolve?: true; fixed?: true; reply?: true; early?: true }
  // rework-mr: the proposed plan agreed, with no model turn; `reply`, and a reply to post
  // ahead of the fix drafted next (the model's turn).
  // `draft`: a plan that is itself a reply, agreed and its reply drafted next.
  | { kind: 'agree'; reply?: true; draft?: true }
  // rework-mr: nothing more to do on it until a reviewer writes again (already handled,
  // replied by hand, no reply needed, a comment that asks nothing).
  | { kind: 'handled'; did: string }
  // rework-mr: the fix in the diff viewer, switched to.
  | { kind: 'hunk' }
  // A pushed fix, from the commits kept in git, in the diff viewer: rework-mr's own fix,
  // review-mr's the author's.
  // `push`: which of the topic's pushed fixes (1-based), the latest where none is given.
  | { kind: 'hunk-pushed'; push?: number }
  // rework-mr: the checked fix committed as a fixup, squashed and force-pushed by the
  // script, with no model turn. Only that: what is said in the thread is the next card's.
  | { kind: 'push' }
  | { kind: 'copy' }
  | { kind: 'drop' }
  // Into the topic the agent named: merging is offered only where it advised it.
  | { kind: 'merge'; into: string }
  // Back to the inbox from a topic opened to look at.
  | { kind: 'leave' }
  // On to the round's next topic from one that needs nothing in this pass: nothing counted.
  | { kind: 'next' }
  // review-mr: a posted comment's card closed, the topic waiting on its author as it was.
  | { kind: 'keep-waiting' }
  | { kind: 'ack' }
  // The next move is the author's after all: the topic waits on them until they push or write.
  | { kind: 'wait' }
  | { kind: 'link' }
  // review-mr: the person's own posting of a draft kept as posted, the check over.
  | { kind: 'posted-ok' }
  | { kind: 'sync' }
  | { kind: 'own-done' }
  | { kind: 'no-follow-up' }
  | { kind: 'settle'; thread: string; how: 'merge' | 'separate' | 'link'; into: string }
  // Whether to generate an explainer for the MR, answering the agent's recommendation.
  | { kind: 'explainer'; how: 'skip' | 'generate' }

export type BandButton = { key: string; label: string; press: Press; isPrimary?: true }

// The step the current topic is at, read off its status and whether it holds a draft.
export type Step = 'topic' | 'draft' | 'check' | 'left' | 'reack' | 'reply' | 'merged' | 'watch' | 'waiting' | 'none'

export const currentTopic = (review: ReviewState | null): ReviewTopic | undefined =>
  review?.topics.find(t => t.id === review.current)

export const stepOf = (topic: ReviewTopic | undefined): Step => {
  if (!topic) return 'none'
  // Posted by the person's own hand, and being checked against the draft.
  if (topic.post_check) return 'check'
  // The MR was merged or closed with this unsettled: the person raises it elsewhere or drops it.
  if (topic.left) return 'left'
  if (topic.follow_up === 'offer' && !topic.draft) return 'merged'
  if (topic.status === 'draft') return topic.draft ? 'draft' : 'topic'
  if (topic.status === 'needs_ack') return topic.draft ? 'reply' : 'reack'
  // Another reviewer's thread, opened from the inbox for the person's information.
  if (topic.peer && topic.peer_view?.says === 'fyi' && topic.status === 'acked') return 'watch'
  // The person's own comment, posted, waiting on the author: nothing is theirs to do, but its
  // card, opened to look at, says what they can do (keep waiting, accept it, follow up).
  // With a follow-up drafted, its card is that draft's, to post.
  if (topic.status === 'open' && topic.posted && !topic.peer) return topic.draft ? 'reply' : 'waiting'
  return 'none'
}

const ask = (prompt: string): Press => ({ kind: 'ask', prompt })

// Whether the round has another topic to go on to: the way on is offered only then. A card
// with none left, or opened while no round runs, is closed with its ✕ instead
// (register.tsx's `drawCard`).
const hasNext = (review: ReviewState | null, tid: string): boolean =>
  review?.in_round !== false && (!review?.round || review.round.some(id => id !== tid))

// A pushed fix kept in git, in the diff viewer: on every card of a topic that has one.
export const showFix: BandButton = { key: 'hunk-pushed', label: 'Show fix in hunk', press: { kind: 'hunk-pushed' } }

// Whether a topic must be finished before another one, or none, comes up: a fix made and not
// pushed holds it (its files are in the working tree). Its card has no ✕ then.
export const isHeldTopic = (t: ReviewTopic | undefined): boolean => t?.step === 'fixed'

// Said with every prompt whose answer comes with a card: the person decides on the card, so
// the answer comes first and the card last.
const SAY_FIRST = 'Say your answer first, then show the card as the last thing in your reply.'

// review-mr: what the agent is asked once the person posted a draft by their own hand, where
// they often reword it: its check of the posted text, and the next topic only once it holds.
export const checkPrompt = (t: string): string => `I posted the comment for ${t} myself and may have reworded it: check it. Run \`posted ${t}\` and compare what I posted with your draft. In two or three lines: does it still say what it should, correctly and in a fitting tone? Where it does, say so and run \`posted ${t} --ok\`, which opens the next topic. Where it does not, store your verdict (a line or two) with a corrected version of what I posted, as close to my wording as it can be: \`posted ${t} --differs "<verdict>" --fix -\` with the text on stdin (a quoted heredoc). It shows its card; editing the comment on GitLab is my press there. ${SAY_FIRST}`

// The language drafts are written in, said with every prompt that asks for one.
const inLang = (review: ReviewState | null): string => `, written in ${review?.lang ?? 'en'}`

// The first button wherever a finding is decided on: the agent explains it in depth, with
// an example, before the person chooses. The card's analysis is the short form.
//
// Once the agent explained it, the button asks for more of the same: what is still unclear,
// from another angle, without repeating the explanation already given.
const explain = (t: string, review?: ReviewState | null): BandButton => (review?.explained ?? []).includes(t)
  ? {
    key: 'explain', label: 'Explain further',
    press: ask(`Explain ${t} further: I read your explanation and it is still not clear to me. Take another angle (a smaller example, what the code does step by step, or what happens if we leave it), and do not repeat what you already said.`),
  }
  : {
    key: 'explain', label: 'Explain',
    press: ask(`Explain ${t} in detail: what goes wrong, with a concrete example (the input or the steps that trigger it), and why your recommendation follows.`),
  }

// The buttons for one step, in the order the person meets them; the first is the usual
// answer. Labels are the design's; the hotkey is the place in the list.
// One rule for every step of both skills: the recommended answer (the primary button) comes
// first; the step's other answers follow in their own fixed order; then Explain, then the
// way on (Next to plan, Next to fix or Next topic). The recommendation only ever picks among the answers,
// so only the head moves from one topic to the next.
const TAIL = ['explain', 'next']
const ordered = (bs: BandButton[]): BandButton[] => {
  const head = bs.find(b => b.isPrimary && !TAIL.includes(b.key))
  const answers = bs.filter(b => b !== head && !TAIL.includes(b.key))
  const tail = TAIL.flatMap(k => bs.filter(b => b.key === k))
  return [...(head ? [head] : []), ...answers, ...tail]
}

// Each action's icon, by its label: one action draws the same icon wherever it is offered,
// in either skill. Code points from the Nerd Fonts name list (v3.5.1), by the names chosen.
const ICON: Record<string, string> = {
  'Agree plan': '\u{F012C}', // md-check
  'Agree and reply': '\u{F0180}', // md-comment_check_outline
  // Every press that has a text written, not posted: the pencil says it, so no label needs
  // to say "Draft".
  'Reply': '\u{F03EB}', 'Reply…': '\u{F03EB}', 'Reply instead…': '\u{F03EB}', 'Reply now': '\u{F03EB}', // md-pencil
  'Comment': '\u{F03EB}', 'Follow up': '\u{F03EB}', // md-pencil
  'Fix it': '\u{F05B7}', 'Fix instead…': '\u{F05B7}', // md-wrench
  'Change plan…': '\u{F04E1}', // md-swap_horizontal
  'Propose a plan': '\u{F0982}', // md-map_outline
  'Explain': '\u{F1739}', 'Explain further': '\u{F1739}', 'Analyse more': '\u{F1739}', // md-chat_question_outline
  'Next': '\u{F0BB1}', // md-page_next_outline
  'Post': '\u{F048A}', 'Send follow-up': '\u{F048A}', '“Fixed”': '\u{F048A}', // md-send
  'Post + resolve': '\u{F1161}', '“Fixed” + resolve': '\u{F1161}', // md-send_check
  'Copy': '\u{F0CFA}', // md-clipboard_arrow_right_outline
  'Check my post': '\u{F0008}', // md-account_check
  'Check again': '\u{F0450}', // md-refresh
  'Apply edit': '\u{F1A7D}', // md-text_box_edit_outline
  'Keep as posted': '\u{F012C}', // md-check
  'Review in hunk': '\u{F08AA}', 'Show fix in hunk': '\u{F08AA}', 'Show in hunk': '\u{F08AA}', // md-file_compare
  'Read hunk notes': '\u{F0A3D}', // md-comment_search_outline
  'Redo…': '\u{F099B}', // md-restore
  'Run the checks': '\u{F0096}', 'Fix the checks': '\u{F0096}', // md-flask_outline
  'Push': '\u{F0552}', 'Push and resolve': '\u{F0552}', 'Ack and push': '\u{F0552}', // md-upload
  'Drop': '\u{F0A7A}', // md-trash_can_outline
  'Summarize': '\u{F0279}', // md-format_list_bulleted
  'Keep separate': '\u{F093C}', // md-arrow_split_vertical
  'Judge': '\u{F029B}', // md-gavel
  'Ack': '\u{F0514}', 'Ack + resolve': '\u{F0514}', 'Accept': '\u{F0514}', 'Accept + resolve': '\u{F0514}', 'Accept anyway': '\u{F0514}', // md-thumb_up_outline
  'Not enough…': '\u{F0512}', // md-thumb_down_outline
  'Wait for author': '\u{F051F}', 'Keep waiting': '\u{F051F}', // md-timer_sand
  'Nothing to add': '\u{F0855}', // md-check_outline
  'Seen': '\u{F0D05}', // md-eye_check_outline
  'Generate explainer': '\u{F0B63}', // md-book_open_outline
  'Start planning': '\u{F040A}', 'Start fixing': '\u{F040A}', 'Start the round': '\u{F040A}', 'Open your comment': '\u{F040A}', 'Check the answers': '\u{F040A}', // md-play
  'sync': '\u{F04E6}', // md-sync
  'Legend': '\u{F064E}', // md-information_variant
  'Show sidebar': '\u{F06FE}', // md-page_layout_sidebar_right
  'Skip explainer': '\u{F04AD}', // md-skip_next
  'My review is done': '\u{F0BE2}', // md-account_check_outline
}

export const iconOf = (label: string): string | undefined =>
  ICON[label] ?? (/^(?:Check t\d+|Check \d+ answers|Decide \d+)$/.test(label) ? '\u{F040A}' // md-play: the box's way into the topics
    : label.startsWith('Merge into ') ? '\u{F0F5C}' // md-merge
    : label.startsWith('Link as ') ? '\u{F0339}' : undefined) // md-link_variant

export const buttonsFor = (review: ReviewState | null): BandButton[] => {
  if (review?.skill === 'rework-mr') return ordered(reworkButtons(review))
  const buttons = stepButtons(review)
  const topic = currentTopic(review)
  // Every topic the person has open can be put off: to the end of the round, and once
  // there, out of it.
  if (!topic || buttons === null || review?.skill !== 'review-mr' || stepOf(topic) === 'watch') return ordered(buttons ?? [])
  // The author's push that changed its lines, to look at: never the recommendation.
  const look = topic.fix_kept && !topic.peer ? [showFix] : []
  return ordered([...buttons, ...look, ...(hasNext(review, topic.id) ? [{ key: 'next', label: 'Next', press: { kind: 'next' } } as BandButton] : [])])
}

// The step's own buttons; null where the step is not a topic's (the person's own review, a
// comment of theirs to match), which are not put off.
const stepButtons = (review: ReviewState | null): BandButton[] | null => {
  // The first question of a fresh review: an explainer, or not. The agent's answer first.
  if (review?.explainer) {
    const skip: BandButton = { key: 'explainer-skip', label: 'Skip explainer', press: { kind: 'explainer', how: 'skip' } }
    const make: BandButton = { key: 'explainer-generate', label: 'Generate explainer', press: { kind: 'explainer', how: 'generate' } }
    const [first, second] = review.explainer.recommend === 'generate' ? [make, skip] : [skip, make]
    return [{ ...first, isPrimary: true }, second]
  }
  if (review?.own_review === 'pending') {
    return [
      // One way on: with no comments of the person's own to match, the press opens the
      // topics itself; with some, the agent matches them (register.tsx's `own-done`).
      { key: 'own-done', label: 'My review is done', press: { kind: 'own-done' }, isPrimary: true },
    ]
  }
  // A comment of the person's own to match is a step of the round of its own, between two
  // topics: never while a topic is open.
  const proposal = review?.current ? undefined : review?.proposals?.[0]
  if (proposal && review?.in_round !== false) {
    // The agent's recommendation first, so `1` is always the answer it gave.
    const merge: BandButton = { key: 'settle-merge', label: `Merge into ${proposal.into}`,
      press: { kind: 'settle', thread: proposal.thread, how: 'merge', into: proposal.into } }
    const separate: BandButton = { key: 'settle-separate', label: 'Keep separate',
      press: { kind: 'settle', thread: proposal.thread, how: 'separate', into: proposal.into } }
    // A comment that is a draft of the agent's, posted by hand, is linked rather than merged.
    const link: BandButton = { key: 'settle-link', label: `Link as ${proposal.into}'s comment`,
      press: { kind: 'settle', thread: proposal.thread, how: 'link', into: proposal.into } }
    const [first, second] = proposal.recommend === 'separate' ? [separate, merge]
      : proposal.recommend === 'link' ? [link, separate] : [merge, separate]
    // Explain first, as on every step where the person decides; then the agent's answer.
    const isExplained = (review?.explained ?? []).includes(proposal.thread)
    return [
      { key: 'explain', label: isExplained ? 'Explain further' : 'Explain',
        press: ask(isExplained
          ? `Explain further whether my comment at ${proposal.where} is the same point as ${proposal.into}: it is still not clear to me. Take another angle, and do not repeat what you already said.`
          : `Explain whether my comment at ${proposal.where} is the same point as ${proposal.into}: compare the two, what each covers that the other does not, and why your recommendation follows.`) },
      { ...first, isPrimary: true },
      second,
    ]
  }
  if (review?.own_review === 'matching' || proposal) return null
  const topic = currentTopic(review)
  const t = topic?.id ?? ''
  switch (stepOf(topic)) {
    case 'topic':
      return [
        explain(t, review),
        { key: 'draft', label: 'Comment', press: ask(`Draft the comment for ${t}${inLang(review)}.`), isPrimary: true },
        { key: 'drop', label: 'Drop', press: { kind: 'drop' } },
      ]
    case 'draft':
      return [
        { key: 'post', label: 'Post', press: { kind: 'post' }, isPrimary: true },
        { key: 'link', label: 'Check my post', press: { kind: 'link' } },
      ]
    case 'left':
      return [
        { key: 'summarize', label: 'Summarize', isPrimary: true,
          press: ask(`!${review?.iid} is ${review?.mr?.state ?? 'merged'}, and ${t} was not settled. Summarize in a few lines why it still needs addressing, and what would address it now (a follow-up change, a ticket, a word with the author).`) },
        { key: 'drop', label: 'Drop', press: { kind: 'drop' } },
      ]
    case 'check':
      if (topic?.post_differs) return differsButtons(t, checkPrompt(t))
      return [
        { key: 'check-again', label: 'Check again', press: ask(`I edited the comment for ${t} on GitLab. ${checkPrompt(t)}`), isPrimary: true },
        { key: 'keep', label: 'Keep as posted', press: { kind: 'posted-ok' } },
      ]
    case 'reack': {
      // An answer with no verdict yet is the background agent's to judge: nothing to decide
      // until its judgment is on the card. One it could not judge is the chat's.
      if (review?.judge?.topics.includes(t)) {
        return (review.unjudged ?? []).includes(t)
          ? [{ key: 'judge', label: 'Judge', isPrimary: true,
            press: ask(topic?.peer
              ? `Judge ${t}, another reviewer's thread the user watches: read it whole (\`bodies ${t}\`, every comment in order) and store your view (\`peer ${t} fyi|needs --why "…"\`, with \`--summary\` where it has none, and for \`needs\` its \`--advice reply|merge|seen\`); then show ${t}.`
              : `Judge the news on ${t}: read the whole thread (\`bodies ${t}\`, every comment in order) and the change since the comment (\`diff ${t}\`), store your verdict (\`verdict ${t} settled|more|wait --asked "…" --did "…" --text "…"\`, with \`--case\` for \`more\`, \`--asked\` only where the topic has none); then show ${t}.`) }]
          : []
      }
      // The agent's verdict on the author's answer is on the card; the buttons follow it.
      const ack: BandButton = { key: 'ack', label: 'Ack', press: { kind: 'ack' } }
      if (topic?.peer) {
        // Another reviewer's thread the agent judged needs the person: its advice first.
        // Seen closes it for the person only; the thread is its reviewer's to resolve.
        const view = topic.peer_view
        const reply: BandButton = { key: 'reply', label: 'Reply…', press: { kind: 'fill', text: `Draft my reply for ${t}${inLang(review)}: ` } }
        const seen: BandButton = { key: 'seen', label: 'Seen', press: { kind: 'ack' } }
        const merge: BandButton[] = view?.advice === 'merge' && view.into
          ? [{ key: 'merge', label: `Merge into ${view.into}`, press: { kind: 'merge', into: view.into } }] : []
        const order = view?.advice === 'merge' ? [...merge, reply, seen]
          : view?.advice === 'seen' ? [seen, reply] : [reply, seen]
        return order.map((b, i) => (i === 0 ? { ...b, isPrimary: true as const } : b))
      }
      // A judged answer: the verdict's own answer first. Accepting resolves the GitLab thread
      // and closes the topic in one press (Accept alone where the thread is resolved already);
      // replying says the answer is not good enough; Analyse more asks the agent to look again.
      const accept = (label: string): BandButton => (topic?.resolved
        ? { ...ack, label: label.replace(' + resolve', '') }
        : { key: 'resolve', label, press: { kind: 'resolve' } })
      const analyse: BandButton = { key: 'explain', label: 'Analyse more',
        press: ask(`Analyse ${t} more: read the answer and the change again, deeper than the verdict on its card, and say whether the verdict still holds and why. Where it does not, store the new one (\`verdict\`, with its \`--case\` where it is \`more\`): its output is a card of the change, and the buttons follow it. Do not show the topic again; your reply gives the details.`) }
      if (topic?.verdict === 'settled') {
        return [
          { ...accept('Accept + resolve'), isPrimary: true },
          { key: 'respond', label: 'Not enough…', press: { kind: 'fill', text: `Reply for ${t}, the answer is not good enough: ` } },
          analyse,
        ]
      }
      if (topic?.verdict === 'more') {
        return [
          { key: 'follow', label: 'Send follow-up', isPrimary: true,
            press: ask(`Draft the follow-up reply for ${t}${inLang(review)}. The verdict on its card, which the background check stored: ${topic.verdict_text ?? '(see its card)'}`) },
          { key: 'wait', label: 'Wait for author', press: { kind: 'wait' } },
          accept('Accept anyway'),
          analyse,
        ]
      }
      return [
        explain(t, review),
        { ...ack, isPrimary: true },
        { key: 'follow', label: 'Follow up', press: ask(`Draft a follow-up reply for ${t}${inLang(review)}.`) },
      ]
    }
    case 'reply':
      return [
        { key: 'post', label: 'Post', press: { kind: 'post' }, isPrimary: true },
        // Posted by hand into the thread: the agent checks it against the draft, as for a
        // comment linked by hand and in rework-mr.
        { key: 'link', label: 'Check my post', press: ask(checkPrompt(t)) },
        // A reply drafted before the author's answer, which the agent judged: the answer may
        // leave the next move to the author, and the draft unsent.
        ...(topic?.verdict === 'more' ? [{ key: 'wait', label: 'Wait for author', press: { kind: 'wait' } } as BandButton] : []),
        // Already waiting on the author: the draft can wait with it, kept for later.
        ...(topic?.status === 'open' ? [{ key: 'keep-waiting', label: 'Keep waiting', press: { kind: 'keep-waiting' } } as BandButton] : []),
      ]
    case 'merged':
      return [
        explain(t, review),
        { key: 'follow', label: 'Follow up', isPrimary: true,
          press: ask(`Draft the follow-up reply for ${t}, below my own comment${inLang(review)}.`) },
        { key: 'no-follow', label: 'Nothing to add', press: { kind: 'no-follow-up' } },
      ]
    case 'watch':
      return [
        { key: 'seen', label: 'Seen', press: { kind: 'leave' }, isPrimary: true },
        { key: 'reply', label: 'Reply…', press: { kind: 'fill', text: `Draft my reply for ${t}${inLang(review)}: ` } },
      ]
    case 'waiting': {
      // Already resolved on GitLab, accepting is an ack; otherwise it resolves the thread too.
      const accept: BandButton = topic?.resolved
        ? { key: 'ack', label: 'Accept anyway', press: { kind: 'ack' } }
        : { key: 'resolve', label: 'Accept anyway', press: { kind: 'resolve' } }
      return [
        { key: 'keep-waiting', label: 'Keep waiting', press: { kind: 'keep-waiting' }, isPrimary: true },
        { key: 'follow', label: 'Follow up', press: ask(`Draft a follow-up reply for ${t}${inLang(review)}.`) },
        accept,
      ]
    }
    case 'none':
      // Nothing to decide: the sidebar's inbox says what waits, and the update check notes
      // what arrives.
      return null
  }
}

// A comment the person posted by hand that the agent found no longer holds: its corrected
// version applied on GitLab with a press, or the person's own edit checked again, or what
// went up kept as it is. One card for both skills.
const differsButtons = (t: string, check: string): BandButton[] => [
  { key: 'apply-edit', label: 'Apply edit', press: { kind: 'apply-edit' }, isPrimary: true },
  { key: 'check-again', label: 'Check again', press: ask(`I edited the comment for ${t} on GitLab. ${check}`) },
  { key: 'keep', label: 'Keep as posted', press: { kind: 'posted-ok' } },
]

// rework-mr's steps (threads.py's `step_of`): a plan to agree, a fix to make, a fix to push,
// a reply to post. The agent's answer first; the way on to the next topic on every card.
// The usual answer is marked; Explain, where there is one, comes before it, as in review-mr.
const primary = (bs: BandButton[]): BandButton[] => {
  if (bs.some(b => b.isPrimary)) return bs
  const at = bs[0]?.key === 'explain' ? 1 : 0
  return bs.map((b, i) => (i === at ? { ...b, isPrimary: true as const } : b))
}

// rework-mr: what the agent is asked for a reply ahead of the fix, from the plan agreed.
// rework-mr: what the agent is asked once the person posted a reply by their own hand, where
// they often reword it. Mirrors review-mr's `checkPrompt`.
export const reworkCheckPrompt = (t: string): string => `I posted the reply for ${t} myself and may have reworded it: check it. Run \`posted ${t}\` and compare what I posted with your draft. In two or three lines: does it still say what it should, correctly and in a fitting tone? Where it does, say so and run \`posted ${t} --ok\`. Where it does not, store your verdict (a line or two) with a corrected version of what I posted, as close to my wording as it can be: \`posted ${t} --differs "<verdict>" --fix -\` with the text on stdin (a quoted heredoc). It shows its card; editing the reply on GitLab is my press there. ${SAY_FIRST}`

// rework-mr: what the agent is asked once a plan that is a reply was agreed.
export const replyPrompt = (t: string): string => `Draft the reply for ${t} from its agreed plan, in the language of its thread. Store it (\`set ${t} --reply -\`) and show it (\`reply-view ${t}\`).`

export const earlyPrompt = (t: string): string => `Draft a reply for ${t}'s thread to post ahead of its fix, in the language of its thread: from the agreed plan, tell the reviewer what we will do (the option chosen, where they offered several) and that the fix follows. Store it as a reply ahead of the fix (\`set ${t} --reply - --early\`) and show it (\`reply-view ${t}\`).`

// The way on from a card: the round's next topic, in either pass. It leaves this one
// as it is, to come up again once the round gets back to it; nothing is put off or counted.
const wayOn = (review: ReviewState, topic: ReviewTopic): BandButton[] => (hasNext(review, topic.id)
  ? [{ key: 'next', label: 'Next', press: { kind: 'next' } }]
  : [])

const reworkButtons = (review: ReviewState): BandButton[] => {
  const topic = currentTopic(review)
  if (!topic) return []
  const t = topic.id
  const lang = ', in the language of its thread'
  // A reply in place of a plan (a question back, a push-back, another suggestion): the
  // person types its gist, and the agent drafts it and stores it as the topic's plan.
  const replyInstead: BandButton = { key: 'reply-instead', label: 'Reply instead…', press: { kind: 'fill', text: `Reply to ${t} instead: ` } }
  const handled = (label: string, did: string): BandButton => ({ key: `handled-${label}`, label, press: { kind: 'handled', did } })
  const isExplained = (review.explained ?? []).includes(t)
  const explain: BandButton = isExplained
    ? { key: 'explain', label: 'Explain further', press: ask(`Explain ${t} further: it is still not clear to me. Take another angle (a smaller example, what the code does step by step, or what happens if we leave it), and do not repeat what you already said.`) }
    : { key: 'explain', label: 'Explain', press: ask(`Explain ${t} in detail: what the reviewer asks, what the code does now, with a concrete example, and why your recommendation follows.`) }
  // A reply ahead of the fix: which option the plan took, that the fix follows. The agent
  // drafts it from the agreed plan; posting it resolves nothing.
  const replyNow: BandButton = { key: 'reply-now', label: 'Reply now', press: ask(earlyPrompt(t)) }
  let bs: BandButton[] = []
  // A reply posted by hand that no longer holds, at whatever step it went up (ahead of the
  // fix, or after it): its card is the corrected version's, whatever the step's own is.
  if (topic.post_differs) return [...differsButtons(t, reworkCheckPrompt(t)), ...wayOn(review, topic)]
  switch (topic.step) {
    case 'plan': {
      const p = topic.proposal
      if (!p && review.judge?.topics.includes(t) && !(review.unjudged ?? []).includes(t)) return []
      if (p?.says === 'fyi') {
        bs = [handled('Seen', 'saw it; it asks nothing of them'), { key: 'reply', label: 'Reply…', press: { kind: 'fill', text: `Draft my reply for ${t}${lang}: ` } }]
      } else if (p && p.outcome && p.outcome !== 'fix') {
        // The plan is a reply (decline, push back, answer): agreeing it is drafting it, and
        // the other answer is doing what was asked after all.
        bs = [{ key: 'draft-reply', label: 'Reply', press: { kind: 'agree', draft: true }, isPrimary: true }, explain,
          { key: 'fix-instead', label: 'Fix instead…', press: { kind: 'fill', text: `Fix ${t} instead: ` } }]
      } else if (p) {
        // A plan that answers the reviewer now (a question, a choice among options) is best
        // agreed with that answer; any other is agreed as it is.
        const isAnswer = p.answer_now === true
        bs = [explain, { key: 'agree', label: 'Agree plan', press: { kind: 'agree' }, ...(isAnswer ? {} : { isPrimary: true as const }) },
          { key: 'agree-reply', label: 'Agree and reply', press: { kind: 'agree', reply: true }, ...(isAnswer ? { isPrimary: true as const } : {}) }, replyInstead]
      } else {
        bs = [{ key: 'propose', label: 'Propose a plan', press: ask(`Research ${t} and propose a plan: store it (\`propose\`), then show ${t}.`) },
          replyInstead]
      }
      break
    }
    case 'fix':
      bs = topic.early
        // The reply ahead of the fix, drafted: posted as it is, or copied to edit by hand.
        ? [{ key: 'post', label: 'Post', press: { kind: 'post', reply: true, early: true } }, { key: 'posted-check', label: 'Check my post', press: ask(reworkCheckPrompt(t)) }]
        // Planned, while the plan pass runs: the fixes come once every topic has a plan.
        : review.pass === 'plan'
          // Once a reply went up ahead of the fix, another would answer the person's own.
          ? [...(topic.early_posted ? [] : [replyNow]), { key: 'replan', label: 'Change plan…', press: { kind: 'fill', text: `Change the plan for ${t}: ` } }]
          : [{ key: 'fix', label: 'Fix it', press: ask(`Make the fix for ${t} as agreed, run its checks (\`verify ${t}\`) until they pass, then show it (\`diff-view.sh ${t}\`, with \`--did\`, \`--fixup\` or \`--new-commit\`, and \`--as-agreed\` or \`--differs\`).`) }]
      break
    case 'fixed': {
      // The diff viewer: opened on a press, then read on one, once the person wrote there.
      // A change of mind is a prompt or a note in the viewer, so there is no Redo.
      const isOpen = review.hunk?.open === true
      const hasNotes = isOpen && (review.hunk?.notes ?? 0) > 0
      const hunk: BandButton = isOpen
        ? { key: 'hunk-notes', label: 'Read hunk notes', ...(hasNotes ? { isPrimary: true as const } : {}),
          press: ask(`Read my notes on ${t}'s fix in the diff viewer (\`hunk-notes\`), change the fix where they ask for it (and run its checks again), answer each note, then show the fix again. ${SAY_FIRST} Pushing is my press on its card.`) }
        // A fix that differs from the plan agreed is looked at before it is pushed.
        : { key: 'hunk', label: 'Review in hunk', press: { kind: 'hunk' }, ...(topic.differs ? { isPrimary: true as const } : {}) }
      if (topic.checks !== 'passed') {
        // Push only for a fix whose checks passed on exactly these files.
        const again: BandButton = { key: 'check', label: topic.checks === 'failed' ? 'Fix the checks' : 'Run the checks',
          press: ask(`Run the checks for ${t} (\`verify ${t}\`), fix what fails, and show the fix again.`), isPrimary: true }
        bs = [again, hunk]
      } else {
        // "Ack": the person approves the change; the press pushes it and does nothing else.
        const isReviewFirst = hasNotes || (topic.differs === true && !isOpen)
        bs = [{ key: 'push', label: 'Ack and push', press: { kind: 'push' }, ...(isReviewFirst ? {} : { isPrimary: true as const }) }, hunk]
      }
      // A fix made and not pushed holds the topic: nothing to put off, and no other topic
      // until it is pushed or undone.
      return ordered(bs)
    }
    case 'reply': {
      // Posted by hand, often reworded: the agent checks it against the draft, as in review-mr.
      const replied: BandButton = { key: 'posted-check', label: 'Check my post', press: ask(reworkCheckPrompt(t)) }
      if (topic.draft) {
        // Rewording is a prompt, or Copy and the person's own edit: no Redo. A reply after
        // the fix explains it and never resolves: the reviewer reads it and resolves.
        bs = [{ key: 'post', label: 'Post', press: { kind: 'post', reply: true }, isPrimary: true },
          ...(topic.pushed ? [] : [{ key: 'post-resolve', label: 'Post + resolve', press: { kind: 'post', reply: true, resolve: true } } as BandButton]),
          replied, ...(topic.fix_kept ? [showFix] : [])]
      } else if (topic.pushed) {
        // After the push: a "Fixed" with its link, resolved or not, or a reply that says more.
        // A reply is the recommendation where there is something to say: the fix differs
        // from the plan agreed, or the plan answers the reviewer and nothing went up yet.
        const hasMore = topic.differs === true || (topic.answer_now === true && !topic.early_posted)
        bs = [{ key: 'post-fixed', label: '“Fixed”', press: { kind: 'post', fixed: true } },
          { key: 'post-fixed-resolve', label: '“Fixed” + resolve', press: { kind: 'post', fixed: true, resolve: true }, ...(hasMore ? {} : { isPrimary: true as const }) },
          { key: 'draft', label: 'Reply…', press: { kind: 'fill', text: `Draft the reply for ${t} after its fix${lang}: ` }, ...(hasMore ? { isPrimary: true as const } : {}) },
          ...(topic.fix_kept ? [showFix] : [])]
      } else {
        bs = [{ key: 'draft', label: 'Reply', press: ask(`Draft the reply for ${t}${lang}, and show it.`) }, replied]
      }
      break
    }
    default:
      // Nothing of the person's to do on it: a pushed fix can still be looked at, which
      // answers nothing, so it is no recommendation.
      return topic.fix_kept ? [showFix, ...wayOn(review, topic)] : []
  }
  return [...primary(bs), ...wayOn(review, topic)]
}

// The script arguments a press runs, for the presses that run one. `copy` takes more than
// arguments (a clipboard) and is built where it runs.
export const argsFor = (press: Press, topic: string, card: ReviewCard | null): string[] | undefined => {
  switch (press.kind) {
    case 'post':
      return ['post', topic, ...(press.fixed ? ['--fixed'] : card?.topic === topic && card.draft ? ['--expect', card.draft] : []),
        ...(press.resolve ? ['--resolve'] : [])]
    case 'agree':
      return ['agree', topic]
    case 'handled':
      return ['wait', topic]
    case 'hunk':
      return ['hunk-show', topic]
    case 'hunk-pushed':
      return ['hunk-show', topic, '--pushed', ...(press.push ? ['--push', String(press.push)] : [])]
    case 'push':
      return ['push', topic]
    case 'drop':
      return ['drop', topic]
    case 'ack':
      return ['set', topic, '--state', 'acked']
    case 'wait':
      return ['verdict', topic, 'wait', '--text', 'The user chose to wait for the author to push or answer.']
    case 'resolve':
      return ['resolve', topic]
    case 'link':
      return ['link', topic]
    case 'posted-ok':
      return ['posted', topic, '--ok']
    case 'apply-edit':
      return ['posted', topic, '--apply']
    case 'sync':
      return ['sync']
    case 'own-done':
      return ['own-review', 'done']
    case 'no-follow-up':
      return ['follow-up', topic, 'none']
    case 'settle':
      return ['settle', press.thread, press.how]
    case 'merge':
      return ['merge', press.into, topic]
    case 'explainer':
      return ['explainer', '--choose', press.how]
    case 'leave':
    case 'next':
    case 'keep-waiting':
      return ['leave']
    default:
      return undefined
  }
}

// What a press did, in the words the band shows and the model reads.
const DID: Partial<Record<Press['kind'], string>> = {
  post: 'posted its draft on GitLab, and the topic is linked to the posted comment',
  copy: 'copied its draft to the clipboard',
  drop: 'dropped it',
  ack: 'acked it',
  wait: 'chose to wait for the author: it is theirs again until they push or answer',
  resolve: 'accepted the answer: resolved its thread on GitLab and closed the topic',
  link: 'linked the comment they posted themselves; the agent checks it against the draft',
  'posted-ok': 'kept their posted comment as it is',
  'apply-edit': 'edited their posted comment on GitLab to the corrected version',
  sync: 'checked the MR for news',
  leave: 'saw it; it is its reviewer\'s to settle',
  next: 'moved on to the next topic; this one stays as it is',
  'keep-waiting': 'keeps waiting for the author; the topic stays as it is',
}

// What a press did to its card, as the card's last line: the pressed button's icon, then one
// sentence, with the link the script printed (the posted comment, the pushed diff) inline
// in it. Null for a press that leaves its card as it was (a prompt for the model, a copy,
// opening the viewer).
export const outcomeOf = (press: Press, stdout: string): ReviewOutcome | null => {
  const url = /https?:\/\/\S+/.exec(stdout)?.[0] ?? null
  const said = (button: string, text: string, link?: { label: string; after?: string }): ReviewOutcome =>
    ({ icon: iconOf(button) ?? null, text, ...(link && url ? { url, label: link.label, after: link.after ?? null } : {}) })
  switch (press.kind) {
    case 'post': {
      const what = press.fixed ? 'Posted “Fixed”' : 'Posted'
      const button = press.fixed ? (press.resolve ? '“Fixed” + resolve' : '“Fixed”') : press.resolve ? 'Post + resolve' : 'Post'
      return said(button, `${what} to`, { label: 'its thread', after: press.resolve ? 'and resolved' : undefined })
    }
    case 'push':
      return said('Push', 'Pushed', { label: 'the fix' })
    case 'apply-edit':
      return said('Apply edit', 'Edited', { label: 'your comment', after: 'on GitLab' })
    case 'resolve':
      return said('Accept + resolve', 'Accepted the answer and resolved its thread')
    case 'agree':
      return said('Agree plan', 'Plan agreed')
    case 'drop':
      return said('Drop', 'Dropped')
    case 'ack':
      return said('Ack', 'Acked')
    case 'wait':
      return said('Wait for author', 'Waiting for the author')
    case 'keep-waiting':
      return said('Keep waiting', 'Waiting for the author')
    case 'handled':
      return said('Seen', 'Seen')
    case 'posted-ok':
      return said('Keep as posted', 'Kept as posted')
    case 'merge':
      return said(`Merge into ${press.into}`, `Merged into ${press.into}`)
    default:
      return null
  }
}

export const didText = (press: Press, topic: string, into?: string): string => {
  if (press.kind === 'handled') return `${topic}: ${press.did}`
  if (press.kind === 'agree') return `${topic}: agreed the proposed plan${press.reply ? '; a reply ahead of the fix comes next' : press.draft ? '; its reply is drafted next' : ''}`
  if (press.kind === 'post' && press.early) return `${topic}: posted the reply ahead of its fix; the fix follows`
  if (press.kind === 'hunk') return `${topic}: opened the fix in the diff viewer`
  if (press.kind === 'hunk-pushed') return `${topic}: opened the pushed fix in the diff viewer`
  if (press.kind === 'push') return `${topic}: pushed the fix`
  if (press.kind === 'post' && press.fixed) return `${topic}: posted “Fixed” with the diff link${press.resolve ? ' and resolved the thread' : ''}`
  if (press.kind === 'post' && press.resolve) return `${topic}: posted the reply and resolved the thread`
  if (press.kind === 'post' && press.reply) return `${topic}: posted the reply in its thread`
  if (press.kind === 'merge') return `merged ${topic} into ${into ?? press.into}`
  if (press.kind === 'own-done') return 'their own review is done; the topics start'
  if (press.kind === 'explainer') return press.how === 'skip' ? 'skipped the explainer' : 'chose to generate an explainer'
  if (press.kind === 'no-follow-up') return `${topic}: nothing to add to their comment`
  if (press.kind === 'settle') {
    return press.how === 'merge'
      ? `merged their comment into ${press.into}`
      : press.how === 'link' ? `linked their comment to ${press.into} as its posted draft`
      : 'kept their comment as a topic of its own'
  }
  const did = DID[press.kind] ?? press.kind
  return topic ? `${topic}: ${did}` : did
}

// The note the model reads at its next turn, so it knows what the person decided without
// a turn of its own having been spent on it.
export const noteForModel = (did: string, before: string | null, after: string | null): string => {
  const moved = after && after !== before
    ? ` ${after} is the current topic now, and its card is on the user's screen.`
    : after ? '' : ' No topic needs the user now.'
  return `[review-ui] The user pressed a button: ${did}.${moved} Carry on from there.`
}

// The review with nothing left to decide for now: every topic is handled, and what is open
// waits on the other side. How many topics wait, and on whom.
export const waitingOf = (review: ReviewState | null): { count: number; who: string } | undefined => {
  // Not while the person's own review is open, nor while their comments are being matched to
  // the topics: the topics are still to come then, though none is current yet.
  if (!review || review.topics.length === 0 || review.own_review === 'pending' || review.own_review === 'matching') return undefined
  // A comment of the person's own to match is the round's step; between rounds it waits in
  // the inbox like the rest.
  if ((review.proposals?.length ?? 0) > 0 && review.in_round !== false) return undefined
  // rework-mr's topics say their own step (threads.py's `step_of`); review-mr's are read
  // off their status, which a rework topic's would pass as settled.
  const cur = currentTopic(review)
  if (review.skill === 'rework-mr' ? !!cur && (cur.step ?? 'none') !== 'none' : !['none', 'waiting'].includes(stepOf(cur))) return undefined
  return {
    count: review.topics.filter(t => t.state === 'waiting').length,
    who: review.skill === 'review-mr' ? 'the author' : 'the reviewers',
  }
}

// What the band says while the review waits and the sidebar, which says more, is closed.
export const waitingLine = (w: { count: number; who: string }): string =>
  w.count > 0
    ? `Done for now: ${w.count} topic${w.count === 1 ? '' : 's'} wait${w.count === 1 ? 's' : ''} on ${w.who}.`
    : 'Done: every topic is settled.'

// The inbox, as the sidebar's box lists it between rounds: one line per kind of news with
// its topics, one per push, then what was put off. `count` is how many things need the
// person; `hasNews` whether anything arrived (else the box only says what waits).
// `detail`: what more there is to say of it (a push's size, or that it was a rebase), after
// the topics, so the topics of every line start in one column.
// The sidebar's box between rounds: what happened since the person last looked, and whether
// it needs them. One line per topic, saying who did what and the agent's verdict where it
// judged; a push is a line of its own; what needs nothing of them (an approval, auto-merge,
// a push touching no topic, another reviewer's thread to know of) is a dim line.
// `tone`: `needs` (theirs to act on, yellow), `busy` (being judged), `info` (dim).
// `kind`: what a line only to know of is about (a push, an approval…), drawn as its icon, plain
// (panel.ts's `INBOX_ICON`); a line that needs the person, or is being judged, has its mark.
export type InboxLine = { tone: 'needs' | 'busy' | 'info'; what: string; topic?: string; url?: string; size?: string; kind?: string }
export type Inbox = { lines: InboxLine[]; more: number; count: number; hasNews: boolean; since: string | null; first: string | null; button: string | null }

// Topic lines shown before the rest are summed up as "+n more": the sidebar lists them all.
const BOX_TOPICS = 5
export const clockOf = (iso: string | null | undefined): string => {
  const d = iso ? new Date(iso) : null
  return d && !Number.isNaN(d.getTime()) ? d.toTimeString().slice(0, 5) : ''
}
// How long until a moment, as the sidebar's foot says when GitLab is asked next: `in 2 min`,
// `in 5 s`, `now` once it is due. Whole minutes, rounded up, from a minute on.
export const untilOf = (at: number | null | undefined, now: number): string => {
  if (!at) return '–'
  const s = Math.ceil((at - now) / 1000)
  if (s <= 0) return 'now'
  return s < 60 ? `in ${s} s` : `in ${Math.ceil(s / 60)} min`
}

export const inboxOf = (review: ReviewState | null, judging: { topics: string[]; failed: string[] } = { topics: [], failed: [] }): Inbox | undefined => {
  const box = review?.inbox
  if (!review || !box) return undefined
  const isRework = review.skill === 'rework-mr'
  const author = review.author ?? 'the author'
  const topicLines: InboxLine[] = []
  const info: InboxLine[] = []
  // Who wrote on a topic: another reviewer's thread says who; review-mr's answers are the
  // author's; rework-mr's replies a reviewer's.
  const byOf = (id: string) => box.threads.find(x => x.id === id)?.by ?? (isRework ? 'a reviewer' : author)
  const verdictOf = (id: string): { text: string; tone: InboxLine['tone'] } | null => {
    if (judging.failed.includes(id)) return { text: 'could not be judged', tone: 'needs' }
    if (judging.topics.includes(id)) return { text: 'judging…', tone: 'busy' }
    const v = review.topics.find(t => t.id === id)?.verdict
    return v === 'settled' ? { text: 'looks fixed', tone: 'needs' } : v === 'more' ? { text: 'not fixed', tone: 'needs' } : null
  }
  const seen = new Set<string>()
  const topicLine = (id: string, did: string, isFyi = false) => {
    if (seen.has(id)) return
    seen.add(id)
    const v = isFyi ? null : verdictOf(id)
    const isResolved = did.startsWith('resolved')
    topicLines.push({
      tone: isFyi ? 'info' : v?.tone ?? 'needs', topic: id, ...(isFyi ? { kind: 'thread' } : {}),
      url: review.topics.find(t => t.id === id)?.thread_url ?? undefined,
      what: `${byOf(id)} ${did}${v ? (isResolved && v.text === 'not fixed' ? ', not fixed' : `: ${v.text}`) : ''}${isFyi ? ' (FYI)' : ''}`,
    })
  }
  for (const id of box.replies) topicLine(id, isRework ? 'replied' : 'answered')
  for (const id of box.resolved) topicLine(id, 'resolved it')
  for (const x of box.threads) topicLine(x.id, 'opened a new thread', x.fyi === true)
  // The person's own comments, posted on GitLab since, and what the agent matched them to.
  for (const p of box.own) topicLines.push({ tone: 'needs', what: `your comment: the same as ${p.into}?` })
  if (box.matching) topicLines.push({ tone: 'busy', what: `${box.matching} comment${box.matching === 1 ? '' : 's'} of yours: matching…` })
  // Merged or closed: what is still the person's, and the drafts nobody saw.
  const left = review.merged
  for (const id of left?.left ?? []) topicLine(id, 'left it open as the MR closed')
  for (const id of left?.drafts ?? []) info.push({ tone: 'info', topic: id, kind: 'draft', what: 'its draft was never posted' })
  // The MR's own news, in the order it happened. Auto-merge once the MR merged is no news;
  // who merged or closed it is the box's title.
  const isMerged = (box.mr ?? []).some(m => m.kind === 'merged')
  const timeline: { at: string; line: InboxLine }[] = [
    // Every push, dim: what it did to the MR in a few words. One that left the code as it was,
    // or a rebase that left the MR's own change as it was, says so, which is why GitLab
    // shows new commits while nothing in the review moved; a rebase that did change it says
    // how many of the MR's files, as its size would count the target branch's commits too.
    ...box.pushes.map(p => {
      const isSame = p.same === true || (p.stat ? sizeOf(p.stat)?.size.files : undefined) === 0
      const isRebaseOnly = p.rebase && p.own === 0
      const what = isSame ? 'no change to the code' : isRebaseOnly ? 'no change to the MR'
        : p.rebase && p.own ? `changes ${p.own} of its files` : null
      return { at: p.at, line: {
        tone: 'info' as const, kind: p.rebase ? 'rebase' : 'push', url: isSame || isRebaseOnly ? undefined : p.url ?? undefined,
        size: what || p.rebase ? undefined : p.stat?.replace(/`/g, '') || undefined,
        what: `${author} ${p.rebase ? 'rebased' : 'pushed'} ${clockOf(p.at)}${what ? ` · ${what}` : ''}${p.topics.length ? ` · touches ${p.topics.join(', ')}` : ''}` } }
    }),
    ...(box.mr ?? []).filter(m => m.kind === 'approved' || (m.kind === 'auto-merge' && !isMerged)).map(m => ({ at: m.at, line: {
      tone: 'info' as const, kind: m.kind, what: m.kind === 'approved' ? `${m.by ?? 'someone'} approved the MR ${clockOf(m.at)}` : `auto-merge set ${clockOf(m.at)}` } })),
  ]
  timeline.sort((a, b) => Date.parse(a.at) - Date.parse(b.at))
  info.push(...timeline.map(x => x.line))
  const shown = topicLines.slice(0, BOX_TOPICS)
  const lines = [...shown, ...info]
  const needs = topicLines.filter(l => l.tone !== 'info')
  const count = review.topics.filter(t => t.inbox === 'new' || t.inbox === 'yours').length + box.own.length
  // The button names the work it opens.
  const answers = needs.filter(l => l.topic)
  const button = isRework ? (review.pass === 'fix' ? 'Start fixing' : 'Start planning')
    : box.own.length && !answers.length ? `Decide ${box.own.length}`
    : answers.length === 1 ? `Check ${answers[0]?.topic}`
    : answers.length > 1 ? `Check ${answers.length} answers`
    : box.first ? 'Start the round' : null
  return { lines, more: topicLines.length - shown.length, count, hasNews: lines.length > 0, since: box.since, first: box.first, button }
}
