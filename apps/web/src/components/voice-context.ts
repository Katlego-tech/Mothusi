// Session context is composed from the same event-folded view as the teacher's screen.
import type { AssessmentState } from "../api/types";
import { stateName } from "../lib/utils";
import type { RunView } from "../run/reducer";

export interface VoiceContext {
  page: string;
  runId: string;
  title: string;
  state: AssessmentState | "";
  waiting: number;
  teacherTask: string;
  halted: boolean;
  previousReply: string;
}

export function voiceContext(page: string, run?: RunView, previousReply = ""): VoiceContext {
  const tasks: string[] = [];
  if (run?.pending) {
    const topic = run.pending.topic;
    tasks.push(topic === "memo" ? "check the memo decision" : topic === "reading"
      ? "check the scanned pages" : topic === "script" ? "answer a script question" : "decide whether to carry on");
  }
  if (run?.needsYou.length) tasks.push("answer the flagged script questions");
  if (run?.state === "awaiting_approval") tasks.push("review and approve the set");
  return {
    page,
    runId: run?.assessmentId ?? "",
    title: run?.title ?? "",
    state: run?.state ?? "",
    waiting: run ? run.needsYou.length + Number(Boolean(run.pending)) + Number(run.state === "awaiting_approval") : 0,
    teacherTask: tasks.join("; "),
    halted: Boolean(run?.halted),
    previousReply,
  };
}

export function composeOpening(context: VoiceContext, given: boolean): string {
  const disclaimer = given ? "" : "I can't remove names from what's said here, so please use learner numbers like L-014, not names. ";
  let now: string;
  if (context.runId) {
    const title = context.title || "Your assessment";
    const stages: Partial<Record<AssessmentState, string>> = {
      draft: `${title} is being set up.`,
      reading_check: `I'm checking the scanned pages for ${title}.`,
      memo_check: `I'm checking the memo for ${title}.`,
      marking: `I'm marking ${title}.`,
      moderating: `I'm moderating ${title}.`,
      re_marking: `I'm re-marking ${title}.`,
      analysing: `I'm analysing ${title}.`,
      writing: `I'm writing the feedback for ${title}.`,
      reviewing: `I'm reviewing the feedback for ${title}.`,
      rejected: `${title} was sent back for redrafting.`,
      awaiting_approval: `${title} is ready for your approval.`,
      approved: `${title} is approved and being delivered.`,
      delivered: `${title} has been delivered.`,
    };
    now = context.halted ? `${title} has stopped.`
      : context.state ? stages[context.state] ?? `${title}: ${stateName(context.state)}.` : `Opening ${title}.`;
    if (context.waiting) {
      now += ` ${context.waiting} ${context.waiting === 1 ? "item needs" : "items need"} your attention on the screen.`;
      if (context.teacherTask) now += ` Please ${context.teacherTask}.`;
    }
  } else {
    now = context.page === "/new"
      ? "You're setting up a new assessment. What would you like to mark?"
      : "You're at your desk. What would you like to work on?";
  }
  return disclaimer + now;
}

const disclaimerKey = (teacher: string) => `mothusi.voice.names-disclaimer:${encodeURIComponent(teacher)}`;

export function disclaimerGiven(teacher: string | null): boolean {
  try {
    return teacher !== null && window.localStorage.getItem(disclaimerKey(teacher)) === "1";
  } catch {
    return false;
  }
}

export function markDisclaimerGiven(teacher: string | null): void {
  try {
    if (teacher !== null) window.localStorage.setItem(disclaimerKey(teacher), "1");
  } catch { /* Private windows can still use voice; the reminder will recur. */ }
}

export function dynamicContext(context: VoiceContext, given: boolean) {
  return {
    page: context.page,
    run_id: context.runId,
    run_title: context.title,
    run_state: context.state,
    waiting_for_teacher: context.waiting,
    teacher_task: context.teacherTask,
    run_stopped: context.halted,
    disclaimer_given: given,
    previous_reply: context.previousReply,
  };
}
