import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { MothusiEvent } from "../api/types";
import { apply, emptyRun } from "../run/reducer";
import plantedLog from "../run/__fixtures__/planted-events.jsonl?raw";
import { ClassSet, ScriptInspector } from "./classset";

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


describe("the class set", () => {
  it("starts empty before the class arrives", () => {
    render(<ClassSet run={emptyRun(id)} selected={null} onSelect={vi.fn()} />);
    expect(screen.getByRole("heading", { name: "The class set" })).toBeInTheDocument();
    expect(screen.getByText("0 of 0 settled")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("names every unmarked script and selects it by keyboard", async () => {
    const run = through((e) => e.kind === "created");
    const select = vi.fn();
    const { rerender } = render(<ClassSet run={run} selected={null} onSelect={select} />);
    for (const cell of run.cells.values()) {
      expect(screen.getByRole("button", { name: `${cell.scriptId}, not marked yet` })).toHaveAttribute("aria-pressed", "false");
    }
    const first = required([...run.cells.keys()][0], "Missing [...run.cells.keys()][0]");
    await userEvent.tab();
    await userEvent.keyboard("{Enter}");
    expect(select).toHaveBeenCalledWith(first);
    rerender(<ClassSet run={run} selected={first} onSelect={select} />);
    expect(screen.getByRole("button", { name: `${first}, not marked yet` })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("This script has not been marked yet.")).toBeInTheDocument();
  });

  it.each(["working", "retry", "marked", "disputed", "moderated"])("shows the planted %s script state", (state) => {
    const run = events.reduce((views, event) => [...views, apply(required(views.at(-1), "Missing views.at(-1)"), event)], [emptyRun(id)]).find((r) =>
      [...r.cells.values()].some((c) => state === "disputed" ? c.disputed : state === "moderated" ? c.moderated && !c.disputed : c.status === state));
    if (!run) throw new Error(`No ${state} state in fixture`);
    const cell = required([...run.cells.values()].find((c) => state === "disputed" ? c.disputed : state === "moderated" ? c.moderated && !c.disputed : c.status === state), `Missing ${state} script cell in fixture`);
    render(<ClassSet run={run} selected={cell.scriptId} onSelect={vi.fn()} />);
    const button = screen.getByRole("button", { name: cell.total === null ? `${cell.scriptId}, not marked yet` : `${cell.scriptId}, ${cell.total} of ${cell.maxMarks}` });
    expect(button).toHaveAttribute("aria-pressed", "true");
    if (["working", "retry", "disputed"].includes(state)) expect(button).toHaveTextContent("?");
    if (state === "moderated") expect(button).toHaveTextContent("✓");
  });

  it("opens the selected script with the real marks and evidence", async () => {
    const scriptId = required([...finished.scripts.keys()][0], "Missing [...finished.scripts.keys()][0]");
    const select = vi.fn();
    render(<ClassSet run={finished} selected={scriptId} onSelect={select} />);
    const cell = required(finished.cells.get(scriptId), "Missing finished.cells.get(scriptId)");
    await userEvent.click(screen.getByRole("button", { name: `${scriptId}, ${cell.total} of ${cell.maxMarks}` }));
    expect(select).toHaveBeenCalledWith(scriptId);
    expect(screen.getByRole("heading", { name: scriptId })).toBeInTheDocument();
    expect(screen.getByText("in the moderation sample")).toBeInTheDocument();
    for (const mark of required(finished.scripts.get(scriptId), "Missing finished.scripts.get(scriptId)")) {
      if (mark.evidence_quote) expect(screen.getAllByText(mark.evidence_quote).length).toBeGreaterThan(0);
    }
  });

  it("handles an unknown script without inventing marks", () => {
    render(<ScriptInspector run={emptyRun(id)} scriptId="not-uploaded" />);
    expect(screen.getByRole("heading", { name: "not-uploaded" })).toBeInTheDocument();
    expect(screen.getByText("This script has not been marked yet.")).toBeInTheDocument();
  });
});

describe("marks settled by a teacher", () => {
  it("shows the teacher's total and decision principle through the real reducer", () => {
    const run = through((e) => e.kind === "created");
    const scriptId = required([...finished.scripts.keys()][0], "Missing [...finished.scripts.keys()][0]");
    const marks = required(finished.scripts.get(scriptId), "Missing finished.scripts.get(scriptId)");
    const q = required(required(run.created, "Missing run.created").questions[0], "Missing run.created.questions[0]");
    const asked = apply(run, {
      seq: run.lastSeq + 1, at: required(run.lastAt, "Missing run.lastAt"), assessment_id: id, agent: null,
      kind: "needs_teacher", summary: "Teacher marks required",
      payload: {
        topic: "script", stage: "marking", title: "Teacher marks required", script_id: scriptId,
        question_id: q.question_id, question: q.question, max_marks: q.max_marks,
        memo_answer: required(marks[0], "Missing marks[0]").memo_line, marking_notes: required(marks[0], "Missing marks[0]").memo_line,
        answer: required(marks[0], "Missing marks[0]").evidence_quote, attempts: [],
      },
    });
    const decided = apply(asked, {
      seq: asked.lastSeq + 1, at: required(run.lastAt, "Missing run.lastAt"), assessment_id: id, agent: null,
      kind: "teacher", summary: "Teacher supplied marks",
      payload: { topic: "script", script_id: scriptId, decision: "set_marks", marks: marks.map(({ question_id, awarded }) => ({ question_id, awarded })) },
    });
    render(<ClassSet run={decided} selected={scriptId} onSelect={vi.fn()} />);
    const total = marks.reduce((sum, m) => sum + m.awarded, 0);
    expect(screen.getByRole("button", { name: `${scriptId}, ${total} of ${decided.totalMarks}` })).toHaveTextContent(String(total));
    expect(screen.getAllByText("teacher's decision")).toHaveLength(required(run.created, "Missing run.created").questions.length);
    expect(screen.getByText("not in the moderation sample")).toBeInTheDocument();
  });
});
