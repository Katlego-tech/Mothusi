// A memo drafted from a typed PDF or Word file, checked and edited before use (intake.md §12).
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router";

import type { DraftItem, MemoDraftView } from "../api/types";
import { SetTaskScreen } from "../screens/SetTaskScreen";
import { MemoDraftPanel, rowProblems } from "./memo-draft";

const LEVELS = ["knowledge", "routine_procedures"];
const row = (over: Partial<DraftItem>): DraftItem => ({
  question_id: "1.1",
  question: "",
  max_marks: 5,
  answer: "x = 3",
  marking_notes: "",
  level: "knowledge",
  ...over,
});

function wrap(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{node}</MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("the Memo contract's checks, in the console", () => {
  it("names every row the teacher still has to fix, and the total", () => {
    expect(rowProblems([row({})], 5, LEVELS)).toEqual([]);
    expect(
      rowProblems([row({}), row({ max_marks: 0, answer: " ", level: "hard" }), row({ question_id: "" })], 9, LEVELS),
    ).toEqual([
      "1.1 appears twice; keep one row",
      "1.1: give its marks",
      "1.1: type the memo's answer",
      "1.1: choose its level",
      "row 3: give the question number",
      "the rows add up to 10, not 9",
    ]);
    expect(rowProblems([], null, LEVELS)).toEqual(["the memo needs at least one row", "give the paper's total"]);
    // Memo's marks are integers: 2.5 + 2.5 = 5 must not pass, or POST /assessments refuses it.
    expect(rowProblems([row({ max_marks: 2.5 }), row({ question_id: "1.2", max_marks: 2.5 })], 5, LEVELS)).toEqual([
      "1.1: marks must be a whole number",
      "1.2: marks must be a whole number",
    ]);
    expect(rowProblems([row({})], 5.5, LEVELS)).toContain("the paper's total must be a whole number");
  });
});

describe("the drafted memo", () => {
  const ready: MemoDraftView = {
    draft_id: "d1",
    state: "ready",
    source_name: "Term 1 memo.pdf",
    total_marks: 8,
    items: [row({}), row({ question_id: "1.2", max_marks: 3, answer: "x^2 + 2x + 1", level: "" })],
    problems: ["1.2: the memo gives no level; choose one"],
    levels: LEVELS,
    message: "",
  };

  it("polls while drafting, then lets the teacher fix it and use it as memo.json", async () => {
    const replies: MemoDraftView[] = [{ ...ready, state: "drafting", items: [] }, ready];
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json(replies.length > 1 ? replies.shift() : replies[0])),
    );
    const onUse = vi.fn();
    wrap(<MemoDraftPanel draftId="d1" onUse={onUse} onDiscard={() => undefined} />);
    expect(await screen.findByRole("status")).toHaveTextContent("copying");
    const use = await screen.findByRole("button", { name: "Use this memo" }, { timeout: 4000 });
    expect(use).toBeDisabled();
    expect(screen.getByRole("list", { name: "Still to fix" })).toHaveTextContent("1.2: choose its level");
    await userEvent.selectOptions(screen.getByLabelText("Row 2 level"), "routine_procedures");
    expect(use).toBeEnabled();
    await userEvent.click(use);
    const [file, facts] = onUse.mock.calls[0] as [File, string];
    expect(file.name).toBe("memo.json");
    const memo = JSON.parse(await file.text()) as { assessment_id: string; total_marks: number; items: DraftItem[] };
    expect(memo.assessment_id).toBe("term-1-memo");
    expect(memo.total_marks).toBe(8);
    expect(memo.items.map((i) => i.level)).toEqual(["knowledge", "routine_procedures"]);
    expect(facts).toBe("2 items · 8 marks, checked by you from Term 1 memo.pdf");
  });

  it("keeps the total honest when a row's marks change", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ ...ready, items: [row({}), row({ question_id: "1.2", max_marks: 3 })] })));
    wrap(<MemoDraftPanel draftId="d1" onUse={() => undefined} onDiscard={() => undefined} />);
    const marks = await screen.findByLabelText("Row 2 marks");
    await userEvent.clear(marks);
    await userEvent.type(marks, "4");
    expect(screen.getByRole("list", { name: "Still to fix" })).toHaveTextContent("the rows add up to 9, not 8");
    expect(screen.getByRole("button", { name: "Use this memo" })).toBeDisabled();
  });

  it("says why a draft failed and offers another memo", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ ...ready, state: "failed", message: "the memo could not be read" })));
    const onDiscard = vi.fn();
    wrap(<MemoDraftPanel draftId="d1" onUse={() => undefined} onDiscard={onDiscard} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Term 1 memo.pdf: the memo could not be read");
    await userEvent.click(screen.getByRole("button", { name: "Choose another memo" }));
    expect(onDiscard).toHaveBeenCalled();
  });
});

