import { render, screen, within } from "@testing-library/react";
import type { MothusiEvent } from "../api/types";
import { apply, emptyRun } from "../run/reducer";
import plantedLog from "../run/__fixtures__/planted-events.jsonl?raw";
import { AgentStrip, Ring, RunHeader, StateRail, TopBar } from "./chrome";
import { clock, rand, stateName } from "../lib/utils";

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


describe("the console chrome", () => {
  it.each([0, 1, 2, 3] as const)("shows the run identity and steps at step %s", (step) => {
    render(<TopBar title={finished.title} subtitle="Teacher's class" step={step} chip={{ text: "delivered", kind: "done" }} />);
    const header = screen.getByRole("banner");
    expect(header).toHaveTextContent(finished.title);
    expect(header).toHaveTextContent("Teacher's class");
    expect(within(header).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Set the task", "Live run", "Approval"]);
    expect(within(header).getByText("delivered")).toBeInTheDocument();
  });

  it("can omit the status chip", () => {
    render(<TopBar title="New assessment" subtitle="" step={0} />);
    expect(screen.getByRole("banner")).toHaveTextContent("New assessment");
    expect(screen.queryByText("delivered")).not.toBeInTheDocument();
  });

  it("keeps the progress decoration out of the accessible tree", () => {
    const { container } = render(<Ring share={0} />);
    expect(container.firstChild).toHaveAttribute("aria-hidden", "true");
  });

  it("shows the empty run without dividing by zero", () => {
    render(<RunHeader run={emptyRun(id)} />);
    expect(screen.getByRole("heading", { name: "0 of 0 scripts settled" })).toBeInTheDocument();
    expect(screen.getByText("Mothusi is working through the class set.")).toBeInTheDocument();
    expect(screen.queryByText(/NaN|Infinity/)).not.toBeInTheDocument();
  });

  it("shows settled scripts, finding, time and sourced cost", () => {
    render(<RunHeader run={finished} />);
    expect(screen.getByRole("heading", { name: `${finished.settled} of ${finished.cells.size} scripts settled` })).toBeInTheDocument();
    expect(screen.getByText((_, el) => el?.tagName === "P" && el.textContent === finished.finding)).toBeInTheDocument();
    expect(screen.getByText(clock(finished.elapsedS))).toBeInTheDocument();
    expect(screen.getAllByText(rand(finished.costZar)).length).toBeGreaterThan(0);
  });

  it("says when cost is not metered", () => {
    render(<RunHeader run={{ ...finished, priced: false }} />);
    expect(screen.getByText("not metered")).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("lists agents still waiting before any briefs", () => {
    render(<AgentStrip run={emptyRun(id)} />);
    expect(screen.getByLabelText("Who is doing what")).toHaveTextContent("Waiting");
    expect(screen.getByText(/Setter · Marker · Moderator/)).toBeInTheDocument();
  });

  it("shows a live agent's actual task and brief count", () => {
    const run = through((e) => e.kind === "brief");
    const agent = required([...run.agents.values()].find((a) => a.live), "Missing [...run.agents.values()].find((a) => a.live)");
    render(<AgentStrip run={run} />);
    expect(screen.getByLabelText("Who is doing what")).toHaveTextContent(agent.task);
    expect(screen.getByText(/1 brief so far/)).toBeInTheDocument();
  });

  it("shows completed agents and code's failed and open checks", () => {
    render(<AgentStrip run={finished} />);
    const strip = screen.getByLabelText("Who is doing what");
    expect(strip).toHaveTextContent(`${finished.checks.run} checks · ${finished.checks.failed} failed · ${finished.checks.open} open`);
    for (const agent of finished.agents.values()) {
      if (agent.role !== "code") expect(strip).toHaveTextContent(agent.lastSummary);
    }
    expect(within(strip).queryByText("Waiting")).not.toBeInTheDocument();
  });

  it("marks the current state accessibly and hides unvisited rejection", () => {
    render(<StateRail run={emptyRun(id)} />);
    expect(screen.getByRole("listitem", { current: "step" })).toHaveTextContent(stateName("draft"));
    expect(screen.queryByText(stateName("rejected"))).not.toBeInTheDocument();
  });

  it("shows a visited rejection and the final current state", () => {
    render(<StateRail run={finished} />);
    expect(screen.getByRole("listitem", { current: "step" })).toHaveTextContent(stateName(finished.state));
    if (finished.events.some((e) => e.kind === "state" && e.payload.to === "rejected")) expect(screen.getByText(stateName("rejected"))).toBeInTheDocument();
  });
});

describe("the state rail over a real run", () => {
  const transitions = events.filter((e) => e.kind === "state");
  it.each(transitions.map((event) => ({ event, state: event.kind === "state" ? event.payload.to : "draft" })))("identifies $state as the current step at event $event.seq", ({ event, state }) => {
    const run = through((e) => e.seq === event.seq);
    render(<StateRail run={run} />);
    expect(screen.getByRole("listitem", { current: "step" })).toHaveTextContent(stateName(state));
    if (state === "rejected") expect(screen.getByText(stateName("rejected"))).toBeInTheDocument();
  });
});
