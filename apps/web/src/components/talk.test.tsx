import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { API } from "../api/client";
import type {
  AssessmentState,
  MessagePayload,
  MothusiEvent,
  NoteCard,
  NoteSentPayload,
  ReplyPayload,
} from "../api/types";
import bundle from "../run/__fixtures__/planted-bundle.json";
import plantedLog from "../run/__fixtures__/planted-events.jsonl?raw";
import { apply, emptyRun, type RunView } from "../run/reducer";
import { TalkBox } from "./talk";

const planted: MothusiEvent[] = plantedLog
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as MothusiEvent);
const assessmentId = bundle.assessment_id;
function required<T>(value: T | undefined): T {
  if (value === undefined) throw new Error("Missing planted fixture value");
  return value;
}
function runAt(state: AssessmentState): RunView {
  const end = planted.findIndex((e) => e.kind === "state" && e.payload.to === state);
  if (end < 0) throw new Error(`Missing planted ${state}`);
  return planted.slice(0, end + 1).reduce(apply, emptyRun(assessmentId));
}
const base = {
  assessment_id: assessmentId,
  at: "2026-11-13T09:05:05Z",
  agent: null,
  summary: "Conversation",
};
const message: MessagePayload = { message_id: "m-1", text: "How far are you?" };
const reply: ReplyPayload = {
  message_id: message.message_id,
  skill: "run_status",
  say: "Marking is underway.",
  card: null,
  promise: null,
};
const messageEvent = (run: RunView): MothusiEvent => ({
  ...base,
  seq: run.lastSeq + 1,
  kind: "message",
  payload: message,
});
const replyEvent = (run: RunView, payload: ReplyPayload = reply): MothusiEvent => ({
  ...base,
  seq: run.lastSeq + 1,
  kind: "reply",
  payload,
});
const note = required(bundle.learner_notes[0]);
const created = planted.find((e) => e.kind === "created");
if (created?.kind !== "created") throw new Error("Missing planted created event");
const card: NoteCard = {
  script_id: required(created.payload.script_ids[0]),
  learner: note.learner,
  outcome: note.outcome,
  mark_with_quote: note.mark_with_quote,
  next_task: note.next_task,
  self_assessment_prompt: note.self_assessment_prompt,
};
function offeredRun() {
  const run = runAt("awaiting_approval");
  const asked = apply(run, {
    ...messageEvent(run),
    kind: "message",
    payload: {
      message_id: "m-1",
      text: `Send ${card.script_id} their feedback`,
    },
  });
  return apply(
    asked,
    replyEvent(asked, {
      message_id: "m-1",
      skill: "send_feedback",
      say: "Here is the note to approve.",
      card,
      promise: null,
    }),
  );
}

