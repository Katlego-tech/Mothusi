// The needs-teacher card (console-ui.md §7 NeedsTeacherCard): MemoDecisionForm, ScriptDecisionForm,
// BudgetForm, drawn to 03-memo-check.png, 06-script-question.png and 07-budget-question.png.
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { ApiError, api, cropSrc } from "../api/client";
import type {
  BudgetQuestion,
  MemoQuestion,
  NeedsTeacher,
  Question,
  ReadingAnswer,
  ReadingItem,
  ReadingQuestion,
  ScriptQuestion,
} from "../api/types";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { MemoPage, ScriptPage } from "./papers";

function Problem({ error }: { error: unknown }) {
  if (!error) return null;
  const message = error instanceof ApiError ? error.message : "That did not go through. Try again.";
  return (
    <p className="error" role="alert">
      {message}
    </p>
  );
}

export function NeedsTeacherCard({
  assessmentId,
  question,
  questions,
}: {
  assessmentId: string;
  question: NeedsTeacher;
  questions: Question[];
}) {
  return (
    <section className="ask arrive" aria-labelledby="ask-title">
      {question.topic === "memo" && <MemoDecisionForm id={assessmentId} q={question} />}
      {question.topic === "script" && <ScriptDecisionForm id={assessmentId} q={question} questions={questions} />}
      {question.topic === "budget" && <BudgetForm id={assessmentId} q={question} />}
      {question.topic === "reading" && <ReadingCheckForm id={assessmentId} q={question} />}
    </section>
  );
}

