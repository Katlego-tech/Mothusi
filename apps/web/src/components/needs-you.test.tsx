// The Needs-you queue on screen: it fills while marking runs, an answer leaves it on a 202, and a
// refusal shows the server's message (console-ui.md, the Needs-you queue).
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { MothusiEvent } from "../api/types";
import { apply, emptyRun, type RunView } from "../run/reducer";
import { NeedsYouQueue } from "./needs-you";

const at = "2026-11-13T20:00:00Z";
const base = { at, assessment_id: "a1", agent: null, summary: "-" };

const marking: MothusiEvent = {
  ...base,
  seq: 0,
  kind: "state",
  payload: { from: "memo_check", to: "marking", reason: "memo confirmed" },
};

const flag = (seq: number, scriptId: string): MothusiEvent => ({
  ...base,
  seq,
  kind: "needs_teacher",
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
});

const runWith = (...events: MothusiEvent[]): RunView => events.reduce(apply, emptyRun("a1"));

function show(run: RunView) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <NeedsYouQueue run={run} />
    </QueryClientProvider>,
  );
}

function answerWith(status: number, body: unknown) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      calls.push(url);
      return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
    }),
  );
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

describe("the Needs-you queue", () => {
  it("shows nothing outside marking or with nothing flagged", () => {
    const { container } = show(runWith(marking));
    expect(container).toBeEmptyDOMElement();
  });

  it("fills with every script flagged while marking runs", () => {
    show(runWith(marking, flag(1, "L-014"), flag(2, "L-024")));
    const queue = screen.getByRole("complementary", { name: "Needs you" });
    expect(within(queue).getByLabelText("2 scripts need you")).toBeInTheDocument();
    expect(within(queue).getByRole("button", { name: /L-014/ })).toBeInTheDocument();
    expect(within(queue).getByRole("button", { name: /L-024/ })).toBeInTheDocument();
  });

  it("removes an item when its answer is accepted (202)", async () => {
    const calls = answerWith(202, { state: "marking" });
    show(runWith(marking, flag(1, "L-014"), flag(2, "L-024")));
    await userEvent.click(screen.getByRole("button", { name: /L-014/ }));
    await userEvent.click(await screen.findByRole("button", { name: /record these marks/i }));
    await waitFor(() => expect(screen.queryByRole("button", { name: /L-014/ })).not.toBeInTheDocument());
    expect(calls.some((u) => u.endsWith("/assessments/a1/scripts/L-014"))).toBe(true);
    expect(screen.getByRole("button", { name: /L-024/ })).toBeInTheDocument();
  });

  it("keeps an item and shows the server's message when the answer is refused (409)", async () => {
    answerWith(409, { message: "L-014 already has an answer waiting to be applied" });
    show(runWith(marking, flag(1, "L-014")));
    await userEvent.click(screen.getByRole("button", { name: /L-014/ }));
    await userEvent.click(await screen.findByRole("button", { name: /record these marks/i }));
    expect(await screen.findByText(/already has an answer waiting/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /L-014/ })).toBeInTheDocument();
  });
});