function show(run: RunView) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const view = (current: RunView) => (
    <QueryClientProvider client={client}>
      <TalkBox run={current} />
    </QueryClientProvider>
  );
  const rendered = render(view(run));
  return { rerun: (current: RunView) => rendered.rerender(view(current)) };
}
const response = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
function answerWith(status: number, body: unknown) {
  const fetch = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(async () => response(status, body));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
const input = () => screen.getByRole("textbox", { name: "Message Mothusi" });
afterEach(() => vi.unstubAllGlobals());

describe("TalkBox", () => {
  it("posts {text} on Enter and shows Sending immediately, then reconciles the stream echo once", async () => {
    const user = userEvent.setup();
    let accept!: (value: Response) => void;
    const fetch = vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          accept = resolve;
        }),
    );
    vi.stubGlobal("fetch", fetch);
    let run = runAt("marking");
    const { rerun } = show(run);
    await user.type(input(), message.text);
    await user.keyboard("{Enter}");
    expect(screen.getByText(message.text)).toBeInTheDocument();
    expect(screen.getByText("Sending…")).toBeInTheDocument();
    expect(input()).toHaveValue("");
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    expect(fetch).toHaveBeenCalledWith(
      `${API}/assessments/${assessmentId}/messages`,
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ text: message.text }),
        headers: expect.objectContaining({
          "Content-Type": "application/json",
        }),
      }),
    );
    await act(async () => accept(response(202, { message_id: message.message_id })));
    await waitFor(() => expect(screen.queryByText("Sending…")).not.toBeInTheDocument());
    expect(screen.getAllByText(message.text)).toHaveLength(1);
    run = apply(run, messageEvent(run));
    rerun(run);
    expect(screen.getAllByText(message.text)).toHaveLength(1);
    expect(screen.getByLabelText("Mothusi is replying")).toBeInTheDocument();
    rerun(apply(run, replyEvent(run)));
    expect(screen.getByText(reply.say)).toBeInTheDocument();
    expect(screen.queryByLabelText("Mothusi is replying")).not.toBeInTheDocument();
  });

  it("inserts a newline on Shift+Enter without posting", async () => {
    const user = userEvent.setup();
    const fetch = answerWith(202, { message_id: "m-1" });
    show(runAt("marking"));
    await user.type(input(), "How far");
    await user.keyboard("{Shift>}{Enter}{/Shift}");
    await user.type(input(), "are you?");
    expect(input()).toHaveValue("How far\nare you?");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("shows a 409 server reason and re-posts with Send again", async () => {
    const user = userEvent.setup();
    const reason = "The run is delivered; start a new run to ask about it";
    const fetch = answerWith(409, { message: reason });
    show(runAt("marking"));
    await user.type(input(), message.text);
    await user.keyboard("{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent(reason);
    expect(screen.queryByLabelText("Mothusi is replying")).not.toBeInTheDocument();
    fetch.mockResolvedValueOnce(response(202, { message_id: "m-1" }));
    await user.click(screen.getByRole("button", { name: "Send again" }));
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    expect(fetch.mock.calls[1]).toEqual(fetch.mock.calls[0]);
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(screen.getAllByText(message.text)).toHaveLength(1);
  });

  it("shows the typing indicator for a streamed unanswered message until its reply arrives", () => {
    const run = runAt("marking");
    const { rerun } = show(run);
    expect(screen.queryByLabelText("Mothusi is replying")).not.toBeInTheDocument();
    const asked = apply(run, messageEvent(run));
    rerun(asked);
    expect(screen.getByLabelText("Mothusi is replying")).toBeInTheDocument();
    rerun(apply(asked, replyEvent(asked)));
    expect(screen.queryByLabelText("Mothusi is replying")).not.toBeInTheDocument();
  });

  it("fills the input from a suggestion chip without sending", async () => {
    const user = userEvent.setup();
    const fetch = answerWith(202, { message_id: "m-1" });
    show(runAt("marking"));
    const suggestions = screen.getByRole("group", {
      name: "Things you can ask",
    });
    await user.click(within(suggestions).getByRole("button", { name: message.text }));
    expect(input()).toHaveValue(message.text);
    expect(fetch).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("Mothusi is replying")).not.toBeInTheDocument();
  });

  it("disables the input when the run is delivered", () => {
    const { rerun } = show(runAt("marking"));
    expect(input()).toBeEnabled();
    rerun(planted.reduce(apply, emptyRun(assessmentId)));
    expect(input()).toBeDisabled();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  });

  it("exposes the narrow-screen Talk to Mothusi toggle and updates aria-expanded on open and close", async () => {
    const user = userEvent.setup();
    show(runAt("marking"));
    const toggle = screen.getByRole("button", { name: /^Talk to Mothusi/, expanded: false });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveAttribute("aria-controls", "talk");
    const box = screen.getByRole("complementary", { name: "Talk to Mothusi" });
    expect(within(box).getByRole("button", { name: "Fold the conversation away" })).toBeInTheDocument();
    expect(within(box).getByRole("separator", { name: "Resize the conversation" })).toHaveAttribute("aria-orientation", "vertical");
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    await user.click(screen.getByRole("button", { name: "Close the conversation" }));
    expect(toggle).toHaveAttribute("aria-expanded", "false");
  });
});

describe("NoteCard in the thread", () => {
  it("shows the planted learner note, posts approval and shows Sent only after note_sent", async () => {
    const user = userEvent.setup();
    const fetch = answerWith(202, { state: "awaiting_approval" });
    const run = offeredRun();
    const { rerun } = show(run);
    const region = screen.getByRole("region", {
      name: `${card.script_id}'s feedback note`,
    });
    for (const text of [
      card.learner,
      card.outcome,
      card.mark_with_quote,
      card.next_task,
      card.self_assessment_prompt,
    ]) {
      expect(within(region).getByText(text)).toBeInTheDocument();
    }
    await user.click(
      within(region).getByRole("button", {
        name: `Approve and release ${card.learner}'s note`,
      }),
    );
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    expect(fetch).toHaveBeenCalledWith(
      `${API}/assessments/${assessmentId}/notes/${card.script_id}/send`,
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetch.mock.calls[0]?.[1]?.body).toBeUndefined();
    await waitFor(() => expect(within(region).getByRole("button", { name: "Releasing…" })).toBeDisabled());
    expect(within(region).queryByText("Released", { exact: true })).not.toBeInTheDocument();
    const payload: NoteSentPayload = {
      script_id: card.script_id,
      written: [`notes/${card.script_id}.md`],
      sent: [card.learner],
    };
    rerun(apply(run, { ...base, seq: run.lastSeq + 1, kind: "note_sent", payload }));
    expect(within(region).getByText("Released", { exact: true })).toBeInTheDocument();
    expect(within(region).getByRole("status")).toHaveTextContent("Released at");
    expect(within(region).queryByRole("button")).not.toBeInTheDocument();
  });

  it("shows the server reason when sending the note is refused", async () => {
    const user = userEvent.setup();
    const reason = "The note failed review; it cannot be sent.";
    answerWith(409, { message: reason });
    show(offeredRun());
    const region = screen.getByRole("region", {
      name: `${card.script_id}'s feedback note`,
    });
    await user.click(
      within(region).getByRole("button", {
        name: `Approve and release ${card.learner}'s note`,
      }),
    );
    expect(await within(region).findByRole("alert")).toHaveTextContent(reason);
    expect(
      within(region).getByRole("button", {
        name: `Approve and release ${card.learner}'s note`,
      }),
    ).toBeEnabled();
    expect(within(region).queryByText("Released", { exact: true })).not.toBeInTheDocument();
  });
});
