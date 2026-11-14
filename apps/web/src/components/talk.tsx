// The communication box (conversation.md §1): docked on the right of the live run, the one place the
// teacher and Mothusi talk. Mothusi's updates, the teacher's messages and Mothusi's replies share one
// thread, folded from the run's events. Writing never pauses the run: a message is accepted at once
// (202) and the reply arrives on the stream. The voice control joins the input row later.
import { useMutation } from "@tanstack/react-query";
import {
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";

import { ApiError, api } from "../api/client";
import type { NoteCard as Card } from "../api/types";
import { clock } from "../lib/utils";
import type { RunView } from "../run/reducer";
import { sentNotes, threadOf, type ThreadItem } from "../run/thread";
import { Button } from "./ui/button";
import "./talk.css";

const MAX = 1000;
const ENDED = new Set(["delivered"]);

/** A message the teacher sent that the stream has not echoed back yet. */
interface Outgoing {
  key: number;
  text: string;
  status: "sending" | "sent" | "failed";
  messageId?: string;
  reason?: string;
}

const SUGGESTIONS = [
  "How far are you?",
  "Why did L-014 get its mark on 2.1?",
  "Send L-001 their feedback",
];

const reason = (error: unknown) =>
  error instanceof ApiError
    ? error.message
    : "That did not go through. Check the connection and send it again.";

export function TalkBox({ run }: { run: RunView }) {
  const thread = useMemo(() => threadOf(run.events), [run.events]);
  const sent = useMemo(() => sentNotes(run.events), [run.events]);
  const [outgoing, setOutgoing] = useState<Outgoing[]>([]);
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const [seen, setSeen] = useState(0);
  const nextKey = useRef(1);
  const list = useRef<HTMLOListElement>(null);
  const stuck = useRef(true); // following the newest line until the teacher scrolls up
  const ended = ENDED.has(run.state) || run.halted !== null;

  // Once the stream echoes a message, the thread shows it: drop the local copy.
  const echoed = useMemo(
    () =>
      new Set(
        thread.flatMap((i) => (i.kind === "message" ? [i.messageId] : [])),
      ),
    [thread],
  );
  const waiting = outgoing.filter(
    (o) => !(o.messageId && echoed.has(o.messageId)),
  );
  const answered = new Set(
    thread.flatMap((i) =>
      i.kind === "reply" && i.messageId ? [i.messageId] : [],
    ),
  );
  const thinking =
    thread.some((i) => i.kind === "message" && !answered.has(i.messageId)) ||
    waiting.some((o) => o.status !== "failed");

  const post = useMutation({
    mutationFn: ({ body }: { key: number; body: string }) =>
      api.postMessage(run.assessmentId, body),
    onSuccess: (r, { key }) =>
      setOutgoing((all) =>
        all.map((o) =>
          o.key === key ? { ...o, status: "sent", messageId: r.message_id } : o,
        ),
      ),
    onError: (e, { key }) =>
      setOutgoing((all) =>
        all.map((o) =>
          o.key === key ? { ...o, status: "failed", reason: reason(e) } : o,
        ),
      ),
  });

  const send = (body: string) => {
    const clean = body.trim();
    if (!clean || clean.length > MAX) return;
    const key = nextKey.current++;
    setOutgoing((all) => [...all, { key, text: clean, status: "sending" }]);
    post.mutate({ key, body: clean });
    setText("");
  };
  const retry = (o: Outgoing) => {
    setOutgoing((all) =>
      all.map((x) =>
        x.key === o.key ? { ...x, status: "sending", reason: undefined } : x,
      ),
    );
    post.mutate({ key: o.key, body: o.text });
  };

  // Follow the newest line, unless the teacher has scrolled up to read.
  const count = thread.length + waiting.length;
  useLayoutEffect(() => {
    const el = list.current;
    if (el && stuck.current) el.scrollTop = el.scrollHeight;
  }, [count, thinking]);

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send(text);
    }
  };
  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    send(text);
  };
  const unread = open ? 0 : Math.max(0, thread.length - seen);

  return (
    <>
      <button
        type="button"
        className="talk-toggle"
        onClick={() => {
          setOpen(true);
          setSeen(thread.length);
        }}
        aria-expanded={open}
        aria-controls="talk"
      >
        Talk to Mothusi
        {unread > 0 && !open && (
          <span className="count" aria-label={`${unread} new`}>
            {unread}
          </span>
        )}
      </button>
      <aside
        id="talk"
        className={`talk ${open ? "open" : ""}`}
        aria-label="Talk to Mothusi"
      >
        <header className="talk-head">
          <div>
            <h2>Talk to Mothusi</h2>
            <p>
              {ended
                ? "The run has finished. Your thread stays here."
                : "The run carries on while you talk."}
            </p>
          </div>
          <button
            type="button"
            className="talk-close"
            onClick={() => {
              setOpen(false);
              setSeen(thread.length);
            }}
            aria-label="Close the conversation"
          >
            ×
          </button>
        </header>

        <ol
          onScroll={(e) => {
            const el = e.currentTarget;
            stuck.current =
              el.scrollHeight - el.scrollTop - el.clientHeight < 48;
          }}
          className="thread"
          ref={list}
          aria-live="polite"
          aria-relevant="additions"
        >
          {thread.length === 0 && waiting.length === 0 && (
            <li className="t-empty">
              <p>
                Ask while it works. Nothing you write here stops the marking.
              </p>
            </li>
          )}
          {thread.map((item) => (
            <Line
              key={`${item.kind}-${item.seq}`}
              item={item}
              start={run.startedAt}
              run={run}
              sentAt={sent}
            />
          ))}
          {waiting.map((o) => (
            <li key={`out-${o.key}`} className={`t-me ${o.status}`}>
              <p>{o.text}</p>
              {o.status === "sending" && <span className="meta">Sending…</span>}
              {o.status === "failed" && (
                <span className="meta fail" role="alert">
                  {o.reason}{" "}
                  <button
                    type="button"
                    className="linkish"
                    onClick={() => retry(o)}
                  >
                    Send again
                  </button>
                </span>
              )}
            </li>
          ))}
          {thinking && (
            <li className="t-thinking" aria-label="Mothusi is replying">
              <span className="who">Mothusi</span>
              <span className="dots" aria-hidden="true">
                <i />
                <i />
                <i />
              </span>
            </li>
          )}
        </ol>

        <form className="talk-input" onSubmit={onSubmit}>
          {thread.every((i) => i.kind === "update") &&
            waiting.length === 0 &&
            !ended && (
              <div
                className="suggest"
                role="group"
                aria-label="Things you can ask"
              >
                {SUGGESTIONS.map((s) => (
                  <button key={s} type="button" onClick={() => setText(s)}>
                    {s}
                  </button>
                ))}
              </div>
            )}
          <label htmlFor="talk-text" className="sr-only">
            Message Mothusi
          </label>
          <div className="row">
            <textarea
              id="talk-text"
              rows={2}
              maxLength={MAX}
              value={text}
              disabled={ended}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={onKey}
              placeholder={
                ended
                  ? "Start a new run to ask about another set."
                  : "Ask how far it is, or about a learner's mark"
              }
            />
            <Button
              type="submit"
              variant="primary"
              disabled={ended || !text.trim()}
            >
              Send
            </Button>
          </div>
        </form>
      </aside>
    </>
  );
}

