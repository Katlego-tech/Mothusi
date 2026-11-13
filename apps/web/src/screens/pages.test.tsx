// Pages mode in the console (console-ui.md §11): the class list, the answer-sheet screen and
// the reading check, whose answers post intake.md §11.6's ReadingAnswer bodies.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router";

import type { ReadingItem, ReadingQuestion } from "../api/types";
import { NeedsTeacherCard, readingAnswers } from "../components/ask";
import { emptyRun } from "../run/reducer";
import { PagesScreen } from "./PagesScreen";
import { classListFacts } from "./SetTaskScreen";

const ITEMS: ReadingItem[] = [
  { script_id: "L-001", question_id: "1.1", line_no: 1, line: "y = 2", crop_url: "/c/a/L-001/k1?exp=1&sig=s", page_missing: false },
  { script_id: "L-002", question_id: "3.2", line_no: null, line: "", crop_url: null, page_missing: true },
];
const QUESTION: ReadingQuestion = { topic: "reading", title: "2 readings to check before marking", items: ITEMS };

function wrap(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{node}</MemoryRouter>
    </QueryClientProvider>,
  );
}

type Call = { url: string; method: string; body: unknown };

function mockFetch(routes: Record<string, unknown>): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const body = typeof init?.body === "string" ? (JSON.parse(init.body) as unknown) : null;
      calls.push({ url, method: init?.method ?? "GET", body });
      const key = Object.keys(routes).find((k) => url.endsWith(k));
      const status = init?.method === "PUT" ? 204 : init?.method === "POST" ? 202 : 200;
      return new Response(status === 204 ? null : JSON.stringify(key ? routes[key] : { state: "x" }), { status });
    }),
  );
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

