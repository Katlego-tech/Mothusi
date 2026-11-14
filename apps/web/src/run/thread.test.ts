import type {
  MessagePayload,
  MothusiEvent,
  NeedsTeacher,
  NoteCard,
  NoteSentPayload,
  ReplyPayload,
} from "../api/types";
import bundle from "./__fixtures__/planted-bundle.json";
import plantedLog from "./__fixtures__/planted-events.jsonl?raw";
import { apply, emptyRun } from "./reducer";
import { sentNotes, threadOf, updateFor } from "./thread";

const planted: MothusiEvent[] = plantedLog
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as MothusiEvent);
const assessmentId = bundle.assessment_id;
function required<T>(value: T | undefined): T {
  if (value === undefined) throw new Error("Missing planted fixture value");
  return value;
}
const base = {
  assessment_id: assessmentId,
  at: "2026-11-13T09:05:05Z",
  agent: null,
  summary: "Conversation",
};
function eventOf<K extends MothusiEvent["kind"]>(
  kind: K,
): Extract<MothusiEvent, { kind: K }> {
  const event = planted.find((e) => e.kind === kind);
  if (!event) throw new Error(`Missing planted ${kind}`);
  return event as Extract<MothusiEvent, { kind: K }>;
}
const note = required(bundle.learner_notes[0]);
const card: NoteCard = {
  script_id: required(eventOf("created").payload.script_ids[0]),
  learner: note.learner,
  outcome: note.outcome,
  mark_with_quote: note.mark_with_quote,
  next_task: note.next_task,
  self_assessment_prompt: note.self_assessment_prompt,
};
const message: MessagePayload = {
  message_id: "m-1",
  text: "Send L-001 their feedback",
};
const reply: ReplyPayload = {
  message_id: message.message_id,
  skill: "send_feedback",
  say: "Here is the note to approve.",
  card,
  promise: null,
};
const sent: NoteSentPayload = {
  script_id: card.script_id,
  written: [`notes/${card.script_id}.md`],
  sent: [card.learner],
};
const conversation: MothusiEvent[] = [
  { ...base, seq: 150, kind: "message", payload: message },
  { ...base, seq: 151, kind: "reply", payload: reply },
  { ...base, seq: 152, kind: "note_sent", payload: sent },
];
const fold = (events: MothusiEvent[]) =>
  events.reduce(apply, emptyRun(assessmentId));

describe("updateFor", () => {
  it("announces marking with the planted script count", () => {
    const event = required(
      planted.find((e) => e.kind === "state" && e.payload.to === "marking"),
    );
    expect(
      updateFor(event, eventOf("created").payload.script_ids.length),
    ).toEqual({ text: "Marking 6 scripts against your memo." });
    expect(updateFor(event, 0)).toEqual({ text: "Marking against your memo." });
  });

  const dispute = eventOf("disagreement").payload;
  const questions: [
    string,
    NeedsTeacher,
    { text: string; scriptId?: string },
  ][] = [
    [
      "memo",
      eventOf("needs_teacher").payload,
      {
        text: "Memo item 2.3 may be wrong. It needs your decision before marking starts.",
      },
    ],
    [
      "script",
      {
        topic: "script",
        stage: "marking",
        title: "Check this script",
        script_id: dispute.script_id,
        question_id: dispute.question_id,
        question: dispute.question,
        max_marks: dispute.max_marks,
        memo_answer: dispute.memo_answer,
        marking_notes: dispute.marking_notes,
        answer: dispute.answer,
        attempts: [],
      },
      {
        text: "L-021 needs you. Marking carries on meanwhile.",
        scriptId: "L-021",
      },
    ],
    [
      "budget",
      {
        topic: "budget",
        title: "Budget",
        spent_zar: "50.00",
        limit_zar: "50.00",
        scripts_settled: 1,
        scripts_total: 6,
        spent: [],
        estimate: [],
      },
      { text: "The budget needs your decision before more marking." },
    ],
    [
      "reading",
      { topic: "reading", title: "Check pages", items: [] },
      { text: "Some pages need you to check how they were read." },
    ],
  ];
  it.each(questions)(
    "words the %s needs_teacher update",
    (_topic, payload, expected) => {
      expect(
        updateFor({ ...base, seq: 150, kind: "needs_teacher", payload }, 6),
      ).toEqual(expected);
    },
  );

  it("announces the disagreement with both marks and the script link", () => {
    expect(updateFor(eventOf("disagreement"), 6)).toEqual({
      text: "The Moderator gave L-021 2 on 4; the Marker gave 5.",
      scriptId: "L-021",
    });
  });

  it("announces the moderation sample decision", () => {
    const event = required(
      planted.find(
        (e) =>
          e.kind === "decision" && e.payload.action === "moderation sample",
      ),
    );
    expect(updateFor(event, 6)).toEqual({
      text: "6 scripts re-marked blind: L-014, L-001, L-007, L-021, L-010, L-003.",
    });
  });

  it("announces delivery counts and singular or plural messages", () => {
    expect(updateFor(eventOf("delivered"), 6)).toEqual({
      text: "Delivered: 12 files written, 0 messages sent.",
    });
    expect(
      updateFor(
        {
          ...eventOf("delivered"),
          payload: { written: sent.written, sent: sent.sent },
        },
        6,
      ),
    ).toEqual({ text: "Delivered: 1 files written, 1 message sent." });
  });

  it("announces note_sent with the script link", () => {
    expect(updateFor(required(conversation[2]), 6)).toEqual({
      text: "L-001's note was sent.",
      scriptId: "L-001",
    });
  });

  it.each(["brief", "result", "check", "usage"] as const)(
    "omits %s from conversation updates",
    (kind) => {
      expect(updateFor(eventOf(kind), 6)).toBeNull();
    },
  );
});

