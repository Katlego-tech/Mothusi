// RunView.apply (console-ui.md §3): one case per event kind, folded over the real planted run.
import type { MothusiEvent } from "../api/types";
import plantedLog from "./__fixtures__/planted-events.jsonl?raw";
import { apply, emptyRun, type RunView } from "./reducer";

const planted: MothusiEvent[] = plantedLog
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as MothusiEvent);

const fold = (events: MothusiEvent[], from: RunView = emptyRun("gr10-maths-t1-planted")) =>
  events.reduce(apply, from);

const until = (predicate: (e: MothusiEvent) => boolean) => {
  const end = planted.findIndex(predicate);
  if (end < 0) throw new Error("no such event in the planted run");
  return fold(planted.slice(0, end + 1));
};

describe("RunView.apply on the planted run", () => {
  it("seeds the title and one pending cell per script from `created` (seq 0)", () => {
    const run = fold(planted.slice(0, 1));
    expect(run.title).toBe("Grade 10 Mathematics · Term 1 Test");
    expect(run.totalMarks).toBe(50);
    expect([...run.cells.keys()]).toEqual(["L-001", "L-003", "L-007", "L-010", "L-014", "L-021"]);
    expect([...run.cells.values()].every((c) => c.status === "pending")).toBe(true);
  });

  it("pins the memo question and clears it when the teacher answers", () => {
    const asked = until((e) => e.kind === "needs_teacher");
    expect(asked.pending?.topic).toBe("memo");
    expect(asked.finding).toContain("2.3");
    const answered = until((e) => e.kind === "teacher");
    expect(answered.pending).toBeNull();
  });

  it("shows L-014 as a retry after its failed evidence check, then marked", () => {
    const failed = until((e) => e.kind === "check" && !e.payload.passed);
    expect(failed.cells.get("L-014")?.status).toBe("retry");
    expect(failed.checks.failed).toBe(1);
    expect(failed.checks.open).toBe(1);
    const settled = until(
      (e) => e.kind === "result" && e.payload.brief_id.endsWith("/L-014/marker/2"),
    );
    expect(settled.cells.get("L-014")).toMatchObject({ status: "marked", total: 40 });
    expect(settled.scripts.get("L-014")?.find((m) => m.question_id === "3.1")?.evidence_quote).toBe(
      "7x = 14 so x = 2",
    );
  });

  it("marks a cell working while its brief is open and the agent live", () => {
    const briefed = until((e) => e.kind === "brief" && e.payload.agent === "marker");
    expect(briefed.cells.get("L-001")?.status).toBe("working");
    expect(briefed.agents.get("marker")).toMatchObject({ live: true, briefs: 1 });
  });

  it("records the L-021 disagreement as the finding and keeps the re-marked total", () => {
    const disputed = until((e) => e.kind === "disagreement");
    expect(disputed.finding).toContain("L-021");
    expect(disputed.disagreements).toHaveLength(1);
    expect(disputed.cells.get("L-021")?.disputed).toBe(true);
    const end = fold(planted);
    expect(end.cells.get("L-021")).toMatchObject({ total: 36, moderated: true });
    expect(end.scripts.get("L-021")?.find((m) => m.question_id === "4")?.awarded).toBe(2);
  });

  it("ends delivered, with the files and every agent idle", () => {
    const end = fold(planted);
    expect(end.state).toBe("delivered");
    expect(end.delivered?.written.some((p) => p.endsWith("mark-sheet.csv"))).toBe(true);
    expect([...end.agents.values()].some((a) => a.live)).toBe(false);
    expect(end.teacherSteps).toBe(2);
    expect(end.settled).toBe(6);
  });

  it("keeps a log row for every event but usage, and ignores an event it has seen", () => {
    const end = fold(planted);
    expect(end.events.length).toBe(planted.filter((e) => e.kind !== "usage").length);
    const again = fold(planted.slice(0, 20), end);
    expect(again).toBe(end);
  });

  it("counts time from the created event to the latest", () => {
    const end = fold(planted);
    const first = Date.parse(planted[0]?.at ?? "");
    const last = Date.parse(planted.at(-1)?.at ?? "");
    expect(end.elapsedS).toBe((last - first) / 1000);
    expect(end.elapsedS).toBeGreaterThan(0);
  });
});

