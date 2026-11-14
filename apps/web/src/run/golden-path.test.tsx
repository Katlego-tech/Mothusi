import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { AssessmentState, MothusiEvent } from "../api/types";
import { NeedsYouQueue, withLocal } from "../components/needs-you";
import plantedLog from "./__fixtures__/planted-events.jsonl?raw";
import { apply, emptyRun, type RunView } from "./reducer";

afterEach(() => vi.unstubAllGlobals());

it("the golden path: a script is flagged and answered while marking runs, then only approval remains on replay", async () => {
  const planted = plantedLog.trim().split("\n").map((line) => JSON.parse(line) as MothusiEvent);
  expect(planted).toHaveLength(150);
  expect(planted.some((event) => event.kind === "needs_teacher" && event.payload.topic === "script")).toBe(false);

  // Locate the insertion by stage, rather than confusing the earlier Setter results with marking.
  let stage: AssessmentState = "draft";
  const firstResult = planted.findIndex((event) => {
    if (event.kind === "state") stage = event.payload.to;
    return stage === "marking" && event.kind === "result";
  });
  const previous = planted[firstResult];
  if (!previous || previous.kind !== "result") throw new Error("Missing first marking result");
  const scriptId = "L-014";
  const flag: MothusiEvent = {
    seq: firstResult + 1,
    at: previous.at,
    assessment_id: previous.assessment_id,
    kind: "needs_teacher",
    agent: null,
    summary: `${scriptId} did not pass the checks in 3 attempts`,
    payload: {
      topic: "script",
      stage: "marking",
      script_id: scriptId,
      title: `${scriptId} did not pass the checks in 3 attempts`,
      question_id: "3.1",
      question: "Solve simultaneously",
      max_marks: 8,
      memo_answer: "x = 2 and y = 3",
      marking_notes: "substitution: 2 marks",
      answer: "x = 2, y = 3",
      attempts: [],
    },
  };
  const events = [...planted.slice(0, firstResult + 1), flag, ...planted.slice(firstResult + 1)]
    .map((event, seq) => ({ ...event, seq }));
  expect(flag.seq).toBe(21);
  expect(events.map((event) => event.seq)).toEqual(Array.from({ length: 151 }, (_, seq) => seq));

  const assessmentId = previous.assessment_id;
  let run = emptyRun(assessmentId);
  let cursor = 0;
  const replay = () => events.slice(0, cursor).reduce(apply, emptyRun(assessmentId));
  const next = () => {
    const event = events[cursor++];
    if (!event) throw new Error("Unexpected end of recorded run");
    run = apply(run, event);
    // A fresh reload starts at seq 0, including the memo question and its answer.
    expect(replay()).toEqual(run);
    return event;
  };
  while (cursor <= flag.seq) next();
  expect(run.created?.script_ids).toContain(scriptId);
  expect(run.cells.get(scriptId)).toMatchObject({ status: "teacher", total: null });
  const queued = [{ scriptId, question: flag.payload, flaggedSeq: flag.seq, status: "open" }];
  expect(run.needsYou).toEqual(queued);
  expect(run.state).toBe("marking");
  expect(run.halted).toBeNull();

  // Keep the flag open through checks and another script's completed marking.
  let results = 0;
  let checks = 0;
  while (results === 0 || checks === 0 || events[cursor]?.kind !== "check") {
    const before = run;
    const event = next();
    expect(run.state).toBe("marking");
    expect(run.halted).toBeNull();
    expect(run.needsYou).toEqual(queued);
    if (event.kind === "result") {
      results++;
      const p = event.payload;
      expect(p.agent).toBe("marker");
      expect(p.script_id).not.toBe(scriptId);
      expect(p.status).toBe("done");
      if (!p.script_id) throw new Error("Marking result has no script id");
      expect(run.cells.get(p.script_id)).toMatchObject({ status: "marked", total: p.awarded });
      expect(run.scripts.get(p.script_id)).toEqual(p.marks);
      expect(run.settled).toBe(before.settled + 1);
    }
    if (event.kind === "check") {
      checks++;
      expect(run.checks.run).toBe(before.checks.run + 1);
      expect(run.checks.failed).toBe(before.checks.failed + (event.payload.passed ? 0 : 1));
    }
  }
  expect(results).toBeGreaterThan(0);
  expect(checks).toBeGreaterThan(0);

  const fetchMock = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(async () => new Response(JSON.stringify({ state: "marking" }), {
    status: 202,
    headers: { "Content-Type": "application/json" },
  }));
  vi.stubGlobal("fetch", fetchMock);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const queue = (view: RunView) => (
    <QueryClientProvider client={client}>
      <NeedsYouQueue run={view} />
    </QueryClientProvider>
  );
  const shown = render(queue(run));
  const user = userEvent.setup();
  const aside = screen.getByRole("complementary", { name: "Needs you" });
  expect(within(aside).getByLabelText("1 scripts need you")).toBeInTheDocument();
  expect(within(aside).getAllByRole("listitem")).toHaveLength(1);
  await user.click(within(aside).getByRole("button", { name: new RegExp(scriptId) }));
  await user.click(await screen.findByRole("button", { name: /record these marks/i }));
  await waitFor(() => expect(screen.queryByRole("complementary", { name: "Needs you" })).not.toBeInTheDocument());
  expect(fetchMock).toHaveBeenCalledExactlyOnceWith(
    expect.stringContaining(`/assessments/${assessmentId}/scripts/${scriptId}`),
    expect.objectContaining({ method: "POST" }),
  );
  const request = fetchMock.mock.calls[0];
  if (!request?.[1]?.body || !run.created) throw new Error("Missing answer POST or created payload");
  const answer = JSON.parse(request[1].body as string) as Record<string, unknown>;
  expect(answer).toEqual({
    decision: "set_marks",
    marks: run.created.questions.map((q) => ({ question_id: q.question_id, awarded: 0 })),
  });

  // No teacher event has arrived: acceptance is local, and the event-only reload still queues it.
  expect(run.events.filter((event) => event.seq > flag.seq && event.kind === "teacher")).toEqual([]);
  expect(run.needsYou).toEqual(queued);
  expect(replay().needsYou).toEqual(queued);
  const accepted = { [scriptId]: { kind: "answered" as const } };
  expect(withLocal(run, accepted).needsYou).toEqual([]);
  expect(withLocal(replay(), accepted)).toEqual(withLocal(run, accepted));

  // More streamed work must not resurrect the locally accepted item.
  expect(next().kind).toBe("check");
  expect(run.state).toBe("marking");
  expect(run.needsYou).toEqual(queued);
  shown.rerender(queue(run));
  expect(screen.queryByRole("complementary", { name: "Needs you" })).not.toBeInTheDocument();

  // Model the later inbox acknowledgement using the actual POST body, then replay it too.
  if (!run.lastAt) throw new Error("Missing event timestamp");
  events.splice(cursor, 0, {
    seq: cursor,
    at: run.lastAt,
    assessment_id: assessmentId,
    kind: "teacher",
    agent: null,
    summary: `Teacher answered ${scriptId}`,
    payload: { topic: "script", script_id: scriptId, ...answer },
  });
  for (const [seq, event] of events.entries()) events[seq] = { ...event, seq };
  expect(next().kind).toBe("teacher");
  expect(run.needsYou).toEqual([]);
  expect(run.pending).toBeNull();
  expect(run.state).toBe("marking");
  shown.unmount();
  const reloaded = render(queue(replay()));
  expect(screen.queryByRole("complementary", { name: "Needs you" })).not.toBeInTheDocument();

  while (run.state !== "awaiting_approval") {
    next();
    expect(run.needsYou).toEqual([]);
  }
  expect(run.pending).toBeNull();
  expect(run.halted).toBeNull();
  expect(run.events.filter((event) => event.kind === "state" && event.payload.to === "awaiting_approval")).toHaveLength(1);
  // Approval is a run state, not a needs_teacher payload. Its answer is the next recorded event.
  expect(events[cursor]).toMatchObject({ kind: "teacher", payload: { topic: "approval", decision: "approve" } });
  reloaded.rerender(queue(replay()));
  expect(screen.queryByRole("complementary", { name: "Needs you" })).not.toBeInTheDocument();

  while (cursor < events.length) next();
  expect(run.state).toBe("delivered");
  expect(run.needsYou).toEqual([]);
  expect(run.delivered?.written).not.toHaveLength(0);
});