describe("threadOf and sentNotes", () => {
  it("folds messages and replies in seq order alongside updates and rebuilds the same thread", () => {
    const events = [...planted, ...conversation];
    const run = fold(events);
    const thread = threadOf(run.events);
    expect(thread.map((item) => item.seq)).toEqual(
      thread.map((item) => item.seq).sort((a, b) => a - b),
    );
    expect(thread).toContainEqual(
      expect.objectContaining({
        kind: "update",
        text: "Marking 6 scripts against your memo.",
      }),
    );
    expect(thread.slice(-3)).toEqual([
      {
        kind: "message",
        seq: 150,
        at: base.at,
        messageId: message.message_id,
        text: message.text,
      },
      {
        kind: "reply",
        seq: 151,
        at: base.at,
        messageId: reply.message_id,
        skill: reply.skill,
        say: reply.say,
        card,
        promise: null,
      },
      {
        kind: "update",
        seq: 152,
        at: base.at,
        text: "L-001's note was sent.",
        scriptId: card.script_id,
      },
    ]);
    expect(threadOf(fold(events).events)).toEqual(thread);
    expect(threadOf(events.reduce(apply, run).events)).toEqual(thread);
  });

  it("derives sent script timestamps only from note_sent and rebuilds them on reload", () => {
    expect(sentNotes(fold(planted).events)).toEqual(new Map());
    const events = [...planted, ...conversation];
    expect(sentNotes(fold(events).events)).toEqual(
      new Map([[card.script_id, base.at]]),
    );
    expect(sentNotes(fold(events).events)).toEqual(
      sentNotes(fold(events).events),
    );
  });
});

describe("a burst of flags", () => {
  const flagged = (seq: number, scriptId: string): MothusiEvent =>
    ({
      ...base,
      seq,
      kind: "needs_teacher",
      payload: {
        topic: "script",
        stage: "marking",
        title: "Check",
        script_id: scriptId,
        question_id: "1.1",
        question: "q",
        max_marks: 5,
        memo_answer: "a",
        marking_notes: "n",
        answer: "a",
        attempts: [],
      },
    }) as MothusiEvent;

  it("is one line naming the first three, and repeats are counted once", () => {
    const ids = ["L-002", "L-004", "L-005", "L-006", "L-004"];
    const items = threadOf(ids.map((id, i) => flagged(i, id)));
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({
      kind: "update",
      text: "4 scripts need you: L-002, L-004, L-005 and 1 more. Marking carries on meanwhile.",
    });
  });

  it("starts a new line once something else is said in between", () => {
    const between: MothusiEvent = {
      ...base,
      seq: 1,
      kind: "message",
      payload: { message_id: "m-1", text: "How far are you?" },
    };
    expect(
      threadOf([flagged(0, "L-002"), between, flagged(2, "L-004")]).map(
        (i) => i.kind,
      ),
    ).toEqual(["update", "message", "update"]);
  });
});
