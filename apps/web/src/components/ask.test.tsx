import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { MothusiEvent } from "../api/types";
import { apply, emptyRun } from "../run/reducer";
import plantedLog from "../run/__fixtures__/planted-events.jsonl?raw";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { waitFor } from "@testing-library/react";
import type { BudgetQuestion, MemoQuestion, NeedsTeacher, ReadingQuestion, ScriptQuestion } from "../api/types";
import { NeedsTeacherCard, ScriptDecisionForm } from "./ask";

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


const memoRun = through((e) => e.kind === "needs_teacher");
const memo = memoRun.pending as MemoQuestion;
const questions = required(finished.created, "Missing finished.created").questions;
const result = events.find((e) => e.kind === "result" && e.payload.agent === "marker" && e.payload.status === "done" && e.payload.marks?.length);
if (result?.kind !== "result") throw new Error("Missing planted marks");
const marks = required(finished.scripts.get(required(result.payload.script_id, "Missing result.payload.script_id")), "Missing finished.scripts.get(result.payload.script_id)");
const firstQuestion = required(questions[0], "Missing questions[0]");
const script: ScriptQuestion = {
  topic: "script", stage: "marking", title: "Check this script", script_id: required(result.payload.script_id, "Missing result.payload.script_id"),
  question_id: memo.question_id, question: memo.question, max_marks: memo.max_marks,
  memo_answer: memo.memo_answer, marking_notes: memo.marking_notes, answer: required(marks.find((m) => m.question_id === memo.question_id), "Missing marks.find((m) => m.question_id === memo.question_id)").evidence_quote,
  attempts: [{ attempt: 1, awarded: null, max_marks: null, failed_check: "evidence", detail: "No marks returned", marks: [] },
    { attempt: 2, awarded: result.payload.awarded, max_marks: result.payload.max_marks, failed_check: "evidence", detail: "Check the quoted work", marks }],
};
const budget: BudgetQuestion = {
  topic: "budget", title: "Spending limit", spent_zar: "12.00", limit_zar: "15.00", scripts_settled: finished.settled,
  scripts_total: finished.cells.size + 1, spent: [{ stage: "marking", zar: "12.00" }], estimate: [{ stage: "writing", zar: "4.00" }],
};
const reading: ReadingQuestion = {
  topic: "reading", title: "Check the readings", items: [
    { script_id: script.script_id, question_id: firstQuestion.question_id, line_no: 0, line: required(marks[0], "Missing marks[0]").evidence_quote, crop_url: "/crops/line.png", page_missing: false },
    { script_id: script.script_id, question_id: memo.question_id, line_no: null, line: "", crop_url: null, page_missing: true },
  ],
};

