// What the review-ui mod keeps for the session. Every value is the scripts' own answer,
// held so a drawing can read it: the mod derives no review state of its own.

// Which review the session is in, as the last view's manifest named it.
export type ReviewSession = { skill: 'review-mr' | 'rework-mr'; script: string; cwd: string; iid: number }

// One topic as `findings.py state` reports it.
export type ReviewTopic = {
  id: string
  status: string
  // The state in the vocabulary both skills share (lib/ui.py): new, draft, waiting,
  // answered, done, wontfix, dropped.
  state: string
  kind?: string | null
  severity?: string | null
  source?: string | null
  summary: string | null
  file: string | null
  line?: number | string | null
  posted: boolean
  draft: string | null
  // review-mr: the user's own comment was merged in; the agent may add a follow-up reply.
  follow_up?: string | null
  // review-mr: posted by the person's own hand, being checked against the draft.
  post_check?: boolean
  // review-mr: the MR is merged or closed and this still matters.
  left?: boolean
  // review-mr: the agent's verdict on the author's answer, while that answer stands.
  verdict?: 'settled' | 'more' | null
  // Its text, which a button acting on it hands the chat: the chat did not judge it.
  verdict_text?: string | null
  // Every GitLab thread of the topic resolved.
  resolved?: boolean
  // Another reviewer's thread (or the MR author's own) that waits on the user.
  peer?: boolean
  // review-mr: the agent's view of another reviewer's thread, which the user watches: for
  // their information, or needs them, and what to do then.
  peer_view?: { says: 'fyi' | 'needs'; advice: 'seen' | 'reply' | 'merge'; into?: string | null } | null
  // review-mr: another reviewer's thread the person watches and has not closed for themselves.
  watching?: boolean
  // review-mr, where it stands in the inbox: news not opened (`new`), opened and not decided
  // (`yours`), another reviewer's thread listed for their information (`fyi`); null for a
  // topic that does not need the user.
  inbox?: 'new' | 'yours' | 'fyi' | null
  // What arrived on it (reply, resolved, push, thread) and when it last did.
  news?: string[]
  news_at?: string | null
  // rework-mr: where it stands (threads.py's `step_of`), the plan the agent proposed for
  // it, whether its fix is pushed, and whether the fix in the working tree is the diff
  // viewer's to look at first (it differs from the change agreed, or is complex).
  step?: 'plan' | 'fix' | 'fixed' | 'reply' | 'none'
  proposal?: { says: 'needs' | 'fyi'; outcome?: string | null; plan?: string | null; answer_now?: boolean | null } | null
  outcome?: string | null
  pushed?: boolean
  diff_url?: string | null
  // A pushed fix kept in git, which Show fix in hunk opens: rework-mr's own, review-mr's the
  // author's push that changed the topic's lines.
  fix_kept?: boolean
  // rework-mr: how many fixes its pushes make (consecutive pushes of it are one), which
  // the sidebar picks between where there are several.
  pushes?: number
  // Its (first) thread on GitLab, where the box's line about it links.
  thread_url?: string | null
  // rework-mr: a reply drafted to post ahead of the fix, and whether one went up already.
  early?: boolean
  early_posted?: boolean
  // rework-mr: the person wrote last in its threads, by hand on GitLab, and nothing says it
  // waits; and a fix dropped earlier is kept in git, which a new fix may start from.
  you_last?: boolean
  dropped?: boolean
  // rework-mr: whether the fix differs from the plan agreed, and whether that plan answers
  // the reviewer: either way a reply after the push has something to explain.
  differs?: boolean
  // rework-mr: a fix made while another topic was current: that topic, to go back to on Put aside.
  made_on?: string | null
  // A comment posted by hand that the agent found no longer holds: a corrected version waits.
  post_differs?: boolean
  answer_now?: boolean
  // rework-mr, a fix in the working tree: whether its checks passed on exactly its files.
  checks?: 'passed' | 'failed' | 'stale' | null
}

