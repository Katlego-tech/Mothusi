// The wire shapes of docs/architecture/design/console-ui.md §6, as the core service sends them.
// Money is a decimal string in rand; times are ISO 8601.

export type AssessmentState =
  | "draft"
  | "reading_check"
  | "memo_check"
  | "marking"
  | "moderating"
  | "re_marking"
  | "analysing"
  | "writing"
  | "reviewing"
  | "awaiting_approval"
  | "approved"
  | "rejected"
  | "delivered";

export type AgentRole =
  | "planner"
  | "intake"
  | "setter"
  | "marker"
  | "moderator"
  | "analyst"
  | "support"
  | "writer"
  | "reviewer"
  | "executor";

export type EventKind =
  | "created"
  | "state"
  | "brief"
  | "result"
  | "check"
  | "decision"
  | "disagreement"
  | "needs_teacher"
  | "usage"
  | "delivered"
  | "teacher"
  | "message"
  | "reply"
  | "note_sent";

export interface Mark {
  question_id: string;
  awarded: number;
  max_marks: number;
  evidence_quote: string;
  memo_line: string;
  principle: string;
}

export interface Question {
  question_id: string;
  question: string;
  max_marks: number;
  level: string;
}

export interface CreatedPayload {
  title: string;
  goal: string;
  pack_id: string;
  poa_task: string;
  subject: string;
  grade: number;
  term: number;
  total_marks: number;
  budget_zar: string;
  script_ids: string[];
  questions: Question[];
  /** "pages": Mothusi answer sheets, printed now and uploaded as photos (console-ui.md §11). */
  mode?: "csv" | "pages";
}

export interface StatePayload {
  from: AssessmentState | null;
  to: AssessmentState;
  reason: string;
}

export interface BriefPayload {
  brief_id: string;
  agent: AgentRole;
  task: string;
  attempt: number;
  script_id: string | null;
}

export interface ResultPayload {
  brief_id: string;
  agent: AgentRole;
  status: "done" | "retry" | "blocked" | "needs_teacher";
  script_id: string | null;
  summary: string;
  awarded: number | null;
  max_marks: number | null;
  reason: string;
  cost_zar: string;
  marks: Mark[] | null;
}

export interface CheckPayload {
  check: string;
  about: string;
  passed: boolean;
  detail: string;
}

export interface DecisionPayload {
  action: string;
  reason: string;
  brief_id: string | null;
}

export interface Pen {
  awarded: number;
  evidence_quote: string;
  memo_line: string;
}

export interface DisagreementPayload {
  script_id: string;
  question_id: string;
  question: string;
  max_marks: number;
  memo_answer: string;
  marking_notes: string;
  answer: string;
  first: Pen;
  second: Pen;
  reason: string;
}

export interface AttemptRow {
  attempt: number;
  awarded: number | null;
  max_marks: number | null;
  failed_check: string;
  detail: string;
  marks: Mark[];
}

export interface MemoQuestion {
  topic: "memo";
  title: string;
  question_id: string;
  question: string;
  max_marks: number;
  marking_notes: string;
  level: string;
  memo_answer: string;
  proposed_answer: string;
  working: string;
}

export interface BudgetQuestion {
  topic: "budget";
  title: string;
  spent_zar: string;
  limit_zar: string;
  scripts_settled: number;
  scripts_total: number;
  spent: { stage: string; zar: string }[];
  estimate: { stage: string; zar: string }[];
}

export interface ScriptQuestion {
  topic: "script";
  stage: "marking" | "moderation";
  title: string;
  script_id: string;
  question_id: string;
  question: string;
  max_marks: number;
  memo_answer: string;
  marking_notes: string;
  answer: string;
  attempts: AttemptRow[];
}

/** One line to check (intake.md §11.5). line_no null: a box that could not be read, or whose
 * page never arrived (page_missing); the teacher types it, or leaves it blank. */
export interface ReadingItem {
  script_id: string;
  question_id: string;
  line_no: number | null;
  line: string;
  crop_url: string | null;
  page_missing: boolean;
}

export interface ReadingQuestion {
  topic: "reading";
  title: string;
  items: ReadingItem[];
}

export interface ReadingAnswer {
  kind: "reading";
  script_id: string;
  question_id: string;
  line_no: number | null;
  action: "confirm" | "correct";
  text?: string;
}

/** One drafted memo row (intake.md §12.4 DraftItem); empty fields are the teacher's to fill. */
export interface DraftItem {
  question_id: string;
  question: string;
  max_marks: number;
  answer: string;
  marking_notes: string;
  level: string;
}

/** GET /memo-drafts/{id} (intake.md §12.4). */
export interface MemoDraftView {
  draft_id: string;
  state: "drafting" | "ready" | "failed";
  source_name: string;
  total_marks: number | null;
  items: DraftItem[];
  problems: string[];
  levels: string[];
  message: string;
}

/** POST /pages/ready's 409: what the geometry check refused or did not find (intake.md §11.4). */
export interface PageGaps {
  refused: string[];
  missing: { script_id: string; pages: number[] }[];
  /** False when no page could be read: there is nothing to start from. */
  can_accept: boolean;
}