function show(q: NeedsTeacher) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><NeedsTeacherCard assessmentId={id} question={q} questions={questions} /></QueryClientProvider>);
}
function respond(status = 202, body: unknown = { state: "marking" }) {
  const fetch = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(async () => new Response(JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
const posted = (fetch: ReturnType<typeof respond>) => {
  const [url, init] = required(fetch.mock.calls.at(-1), "Missing fetch.mock.calls.at(-1)");
  return { url, method: init?.method, body: JSON.parse(String(init?.body)) as unknown };
};
afterEach(() => vi.unstubAllGlobals());

describe("the memo decision", () => {
  it("shows the memo and the Setter's actual working", () => {
    show(memo);
    expect(screen.getByRole("region", { name: `Memo item ${memo.question_id} may be wrong` })).toBeInTheDocument();
    expect(screen.getAllByRole("figure")).toHaveLength(2);
    expect(screen.getByText(memo.question)).toBeInTheDocument();
    expect(screen.getByText(/nothing is marked yet/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: `Use ${memo.proposed_answer} and mark` })).toBeEnabled();
  });

  it.each(["use_proposed", "keep_memo"] as const)("posts the %s decision", async (decision) => {
    const fetch = respond();
    show(memo);
    await userEvent.click(screen.getByRole("button", { name: decision === "keep_memo" ? `Keep the memo's ${memo.memo_answer}` : `Use ${memo.proposed_answer} and mark` }));
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    expect(posted(fetch)).toEqual({ url: `/api/assessments/${id}/memo`, method: "POST", body: { decision, question_id: memo.question_id } });
  });

  it("requires a nonblank amendment and posts the teacher's answer", async () => {
    const fetch = respond();
    show(memo);
    await userEvent.click(screen.getByRole("button", { name: "Write a different answer" }));
    const input = screen.getByRole("textbox", { name: `The memo's answer for ${memo.question_id}` });
    const submit = screen.getByRole("button", { name: "Use this answer and mark" });
    expect(input).toHaveFocus();
    expect(submit).toBeDisabled();
    await userEvent.type(input, "   ");
    expect(submit).toBeDisabled();
    await userEvent.clear(input);
    await userEvent.type(input, memo.proposed_answer);
    await userEvent.click(submit);
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    expect(posted(fetch).body).toEqual({ decision: "amend", question_id: memo.question_id, answer: memo.proposed_answer });
  });

  it("does not offer an absent proposed answer", () => {
    show({ ...memo, proposed_answer: "", working: "" });
    expect(screen.queryByRole("button", { name: /^Use .* and mark$/ })).not.toBeInTheDocument();
    expect(screen.getByText("(no answer given)")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: `Keep the memo's ${memo.memo_answer}` })).toBeEnabled();
  });

  it("disables decisions while a request is pending", async () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
    show(memo);
    await userEvent.click(screen.getByRole("button", { name: `Keep the memo's ${memo.memo_answer}` }));
    for (const button of screen.getAllByRole("button")) expect(button).toBeDisabled();
  });

  it("reports a refused decision and permits retry", async () => {
    const fetch = respond(409, { message: "The memo has already changed" });
    show(memo);
    const button = screen.getByRole("button", { name: `Keep the memo's ${memo.memo_answer}` });
    await userEvent.click(button);
    expect(await screen.findByRole("alert")).toHaveTextContent("The memo has already changed");
    expect(button).toBeEnabled();
    fetch.mockResolvedValue(new Response(JSON.stringify({ state: "marking" }), { status: 202 }));
    await userEvent.click(button);
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });

  it("explains a network failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));
    show(memo);
    await userEvent.click(screen.getByRole("button", { name: `Keep the memo's ${memo.memo_answer}` }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Mothusi's service is not answering");
  });
});

