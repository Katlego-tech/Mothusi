// SPEC US8 on the real planted run: the approval screen offers approve and reject-with-note and
// nothing else; a retry is visible in order; the teacher's answers post console-ui.md §6's bodies.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router";

import type { Bundle, MothusiEvent } from "../api/types";
import { NeedsTeacherCard } from "../components/ask";
import { apply, emptyRun, type RunView } from "../run/reducer";
import bundleJson from "../run/__fixtures__/planted-bundle.json";
import plantedLog from "../run/__fixtures__/planted-events.jsonl?raw";
import { ApprovalScreen } from "./ApprovalScreen";
import { DeliveredScreen } from "./DeliveredScreen";
import { RunHeader } from "../components/chrome";
import { RunScreen } from "./RunScreen";
import { SetTaskScreen, memoFacts, scriptFacts } from "./SetTaskScreen";
import demoMemo from "../../../../services/core/fixtures/gr10-maths-t1/memo.toml?raw";
import demoScripts from "../../../../services/core/fixtures/gr10-maths-t1/scripts.csv?raw";

const planted = plantedLog
  .trim()
  .split("\n")
  .map((l) => JSON.parse(l) as MothusiEvent);
const bundle = bundleJson as unknown as Bundle;

function foldUntil(predicate: (e: MothusiEvent) => boolean): RunView {
  const end = planted.findIndex(predicate);
  return planted.slice(0, end + 1).reduce(apply, emptyRun("gr10-maths-t1-planted"));
}

function wrap(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{node}</MemoryRouter>
    </QueryClientProvider>,
  );
}

type Call = { url: string; body: unknown };

function mockFetch(routes: Record<string, unknown>): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const body = typeof init?.body === "string" ? (JSON.parse(init.body) as unknown) : null;
      calls.push({ url, body });
      const key = Object.keys(routes).find((k) => url.endsWith(k));
      const status = init?.method === "POST" ? 202 : 200;
      return new Response(JSON.stringify(key ? routes[key] : { state: "x" }), { status });
    }),
  );
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

describe("the approval screen (SPEC US8)", () => {
  const atApproval = foldUntil((e) => e.kind === "state" && e.payload.to === "awaiting_approval");

  it("offers approve and reject-with-note, and nothing else", async () => {
    mockFetch({ "/bundle": bundle });
    wrap(<ApprovalScreen run={atApproval} />);
    const bar = await screen.findByRole("contentinfo");
    await waitFor(() => expect(within(bar).getByRole("button", { name: "Approve the bundle" })).toBeEnabled());
    expect(within(bar).getAllByRole("button").map((b) => b.textContent)).toEqual([
      "Reject with a note",
      "Approve the bundle",
    ]);
  });

  it("shows the bundle: mark sheet with names, the five documents as tabs", async () => {
    mockFetch({ "/bundle": bundle });
    wrap(<ApprovalScreen run={atApproval} />);
    const learner = bundle.mark_sheet[0]?.learner ?? "";
    expect(await screen.findByText(learner)).toBeInTheDocument();
    expect(screen.getAllByRole("tab").map((t) => t.textContent?.split(/\d/)[0]?.trim())).toEqual([
      "Mark sheet",
      "Item analysis",
      "Catch-up plan",
      "Learner notes",
      "Moderation report",
    ]);
  });

  it("approves with {decision: approve}", async () => {
    const calls = mockFetch({ "/bundle": bundle });
    wrap(<ApprovalScreen run={atApproval} />);
    const button = await screen.findByRole("button", { name: "Approve the bundle" });
    await waitFor(() => expect(button).toBeEnabled());
    await userEvent.click(button);
    await screen.findByText("Approved.");
    expect(calls.at(-1)).toEqual({ url: "/api/assessments/gr10-maths-t1-planted/approval", body: { decision: "approve" } });
  });

  it("rejects only with a note, and says the marks stay", async () => {
    const calls = mockFetch({ "/bundle": bundle });
    wrap(<ApprovalScreen run={atApproval} />);
    const reject = await screen.findByRole("button", { name: "Reject with a note" });
    await waitFor(() => expect(reject).toBeEnabled());
    await userEvent.click(reject);
    const send = screen.getByRole("button", { name: "Send it back to the Writer" });
    expect(send).toBeDisabled();
    await userEvent.type(screen.getByLabelText("What should change?"), "Name the ATP week in each next task.");
    await userEvent.click(send);
    await screen.findByText("Sent back to the Writer.");
    expect(calls.at(-1)?.body).toEqual({ decision: "reject", note: "Name the ATP week in each next task." });
  });
});