// review-mr: what arrived since the user last worked through the review, as the sidebar's
// inbox lists it. `first` is the topic a round opened from the inbox starts at.
export type ReviewInbox = {
  since: string | null
  replies: string[]; resolved: string[]
  threads: { id: string; by?: string | null; fyi?: boolean }[]
  // `same`: the code is as it was (commits rewritten); `own`: for a rebase, how many of the
  // MR's own files it changed (0: the MR's change is as it was).
  pushes: { at: string; stat?: string | null; topics: string[]; rebase: boolean; url?: string | null; same?: boolean | null; own?: number | null }[]
  // What changed in the MR as a whole: merged, closed, auto-merge set, approved.
  mr?: { kind: 'merged' | 'closed' | 'auto-merge' | 'approved'; at: string; by?: string | null }[]
  own: { thread: string; into: string }[]
  matching: number
  waiting: number
  first: string | null
  // A push came after the round opened: the sidebar's code is from before it.
  newer_push: boolean
}

// One comment of a thread, or on the MR itself (rework-mr).
export type ReviewNote = { author: string | null; body: string | null; at?: string | null; mine?: boolean; url?: string | null }
export type ReviewThread = { url?: string | null; file?: string | null; line?: number | null; resolved?: boolean; notes: ReviewNote[] }

// review-mr: one of the user's own posted threads, and what became of it: the topic it is
// on, or the topic the agent proposed merging it into.
export type ReviewOwnThread = {
  id: string; file: string | null; line: number | null; where: string; text: string
  topic: string | null; proposal: string | null
  thread?: ReviewThread[]
}

export type ReviewState = {
  skill: string
  // review-mr: the language drafts are written in, which a drafting press names.
  lang?: string
  // review-mr: the MR author's first name, and the topics the round visits, in its order
  // (between rounds, the next round's).
  author?: string | null
  round?: string[]
  // review-mr: the agent's recommendation on generating an explainer, until the person answers.
  explainer?: { recommend: 'skip' | 'generate'; why: string } | null
  iid: number
  title: string | null
  url?: string | null       // the MR on GitLab
  // rework-mr, a fix waiting for its push: the diff viewer's window for it, and how many
  // notes the user left there (null where the viewer could not be asked).
  hunk?: { open: boolean; notes: number | null } | null
  // review-mr: where the MR stands as a whole, from the update check.
  mr?: {
    state: 'opened' | 'merged' | 'closed' | 'locked' | null; merged_at?: string | null; closed_at?: string | null
    merged_by?: string | null; closed_by?: string | null; auto_merge?: boolean
    approved_by?: string[]; approvals_left?: number | null
  } | null
  // review-mr: what a merged or closed MR left: the topics still to see to, the drafts never
  // posted, and how many closed quietly.
  merged?: { left: string[]; drafts: string[]; quiet: number } | null
  current: string | null
  topics: ReviewTopic[]
  // rework-mr: the current topic's threads in full, and the comments on the MR itself.
  thread?: ReviewThread[]
  mr_comments?: ReviewNote[]
  // review-mr: the user's own review before the topics (pending, matching, done, skipped),
  // and their comments the agent matched to a topic, each waiting for the user's press.
  own_review?: string | null
  proposals?: ReviewProposal[]
  own_threads?: ReviewOwnThread[]
  // review-mr: whether a round runs (the user opened the inbox, and something is left), the
  // inbox, and what the mod's background agent has to judge.
  in_round?: boolean
  // rework-mr: the pass that runs, a plan for every open topic first, then the fixes.
  pass?: 'plan' | 'fix'
  inbox?: ReviewInbox
  judge?: { topics: string[]; own: string[] }
  // The topics the agent was asked to explain, kept by the mod (not the scripts).
  explained?: string[]
  // The topics the mod's background agent could not judge, kept by the mod.
  unjudged?: string[]
}

// `why` and `recommend` are the agent's: the reason the person decides on, and its answer.
export type ReviewProposal = {
  thread: string; into: string; summary: string; where: string; comment: string
  why?: string; recommend?: 'merge' | 'separate' | 'link'
}

// The mod's background agent judging news, and what it was given; `failed`: what it was
// given and did not store, which the user's opening of it hands to the chat instead.
export type ReviewJudging = { agentId: string | null; topics: string[]; own: string[]; failed: string[]; at?: number | null; ids?: string[] }

// The draft the newest card showed: what a Post press posts, and refuses past.
export type ReviewCard = { topic: string; draft: string | null }

// The newest card in the chat, which carries the current step's buttons while it is that
// step's: its row (a tool call's id, or a placed row's) and the topic it shows, null for one
// that shows none (the opener, a comment's match, the explainer question).
export type ReviewHost = { id: string; topic: string | null; step?: string | null }

// A view the mod drew itself after a press (no tool call to hang it under), by row id.
export type ReviewPlaced = Record<string, string>