describe("the script decision", () => {
  it("shows failed attempts, prefills real marks and exposes every numeric control by name", () => {
    show(script);
    expect(screen.getByRole("heading", { name: script.title })).toBeInTheDocument();
    expect(screen.getByText("no marks")).toBeInTheDocument();
    expect(screen.getByText(/Pre-filled from attempt 2/)).toBeInTheDocument();
    for (const q of questions) {
      expect(screen.getByRole("spinbutton", { name: `${q.question_id} of ${q.max_marks}` })).toHaveValue(marks.find((m) => m.question_id === q.question_id)?.awarded ?? 0);
    }
    expect(screen.queryByRole("button", { name: "Use attempt 1" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Use attempt 2" })).toBeEnabled();
  });

  it("posts the complete attempt the teacher chooses", async () => {
    const fetch = respond();
    show(script);
    await userEvent.click(screen.getByRole("button", { name: "Use attempt 2" }));
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    expect(posted(fetch)).toEqual({ url: `/api/assessments/${id}/scripts/${script.script_id}`, method: "POST", body: { decision: "use_attempt", attempt: 2 } });
  });

  it("updates the total and posts the teacher's marks for every question", async () => {
    const fetch = respond();
    show(script);
    const input = screen.getByRole("spinbutton", { name: `${firstQuestion.question_id} of ${firstQuestion.max_marks}` });
    await userEvent.clear(input);
    await userEvent.type(input, String(firstQuestion.max_marks));
    const expected = questions.map((q) => ({ question_id: q.question_id, awarded: q === firstQuestion ? q.max_marks : marks.find((m) => m.question_id === q.question_id)?.awarded ?? 0 }));
    const total = expected.reduce((s, m) => s + m.awarded, 0);
    await userEvent.click(screen.getByRole("button", { name: `Record these marks (${total} of ${finished.totalMarks})` }));
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    expect(posted(fetch).body).toEqual({ decision: "set_marks", marks: expected });
  });

  it("starts at zero when no attempt returned marks and identifies moderation", () => {
    show({ ...script, stage: "moderation", attempts: [] });
    expect(screen.getByText(/stand in for the blind second marking/)).toBeInTheDocument();
    for (const input of screen.getAllByRole("spinbutton")) expect(input).toHaveValue(0);
    expect(screen.getByRole("button", { name: `Record these marks (0 of ${finished.totalMarks})` })).toBeEnabled();
    expect(screen.queryByRole("button", { name: /Use attempt/ })).not.toBeInTheDocument();
  });

  it.each([202, 409])("notifies the queue of send and result on HTTP %s", async (status) => {
    respond(status, status === 202 ? { state: "marking" } : { message: "Already answered" });
    const onSend = vi.fn();
    const onResult = vi.fn();
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    render(<QueryClientProvider client={client}><ScriptDecisionForm id={id} q={script} questions={questions} onSend={onSend} onResult={onResult} /></QueryClientProvider>);
    await userEvent.click(screen.getByRole("button", { name: "Use attempt 2" }));
    await waitFor(() => expect(onResult).toHaveBeenCalledWith(status === 202 ? { ok: true } : { ok: false, message: "Already answered" }));
    expect(onSend).toHaveBeenCalledTimes(1);
    if (status === 409) expect(screen.getByRole("alert")).toHaveTextContent("Already answered");
  });
});

describe("the budget decision", () => {
  it("shows spent and estimated costs and suggests a higher limit", () => {
    show(budget);
    expect(screen.getByRole("heading", { name: "Raise the spending limit to finish the last 1 scripts?" })).toBeInTheDocument();
    expect(screen.getByText("Spent so far")).toBeInTheDocument();
    expect(screen.getByText("Still to do")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Raise the limit to R" })).toHaveValue("25.00");
  });

  it("posts the limit as entered", async () => {
    const fetch = respond();
    show(budget);
    const input = screen.getByRole("textbox", { name: "Raise the limit to R" });
    await userEvent.clear(input);
    await userEvent.type(input, "30.50");
    await userEvent.click(screen.getByRole("button", { name: "Raise it and continue" }));
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    expect(posted(fetch)).toEqual({ url: `/api/assessments/${id}/budget`, method: "POST", body: { raise_to_zar: "30.50" } });
  });

  it("can stop locally and change its mind without sending a request", async () => {
    const fetch = respond();
    show({ ...budget, scripts_total: budget.scripts_settled });
    expect(screen.getByRole("heading", { name: "Raise the spending limit to go on?" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Leave it; stop here" }));
    expect(screen.getByText(/Stopped. Everything marked so far is kept/)).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Change my mind" }));
    expect(screen.getByRole("button", { name: "Raise it and continue" })).toBeEnabled();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("shows a budget refusal", async () => {
    respond(422, { message: "The new limit must exceed spending" });
    show(budget);
    await userEvent.click(screen.getByRole("button", { name: "Raise it and continue" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The new limit must exceed spending");
  });
});

describe("the reading check", () => {
  it("uses the event's readings while fresh crop links are loading", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
    show(reading);
    expect(screen.getByRole("textbox", { name: new RegExp(`${script.script_id} · question ${firstQuestion.question_id} · line 1`) })).toHaveValue(required(reading.items[0], "Missing reading.items[0]").line);
    expect(screen.getByRole("img", { name: `${script.script_id}, question ${firstQuestion.question_id}, as photographed` })).toHaveAttribute("src", "/api/crops/line.png");
    expect(screen.getByText("This page was not uploaded.")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: /page missing/ })).toHaveValue("");
  });

  it("refreshes the crop links, confirms untouched lines and corrects unreadable boxes", async () => {
    const fetch = respond(200, { items: reading.items.map((i) => ({ ...i, crop_url: i.crop_url ? "/crops/fresh.png" : null })) });
    show(reading);
    await waitFor(() => expect(screen.getByRole("img")).toHaveAttribute("src", "/api/crops/fresh.png"));
    expect(required(fetch.mock.calls[0], "Missing fetch.mock.calls[0]")[0]).toBe(`/api/assessments/${id}/reading`);
    await userEvent.click(screen.getByRole("button", { name: "Confirm these readings and mark" }));
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    expect(posted(fetch)).toEqual({ url: `/api/assessments/${id}/reading`, method: "POST", body: { answers: reading.items.map((i) => ({ kind: "reading", script_id: i.script_id, question_id: i.question_id, line_no: i.line_no, action: i.line_no === null ? "correct" : "confirm", ...(i.line_no === null ? { text: "" } : {}) })) } });
    await waitFor(() => expect(screen.getByRole("button", { name: "Confirm these readings and mark" })).toBeDisabled());
  });

  it("saves edited lines and boxes, including line breaks", async () => {
    const fetch = respond(200, { items: reading.items });
    show(reading);
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    const inputs = screen.getAllByRole("textbox");
    await userEvent.clear(required(inputs[0], "Missing inputs[0]"));
    await userEvent.type(required(inputs[0], "Missing inputs[0]"), memo.proposed_answer);
    await userEvent.type(required(inputs[1], "Missing inputs[1]"), "First line{Enter}Second line");
    await userEvent.click(screen.getByRole("button", { name: "Save 2 corrections and mark" }));
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    expect(posted(fetch).body).toEqual({ answers: reading.items.map((i, n) => ({ kind: "reading", script_id: i.script_id, question_id: i.question_id, line_no: i.line_no, action: "correct", text: n === 0 ? memo.proposed_answer : "First line\nSecond line" })) });
  });

  it("retains event readings if refresh fails and shows submission failures", async () => {
    respond(409, { message: "Readings already confirmed" });
    show(reading);
    await userEvent.click(screen.getByRole("button", { name: "Confirm these readings and mark" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Readings already confirmed");
    expect(screen.getAllByRole("textbox")).toHaveLength(reading.items.length);
    expect(screen.getByRole("button", { name: "Confirm these readings and mark" })).toBeEnabled();
  });

  it("handles an empty list of flagged readings", async () => {
    respond(200, { items: [] });
    show({ ...reading, items: [] });
    expect(screen.getByRole("heading", { name: reading.title })).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm these readings and mark" })).toBeEnabled();
    await waitFor(() => expect(fetch).toHaveBeenCalled());
  });
});

describe("pending teacher requests", () => {
  it.each([
    { topic: "script", question: script, button: "Use attempt 2" },
    { topic: "budget", question: budget, button: "Raise it and continue" },
    { topic: "reading", question: reading, button: "Confirm these readings and mark" },
  ])("prevents duplicate $topic submissions", async ({ question, button }) => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
    show(question);
    const submit = screen.getByRole("button", { name: button });
    await userEvent.click(submit);
    expect(submit).toBeDisabled();
    if (question.topic === "script") expect(screen.getByRole("button", { name: /Record these marks/ })).toBeDisabled();
  });

  it("uses a readable fallback for an unexpected response", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("not JSON", { status: 200 })));
    show(memo);
    await userEvent.click(screen.getByRole("button", { name: `Keep the memo's ${memo.memo_answer}` }));
    expect(await screen.findByRole("alert")).toHaveTextContent("That did not go through. Try again.");
  });
});

describe("reading corrections", () => {
  it("counts a single edit and treats a reverted line as confirmed", async () => {
    const fetch = respond(200, { items: [required(reading.items[0], "Missing reading.items[0]")] });
    show({ ...reading, items: [required(reading.items[0], "Missing reading.items[0]")] });
    const input = screen.getByRole("textbox", { name: /line 1/ });
    await userEvent.clear(input);
    await userEvent.type(input, memo.proposed_answer);
    expect(screen.getByRole("button", { name: "Save 1 correction and mark" })).toBeEnabled();
    await userEvent.clear(input);
    await userEvent.type(input, required(reading.items[0], "Missing reading.items[0]").line);
    await userEvent.click(screen.getByRole("button", { name: "Confirm these readings and mark" }));
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    expect(posted(fetch).body).toEqual({ answers: [{ kind: "reading", script_id: required(reading.items[0], "Missing reading.items[0]").script_id, question_id: required(reading.items[0], "Missing reading.items[0]").question_id, line_no: 0, action: "confirm" }] });
  });

  it("distinguishes an unreadable uploaded box from a missing page", async () => {
    const item = { ...required(reading.items[1], "Missing reading.items[1]"), page_missing: false, crop_url: "/crops/box.png" };
    respond(200, { items: [item] });
    show({ ...reading, items: [item] });
    expect(screen.getByRole("textbox", { name: /could not be read/ })).toHaveValue("");
    expect(screen.getByRole("img", { name: `${item.script_id}, question ${item.question_id}, as photographed` })).toHaveAttribute("src", "/api/crops/box.png");
    expect(screen.queryByText("This page was not uploaded.")).not.toBeInTheDocument();
    await waitFor(() => expect(fetch).toHaveBeenCalled());
  });
});
