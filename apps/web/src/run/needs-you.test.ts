import type { AssessmentState, MothusiEvent, ScriptQuestion } from "../api/types";
import {
  apply,
  emptyRun,
  needsYouAnswered,
  needsYouFailed,
  needsYouSending,
  type RunView,
} from "./reducer";

const envelope = {
  at: "2026-11-13T20:00:00Z",
  assessment_id: "assessment",
  agent: null,
  summary: "Script needs a decision",
};

const flag = (seq: number, scriptId = "L-001", stage: ScriptQuestion["stage"] = "marking"): MothusiEvent => ({
  ...envelope,
  seq,
  kind: "needs_teacher",
  payload: {
    topic: "script",
    stage,
    script_id: scriptId,
    title: "Check the evidence",
    question_id: "1",
    question: "Find x",
    max_marks: 5,
    memo_answer: "2",
    marking_notes: "Show working",
    answer: "x = 2",
    attempts: [],
  },
});

const state = (seq: number, to: AssessmentState): MothusiEvent => ({
  ...envelope,
  seq,
  kind: "state",
  payload: { from: "marking", to, reason: "Next stage" },
});

const answer = (seq: number, payload: Record<string, unknown>): MothusiEvent => ({
  ...envelope,
  seq,
  kind: "teacher",
  payload,
});

const marking = (): RunView => {
  const run = apply(emptyRun("assessment"), state(0, "marking"));
  run.cells.set("L-001", {
    scriptId: "L-001",
    status: "working",
    total: null,
    maxMarks: 5,
    moderated: false,
    disputed: false,
  });
  return run;
};

const queued = () => [flag(1), flag(2, "L-002")].reduce(apply, marking());

describe("Needs you state", () => {
  it("starts empty and adds marking flags in event order without changing the existing question flow", () => {
    const start = marking();
    expect(start.needsYou).toEqual([]);
    const first = flag(1);
    const run = apply(start, first);
    expect(run.needsYou).toEqual([
      { scriptId: "L-001", question: first.payload, flaggedSeq: 1, status: "open" },
    ]);
    expect(run.pending).toBe(first.payload);
    expect(run.finding).toBe("Check the evidence");
    expect(run.cells.get("L-001")?.status).toBe("teacher");
    expect(start.needsYou).toEqual([]);
    expect(queued().needsYou.map((item) => item.scriptId)).toEqual(["L-001", "L-002"]);
  });

  it("does not duplicate a script, even with a new event sequence or an error in progress", () => {
    const run = needsYouFailed(queued(), "L-001", "Try again");
    expect(apply(run, flag(3)).needsYou).toEqual(run.needsYou);
  });

  it.each(["marked", "teacher"] as const)("does not queue an already %s script", (status) => {
    const run = marking();
    const cell = run.cells.get("L-001");
    if (!cell) throw new Error("Missing script cell");
    run.cells.set("L-001", { ...cell, status, total: 0 });
    expect(apply(run, flag(1)).needsYou).toEqual([]);
  });

  it("does not queue moderation or other topics, but preserves their pending question", () => {
    const moderation = flag(1, "L-001", "moderation");
    const run = apply(marking(), moderation);
    expect(run.needsYou).toEqual([]);
    expect(run.pending).toBe(moderation.payload);
    const budget: MothusiEvent = {
      ...envelope,
      seq: 2,
      kind: "needs_teacher",
      payload: { topic: "budget", title: "Budget", spent_zar: "1", limit_zar: "1", scripts_settled: 0, scripts_total: 2, spent: [], estimate: [] },
    };
    expect(apply(run, budget).needsYou).toEqual([]);
    expect(apply(run, budget).pending).toBe(budget.payload);
  });

  it("removes only the script identified by a teacher event, even without a pending question", () => {
    const run = { ...queued(), pending: null };
    const next = apply(run, answer(3, { topic: "script", script_id: "L-001" }));
    expect(next.needsYou).toEqual([run.needsYou[1]]);
    expect(run.needsYou).toHaveLength(2);
    expect(apply(run, answer(3, { topic: "memo", script_id: "L-001" })).needsYou).toEqual(run.needsYou);
    expect(apply(run, answer(3, { topic: "script" })).needsYou).toEqual(run.needsYou);
  });

  it("removes only the answered item when the POST is accepted", () => {
    const run = queued();
    const next = needsYouAnswered(run, "L-001");
    expect(next).not.toBe(run);
    expect(next.needsYou).toEqual([run.needsYou[1]]);
    expect(next.pending).toBe(run.pending);
    expect(run.needsYou).toHaveLength(2);
  });

  it("sets sending, records an error, and clears the error when resubmitting without mutation", () => {
    const run = queued();
    const sending = needsYouSending(run, "L-001");
    expect(sending).not.toBe(run);
    expect(sending.needsYou[0]).toEqual({ ...run.needsYou[0], status: "sending" });
    const failed = needsYouFailed(sending, "L-001", "Answer already received");
    expect(failed).not.toBe(sending);
    expect(failed.needsYou[0]).toEqual({ ...run.needsYou[0], status: "error", error: "Answer already received" });
    expect(needsYouSending(failed, "L-001").needsYou[0]).toEqual(sending.needsYou[0]);
    expect(run.needsYou[0]?.status).toBe("open");
    expect(sending.needsYou[0]?.status).toBe("sending");
    expect(failed.needsYou[1]).toBe(run.needsYou[1]);
  });

  it("returns a new view without adding an unknown script for any helper", () => {
    const run = queued();
    for (const next of [needsYouSending(run, "unknown"), needsYouAnswered(run, "unknown"), needsYouFailed(run, "unknown", "Failed")]) {
      expect(next).not.toBe(run);
      expect(next.needsYou).toEqual(run.needsYou);
    }
  });

  it("empties the list when marking ends and preserves the one-question flow", () => {
    const run = queued();
    expect(apply(run, state(3, "marking")).needsYou).toEqual(run.needsYou);
    const next = apply(run, state(3, "moderating"));
    expect(next.needsYou).toEqual([]);
    expect(next.pending).toBe(run.pending);
    expect(run.needsYou).toHaveLength(2);
  });

  it("replays the same log twice without duplicates or resurrecting answered items", () => {
    const events = [state(0, "marking"), flag(1), flag(2, "L-002"), answer(3, { topic: "script", script_id: "L-001" })];
    const once = events.reduce(apply, emptyRun("assessment"));
    const twice = events.reduce(apply, once);
    expect(once.needsYou.map((item) => item.scriptId)).toEqual(["L-002"]);
    expect(twice.needsYou).toEqual(once.needsYou);
    expect(events.reduce(apply, emptyRun("assessment")).needsYou).toEqual(once.needsYou);
  });
});