function MemoDecisionForm({ id, q }: { id: string; q: MemoQuestion }) {
  const [writing, setWriting] = useState(false);
  const [answer, setAnswer] = useState("");
  const send = useMutation({ mutationFn: (body: Parameters<typeof api.memo>[1]) => api.memo(id, body) });
  return (
    <>
      <div className="ask-head">
        <h3 id="ask-title">Memo item {q.question_id} may be wrong</h3>
        <span className="small muted">Needs your decision · nothing is marked yet</span>
      </div>
      <div className="papers">
        <MemoPage
          heading={
            <>
              <b>Your memo</b> · item {q.question_id}
            </>
          }
          meta={`${q.max_marks} marks`}
          question={q.question}
          notes={q.marking_notes}
          answer={q.memo_answer}
          quotes={[{ text: q.memo_answer, ink: "red" }]}
          foot={<span className="muted">{q.level.replace("_", " ")}</span>}
        />
        <ScriptPage
          heading={<b>The Setter's working</b>}
          meta="sat it without the memo"
          caption="Sat the question without seeing the memo's answer"
          answer={
            (q.working.includes(q.proposed_answer) ? q.working : [q.working, q.proposed_answer].filter(Boolean).join("\n")) ||
            "(no answer given)"
          }
          quotes={[{ text: q.proposed_answer, ink: "green" }]}
          foot={
            <span className="muted">
              Code: equivalent("{q.proposed_answer || "—"}", "{q.memo_answer}") is false
            </span>
          }
        />
      </div>
      <div className="choices">
        {q.proposed_answer && (
          <Button
            variant="primary"
            disabled={send.isPending}
            onClick={() => send.mutate({ decision: "use_proposed", question_id: q.question_id })}
          >
            Use {q.proposed_answer} and mark
          </Button>
        )}
        <Button disabled={send.isPending} onClick={() => send.mutate({ decision: "keep_memo", question_id: q.question_id })}>
          Keep the memo's {q.memo_answer}
        </Button>
        {!writing && (
          <Button disabled={send.isPending} onClick={() => setWriting(true)}>
            Write a different answer
          </Button>
        )}
        <span className="note">Your choice is written into the log.</span>
      </div>
      {writing && (
        <form
          className="choices"
          onSubmit={(e) => {
            e.preventDefault();
            send.mutate({ decision: "amend", question_id: q.question_id, answer });
          }}
        >
          <label className="visually-hidden" htmlFor="amend">
            The memo's answer for {q.question_id}
          </label>
          <Input id="amend" value={answer} onChange={(e) => setAnswer(e.target.value)} placeholder="x = -4" autoFocus />
          <Button variant="primary" type="submit" disabled={!answer.trim() || send.isPending}>
            Use this answer and mark
          </Button>
        </form>
      )}
      <Problem error={send.error} />
    </>
  );
}

/** The answer's outcome, for a caller that tracks it (the Needs-you queue). */
export type ScriptResult = { ok: true } | { ok: false; message: string };

export function ScriptDecisionForm({
  id,
  q,
  questions,
  onSend,
  onResult,
}: {
  id: string;
  q: ScriptQuestion;
  questions: Question[];
  onSend?: () => void;
  onResult?: (result: ScriptResult) => void;
}) {
  const lastWithMarks = [...q.attempts].reverse().find((a) => a.marks.length > 0);
  const start = Object.fromEntries(
    questions.map((x) => [x.question_id, lastWithMarks?.marks.find((m) => m.question_id === x.question_id)?.awarded ?? 0]),
  );
  const [marks, setMarks] = useState<Record<string, number>>(start);
  const send = useMutation({
    mutationFn: (body: Parameters<typeof api.script>[2]) => api.script(id, q.script_id, body),
    onMutate: () => onSend?.(),
    onSuccess: () => onResult?.({ ok: true }),
    onError: (error) => onResult?.({ ok: false, message: error instanceof Error ? error.message : String(error) }),
  });
  const total = Object.values(marks).reduce((s, n) => s + (Number.isFinite(n) ? n : 0), 0);
  const outOf = questions.reduce((s, x) => s + x.max_marks, 0);
  const complete = (n: number) => q.attempts[n - 1]?.marks.length === questions.length;
  return (
    <>
      <div className="ask-head">
        <h3 id="ask-title">{q.title}</h3>
        <span className="small muted">
          Needs your decision · {q.stage === "moderation" ? "your marks stand in for the blind second marking" : "the other scripts are unaffected"}
        </span>
      </div>
      <div className="attempts">
        {q.attempts.map((a) => (
          <div className="attempt" key={a.attempt}>
            <span className="small muted">Attempt {a.attempt}</span>
            <span>
              {a.awarded === null ? (
                <span className="muted">no marks</span>
              ) : (
                <>
                  <span className="ink red">{a.awarded}</span> <span className="muted">of {a.max_marks}</span>
                </>
              )}
            </span>
            <span className="detail">
              {a.failed_check}: {a.detail}
            </span>
          </div>
        ))}
      </div>
      <div className="papers">
        <MemoPage
          heading={
            <>
              <b>The memo</b> · question {q.question_id}
            </>
          }
          meta={`${q.max_marks} marks`}
          question={q.question}
          notes={q.marking_notes}
          answer={q.memo_answer}
          quotes={[]}
        />
        <ScriptPage
          heading={
            <>
              <b>{q.script_id}'s script</b> · question {q.question_id}
            </>
          }
          meta=""
          caption="The learner's answer as written"
          answer={q.answer}
          quotes={[]}
        />
      </div>
      <form
        className="field"
        onSubmit={(e) => {
          e.preventDefault();
          send.mutate({
            decision: "set_marks",
            marks: questions.map((x) => ({ question_id: x.question_id, awarded: marks[x.question_id] ?? 0 })),
          });
        }}
      >
        <span className="lbl">Set the marks yourself</span>
        <div className="setmarks">
          {questions.map((x) => (
            <label key={x.question_id}>
              {x.question_id} of {x.max_marks}
              <input
                type="number"
                min={0}
                max={x.max_marks}
                inputMode="numeric"
                className={marks[x.question_id] !== start[x.question_id] ? "changed" : undefined}
                value={marks[x.question_id] ?? 0}
                onChange={(e) => setMarks({ ...marks, [x.question_id]: Number(e.target.value) })}
              />
            </label>
          ))}
        </div>
        <span className="hint">
          {lastWithMarks ? `Pre-filled from attempt ${lastWithMarks.attempt}. ` : ""}Marks you set carry the principle
          "teacher's decision" and skip the evidence check; the log says so.
        </span>
        <div className="choices">
          <Button variant="primary" type="submit" disabled={send.isPending}>
            Record these marks ({total} of {outOf})
          </Button>
          {q.attempts.map((a) =>
            complete(a.attempt) ? (
              <Button
                key={a.attempt}
                type="button"
                disabled={send.isPending}
                onClick={() => send.mutate({ decision: "use_attempt", attempt: a.attempt as 1 | 2 | 3 })}
              >
                Use attempt {a.attempt}
              </Button>
            ) : null,
          )}
        </div>
      </form>
      <Problem error={send.error} />
    </>
  );
}

function BudgetForm({ id, q }: { id: string; q: BudgetQuestion }) {
  const suggested = (Number(q.spent_zar) + q.estimate.reduce((s, e) => s + Number(e.zar), 0)) * 1.25;
  const limit = Math.max(Math.ceil(suggested / 10) * 10, Number(q.limit_zar) + 10).toFixed(2);
  const send = useMutation({ mutationFn: (to: string) => api.budget(id, to) });
  const left = q.scripts_total - q.scripts_settled;
  return (
    <>
      <div className="ask-head">
        <h3 id="ask-title">This set is taking longer than planned</h3>
      </div>
      <p>{left > 0 ? `Carry on marking the last ${left} scripts?` : "Carry on?"}</p>
      <form
        className="choices"
        onSubmit={(e) => {
          e.preventDefault();
          send.mutate(limit);
        }}
      >
        <Button variant="primary" type="submit" disabled={send.isPending}>
          Carry on
        </Button>
        <span className="note">Or leave it paused; nothing is lost.</span>
      </form>
      <Problem error={send.error} />
    </>
  );
}


/** One flagged line's place in the reading check: its key, and what the teacher sees. */
export const itemKey = (i: ReadingItem) => `${i.script_id}|${i.question_id}|${i.line_no ?? "box"}`;

/** The teacher's answers: an edited line is a correction, an untouched one is confirmed; a box
 * that could not be read (or whose page is missing) is always "correct" with what was typed. */
export function readingAnswers(items: ReadingItem[], typed: Record<string, string>): ReadingAnswer[] {
  return items.map((i) => {
    const base = { kind: "reading" as const, script_id: i.script_id, question_id: i.question_id, line_no: i.line_no };
    const text = typed[itemKey(i)];
    if (i.line_no === null) return { ...base, action: "correct", text: text ?? "" };
    return text !== undefined && text !== i.line ? { ...base, action: "correct", text } : { ...base, action: "confirm" };
  });
}

function ReadingCheckForm({ id, q }: { id: string; q: ReadingQuestion }) {
  // The event's crop links may have expired; ask for freshly signed ones whenever this shows.
  const fresh = useQuery({ queryKey: ["reading", id, q.items.length], queryFn: () => api.readingItems(id) });
  const items = fresh.data?.items ?? q.items;
  const [typed, setTyped] = useState<Record<string, string>>({});
  const send = useMutation({ mutationFn: () => api.reading(id, readingAnswers(items, typed)) });
  const changed = items.filter((i) => typed[itemKey(i)] !== undefined && typed[itemKey(i)] !== i.line).length;
  return (
    <>
      <div className="ask-head">
        <h3 id="ask-title">{q.title}</h3>
        <span className="small muted">Needs your check · nothing is marked yet</span>
      </div>
      <p className="small">
        The reader was unsure of these lines. Each one shows the learner's own box beside what was read. Correct a line by
        typing over it; leave it as it is to confirm it.
      </p>
      <ol className="readings">
        {items.map((i) => {
          const key = itemKey(i);
          const unreadable = i.line_no === null;
          return (
            <li key={key} className={`reading ${unreadable ? "unread" : ""}`}>
              <div className="crop">
                {i.crop_url ? (
                  <img src={cropSrc(i.crop_url)} alt={`${i.script_id}, question ${i.question_id}, as photographed`} />
                ) : (
                  <span className="muted small">This page was not uploaded.</span>
                )}
              </div>
              <div className="reading-line">
                <label htmlFor={`r-${key}`} className="small">
                  <b>{i.script_id}</b> · question {i.question_id}
                  {unreadable ? (i.page_missing ? " · page missing" : " · could not be read") : ` · line ${(i.line_no ?? 0) + 1}`}
                </label>
                {/* A textarea for both: a whole box can hold several lines of working, and a
                    corrected line may really be two (ReadingAnswer keeps the line breaks). */}
                <textarea
                  id={`r-${key}`}
                  className="input mono"
                  rows={unreadable ? 4 : 1}
                  value={typed[key] ?? i.line}
                  placeholder={unreadable ? "Type what the box says, line by line, or leave it blank if it is empty" : undefined}
                  onChange={(e) => setTyped({ ...typed, [key]: e.target.value })}
                />
              </div>
            </li>
          );
        })}
      </ol>
      <div className="ask-actions">
        <button type="button" className="btn primary" disabled={send.isPending || send.isSuccess} onClick={() => send.mutate()}>
          {changed ? `Save ${changed} correction${changed === 1 ? "" : "s"} and mark` : "Confirm these readings and mark"}
        </button>
        <span className="small muted">Marking starts with what you confirm here.</span>
      </div>
      <Problem error={send.error} />
    </>
  );
}