function Line({
  item,
  start,
  run,
  sentAt,
}: {
  item: ThreadItem;
  start: string | null;
  run: RunView;
  sentAt: Map<string, string>;
}) {
  const at = (
    <time dateTime={item.at}>
      {clock(
        start
          ? Math.max(0, (Date.parse(item.at) - Date.parse(start)) / 1000)
          : 0,
      )}
    </time>
  );
  if (item.kind === "update") {
    return (
      <li className="t-update">
        {at}
        <p>{item.text}</p>
      </li>
    );
  }
  if (item.kind === "message") {
    return (
      <li className="t-me">
        <p>{item.text}</p>
        <span className="meta">You · {at}</span>
      </li>
    );
  }
  return (
    <li className="t-mothusi">
      <span className="who">Mothusi · {at}</span>
      <p>{item.say}</p>
      {item.promise && !item.card && (
        <p className="promise">
          I'll come back to you about {item.promise} here.
        </p>
      )}
      {item.card && (
        <NoteCard
          card={item.card}
          run={run}
          sentAt={sentAt.get(item.card.script_id) ?? null}
          start={start}
        />
      )}
    </li>
  );
}

function NoteCard({
  card,
  run,
  sentAt,
  start,
}: {
  card: Card;
  run: RunView;
  sentAt: string | null;
  start: string | null;
}) {
  const send = useMutation({
    mutationFn: () => api.sendNote(run.assessmentId, card.script_id),
  });
  const done = sentAt !== null;
  const waiting = send.isSuccess && !done;
  return (
    <section
      className="note-card"
      aria-label={`${card.script_id}'s feedback note`}
    >
      <header>
        <span className="pseudo">{card.script_id}</span>
        <span className="name">{card.learner}</span>
        <span className={`state ${done ? "sent" : ""}`}>
          {done ? "Released" : "Ready to release"}
        </span>
      </header>
      <dl>
        <dt>What it tested</dt>
        <dd>{card.outcome}</dd>
        <dt>The mark</dt>
        <dd>{card.mark_with_quote}</dd>
        <dt>Next</dt>
        <dd>{card.next_task}</dd>
        <dt>Ask yourself</dt>
        <dd>{card.self_assessment_prompt}</dd>
      </dl>
      {done ? (
        <p className="sent-line" role="status">
          Released at{" "}
          {clock(
            start
              ? Math.max(0, (Date.parse(sentAt) - Date.parse(start)) / 1000)
              : 0,
          )}
          . Its link works now; the rest of the set waits for your approval.
        </p>
      ) : (
        <div className="actions">
          <Button
            variant="primary"
            size="small"
            disabled={send.isPending || waiting}
            onClick={() => send.mutate()}
          >
            {send.isPending || waiting
              ? "Releasing…"
              : `Approve and release ${card.learner}'s note`}
          </Button>
          <span className="hint">
            Only this note is released. The rest of the set waits for your
            approval.
          </span>
          {send.isError && (
            <p className="meta fail" role="alert">
              {reason(send.error)}
            </p>
          )}
        </div>
      )}
    </section>
  );
}