// What the last press did, for the band's one line of feedback.
// `topic`: the topic the press was on; a success or a warning is shown only while it is
// current. A warning says what to do next, in yellow.
// A notice may carry one link, drawn inline after its text under a short label.
export type ReviewNotice = { text: string; isError: boolean; isWarning?: boolean; topic?: string | null; url?: string | null; label?: string | null } | null

// What a press did to the card it was pressed on, said as the card's last line once the
// card is done: a few words, and where to see it on GitLab.
// `icon`, the pressed button's; `text`, the words before the link; `after`, those after it.
export type ReviewOutcome = { text: string; url?: string | null; label?: string | null; icon?: string | null; after?: string | null }

// A file as the scripts' `code` command answers it, for the sidebar (lib/code_view.py).
// `rows`: the file as the MR changes it, `[kind, old, new, text]` each; `at`, `changed`,
// `from`: rows (1-based); `line`, `start`: where the comment is, in its side's line numbers.
export type ReviewCode = {
  file: string; side: 'old' | 'new'; line: number | null; start?: number | null; moved: boolean
  // The name the file had at the comment, where a push since renamed it.
  renamed_from?: string | null
  rows: [' ' | '-' | '+', number | null, number | null, string][]
  at: [number, number] | null; changed: number[]; from?: number | null
  // review-mr: `since` when the rows are the change since the comment, not the MR's whole
  // change, and when the push that made it was noted.
  // rework-mr: `fix` when the rows are the fix in the working tree, against HEAD, and
  // `files` every file it changes.
  // `pushed` when the rows are a pushed fix's change (rework-mr's own, review-mr's the
  // author's), from the commit before it to the one it made.
  mode?: 'since' | 'mr' | 'fix' | 'pushed'; since_at?: string | null
  // A fix's files, each with its size: the working tree's, or a pushed one kept in git. The
  // one shown is `file`; `commented` is the file the comment is on.
  fix_files?: [string, number, number][]
  commented?: string | null
  // A file the MR adds or removes whole (never a renamed one), drawn plain and marked so.
  whole?: 'added' | 'removed' | null
}

declare module 'claude-code' {
  interface PluginState {
    'review-ui': {
      session: Shaped<ReviewSession | null>
      review: Shaped<ReviewState | null>
      card: Shaped<ReviewCard | null>
      placed: Shaped<ReviewPlaced>
      notice: Shaped<ReviewNotice>
      busy: Shaped<string | null>
      panel: Shaped<boolean>
      folded: Shaped<Record<string, boolean>>
      'own-pick': Shaped<string | null>
      'code-at': Shaped<Record<string, number>>
      'code-seen': Shaped<Record<string, { stamp: number; view: ReviewCode | null }>>
      stamp: Shaped<number>
      sidebar: Shaped<{ isClosedByPerson: boolean }>
      ticks: Shaped<number>
      working: Shaped<boolean>
      'in-use': Shaped<boolean>
      'in-use-by': Shaped<'skill' | 'recovered' | null>
      'legend-shown': Shaped<boolean>
      explained: Shaped<string[]>
      'thread-open': Shaped<Record<string, boolean>>
      'code-open': Shaped<Record<string, boolean>>
      'box-at': Shaped<Record<string, number>>
      // `running`: a check is asking GitLab now, its time come or the person's sync.
      checks: Shaped<{ last: number | null; next: number | null; failed?: number | null; running?: boolean }>
      judging: Shaped<ReviewJudging>
      notified: Shaped<string[]>
      host: Shaped<ReviewHost | null>
      // The topic and draft version of each card a press placed, by its id, as a model's
      // card has them in its manifest.
      'placed-of': Shaped<Record<string, { topic?: string; draft?: string }>>
      // What a press did to each card it finished, by the card's id.
      outcomes: Shaped<Record<string, ReviewOutcome>>
      // A draft's Copy reading "Copied!", by its topic; the topics told where to paste one.
      copied: Shaped<string | null>
      'copy-told': Shaped<string[]>
      // How far each sidebar area that scrolls inside itself is scrolled, for which item.
      'area-at': Shaped<Record<string, { at: number; on: string | null }>>
      // The fix's files opened past their folded rows, by the code view's key.
      // The file picked from a fix's list, by the item's code key without a pick.
      'file-pick': Shaped<Record<string, string>>
      'push-pick': Shaped<Record<string, number>>
    }
  }
}
