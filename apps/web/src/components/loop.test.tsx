// console-ui.md §14: the loop strip reads the live station and a correction from the stream, and
// the run screen mounts the class set only once marking has begun.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router";

import type { MothusiEvent } from "../api/types";
import { apply, emptyRun, type RunView } from "../run/reducer";
import plantedLog from "../run/__fixtures__/planted-events.jsonl?raw";
import { RunScreen, markingBegun } from "../screens/RunScreen";
import { correctionInFlight, corrections, liveStation } from "./loop";

const planted = plantedLog
  .trim()
  .split("\n")
  .map((l) => JSON.parse(l) as MothusiEvent);

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

describe("the loop strip", () => {
  it("puts the brief with the Setter during the memo check, and with the teacher when asked", () => {
    const sitting = foldUntil((e) => e.kind === "brief" && e.payload.agent === "setter");
    expect(liveStation(sitting)).toBe("setter");
    const asked = foldUntil((e) => e.kind === "needs_teacher");
    expect(liveStation(asked)).toBe("teacher");
  });

  it("draws the L-014 re-brief as a correction from Code to the Marker, and counts it", () => {
    const run = foldUntil((e) => e.kind === "brief" && e.payload.brief_id.endsWith("/L-014/marker/2"));
    expect(correctionInFlight(run.events)).toEqual({ from: "code", to: "marker", attempt: 2, scriptId: "L-014" });
    expect(corrections(run.events)).toBe(1);
    const { container } = wrap(<RunScreen run={run} connection="live" />);
    expect(screen.getByText(/sent back · L-014 · attempt 2 of 3/)).toBeInTheDocument();
    expect(container.querySelector(".packet.red")).not.toBeNull();
    expect(container.querySelector(".station.live.marker")).not.toBeNull();
  });

  it("clears the correction once the retry's result arrives", () => {
    const run = foldUntil((e) => e.kind === "result" && e.payload.brief_id.endsWith("/L-014/marker/2"));
    expect(correctionInFlight(run.events)).toBeNull();
    expect(corrections(run.events)).toBe(1);
  });
});

describe("intake (reading uploaded pages) works under the Planner's tile", () => {
  it("reports the Planner as live with intake's task, not a missing station", () => {
    const created = planted.find((e) => e.kind === "created");
    if (!created) throw new Error("the planted run starts with created");
    const brief: MothusiEvent = {
      seq: 1,
      at: created.at,
      assessment_id: created.assessment_id,
      kind: "brief",
      agent: "intake",
      summary: "intake briefed",
      payload: { brief_id: "x/intake/1", agent: "intake", task: "Read the uploaded pages", attempt: 1, script_id: null },
    };
    const run = [created, brief].reduce(apply, emptyRun("gr10-maths-t1-planted"));
    expect(liveStation(run)).toBe("planner");
    const { container } = wrap(<RunScreen run={run} connection="live" />);
    expect(container.querySelector(".station.live")?.textContent).toContain("Read the uploaded pages");
  });
});

describe("the run screen mounts what the state needs", () => {
  it("has no class set before marking, and the goal verbatim", () => {
    const run = foldUntil((e) => e.kind === "brief" && e.payload.agent === "setter");
    expect(markingBegun(run)).toBe(false);
    const { container } = wrap(<RunScreen run={run} connection="live" />);
    expect(container.querySelector(".set")).toBeNull();
    expect(screen.getByText(run.created?.goal ?? "")).toBeInTheDocument();
    expect(container.querySelector(".agentrun.no-set")).not.toBeNull();
  });

  it("opens the class set once marking has begun", async () => {
    const run = foldUntil((e) => e.kind === "brief" && e.payload.agent === "marker");
    expect(markingBegun(run)).toBe(true);
    wrap(<RunScreen run={run} connection="live" />);
    expect(await screen.findByText("The class set")).toBeInTheDocument();
  });

  it("shows the teacher's form in the now panel when a question is open", async () => {
    const run = foldUntil((e) => e.kind === "needs_teacher");
    const { container } = wrap(<RunScreen run={run} connection="live" />);
    expect(await screen.findByRole("button", { name: "Use x = -4 and mark" })).toBeInTheDocument();
    expect(container.querySelector(".now.ask.wide")).not.toBeNull(); // the form needs the row's width
    expect(container.querySelector(".agentrun.asking")).not.toBeNull(); // and the feed widens under it
  });
});