describe("a script the teacher settles (Codex review)", () => {
  const base = fold(planted.slice(0, 1));
  const at = planted[0]?.at ?? "2026-11-13T09:00:00Z";
  const mark = (question_id: string, awarded: number, max_marks: number) => ({
    question_id,
    awarded,
    max_marks,
    evidence_quote: awarded ? "x" : "",
    memo_line: "m",
    principle: "p",
  });
  const ask = (stage: "marking" | "moderation", seq: number): MothusiEvent => ({
    seq,
    at,
    assessment_id: "gr10-maths-t1-planted",
    kind: "needs_teacher",
    agent: null,
    summary: "L-003 did not pass",
    payload: {
      topic: "script",
      stage,
      title: "L-003 did not pass",
      script_id: "L-003",
      question_id: "1.1",
      question: "q",
      max_marks: 5,
      memo_answer: "a",
      marking_notes: "",
      answer: "",
      attempts: [
        { attempt: 1, awarded: 7, max_marks: 50, failed_check: "evidence", detail: "d", marks: [mark("1.1", 5, 5), mark("1.2", 2, 6)] },
      ],
    },
  });
  const answer = (seq: number, payload: Record<string, unknown>): MothusiEvent => ({
    seq,
    at,
    assessment_id: "gr10-maths-t1-planted",
    kind: "teacher",
    agent: null,
    summary: "teacher",
    payload: { topic: "script", script_id: "L-003", ...payload },
  });

  it("use_attempt takes that attempt's marks for the cell and the inspector", () => {
    const run = [ask("marking", 1), answer(2, { decision: "use_attempt", attempt: 1 })].reduce(apply, base);
    expect(run.cells.get("L-003")).toMatchObject({ status: "teacher", total: 7 });
    expect(run.scripts.get("L-003")?.map((m) => m.awarded)).toEqual([5, 2]);
  });

  it("set_marks takes the teacher's marks, as the teacher's decision", () => {
    const marks = [
      { question_id: "1.1", awarded: 4 },
      { question_id: "1.2", awarded: 6 },
    ];
    const run = [ask("marking", 1), answer(2, { decision: "set_marks", marks })].reduce(apply, base);
    expect(run.cells.get("L-003")).toMatchObject({ status: "teacher", total: 10 });
    expect(run.scripts.get("L-003")?.every((m) => m.principle === "teacher's decision")).toBe(true);
  });

  it("a moderation-stage answer never replaces the first marking", () => {
    const run = [ask("moderation", 1), answer(2, { decision: "use_attempt", attempt: 1 })].reduce(apply, base);
    expect(run.cells.get("L-003")).toMatchObject({ status: "pending", total: null, moderated: true });
    expect(run.scripts.has("L-003")).toBe(false);
  });
});

it("a teacher-settled moderation restores a marked script to settled (Codex review)", () => {
  const start = fold(planted);
  const at = planted[0]?.at ?? "";
  const retry: MothusiEvent = { seq: 1000, at, assessment_id: "gr10-maths-t1-planted", kind: "check", agent: "moderator", summary: "s", payload: { check: "evidence", about: "L-001", passed: false, detail: "d" } };
  const ask: MothusiEvent = { seq: 1001, at, assessment_id: "gr10-maths-t1-planted", kind: "needs_teacher", agent: null, summary: "s", payload: { topic: "script", stage: "moderation", title: "t", script_id: "L-001", question_id: "1.1", question: "q", max_marks: 5, memo_answer: "a", marking_notes: "", answer: "", attempts: [] } };
  const answer: MothusiEvent = { seq: 1002, at, assessment_id: "gr10-maths-t1-planted", kind: "teacher", agent: null, summary: "s", payload: { topic: "script", script_id: "L-001", decision: "set_marks", marks: [] } };
  const run = [retry, ask, answer].reduce(apply, start);
  expect(run.cells.get("L-001")).toMatchObject({ status: "marked", moderated: true });
  expect(run.checks.open).toBe(0);
  expect(run.settled).toBe(6);
});
