// The communication box (conversation.md §1, §11): on every page, the one place the teacher and
// Mothusi talk. On a run's pages it is the run's thread: Mothusi's updates, the teacher's messages and
// the replies, folded from the run's events; a message is accepted at once (202) and never pauses the
// run. Off a run (§11.2) the thread is this session's, and each message is answered directly.
// The teacher can widen, narrow or fold the box; the voice control joins the input row later.
import { useMutation } from "@tanstack/react-query";
import {
  lazy,
  Suspense,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";

import { ApiError, api, type LessonSlots } from "../api/client";
import type { NoteCard as Card, ReplyPayload } from "../api/types";
import { clock } from "../lib/utils";
import type { RunView } from "../run/reducer";
import { sentNotes, threadOf, type ThreadItem } from "../run/thread";
import { actionLabel, openOnScreen } from "../lib/ui-actions";
import { Button } from "./ui/button";
import "./talk.css";

// The voice (conversation.md §12) loads only when the box is shown: the SDK and its audio code stay
// out of the first paint and out of the tests' way.
const VoiceControl = lazy(() => import("./voice").then((m) => ({ default: m.VoiceControl })));

const MAX = 1000;
const ENDED = new Set(["delivered"]);
const WIDTH = { min: 320, max: 760, start: 440, step: 24 };

/** A message the teacher sent that has no place in the thread yet. */
interface Outgoing {
  key: number;
  text: string;
  status: "sending" | "sent" | "failed";
  messageId?: string;
  reason?: string;
}

const RUN_SUGGESTIONS = ["How far are you?", "Where did you get stuck?", "Give me the stats for this run"];
const DESK_SUGGESTIONS = [
  "What should I teach in term 2 week 3?",
  "How much does the term 1 test count?",
  "Prepare a lesson plan for week 6",
];

const reason = (error: unknown) =>
  error instanceof ApiError ? error.message : "That did not go through. Check the connection and send it again.";

// Per-viewer conveniences only: the box's width and whether it is folded.
const remembered = (key: string): string | null => {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
};
const remember = (key: string, value: string) => {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* private window: the box still works, it just forgets */
  }
};
const clampWidth = (w: number) => Math.min(WIDTH.max, Math.max(WIDTH.min, Math.round(w)));

interface Talk {
  thread: ThreadItem[];
  waiting: Outgoing[];
  thinking: boolean;
  ended: boolean;
  send: (text: string) => void;
  /** The same path as typing, awaited: what the voice reads aloud (conversation.md §12). */
  ask: (text: string) => Promise<string>;
  retry: (o: Outgoing) => void;
  sent: Map<string, string>;
  start: string | null;
  run: RunView | null;
}

const ASK_WAIT_MS = 60_000;
const STILL_WORKING = "I'm still working on that. The answer will appear in the box when it's ready.";
const pause = (ms: number) => new Promise((r) => setTimeout(r, ms));
const spoken = (say: string, ask: string | null | undefined) => (ask ? `${say}${say ? "\n\n" : ""}${ask}` : say);

