import { render, screen } from "@testing-library/react";
import type { MothusiEvent } from "../api/types";
import { apply, emptyRun } from "../run/reducer";
import plantedLog from "../run/__fixtures__/planted-events.jsonl?raw";
import userEvent from "@testing-library/user-event";
import { fireEvent } from "@testing-library/react";
import { EventLog } from "./log";
import { clock, rand, stateName } from "../lib/utils";

function required<T>(value: T | null | undefined, description: string): T {
  if (value === null || value === undefined) throw new Error(description);
  return value;
}

const events = plantedLog.trim().split("\n").map((line) => JSON.parse(line) as MothusiEvent);
const id = "gr10-maths-t1-planted";
const finished = events.reduce(apply, emptyRun(id));


const show = (log: MothusiEvent[], priced = true) => render(<EventLog events={log} start={finished.startedAt} priced={priced} />);

describe("the event log", () => {
  it("shows an accessible empty log and its supplied footer", async () => {
    render(<EventLog events={[]} start={null} priced={false} title="Run history" note="No events yet" footer={<p>Waiting for events</p>} />);
    expect(screen.getByRole("main")).toHaveAttribute("aria-live", "polite");
    expect(screen.getByRole("heading", { name: "Run history" })).toBeInTheDocument();
    expect(screen.getByText("No events yet")).toBeInTheDocument();
    expect(screen.getByText("Waiting for events")).toBeInTheDocument();
    await userEvent.tab();
    expect(screen.getByLabelText("The log, newest at the bottom")).toHaveFocus();
  });

  it("shows the goal, state reasons, decisions, teacher answers and delivery", () => {
    show(finished.events);
    const created = required(finished.created, "Missing finished.created");
    expect(screen.getByText(`Started: ${created.title}`)).toBeInTheDocument();
    expect(screen.getByText(`"${created.goal}"`)).toBeInTheDocument();
    for (const event of finished.events) {
      if (event.kind === "state") expect(screen.getAllByText(`${event.payload.reason}; ${stateName(event.payload.to)}`).length).toBeGreaterThan(0);
      if (event.kind === "teacher") expect(screen.getByText(event.summary)).toBeInTheDocument();
    }
    expect(screen.getByText(`Wrote ${required(finished.delivered, "Missing finished.delivered").written.length} files and sent 0 messages`)).toBeInTheDocument();
    expect(screen.getAllByText("You")).toHaveLength(finished.teacherSteps);
  });

  it("shows an unanswered brief, then replaces it with its marked result and check tags", () => {
    const result = events.find((e) => e.kind === "result" && e.payload.agent === "marker" && e.payload.status === "done");
    if (result?.kind !== "result") throw new Error("Missing marker result");
    const brief = events.find((e) => e.kind === "brief" && e.payload.brief_id === result.payload.brief_id);
    if (brief?.kind !== "brief") throw new Error("Missing marker brief");
    const after = events.slice(events.indexOf(result) + 1);
    const checks = after.slice(0, after.findIndex((e) => e.kind !== "check"));
    const { rerender } = show([brief]);
    expect(screen.getByText(brief.payload.task)).toBeInTheDocument();
    expect(screen.getByText(/brief .* · attempt/)).toBeInTheDocument();
    rerender(<EventLog events={[brief, result, ...checks]} start={finished.startedAt} priced={true} />);
    expect(screen.queryByText(brief.payload.task)).not.toBeInTheDocument();
    expect(screen.getByText(`${result.payload.script_id} marked`)).toHaveTextContent(`${result.payload.awarded}`);
    for (const check of checks) if (check.kind === "check") expect(screen.getAllByText(check.payload.check.replace("_", " ")).length).toBeGreaterThan(0);
  });

  it("keeps failed checks visible with their explanation", () => {
    const failed = events.find((e) => e.kind === "check" && !e.payload.passed);
    if (failed?.kind !== "check") throw new Error("Missing failed check");
    show([failed]);
    expect(screen.getByText("FAIL")).toBeInTheDocument();
    expect(screen.getByText(failed.payload.detail)).toBeInTheDocument();
    expect(screen.getByText("Code")).toBeInTheDocument();
  });

  it("groups adjacent passing checks of the same kind", () => {
    const checks = events.filter((e) => e.kind === "check" && e.payload.passed);
    const first = required(checks[0], "Missing checks[0]");
    if (first.kind !== "check") throw new Error("Missing checks");
    const same = checks.filter((e) => e.kind === "check" && e.payload.check === first.payload.check).slice(0, 3);
    show(same);
    expect(screen.getAllByText("PASS")).toHaveLength(1);
    expect(screen.getByText(new RegExp(`check on ${same.length}`))).toBeInTheDocument();
  });

  it("shows unsuccessful result reasons, and leaves teacher questions to the question card", () => {
    const result = events.find((e) => e.kind === "result");
    if (result?.kind !== "result") throw new Error("Missing result");
    // The planted run retries failed checks; this separate response exercises a failed agent call.
    const failed: MothusiEvent = { ...result, payload: { ...result.payload, status: "blocked", reason: "The model did not return a result" } };
    const question = required(events.find((e) => e.kind === "needs_teacher"), "Missing events.find((e) => e.kind === \"needs_teacher\")");
    if (failed?.kind !== "result") throw new Error("Missing failed result");
    show([failed, question]);
    expect(screen.getByText(failed.payload.summary)).toBeInTheDocument();
    expect(screen.getByText(failed.payload.reason)).toBeInTheDocument();
    expect(screen.queryByText(question.summary)).not.toBeInTheDocument();
  });

  it("shows blind moderation and disagreement evidence with elapsed times", () => {
    const result = events.find((e) => e.kind === "result" && e.payload.agent === "moderator");
    const disagreement = required(events.find((e) => e.kind === "disagreement"), "Missing events.find((e) => e.kind === \"disagreement\")");
    if (result?.kind !== "result") throw new Error("Missing moderation result");
    show([result, disagreement]);
    expect(screen.getByText(`${result.payload.script_id} re-marked blind`)).toBeInTheDocument();
    expect(screen.getAllByRole("figure")).toHaveLength(2);
    const elapsed = (Date.parse(result.at) - Date.parse(required(finished.startedAt, "Missing finished.startedAt"))) / 1000;
    expect(screen.getByText(clock(elapsed))).toHaveAttribute("dateTime", result.at);
  });

  it("shows a result's cost only when prices are sourced", () => {
    const result = events.find((e) => e.kind === "result");
    if (result?.kind !== "result") throw new Error("Missing result");
    const priced = { ...result, payload: { ...result.payload, cost_zar: "1.25" } };
    const { rerender } = show([priced]);
    expect(screen.getByText(rand("1.25"))).toBeInTheDocument();
    rerender(<EventLog events={[priced]} start={null} priced={false} />);
    expect(screen.queryByText(rand("1.25"))).not.toBeInTheDocument();
    expect(screen.getByText(clock(0))).toBeInTheDocument();
  });

  it("follows new events until the teacher scrolls up, and resumes at the bottom", () => {
    const { rerender } = show(events.slice(0, 1));
    const log = screen.getByLabelText("The log, newest at the bottom");
    Object.defineProperties(log, { scrollHeight: { configurable: true, value: 1000 }, clientHeight: { configurable: true, value: 200 } });
    rerender(<EventLog events={events.slice(0, 2)} start={null} priced={false} />);
    expect(log.scrollTop).toBe(1000);
    log.scrollTop = 100;
    fireEvent.scroll(log);
    rerender(<EventLog events={events.slice(0, 3)} start={null} priced={false} />);
    expect(log.scrollTop).toBe(100);
    log.scrollTop = 800;
    fireEvent.scroll(log);
    rerender(<EventLog events={events.slice(0, 4)} start={null} priced={false} />);
    expect(log.scrollTop).toBe(1000);
  });
});
