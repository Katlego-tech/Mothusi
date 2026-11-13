// The evidence pair (console-ui.md §2 signature): the memo page beside the learner's page, each
// pen's quote underlined in its ink, the marks beside the lines that earned them.
import type { ReactNode } from "react";

import type { DisagreementPayload } from "../api/types";

export interface Quote {
  text: string;
  ink: "red" | "green";
}

const squash = (s: string) => s.replace(/\s+/g, " ").trim();

/** `text` with every quote it contains wrapped in <mark class="quote">, in the quote's ink. */
export function highlight(text: string, quotes: Quote[]): ReactNode {
  type Span = { from: number; to: number; ink: Quote["ink"] };
  const spans: Span[] = [];
  for (const q of quotes) {
    const needle = squash(q.text);
    if (!needle) continue;
    const at = text.indexOf(needle);
    if (at >= 0) spans.push({ from: at, to: at + needle.length, ink: q.ink });
    else if (needle.includes(squash(text)) && squash(text)) spans.push({ from: 0, to: text.length, ink: q.ink });
  }
  spans.sort((a, b) => a.from - b.from);
  const out: ReactNode[] = [];
  let at = 0;
  for (const [i, s] of spans.entries()) {
    if (s.from < at) continue; // overlapping quotes: the first pen's underline stands
    if (s.from > at) out.push(text.slice(at, s.from));
    out.push(
      <mark key={i} className={`quote ${s.ink === "green" ? "green" : ""}`}>
        {text.slice(s.from, s.to)}
      </mark>,
    );
    at = s.to;
  }
  if (at < text.length) out.push(text.slice(at));
  return out.length ? out : text;
}

/** "1/8 = 2^(-3): 2 marks; x + 1 = -3: 1 mark" -> [{text, marks}], as the memo page lists them. */
export function memoLines(notes: string): { text: string; marks: string }[] {
  return notes
    .split(";")
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => {
      const m = /^(.*):\s*(\d+)\s*marks?$/i.exec(part);
      return m?.[1] && m[2] ? { text: m[1].trim(), marks: m[2] } : { text: part, marks: "" };
    });
}

/** The marking notes' own "answer: 1 mark" line: drawn as the memo's Answer line. */
const isAnswerLine = (text: string) => /^(the )?(final )?answer$/i.test(text.trim());

export function MemoPage({
  heading,
  meta,
  question,
  notes,
  answer,
  quotes,
  foot,
}: {
  heading: ReactNode;
  meta: ReactNode;
  question: string;
  notes: string;
  answer: string;
  quotes: Quote[];
  foot?: ReactNode;
}) {
  const lines = memoLines(notes);
  const answerMarks = lines.find((l) => isAnswerLine(l.text))?.marks ?? "";
  return (
    <figure className="paper">
      <div className="paper-head">
        <span>{heading}</span>
        <span>{meta}</span>
      </div>
      <p className="paper-q">{question}</p>
      <div className="lines">
        {lines
          .filter((line) => !isAnswerLine(line.text))
          .map((line, i) => (
            <div className="line" key={i}>
              <span>{highlight(line.text, quotes)}</span>
              {line.marks && <span className="mk grey">{line.marks}</span>}
            </div>
          ))}
        <div className="line">
          <span>Answer: {highlight(answer, quotes)}</span>
          {answerMarks && <span className="mk grey">{answerMarks}</span>}
        </div>
      </div>
      {foot && <div className="paper-foot">{foot}</div>}
    </figure>
  );
}

export function ScriptPage({
  heading,
  meta,
  caption,
  answer,
  quotes,
  foot,
}: {
  heading: ReactNode;
  meta: ReactNode;
  caption: string;
  answer: string;
  quotes: Quote[];
  foot?: ReactNode;
}) {
  const lines = answer.split("\n").filter((l) => l.trim());
  return (
    <figure className="paper script">
      <div className="paper-head">
        <span>{heading}</span>
        <span>{meta}</span>
      </div>
      <p className="paper-q">{caption}</p>
      <div className="lines">
        {lines.length ? (
          lines.map((line, i) => (
            <div className="line" key={i}>
              <span>{highlight(line, quotes)}</span>
            </div>
          ))
        ) : (
          <div className="line">
            <span className="muted">no answer</span>
          </div>
        )}
      </div>
      {foot && <div className="paper-foot">{foot}</div>}
    </figure>
  );
}

/** The disagreement's two pages: the memo with each pen's memo line, the script with each quote. */
export function EvidencePair({ d }: { d: DisagreementPayload }) {
  const lines: Quote[] = [
    { text: d.second.memo_line, ink: "green" },
    { text: d.first.memo_line, ink: "red" },
  ];
  const quotes: Quote[] = [
    { text: d.second.evidence_quote, ink: "green" },
    { text: d.first.evidence_quote, ink: "red" },
  ];
  return (
    <div className="papers">
      <MemoPage
        heading={
          <>
            <b>The memo</b> · question {d.question_id}
          </>
        }
        meta={`${d.max_marks} marks`}
        question={d.question}
        notes={d.marking_notes}
        answer={d.memo_answer}
        quotes={lines}
        foot={
          <>
            <span className="muted">
              lines applied: <b className="pen red">Marker</b> {d.first.memo_line} ·{" "}
              <b className="pen green">Moderator</b> {d.second.memo_line}
            </span>
            <span>
              <span className="ink red">{d.first.awarded}</span> <span className="muted">·</span>{" "}
              <span className="ink green">{d.second.awarded}</span>
            </span>
          </>
        }
      />
      <ScriptPage
        heading={
          <>
            <b>{d.script_id}'s script</b> · question {d.question_id}
          </>
        }
        meta={`${d.answer.split("\n").filter((l) => l.trim()).length} lines`}
        caption="The learner's answer as written; each pen's quote underlined in its ink"
        answer={d.answer}
        quotes={quotes}
        foot={d.reason ? <span className="muted">{d.reason}</span> : undefined}
      />
    </div>
  );
}