/** The run's thread: the stream carries both sides; local copies cover the gap until the echo. */
function useRunTalk(run: RunView | undefined): Talk | null {
  const thread = useMemo(() => (run ? threadOf(run.events) : []), [run]);
  const sent = useMemo(() => (run ? sentNotes(run.events) : new Map<string, string>()), [run]);
  const [outgoing, setOutgoing] = useState<Outgoing[]>([]);
  const nextKey = useRef(1);
  const events = useRef<RunView["events"]>([]);
  useEffect(() => {
    events.current = run?.events ?? [];
  }, [run]);
  const post = useMutation({
    mutationFn: ({ body }: { key: number; body: string }) => api.postMessage(run?.assessmentId ?? "", body),
    onSuccess: (r, { key }) =>
      setOutgoing((all) => all.map((o) => (o.key === key ? { ...o, status: "sent", messageId: r.message_id } : o))),
    onError: (e, { key }) =>
      setOutgoing((all) => all.map((o) => (o.key === key ? { ...o, status: "failed", reason: reason(e) } : o))),
  });
  if (!run) return null;
  const echoed = new Set(thread.flatMap((i) => (i.kind === "message" ? [i.messageId] : [])));
  const waiting = outgoing.filter((o) => !(o.messageId && echoed.has(o.messageId)));
  const answered = new Set(thread.flatMap((i) => (i.kind === "reply" && i.messageId ? [i.messageId] : [])));
  const queue = (body: string) => {
    const key = nextKey.current++;
    setOutgoing((all) => [...all, { key, text: body, status: "sending" }]);
    return key;
  };
  return {
    thread,
    waiting,
    thinking:
      thread.some((i) => i.kind === "message" && !answered.has(i.messageId)) ||
      waiting.some((o) => o.status !== "failed"),
    ended: ENDED.has(run.state) || run.halted !== null,
    send: (body) => post.mutate({ key: queue(body), body }),
    ask: async (body) => {
      let id: string;
      try {
        id = (await post.mutateAsync({ key: queue(body), body })).message_id;
      } catch (e) {
        return reason(e);
      }
      for (let waited = 0; waited < ASK_WAIT_MS; waited += 300) {
        const reply = events.current.find((e) => e.kind === "reply" && e.payload.message_id === id);
        if (reply?.kind === "reply") return reply.payload.say;
        await pause(300);
      }
      return STILL_WORKING;
    },
    retry: (o) => {
      setOutgoing((all) => all.map((x) => (x.key === o.key ? { ...x, status: "sending", reason: undefined } : x)));
      post.mutate({ key: o.key, body: o.text });
    },
    sent,
    start: run.startedAt,
    run,
  };
}

type DeskAnswer = Awaited<ReturnType<typeof api.colleague>>;

/** Off a run (§11.2): this session's thread; each message is answered in the response, or later (§13). */
function useDeskTalk(): Talk {
  const [thread, setThread] = useState<ThreadItem[]>([]);
  const [outgoing, setOutgoing] = useState<Outgoing[]>([]);
  const [deferred, setDeferred] = useState<{ id: string; messageId: string }[]>([]);
  const [start] = useState(() => new Date().toISOString());
  const lesson = useRef<LessonSlots | null>(null);
  const seq = useRef(0);
  const nextKey = useRef(1);

  const replyItem = (messageId: string, r: Pick<DeskAnswer, "reply" | "ask">): ThreadItem => {
    const reply: ReplyPayload = r.reply;
    return {
      kind: "reply",
      seq: seq.current++,
      at: new Date().toISOString(),
      messageId,
      skill: reply.skill,
      say: spoken(reply.say, r.ask),
      card: reply.card,
      promise: reply.promise,
      action: reply.action ?? null,
    };
  };

  const ask = useMutation({
    mutationFn: ({ body }: { key: number; body: string }) =>
      api.colleague(body, lesson.current ? { lesson: lesson.current } : {}),
    onSuccess: (r, { key, body }) => {
      lesson.current = r.lesson;
      const messageId = `local-${key}`;
      const message: ThreadItem = { kind: "message", seq: seq.current++, at: new Date().toISOString(), messageId, text: body };
      setThread((all) => [...all, message, replyItem(messageId, r)]);
      setOutgoing((all) => all.filter((o) => o.key !== key));
      const id = r.deferred_id;
      if (id) setDeferred((all) => [...all, { id, messageId }]);
    },
    onError: (e, { key }) =>
      setOutgoing((all) => all.map((o) => (o.key === key ? { ...o, status: "failed", reason: reason(e) } : o))),
  });

  // §13: a request deferred while Mothusi was at capacity is answered here when the GPU frees.
  useEffect(() => {
    if (deferred.length === 0) return;
    const timer = setInterval(() => {
      for (const d of deferred) {
        api
          .deferredReply(d.id)
          .then((r) => {
            if (!("reply" in r)) return;
            setThread((all) => [...all, replyItem(d.messageId, r)]);
            setDeferred((all) => all.filter((x) => x.id !== d.id));
          })
          .catch(() => setDeferred((all) => all.filter((x) => x.id !== d.id)));
      }
    }, 10_000);
    return () => clearInterval(timer);
  }, [deferred]);

  const queue = (body: string) => {
    const key = nextKey.current++;
    setOutgoing((all) => [...all, { key, text: body, status: "sending" }]);
    return key;
  };
  return {
    thread,
    waiting: outgoing,
    thinking: outgoing.some((o) => o.status === "sending"),
    ended: false,
    send: (body) => ask.mutate({ key: queue(body), body }),
    ask: async (body) => {
      try {
        const r = await ask.mutateAsync({ key: queue(body), body });
        return spoken(r.reply.say, r.ask);
      } catch (e) {
        return reason(e);
      }
    },
    retry: (o) => {
      setOutgoing((all) => all.map((x) => (x.key === o.key ? { ...x, status: "sending", reason: undefined } : x)));
      ask.mutate({ key: o.key, body: o.text });
    },
    sent: new Map(),
    start,
    run: null,
  };
}