describe("the reading check", () => {
  it("confirms untouched lines, corrects edited ones, and types unreadable boxes", () => {
    const answers = readingAnswers(ITEMS, { "L-002|3.2|box": "x = 4" });
    expect(answers).toEqual([
      { kind: "reading", script_id: "L-001", question_id: "1.1", line_no: 1, action: "confirm" },
      { kind: "reading", script_id: "L-002", question_id: "3.2", line_no: null, action: "correct", text: "x = 4" },
    ]);
    expect(readingAnswers(ITEMS, { "L-001|1.1|1": "y = 7" })[0]).toMatchObject({ action: "correct", text: "y = 7" });
  });

  it("shows each crop beside its reading, says when a page is missing, and posts the answers", async () => {
    const calls = mockFetch({ "/reading": { items: ITEMS } });
    wrap(<NeedsTeacherCard assessmentId="a1" question={QUESTION} questions={[]} />);
    expect(screen.getByRole("img", { name: /L-001, question 1.1/ })).toHaveAttribute("src", "/api/c/a/L-001/k1?exp=1&sig=s");
    expect(screen.getByText("This page was not uploaded.")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/L-002 · question 3.2 · page missing/), "x = 4");
    await userEvent.click(screen.getByRole("button", { name: "Save 1 correction and mark" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    const post = calls.find((c) => c.method === "POST");
    expect(post?.url).toBe("/api/assessments/a1/reading");
    expect((post?.body as { answers: unknown[] }).answers).toHaveLength(2);
  });
});

describe("pages mode", () => {
  it("reads a class list as learners, and refuses any other shape", () => {
    expect(classListFacts("learner\nZanele M\nAarav P\n")).toMatch(/^2 learners/);
    expect(classListFacts("learner,question_id,answer\nA,1,x\n")).toBeNull();
  });

  it("uploads the chosen pages and only then lets the teacher start reading", async () => {
    const calls = mockFetch({
      "/uploads": { uploads: [{ name: "p1.png", method: "PUT", url: "/assessments/a1/uploads/p1.png", headers: { "Content-Type": "image/png" } }] },
    });
    const run = { ...emptyRun("a1"), title: "Grade 10 Mathematics · Term 1 Test" };
    wrap(<PagesScreen run={run} />);
    const start = screen.getByRole("button", { name: "Upload the pages first" });
    expect(start).toBeDisabled();
    const page = new File([new Uint8Array([137, 80, 78, 71])], "p1.png", { type: "image/png" });
    await userEvent.upload(screen.getByLabelText("Choose photos or PDFs"), page);
    await waitFor(() => expect(screen.getByText("uploaded")).toBeInTheDocument());
    expect(calls.some((c) => c.method === "PUT" && c.url === "/api/assessments/a1/uploads/p1.png")).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: "Read 1 file and start" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/pages/ready"))).toBe(true));
    expect(calls.find((c) => c.url.endsWith("/pages/ready"))?.body).toEqual({ accept_gaps: false });
  });

  it("names refused and missing pages before reading, then starts without them on the teacher's word", async () => {
    const calls: Call[] = [];
    const gaps = {
      message: "some pages were refused or have not arrived",
      refused: ["blurry.png: retake the photo: all four corner squares must be visible"],
      missing: [{ script_id: "L-002", pages: [1, 2] }],
      can_accept: true,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        const body = typeof init?.body === "string" ? (JSON.parse(init.body) as unknown) : null;
        calls.push({ url, method: init?.method ?? "GET", body });
        if (url.endsWith("/uploads"))
          return Response.json({
            uploads: ["good.png", "blurry.png"].map((n) => ({ name: n, method: "PUT", url: `/assessments/a1/uploads/${n}`, headers: {} })),
          });
        if (init?.method === "PUT" || init?.method === "DELETE") return new Response(null, { status: 204 });
        if (url.endsWith("/pages/ready"))
          return (body as { accept_gaps: boolean }).accept_gaps
            ? Response.json({ state: "draft" }, { status: 202 })
            : Response.json(gaps, { status: 409 });
        return Response.json({});
      }),
    );
    wrap(<PagesScreen run={emptyRun("a1")} />);
    const files = ["good.png", "blurry.png"].map((n) => new File([new Uint8Array([1])], n, { type: "image/png" }));
    await userEvent.upload(screen.getByLabelText("Choose photos or PDFs"), files);
    await userEvent.click(await screen.findByRole("button", { name: "Read 2 files and start" }));

    const panel = await screen.findByRole("alert");
    expect(panel).toHaveTextContent("Some pages are not ready");
    expect(panel).toHaveTextContent("L-002 · pages 1 and 2");
    expect(screen.getByText("retake the photo: all four corner squares must be visible")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Check the pages again" })).toBeEnabled();

    // Removing the refused photo clears the old check; the readable file can be checked again.
    await userEvent.click(screen.getByRole("button", { name: "Remove blurry.png" }));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(calls.some((c) => c.method === "DELETE" && c.url.endsWith("/uploads/blurry.png"))).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: "Read 1 file and start" }));
    await screen.findByRole("alert");
    await userEvent.click(screen.getByRole("button", { name: "Read without them" }));
    await waitFor(() => expect(calls.filter((c) => c.url.endsWith("/pages/ready"))).toHaveLength(3));
    expect(calls.filter((c) => c.url.endsWith("/pages/ready")).map((c) => c.body)).toEqual([
      { accept_gaps: false },
      { accept_gaps: false },
      { accept_gaps: true },
    ]);
  });

  it("removes an uploaded file on the service, not only from the list", async () => {
    const calls = mockFetch({
      "/uploads": { uploads: [{ name: "p1.png", method: "PUT", url: "/assessments/a1/uploads/p1.png", headers: {} }] },
    });
    wrap(<PagesScreen run={emptyRun("a1")} />);
    await userEvent.upload(screen.getByLabelText("Choose photos or PDFs"), new File([new Uint8Array([1])], "p1.png", { type: "image/png" }));
    await userEvent.click(await screen.findByRole("button", { name: "Remove p1.png" }));
    await waitFor(() => expect(screen.queryByText("p1.png")).not.toBeInTheDocument());
    expect(calls.some((c) => c.method === "DELETE" && c.url === "/api/assessments/a1/uploads/p1.png")).toBe(true);
    expect(screen.getByRole("button", { name: "Upload the pages first" })).toBeDisabled();
  });

  it("keeps the line breaks of a box typed in full", async () => {
    const calls = mockFetch({});
    wrap(<NeedsTeacherCard assessmentId="a1" question={{ ...QUESTION, items: ITEMS.slice(1) }} questions={[]} />);
    await userEvent.type(screen.getByLabelText(/L-002 · question 3.2/), "x + 1 = 4{enter}x = 3");
    await userEvent.click(screen.getByRole("button", { name: "Save 1 correction and mark" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    const answers = (calls.find((c) => c.method === "POST")?.body as { answers: { text: string }[] }).answers;
    expect(answers[0]?.text).toBe("x + 1 = 4\nx = 3");
  });
});