export interface UploadTarget {
  name: string;
  method: "PUT";
  url: string;
  headers: Record<string, string>;
}

export type NeedsTeacher = MemoQuestion | BudgetQuestion | ScriptQuestion | ReadingQuestion;

export interface UsagePayload {
  cost_zar_total: string;
  input_tokens: number;
  output_tokens: number;
  scripts_settled: number;
  teacher_steps: number;
  priced: boolean;
}

export interface DeliveredPayload {
  written: string[];
  sent: string[];
}

interface EventBase<K extends EventKind, P> {
  seq: number;
  at: string;
  assessment_id: string;
  kind: K;
  agent: AgentRole | null;
  summary: string;
  payload: P;
}

export type MothusiEvent =
  | EventBase<"created", CreatedPayload>
  | EventBase<"state", StatePayload>
  | EventBase<"brief", BriefPayload>
  | EventBase<"result", ResultPayload>
  | EventBase<"check", CheckPayload>
  | EventBase<"decision", DecisionPayload>
  | EventBase<"disagreement", DisagreementPayload>
  | EventBase<"needs_teacher", NeedsTeacher>
  | EventBase<"usage", UsagePayload>
  | EventBase<"delivered", DeliveredPayload>
  | EventBase<"teacher", Record<string, unknown>>
  | EventBase<"message", MessagePayload>
  | EventBase<"reply", ReplyPayload>
  | EventBase<"note_sent", NoteSentPayload>;

// The conversation (conversation.md §6.1): the teacher writes, Mothusi replies while the run goes on.
export interface MessagePayload {
  message_id: string;
  text: string;
}

export type Skill =
  | "run_status"
  | "run_report"
  | "run_walls"
  | "explain_mark"
  | "send_feedback"
  | "memo_item"
  | "poa"
  | "atp"
  | "curriculum"
  | "lesson_plan"
  | "cannot"
  | "open"
  | "deferred"
  | "fallback";

/** Something on the screen the colleague opens beside its reply (conversation.md §14). */
export interface UiAction {
  open: "script" | "note" | "stats" | "approval" | "desk" | "new";
  script_id: string | null;
}

/** One learner's note offered for sending before the whole set (conversation.md §5). */
export interface NoteCard {
  script_id: string;
  learner: string;
  outcome: string;
  mark_with_quote: string;
  next_task: string;
  self_assessment_prompt: string;
}

export interface ReplyPayload {
  message_id: string | null;
  skill: Skill;
  say: string;
  card: NoteCard | null;
  promise: string | null;
  action?: UiAction | null;
}

export interface NoteSentPayload {
  script_id: string;
  sent: string[];
  written: string[];
}

export interface PackTask {
  term: number;
  name: string;
  max_marks: number;
  weight_percent: string;
}

/** One run on the desk (desk.md §6): from its event log, never the bundle (no names). */
export interface RunSummary {
  assessment_id: string;
  title: string;
  state: AssessmentState;
  waiting: boolean;
  halted: string | null;
  started_at: string;
  scripts: number;
  subject: string;
  grade: number;
}

/** One teaching week of the ATP, with the DBE page it comes from (desk.md §6). */
export interface AtpRow {
  week: number;
  term: number;
  term_week: number;
  topic: string;
  source: string;
}

export interface Pack {
  pack_id: string;
  subject: string;
  grade: number;
  moderation: {
    sample_percent: string;
    min_scripts: number;
    spread: string;
    widen_above_gap_percent: string;
  };
  tasks: PackTask[];
}

export interface ItemStat {
  question_id: string;
  max_marks: number;
  scripts: number;
  mean_awarded: string;
  facility_percent: string;
  flag: "re-teach" | "review-item" | null;
}

export interface Misconception {
  question_id: string;
  pattern: string;
  evidence_quotes: string[];
  scripts: number;
}

export interface Bundle {
  assessment_id: string;
  title: string;
  state: AssessmentState;
  summary: {
    class_average_percent: string;
    scripts: number;
    moderated: number;
    questions_compared: number;
    questions_agreed: number;
    re_marked: number;
    cost_zar: string;
    per_script_zar: string;
    priced: boolean;
    elapsed_s: number;
    teacher_steps: number;
  };
  mark_sheet: {
    learner: string;
    pseudonym?: string;
    raw: number;
    out_of: number;
    sba_points: string;
    year_points: string;
    moderated: boolean;
    re_marked: boolean;
  }[];
  mark_sheet_csv: string;
  item_analysis: ItemStat[];
  misconceptions: Misconception[];
  catch_up_plan_md: string;
  learner_notes: {
    learner: string;
    pseudonym?: string; // conversation.md §14: lets the colleague open this learner's card
    outcome: string;
    mark_with_quote: string;
    next_task: string;
    self_assessment_prompt: string;
    review: { passed: boolean; reason: string };
  }[];
  moderation_report_md: string;
  class_sheet_md: string;
  class_sheet_review: { passed: boolean; reason: string };
  memo_unverified: { question_id: string; memo_answer: string; setter_answer: string; working: string }[];
}

export interface FieldError {
  field: string;
  message: string;
}