export function TalkBox({ run }: { run?: RunView }) {
  const onRun = useRunTalk(run);
  const atDesk = useDeskTalk();
  const talk = onRun ?? atDesk;
  const { thread, waiting, thinking, ended } = talk;
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false); // the phone sheet
  const [folded, setFolded] = useState(() => remembered("mothusi.talk.folded") === "1");
  const [width, setWidth] = useState(() => clampWidth(Number(remembered("mothusi.talk.width")) || WIDTH.start));
  const [seen, setSeen] = useState(0);
  const list = useRef<HTMLOListElement>(null);
  const stuck = useRef(true); // following the newest line until the teacher scrolls up

  const send = (body: string) => {
    const clean = body.trim();
    if (!clean || clean.length > MAX || ended) return;
    stuck.current = true;
    talk.send(clean);
    setText("");
  };

  // §14: carry out a reply's action when it first arrives, never for replies already there on load.
  const acted = useRef<Set<string> | null>(null);
  useEffect(() => {
    const keyOf = (i: ThreadItem) => `${i.kind}-${i.seq}-${i.at}`;
    if (acted.current === null) {
      acted.current = new Set(onRun ? thread.map(keyOf) : []);
      return;
    }
    for (const item of thread) {
      if (item.kind !== "reply" || !item.action || acted.current.has(keyOf(item))) continue;
      acted.current.add(keyOf(item));
      openOnScreen(item.action);
    }
  }, [thread, onRun]);

  const count = thread.length + waiting.length;
  useLayoutEffect(() => {
    const el = list.current;
    if (el && stuck.current) el.scrollTop = el.scrollHeight;
  }, [count, thinking, folded]);

  const fold = (value: boolean) => {
    setFolded(value);
    setSeen(thread.length);
    remember("mothusi.talk.folded", value ? "1" : "0");
  };
  const resizeTo = (w: number) => {
    const next = clampWidth(w);
    setWidth(next);
    remember("mothusi.talk.width", String(next));
  };
  const onDrag = (e: ReactPointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    const from = e.clientX;
    const was = width;
    const move = (ev: PointerEvent) => resizeTo(was + (from - ev.clientX));
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      document.body.classList.remove("talk-resizing");
    };
    document.body.classList.add("talk-resizing");
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
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
  const unread = open || !folded ? 0 : Math.max(0, thread.length - seen);
  const phoneUnread = open ? 0 : Math.max(0, thread.length - seen);
  const suggestions = run ? RUN_SUGGESTIONS : DESK_SUGGESTIONS;

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
        {phoneUnread > 0 && !open && (
          <span className="count" aria-label={`${phoneUnread} new`}>
            {phoneUnread}
          </span>
        )}
      </button>
      {folded && (
        <button type="button" className="talk-rail" onClick={() => fold(false)} aria-label="Open the conversation">
          <span className="rail-label">Talk to Mothusi</span>
          {unread > 0 && <span className="count">{unread}</span>}
        </button>
      )}
      <aside
        id="talk"
        className={`talk ${open ? "open" : ""} ${folded ? "folded" : ""}`}
        aria-label="Talk to Mothusi"
        style={{ width }}
      >
        <div
          className="talk-resize"
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize the conversation"
          aria-valuemin={WIDTH.min}
          aria-valuemax={WIDTH.max}
          aria-valuenow={width}
          tabIndex={0}
          onPointerDown={onDrag}
          onKeyDown={(e) => {
            if (e.key === "ArrowLeft") resizeTo(width + WIDTH.step);
            if (e.key === "ArrowRight") resizeTo(width - WIDTH.step);
          }}
        />
        <header className="talk-head">
          <div>
            <h2>Talk to Mothusi</h2>
            <p>
              {!run
                ? "Ask about the ATP, the PoA, or work you want prepared."
                : ended
                  ? "The run has finished. Your thread stays here."
                  : "The run carries on while you talk."}
            </p>
          </div>
          <button
            type="button"
            className="talk-fold"
            onClick={() => fold(true)}
            aria-label="Fold the conversation away"
          >
            »
          </button>
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
            stuck.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
          }}
          className="thread"
          ref={list}
          aria-live="polite"
          aria-relevant="additions"
        >
          {thread.length === 0 && waiting.length === 0 && (
            <li className="t-empty">
              <p>
                {run
                  ? "Ask while it works. Nothing you write here stops the marking."
                  : "I work from the Grade 10 ATPs, the programme of assessment and your memos. Ask, or give me something to prepare."}
              </p>
            </li>
          )}
          {thread.map((item) => (
            <Line key={`${item.kind}-${item.seq}`} item={item} talk={talk} />
          ))}
          {waiting.map((o) => (
            <li key={`out-${o.key}`} className={`t-me ${o.status}`}>
              <p>{o.text}</p>
              {o.status === "sending" && <span className="meta">Sending…</span>}
              {o.status === "failed" && (
                <span className="meta fail" role="alert">
                  {o.reason}{" "}
                  <button type="button" className="linkish" onClick={() => talk.retry(o)}>
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
          {thread.every((i) => i.kind === "update") && waiting.length === 0 && !ended && (
            <div className="suggest" role="group" aria-label="Things you can ask">
              {suggestions.map((s) => (
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
                  : run
                    ? "Ask how far it is, or about a learner's mark"
                    : "Ask about the ATP or the PoA, or ask for a lesson plan"
              }
            />
            {!ended && (
              <Suspense fallback={null}>
                <VoiceControl ask={talk.ask} />
              </Suspense>
            )}
            <Button type="submit" variant="primary" disabled={ended || !text.trim()}>
              Send
            </Button>
          </div>
        </form>
      </aside>
    </>
  );
}

function sinceStart(at: string, start: string | null): string {
  return clock(start ? Math.max(0, (Date.parse(at) - Date.parse(start)) / 1000) : 0);
}

function Line({ item, talk }: { item: ThreadItem; talk: Talk }) {
  const at = <time dateTime={item.at}>{sinceStart(item.at, talk.start)}</time>;
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
      {item.promise && !item.card && <p className="promise">I'll come back to you about {item.promise} here.</p>}
      {item.action && (
        <button type="button" className="linkish open-again" onClick={() => item.action && openOnScreen(item.action)}>
          {actionLabel(item.action)}
        </button>
      )}
      {item.card && talk.run && (
        <NoteCard
          card={item.card}
          run={talk.run}
          sentAt={talk.sent.get(item.card.script_id) ?? null}
          start={talk.start}
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
    <section className="note-card" aria-label={`${card.script_id}'s feedback note`}>
      <header>
        <span className="pseudo">{card.script_id}</span>
        <span className="name">{card.learner}</span>
        <span className={`state ${done ? "sent" : ""}`}>{done ? "Released" : "Ready to release"}</span>
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
          Released at {sinceStart(sentAt, start)}. Its link works now; the rest of the set waits for your approval.
        </p>
      ) : (
        <div className="actions">
          <Button variant="primary" size="small" disabled={send.isPending || waiting} onClick={() => send.mutate()}>
            {send.isPending || waiting ? "Releasing…" : `Approve and release ${card.learner}'s note`}
          </Button>
          <span className="hint">Only this note is released. The rest of the set waits for your approval.</span>
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
