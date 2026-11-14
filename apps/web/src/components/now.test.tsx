import { render, screen, within } from "@testing-library/react";
import type { MothusiEvent } from "../api/types";
import { apply, emptyRun } from "../run/reducer";
import plantedLog from "../run/__fixtures__/planted-events.jsonl?raw";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { NowPanel } from "./now";
import type { RunView } from "../run/reducer";

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


const show = (run: RunView) => render(<NowPanel run={run} questionKey={String(run.lastSeq)} />);

describe("who is working now", () => {
  it("starts with the Planner deciding what to do", () => {
    show(emptyRun(id));
    const panel = screen.getByRole("region", { name: "Who is working now" });
    expect(within(panel).getByRole("heading", { name: "Planner" })).toBeInTheDocument();
    expect(panel).toHaveTextContent("Deciding what to do next.");
  });

  it("shows the agent and task from its open brief", () => {
    const run = through((e) => e.kind === "brief" && e.payload.agent === "marker");
    const card = required(run.agents.get("marker"), "Missing run.agents.get(\"marker\")");
    show(run);
    expect(screen.getByRole("heading", { name: "Marker" })).toBeInTheDocument();
    expect(screen.getByText("Working now")).toBeInTheDocument();
    expect(screen.getByText(card.task)).toBeInTheDocument();
    expect(screen.getByText(/briefs? so far/)).toHaveTextContent(String(card.briefs));
  });

  it("identifies a retry sent back by Code", () => {
    const run = through((e) => e.kind === "brief" && e.payload.agent === "marker" && e.payload.attempt === 2);
    show(run);
    expect(screen.getByText(/sent back by Code/)).toHaveTextContent("attempt 2 of 3");
  });

  it("opens the teacher's real memo question and its named controls", async () => {
    const run = through((e) => e.kind === "needs_teacher");
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><NowPanel run={run} questionKey="memo" /></QueryClientProvider>);
    expect(screen.getByRole("region", { name: "Mothusi needs you" })).toBeInTheDocument();
    expect(screen.getByText("Opening your question…")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Use x = -4 and mark" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Keep the memo's x = -2" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Who is working now" })).not.toBeInTheDocument();
  });

  it("waits for a brief when a station has no agent card", () => {
    show({ ...emptyRun(id), state: "marking" });
    expect(screen.getByRole("heading", { name: "Marker" })).toBeInTheDocument();
    expect(screen.getByText("Waiting for a Brief.")).toBeInTheDocument();
  });

  it("waits for approval before the executor is briefed", () => {
    show({ ...emptyRun(id), state: "approved" });
    expect(screen.getByRole("heading", { name: "Executor" })).toBeInTheDocument();
    expect(screen.getByText("Waiting for your approval before anything is written.")).toBeInTheDocument();
  });

  it("reports a halt with its reason", () => {
    show({ ...finished, halted: "The bundle directory is not empty." });
    expect(screen.getByText("The run stopped: The bundle directory is not empty.")).toBeInTheDocument();
  });

  it("reports completed delivery", () => {
    show(finished);
    expect(screen.getByText("The bundle is written and the notes are with you.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Executor" })).toBeInTheDocument();
  });
});

describe("station updates", () => {
  it("shows the last result while the station waits for more work", () => {
    const run = through((e) => e.kind === "result" && e.payload.agent === "marker");
    show(run);
    expect(screen.getByRole("heading", { name: "Marker" })).toBeInTheDocument();
    expect(screen.getByText(required(run.agents.get("marker"), "Missing run.agents.get(\"marker\")").lastSummary)).toBeInTheDocument();
    expect(screen.queryByText("Working now")).not.toBeInTheDocument();
  });
});
