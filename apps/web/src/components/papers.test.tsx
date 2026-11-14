import { render, screen, within } from "@testing-library/react";
import type { MothusiEvent } from "../api/types";
import { apply, emptyRun } from "../run/reducer";
import plantedLog from "../run/__fixtures__/planted-events.jsonl?raw";
import { EvidencePair, MemoPage, ScriptPage, highlight } from "./papers";

function required<T>(value: T | null | undefined, description: string): T {
  if (value === null || value === undefined) throw new Error(description);
  return value;
}

const events = plantedLog.trim().split("\n").map((line) => JSON.parse(line) as MothusiEvent);
const id = "gr10-maths-t1-planted";
const through = (predicate: (e: MothusiEvent) => boolean) => {
  const end = events.findIndex(predicate);
  if (end < 0) throw new Error("Missing planted event");
  return events.slice(0, end + 1).reduce(apply, emptyRun(id));
};
const finished = events.reduce(apply, emptyRun(id));


const disagreement = required(finished.disagreements[0], "Missing finished.disagreements[0]");

describe("the evidence papers", () => {
  it("pairs the memo with the learner's actual answer and both awards", () => {
    render(<EvidencePair d={disagreement} />);
    const figures = screen.getAllByRole("figure");
    expect(figures).toHaveLength(2);
    expect(figures[0]).toHaveTextContent(disagreement.question);
    expect(figures[0]).toHaveTextContent(`Answer: ${disagreement.memo_answer}`);
    expect(figures[0]).toHaveTextContent("Marker");
    expect(figures[0]).toHaveTextContent("Moderator");
    expect(figures[0]).toHaveTextContent(String(disagreement.first.awarded));
    expect(figures[0]).toHaveTextContent(String(disagreement.second.awarded));
    expect(figures[1]).toHaveTextContent(`${disagreement.script_id}'s script`);
    for (const line of disagreement.answer.split("\n")) expect(figures[1]).toHaveTextContent(line);
    expect(within(required(figures[1], "Missing figures[1]")).getAllByRole("mark").map((m) => m.textContent)).toEqual([
      disagreement.second.evidence_quote, disagreement.first.evidence_quote,
    ]);
  });

  it("shows the disagreement reason when supplied", () => {
    render(<EvidencePair d={{ ...disagreement, reason: "The perimeter needs both pairs of sides." }} />);
    expect(screen.getByText("The perimeter needs both pairs of sides.")).toBeInTheDocument();
  });

  it("splits the real memo criteria and puts final-answer marks on the answer", () => {
    const question = through((e) => e.kind === "needs_teacher").pending;
    if (question?.topic !== "memo") throw new Error("Missing memo question");
    render(<MemoPage heading="Your memo" meta={`${question.max_marks} marks`} question={question.question} notes={question.marking_notes} answer={question.memo_answer} quotes={[]} foot="Check this answer" />);
    const paper = screen.getByRole("figure");
    expect(paper).toHaveTextContent("1/8 = 2^(-3)");
    expect(screen.getByText(`Answer: ${question.memo_answer}`).parentElement).toHaveTextContent("1");
    expect(screen.queryByText("answer")).not.toBeInTheDocument();
    expect(paper).toHaveTextContent("Check this answer");
  });

  it("handles empty memo notes and an empty answer without extra marks", () => {
    render(<MemoPage heading="Memo" meta="" question="" notes="" answer="" quotes={[]} />);
    expect(screen.getByRole("figure")).toHaveTextContent("Answer:");
    expect(screen.queryByRole("mark")).not.toBeInTheDocument();
  });

  it("shows 'no answer' for blank script lines", () => {
    render(<ScriptPage heading="Script" meta="" caption="As written" answer={" \n\n "} quotes={[]} />);
    expect(screen.getByRole("figure")).toHaveTextContent("no answer");
  });

  it("preserves all text when quotes overlap, repeat or are absent", () => {
    const text = disagreement.answer;
    render(<p>{highlight(text, [{ text: disagreement.second.evidence_quote, ink: "green" }, { text: disagreement.second.evidence_quote, ink: "red" }, { text: "", ink: "red" }, { text: "not in this answer", ink: "red" }])}</p>);
    expect(screen.getAllByRole("mark")).toHaveLength(1);
    expect(screen.getByRole("paragraph")).toHaveTextContent(text.replace(/\s+/g, " "));
  });

  it("can underline a line from a multi-line quote without dropping its text", () => {
    const line = required(disagreement.answer.split("\n")[0], "Missing disagreement.answer.split(\"\\n\")[0]");
    render(<ScriptPage heading="Script" meta="" caption="As written" answer={line} quotes={[{ text: disagreement.answer, ink: "red" }]} foot="Teacher copy" />);
    expect(screen.getByRole("mark")).toHaveTextContent(line);
    expect(screen.getByRole("figure")).toHaveTextContent("Teacher copy");
  });
});
