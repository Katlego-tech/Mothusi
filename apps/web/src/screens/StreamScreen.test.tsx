import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { useEventStream } from "../api/events";
import type { MothusiEvent } from "../api/types";
import { apply, emptyRun } from "../run/reducer";
import plantedLog from "../run/__fixtures__/planted-events.jsonl?raw";
import { StreamScreen } from "./StreamScreen";

vi.mock("../api/events", () => ({ useEventStream: vi.fn() }));
vi.mock("../components/colleague", () => ({ WithColleague: ({ children }: { children: React.ReactNode }) => children }));
const events = plantedLog.trim().split("\n").map((line) => JSON.parse(line) as MothusiEvent);
const show = () => <MemoryRouter initialEntries={["/runs/test/stream"]}><Routes><Route path="/runs/:id/stream" element={<StreamScreen />} /></Routes></MemoryRouter>;

it("opens the existing run stream and keeps all ten agent lanes visible while connecting", () => {
  vi.mocked(useEventStream).mockReturnValue({ run: emptyRun("test"), connection: "connecting" });
  render(show());
  expect(useEventStream).toHaveBeenCalledWith("test");
  for (const agent of ["Planner", "Setter", "Marker", "Moderator", "Code", "Analyst", "Support", "Writer", "Reviewer", "Executor"])
    expect(screen.getByRole("region", { name: `${agent} output` })).toHaveTextContent("No output yet");
  expect(screen.getByRole("status")).toHaveTextContent("Connecting");
  expect(screen.getByRole("link", { name: "Current run" })).toHaveAttribute("href", "/runs/test");
  expect(screen.getByRole("link", { name: "Agent output" })).toHaveAttribute("aria-current", "page");
});

it("adds live results newest first and routes checks to Code with status and time", () => {
  const brief = events.find((e) => e.kind === "brief" && e.agent === "marker");
  const result = events.find((e) => e.kind === "result" && e.agent === "marker");
  const check = events.find((e) => e.kind === "check" && !e.payload.passed);
  if (!brief || !result || !check) throw new Error("Missing planted agent events");
  const fold = (items: MothusiEvent[]) => items.reduce(apply, emptyRun("test"));
  vi.mocked(useEventStream).mockReturnValue({ run: fold([brief]), connection: "live" });
  const { rerender } = render(show());
  const marker = screen.getByRole("region", { name: "Marker output" });
  if (brief.kind !== "brief") throw new Error("Expected a brief");
  expect(marker).toHaveTextContent(brief.payload.task);
  vi.mocked(useEventStream).mockReturnValue({ run: fold([brief, result, check]), connection: "reconnecting" });
  rerender(show());
  expect(within(marker).getAllByRole("listitem").map((e) => e.getAttribute("data-seq"))).toEqual([String(result.seq), String(brief.seq)]);
  expect(marker.querySelector("time")).toHaveAttribute("datetime", result.at);
  expect(marker).toHaveTextContent("result · done");
  expect(screen.getByRole("region", { name: "Code output" })).toHaveTextContent("check · failed");
  expect(screen.getByRole("status")).toHaveTextContent("Reconnecting");
});

it("shows a stopped run and closed connection without claiming agents are working", () => {
  vi.mocked(useEventStream).mockReturnValue({ run: { ...emptyRun("test"), halted: "Model unavailable" }, connection: "closed" });
  render(show());
  expect(screen.getByRole("alert")).toHaveTextContent("Model unavailable");
  expect(screen.getByRole("status")).toHaveTextContent("Closed");
});

it("keeps blocked results and passing checks visible, excludes conversation, and preserves intake attribution", () => {
  const result = events.find((e) => e.kind === "result");
  const check = events.find((e) => e.kind === "check");
  if (result?.kind !== "result" || check?.kind !== "check") throw new Error("Missing planted events");
  const blocked: MothusiEvent = { ...result, agent: "intake", payload: { ...result.payload, agent: "intake", status: "blocked", reason: "Page unreadable" } };
  const passed: MothusiEvent = { ...check, payload: { ...check.payload, passed: true } };
  const message: MothusiEvent = { ...result, kind: "message", agent: null, summary: "Teacher private request", payload: { message_id: "m-1", text: "Teacher private request" } };
  vi.mocked(useEventStream).mockReturnValue({ run: { ...emptyRun("test"), events: [blocked, passed, message] }, connection: "live" });
  render(show());
  const intake = screen.getByRole("region", { name: "Intake output" });
  expect(intake).toHaveTextContent("result · blocked");
  expect(intake).toHaveTextContent("Page unreadable");
  expect(screen.getByRole("region", { name: "Code output" })).toHaveTextContent("check · passed");
  expect(screen.queryByText("Teacher private request")).not.toBeInTheDocument();
});