describe("the live run", () => {
  it("shows the L-014 retry in order: the failed check, the re-brief, the passing result (US8)", () => {
    const run = foldUntil((e) => e.kind === "result" && e.payload.brief_id.endsWith("/L-014/marker/2"));
    const { container } = wrap(<RunScreen run={run} connection="live" />);
    const rows = [...container.querySelectorAll(".ev")].map((r) => r.textContent ?? "");
    const failed = rows.findIndex((t) => t.includes("FAIL") && t.includes("L-014"));
    const rebrief = rows.findIndex((t) => t.includes("Re-brief marker attempt 2"));
    const passed = rows.findIndex((t, i) => i < rebrief && t.includes("L-014 marked"));
    expect(failed).toBeGreaterThan(-1);
    expect(rebrief).toBeLessThan(failed);
    expect(passed).toBeGreaterThan(-1);
    expect(passed).toBeLessThan(rebrief);
  });

  it("draws the L-021 disagreement as the evidence pair, each pen's quote in its ink", () => {
    const run = foldUntil((e) => e.kind === "disagreement");
    const { container } = wrap(<RunScreen run={run} connection="live" />);
    const red = [...container.querySelectorAll("mark.quote:not(.green)")].map((m) => m.textContent);
    const green = [...container.querySelectorAll("mark.quote.green")].map((m) => m.textContent);
    expect(green).toContain("l = 2w + 3");
    expect(red.length).toBeGreaterThan(0);
    expect(screen.getByText(/L-021, question 4: the marks differ/)).toBeInTheDocument();
  });

  it("pins the memo question and posts use_proposed for the Setter's answer", async () => {
    const run = foldUntil((e) => e.kind === "needs_teacher");
    const calls = mockFetch({});
    if (run.pending?.topic !== "memo") throw new Error("the planted run asks about the memo first");
    wrap(<NeedsTeacherCard assessmentId={run.assessmentId} question={run.pending} questions={run.created?.questions ?? []} />);
    await userEvent.click(screen.getByRole("button", { name: "Use x = -4 and mark" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toEqual({
      url: "/api/assessments/gr10-maths-t1-planted/memo",
      body: { decision: "use_proposed", question_id: "2.3" },
    });
  });
});

describe("set the task", () => {
  it("reads the facts the mockup shows from the demo files", () => {
    expect(memoFacts(demoMemo)).toBe("50 marks · 9 questions");
    expect(scriptFacts(demoScripts)).toMatch(/^32 learners · 278 answers · names become L-001 to L-032 here/);
  });
});

describe("after approval (Codex review)", () => {
  it("shows why delivery stopped instead of 'writing the files' forever", async () => {
    mockFetch({ "/bundle": bundle });
    const approved = foldUntil((e) => e.kind === "state" && e.payload.to === "approved");
    const halted = { ...approved, halted: "delivery stopped: bundle root is not empty" };
    wrap(<ApprovalScreen run={halted} />);
    expect(await screen.findByText("Delivery stopped.")).toBeInTheDocument();
    expect(screen.getByText(/bundle root is not empty/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve the bundle" })).toBeNull();
  });
});

function expectNoMoney() {
  expect(screen.queryByText(/R\s?\d/)).toBeNull();
  expect(screen.queryByText(/Cost|Spending limit/i)).toBeNull();
  expect(screen.queryByText(/not metered/i)).toBeNull();
}

describe("console without money displays", () => {
  it("shows the live run without money", () => {
    mockFetch({ "/packs": [] });
    wrap(<RunScreen run={foldUntil((e) => e.kind === "disagreement")} connection="live" />);
    expectNoMoney();
  });
  it("shows the approval summary without money", async () => {
    mockFetch({ "/bundle": bundle });
    wrap(<ApprovalScreen run={foldUntil((e) => e.kind === "state" && e.payload.to === "awaiting_approval")} />);
    await screen.findByText("Class average");
    expect(screen.getByLabelText("Summary of the run").children).toHaveLength(6);
    expectNoMoney();
  });
  it("shows the delivered summary and log without money", async () => {
    mockFetch({ "/bundle": bundle });
    wrap(<DeliveredScreen run={planted.reduce(apply, emptyRun("gr10-maths-t1-planted"))} />);
    await waitFor(() => expect(screen.getByText("Moderator agreed").parentElement).not.toHaveTextContent("—"));
    expect(screen.getByLabelText("The run in numbers").children).toHaveLength(3);
    expectNoMoney();
  });
  it("sets the task without money fields and retains the backend guard", async () => {
    const { api } = await import("../api/client");
    mockFetch({ "/packs": [{ pack_id: "dbe-caps/mathematics-gr10", subject: "Mathematics", grade: 10,
      moderation: { min_scripts: 1, sample_percent: "10", spread: "low-middle-high" },
      tasks: [{ name: "Test", term: 1, max_marks: 50, weight_percent: "25" }] }] });
    const create = vi.spyOn(api, "create").mockResolvedValue({ assessment_id: "new-run", state: "draft" });
    try {
      wrap(<SetTaskScreen />);
      const demo = screen.getByRole("button", { name: "Load the demo class set" });
      await waitFor(() => expect(demo).toBeEnabled());
      await userEvent.click(demo);
      const start = screen.getByRole("button", { name: "Start marking" });
      await waitFor(() => expect(start).toBeEnabled());
      expectNoMoney();
      await userEvent.click(start);
      await waitFor(() => expect(create).toHaveBeenCalledWith(expect.objectContaining({ budget_zar: "40.00" })));
    } finally {
      create.mockRestore();
    }
  });
  it("reflows the run header around its remaining KPIs", () => {
    const { container } = wrap(<RunHeader run={emptyRun("a1")} />);
    expect(container.querySelectorAll(".strip > div")).toHaveLength(2);
    expectNoMoney();
  });
  it.each([0, 4])("asks to carry on with %i scripts left and posts the suggested raise", async (left) => {
    const calls = mockFetch({});
    wrap(<NeedsTeacherCard assessmentId="a1" questions={[]} question={{
      topic: "budget", title: "Budget R40", spent_zar: "35", limit_zar: "40",
      scripts_total: 6, scripts_settled: 6 - left,
      spent: [{ stage: "marking", zar: "35" }], estimate: [{ stage: "marking", zar: "10" }],
    }} />);
    expect(screen.getByRole("heading", { name: "This set is taking longer than planned" })).toBeInTheDocument();
    expect(screen.getByText(left ? `Carry on marking the last ${left} scripts?` : "Carry on?")).toBeInTheDocument();
    expect(screen.getByText("Or leave it paused; nothing is lost.")).toBeInTheDocument();
    expect(screen.getAllByRole("button")).toHaveLength(1);
    expectNoMoney();
    await userEvent.click(screen.getByRole("button", { name: /^Carry on$/ }));
    await waitFor(() => expect(calls).toContainEqual({ url: "/api/assessments/a1/budget", body: { raise_to_zar: "60.00" } }));
  });
});
