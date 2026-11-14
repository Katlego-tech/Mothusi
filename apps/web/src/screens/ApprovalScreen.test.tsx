import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { MothusiEvent } from "../api/types";
import { apply, emptyRun } from "../run/reducer";
import plantedLog from "../run/__fixtures__/planted-events.jsonl?raw";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import type { Bundle } from "../api/types";
import bundleJson from "../run/__fixtures__/planted-bundle.json";
import { ApprovalBar, ApprovalScreen } from "./ApprovalScreen";

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

const bundle = bundleJson as unknown as Bundle;
function wrap(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return { ...render(<QueryClientProvider client={client}>{node}</QueryClientProvider>), client };
}
function respond(status = 200, body: unknown = bundle) {
  const fetch = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(async () => new Response(JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

const approval = through((e) => e.kind === "state" && e.payload.to === "awaiting_approval");
const show = () => wrap(<ApprovalScreen run={approval} />);
const loaded = async () => {
  const button = await screen.findByRole("button", { name: "Approve the bundle" });
  await waitFor(() => expect(button).toBeEnabled());
};

describe("approval details beyond the existing screen tests", () => {
  it("shows loading and keeps both decisions disabled until the bundle arrives", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
    show();
    expect(screen.getByText("Opening the bundle…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve the bundle" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reject with a note" })).toBeDisabled();
  });

  it("shows a bundle fetch error and never enables approval", async () => {
    respond(500, { message: "Bundle is unavailable" });
    show();
    expect(await screen.findByRole("alert")).toHaveTextContent("The bundle could not be opened: Bundle is unavailable");
    expect(screen.getByRole("button", { name: "Approve the bundle" })).toBeDisabled();
    expect(screen.queryByText("Opening the bundle…")).not.toBeInTheDocument();
  });

  it("filters learner names without changing the class totals", async () => {
    respond();
    show();
    await loaded();
    const input = screen.getByRole("textbox", { name: "Find a learner" });
    const learner = required(bundle.mark_sheet[0], "Missing bundle.mark_sheet[0]");
    await userEvent.type(input, `  ${learner.learner.toUpperCase()}  `);
    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(3);
    expect(table).toHaveTextContent(learner.learner);
    expect(table).toHaveTextContent(`Class total · ${bundle.mark_sheet.length} learners`);
    await userEvent.clear(input);
    await userEvent.type(input, "no such learner");
    expect(within(table).getAllByRole("row")).toHaveLength(2);
    await userEvent.clear(input);
    expect(within(table).getAllByRole("row")).toHaveLength(bundle.mark_sheet.length + 2);
  });

  it("downloads the fixture CSV and revokes its temporary URL", async () => {
    respond();
    const create = vi.fn(() => "blob:mark-sheet");
    const revoke = vi.fn();
    vi.stubGlobal("URL", class extends URL { static createObjectURL = create; static revokeObjectURL = revoke; });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    show();
    await loaded();
    await userEvent.click(screen.getByRole("button", { name: "Download CSV" }));
    expect(create).toHaveBeenCalledTimes(1);
    expect(create.mock.calls[0]).toEqual([expect.objectContaining({ type: "text/csv", size: new Blob([bundle.mark_sheet_csv]).size })]);
    const anchor = required(click.mock.contexts[0], "Missing clicked download anchor");
    if (!(anchor instanceof HTMLAnchorElement)) throw new Error("Download click context is not an anchor");
    expect(anchor.download).toBe("mark-sheet.csv");
    expect(anchor.href).toBe("blob:mark-sheet");
    expect(revoke).toHaveBeenCalledWith("blob:mark-sheet");
  });

  it("opens item analysis with facility, real misconceptions and worked corrections", async () => {
    respond();
    show();
    await loaded();
    await userEvent.click(screen.getByRole("tab", { name: /^Item analysis/ }));
    const panel = screen.getByRole("tabpanel", { name: /^Item analysis/ });
    expect(screen.getByRole("tab", { name: /^Item analysis/ })).toHaveAttribute("aria-selected", "true");
    for (const item of bundle.item_analysis) expect(within(panel).getAllByText(`${Number(item.facility_percent).toFixed(1)}%`).length).toBeGreaterThan(0);
    for (const m of bundle.misconceptions) expect(panel).toHaveTextContent(m.pattern);
    expect(within(panel).getByRole("heading", { name: "Worked corrections" })).toBeInTheDocument();
  });

  it.each(["Catch-up plan", "Moderation report"])("opens the %s document", async (name) => {
    respond();
    show();
    await loaded();
    await userEvent.click(screen.getByRole("tab", { name: new RegExp(`^${name}`) }));
    const panel = screen.getByRole("tabpanel", { name: new RegExp(`^${name}`) });
    expect(within(panel).getAllByRole("heading", { name, level: 1 }).length).toBeGreaterThan(0);
    expect(required(panel.textContent, "Missing panel.textContent").length).toBeGreaterThan(name.length);
    if (name === "Catch-up plan") expect(within(panel).getByRole("columnheader", { name: "ATP source" })).toBeInTheDocument();
    else expect(panel).toHaveTextContent(String(bundle.summary.questions_agreed));
  });

  it("shows each learner's note with its evidence and next task", async () => {
    respond();
    show();
    await loaded();
    await userEvent.click(screen.getByRole("tab", { name: /^Learner notes/ }));
    const panel = screen.getByRole("tabpanel", { name: /^Learner notes/ });
    expect(panel).toHaveTextContent("Every note passed the Reviewer.");
    for (const note of bundle.learner_notes) for (const text of [note.learner, note.mark_with_quote, note.next_task, note.self_assessment_prompt]) expect(panel).toHaveTextContent(text);
  });

  it("warns about unverified memo answers and rejected documents", async () => {
    const memo = through((e) => e.kind === "needs_teacher").pending;
    if (memo?.topic !== "memo") throw new Error("Missing planted memo");
    respond(200, { ...bundle, memo_unverified: [{ question_id: memo.question_id, memo_answer: memo.memo_answer, setter_answer: memo.proposed_answer }], class_sheet_review: { passed: false, reason: "Explain the correction" }, learner_notes: bundle.learner_notes.map((n, i) => i === 0 ? { ...n, review: { passed: false, reason: "Name the next task" } } : n) });
    show();
    await loaded();
    expect(screen.getByText(/Code could not check memo/)).toHaveTextContent(memo.question_id);
    await userEvent.click(screen.getByRole("tab", { name: /^Item analysis/ }));
    expect(screen.getByText(/Reviewer did not pass this class sheet/)).toHaveTextContent("Explain the correction");
    await userEvent.click(screen.getByRole("tab", { name: /^Learner notes/ }));
    expect(screen.getByText(/1 still rejected/)).toBeInTheDocument();
    expect(screen.getByText("Rejected by the Reviewer: Name the next task")).toBeInTheDocument();
  });

  it("handles an empty bundle without NaN totals and explains no misconceptions", async () => {
    respond(200, { ...bundle, mark_sheet: [], item_analysis: [], misconceptions: [], learner_notes: [], summary: { ...bundle.summary, scripts: 0, priced: false } });
    show();
    await loaded();
    expect(screen.getByText("Class total · 0 learners")).toBeInTheDocument();
    const summary = screen.getByLabelText("Summary of the run");
    expect(summary).toHaveTextContent("Your steps");
    expect(summary).toHaveTextContent("answers you gave");
    expect(summary).not.toHaveTextContent(/cost|metered|R\s?\d/i);
    expect(screen.queryByText(/NaN|Infinity/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: /^Item analysis/ }));
    expect(screen.getByText("No item needs re-teaching, so there is no misconception to name.")).toBeInTheDocument();
  });

  it("cancels rejection without posting and rejects whitespace-only notes", async () => {
    const fetch = respond();
    show();
    await loaded();
    await userEvent.click(screen.getByRole("button", { name: "Reject with a note" }));
    await userEvent.type(screen.getByRole("textbox", { name: "What should change?" }), "   ");
    expect(screen.getByRole("button", { name: "Send it back to the Writer" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("button", { name: "Approve the bundle" })).toBeEnabled();
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it.each(["approve", "reject"])("shows a refused %s decision and allows retry", async (decision) => {
    const fetch = respond(409, { message: "This approval round is closed" });
    wrap(<ApprovalBar assessmentId={id} disabled={false} />);
    if (decision === "reject") {
      await userEvent.click(screen.getByRole("button", { name: "Reject with a note" }));
      await userEvent.type(screen.getByRole("textbox", { name: "What should change?" }), "Explain the next task");
    }
    const button = screen.getByRole("button", { name: decision === "approve" ? "Approve the bundle" : "Send it back to the Writer" });
    await userEvent.click(button);
    expect(await screen.findByText("This approval round is closed")).toBeInTheDocument();
    expect(button).toBeEnabled();
    expect(required(fetch.mock.calls[0], "Missing fetch.mock.calls[0]")[0]).toBe(`/api/assessments/${id}/approval`);
    expect(required(fetch.mock.calls[0], "Missing fetch.mock.calls[0]")[1]?.method).toBe("POST");
  });

  it("disables both decisions while approval is being posted", async () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
    wrap(<ApprovalBar assessmentId={id} disabled={false} />);
    await userEvent.click(screen.getByRole("button", { name: "Approve the bundle" }));
    expect(screen.getByRole("button", { name: "Approve the bundle" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reject with a note" })).toBeDisabled();
  });

  it("fetches a separate bundle for a new approval round", async () => {
    const fetch = respond();
    const { rerender, client } = show();
    await loaded();
    const newRound = apply(approval, { seq: approval.lastSeq + 1, at: required(approval.lastAt, "Missing approval.lastAt"), assessment_id: id, kind: "state", agent: null, summary: "Rewritten", payload: { from: "reviewing", to: "awaiting_approval", reason: "Rewritten" } });
    fetch.mockClear();
    fetch.mockImplementation(() => new Promise<Response>(() => {}));
    rerender(<QueryClientProvider client={client}><ApprovalScreen run={newRound} /></QueryClientProvider>);
    expect(screen.getByText("Opening the bundle…")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
  });

  it("shows the approved delivery wait without offering decisions again", async () => {
    respond();
    const approved = through((e) => e.kind === "state" && e.payload.to === "approved");
    wrap(<ApprovalScreen run={approved} />);
    expect(screen.getByRole("status")).toHaveTextContent("The Executor is writing the files now.");
    expect(screen.queryByRole("button", { name: "Approve the bundle" })).not.toBeInTheDocument();
    await screen.findByRole("table");
  });
});
