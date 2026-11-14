import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { api } from "../api/client";
import { emptyRun } from "../run/reducer";
import { VoiceControl } from "./voice";
import { composeOpening, disclaimerGiven, markDisclaimerGiven, voiceContext } from "./voice-context";

const sdk = vi.hoisted(() => ({
  status: "disconnected",
  startSession: vi.fn(),
  endSession: vi.fn(),
  sendContextualUpdate: vi.fn(),
}));
vi.mock("@elevenlabs/react", () => ({
  ConversationProvider: ({ children }: { children: React.ReactNode }) => children,
  useConversation: () => sdk,
}));
vi.mock("../auth/session", () => ({ teacherId: () => "teacher-a" }));

beforeEach(() => {
  localStorage.clear();
  sdk.status = "disconnected";
  sdk.startSession.mockReset().mockResolvedValue("session-id");
  sdk.sendContextualUpdate.mockReset();
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.spyOn(api, "voiceSession").mockResolvedValue({ signed_url: "wss://offline.test" });
});
afterEach(() => vi.restoreAllMocks());

describe("grounded opening", () => {
  it("introduces the names limitation once, beside the page context", () => {
    const context = voiceContext("/");
    expect(composeOpening(context, false)).toMatch(/can't remove names/);
    expect(composeOpening(context, false)).toContain("desk");
    expect(composeOpening(context, true)).not.toMatch(/names|L-014/);
    expect(composeOpening(voiceContext("/new"), true)).toContain("new assessment");
  });
  it("opens with the run title, current state and work waiting for the teacher", () => {
    const run = { ...emptyRun("a1"), title: "Term 1 test", state: "awaiting_approval" as const };
    expect(composeOpening(voiceContext("/runs/a1", run), true)).toContain("Term 1 test");
    expect(composeOpening(voiceContext("/runs/a1", run), true)).toMatch(/ready for your approval/i);
    expect(composeOpening(voiceContext("/runs/a1", { ...run, state: "marking" }), true)).toMatch(/marking/i);
    expect(composeOpening({ ...voiceContext("/runs/a1", run), waiting: 2 }, true)).toContain("2");
    expect(composeOpening(voiceContext("/runs/a1", { ...run, halted: "failure" }), true)).toMatch(/stopped/i);
  });
});

describe("per-teacher names disclaimer", () => {
  it("keeps teachers separate and does not store an unknown identity", () => {
    expect(disclaimerGiven("teacher-a")).toBe(false);
    markDisclaimerGiven("teacher-a");
    expect(disclaimerGiven("teacher-a")).toBe(true);
    expect(disclaimerGiven("teacher-b")).toBe(false);
    markDisclaimerGiven(null);
    expect(disclaimerGiven(null)).toBe(false);
  });
  it("works when storage throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("private"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("private"); });
    expect(disclaimerGiven("teacher-a")).toBe(false);
    expect(() => markDisclaimerGiven("teacher-a")).not.toThrow();
  });
});

describe("voice session context", () => {
  it("opens later sessions with current work and skips the names reminder", async () => {
    markDisclaimerGiven("teacher-a");
    const run = { ...emptyRun("a1"), title: "Term 1 test", state: "awaiting_approval" as const };
    render(<VoiceControl ask={async () => "reply"} context={voiceContext("/runs/a1", run, "Marking is underway.")} />);
    fireEvent.click(screen.getByRole("button"));
    await waitFor(() => expect(sdk.startSession).toHaveBeenCalled());
    const options = sdk.startSession.mock.calls[0]?.[0];
    expect(options.overrides.agent.firstMessage).toContain("ready for your approval");
    expect(options.overrides.agent.firstMessage).not.toContain("names");
    expect(options.dynamicVariables).toMatchObject({
      disclaimer_given: true,
      waiting_for_teacher: 1,
      teacher_task: "review and approve the set",
      previous_reply: "Marking is underway.",
    });
    expect(window.confirm).not.toHaveBeenCalled();
  });

  it("starts with overrides and dynamic context, then remembers the disclaimer", async () => {
    const context = voiceContext("/new");
    render(<VoiceControl ask={async () => "reply"} context={context} />);
    fireEvent.click(screen.getByRole("button"));
    await waitFor(() => expect(disclaimerGiven("teacher-a")).toBe(true));
    expect(sdk.startSession).toHaveBeenCalledWith(expect.objectContaining({
      signedUrl: "wss://offline.test",
      overrides: { agent: { firstMessage: composeOpening(context, false) } },
      dynamicVariables: expect.objectContaining({ page: "/new", disclaimer_given: false }),
    }));
  });
  it("does not mark a failed or cancelled session as having given the disclaimer", async () => {
    sdk.startSession.mockRejectedValue(new Error("offline"));
    render(<VoiceControl ask={async () => "reply"} context={voiceContext("/")} />);
    fireEvent.click(screen.getByRole("button"));
    await screen.findByRole("alert");
    expect(disclaimerGiven("teacher-a")).toBe(false);
  });
  it("leaves the disclaimer pending if the teacher cancels", async () => {
    vi.mocked(window.confirm).mockReturnValue(false);
    render(<VoiceControl ask={async () => "reply"} context={voiceContext("/")} />);
    fireEvent.click(screen.getByRole("button"));
    await waitFor(() => expect(window.confirm).toHaveBeenCalled());
    expect(sdk.startSession).not.toHaveBeenCalled();
    expect(disclaimerGiven("teacher-a")).toBe(false);
  });
  it("sends a contextual update for changed run state, without repeating unchanged context", () => {
    const run = { ...emptyRun("a1"), title: "Term 1 test" };
    const ask = async () => "reply";
    const { rerender } = render(<VoiceControl ask={ask} context={voiceContext("/runs/a1", run)} />);
    sdk.status = "connected";
    rerender(<VoiceControl ask={ask} context={voiceContext("/runs/a1", run)} />);
    sdk.sendContextualUpdate.mockClear();
    rerender(<VoiceControl ask={ask} context={voiceContext("/runs/a1", { ...run, state: "marking" })} />);
    expect(sdk.sendContextualUpdate).toHaveBeenCalledTimes(1);
    expect(sdk.sendContextualUpdate.mock.calls[0]?.[0]).toContain("marking");
    rerender(<VoiceControl ask={ask} context={voiceContext("/runs/a1", { ...run, state: "marking" })} />);
    expect(sdk.sendContextualUpdate).toHaveBeenCalledTimes(1);
  });
});