it("sends a PDF memo to be drafted for the chosen pack", async () => {
  const posts: { url: string; body: FormData }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/packs"))
        return Response.json([
          {
            pack_id: "dbe-caps/mathematics-gr10",
            subject: "Mathematics",
            grade: 10,
            moderation: { sample_percent: "10", min_scripts: 5, spread: "x", widen_above_gap_percent: "5" },
            tasks: [{ name: "Test", term: 1, max_marks: 50, weight_percent: "14" }],
          },
        ]);
      if (url.endsWith("/memo-drafts") && init?.body instanceof FormData) {
        posts.push({ url, body: init.body });
        return Response.json({ draft_id: "d9", state: "drafting" }, { status: 202 });
      }
      return Response.json({ draft_id: "d9", state: "drafting", source_name: "memo.pdf", items: [], levels: [], problems: [], total_marks: null, message: "" });
    }),
  );
  wrap(<SetTaskScreen />);
  await screen.findByRole("option", { name: /Test/ }, { timeout: 4000 }).catch(() => undefined);
  const pdf = new File([new Uint8Array([37, 80, 68, 70])], "memo.pdf", { type: "application/pdf" });
  await userEvent.upload(screen.getByLabelText("Memo"), pdf);
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0]?.body.get("pack_id")).toBe("dbe-caps/mathematics-gr10");
  expect((posts[0]?.body.get("memo") as File).name).toBe("memo.pdf");
  expect(await screen.findByRole("status")).toHaveTextContent("copying");
});

it("ignores a draft that answers after the teacher chose another memo", async () => {
  let release: (r: Response) => void = () => undefined;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.endsWith("/packs"))
        return Response.json([
          {
            pack_id: "dbe-caps/mathematics-gr10",
            subject: "Mathematics",
            grade: 10,
            moderation: { sample_percent: "10", min_scripts: 5, spread: "x", widen_above_gap_percent: "5" },
            tasks: [{ name: "Test", term: 1, max_marks: 50, weight_percent: "14" }],
          },
        ]);
      if (url.endsWith("/memo-drafts")) return new Promise<Response>((r) => (release = r)); // a slow upload
      return Response.json({});
    }),
  );
  wrap(<SetTaskScreen />);
  await screen.findByRole("option", { name: /Test/ }, { timeout: 4000 }).catch(() => undefined);
  await userEvent.upload(screen.getByLabelText("Memo"), new File([new Uint8Array([37])], "slow.pdf", { type: "application/pdf" }));
  const toml = new File(['total_marks = 5\n[[item]]\nquestion_id = "1"\n'], "memo.toml", { type: "text/plain" });
  await userEvent.upload(screen.getByLabelText("Memo"), toml);
  release(Response.json({ draft_id: "stale", state: "drafting" }, { status: 202 }));
  await new Promise((r) => setTimeout(r, 50));
  expect(screen.queryByLabelText("Memo draft")).not.toBeInTheDocument();
  expect(screen.getByText(/memo\.toml/)).toBeInTheDocument();
});
