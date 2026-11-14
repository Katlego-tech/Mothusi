// The live log (console-ui.md §7 EventLog): one row component per event kind. Passed checks are
// folded into the row of the result they checked, as tags; a failed check is a row of its own.
import { useEffect, useRef, useState, type ReactNode } from "react";

import type { CheckPayload, MothusiEvent } from "../api/types";
import { clock, stateName } from "../lib/utils";
import { agentLabel, inkOf } from "./chrome";
import { EvidencePair } from "./papers";

type Row = { event: MothusiEvent; passed: CheckPayload[]; group: CheckPayload[] };

/** The log's rows. Nothing is dropped, only folded: a brief whose result has arrived is shown by
 *  that result; passed checks on a result's script become its tags; a run of passed checks of one
 *  kind is one row; the teacher's question is the card at the foot. */
export function rows(events: MothusiEvent[]): Row[] {
  const answered = new Set(events.flatMap((e) => (e.kind === "result" ? [e.payload.brief_id] : [])));
  const out: Row[] = [];
  for (const event of events) {
    if (event.kind === "needs_teacher") continue;
    if (event.kind === "brief" && answered.has(event.payload.brief_id)) continue;
    const last = out[out.length - 1];
    if (event.kind === "check" && event.payload.passed && last) {
      if (last.event.kind === "result" && last.event.payload.script_id === event.payload.about) {
        last.passed.push(event.payload);
        continue;
      }
      if (last.event.kind === "check" && last.event.payload.passed && last.event.payload.check === event.payload.check) {
        last.group.push(event.payload);
        continue;
      }
    }
    out.push({ event, passed: [], group: [] });
  }
  return out;
}

function Ev({
  event,
  start,
  className,
  who,
  ink,
  children,
}: {
  event: MothusiEvent;
  start: string | null;
  className?: string;
  who: string;
  ink: string;
  children: ReactNode;
}) {
  const t = start ? (Date.parse(event.at) - Date.parse(start)) / 1000 : 0;
  return (
    <div className={`ev ${className ?? ""}`} data-seq={event.seq}>
      <time dateTime={event.at}>{clock(t)}</time>
      <span className={`who ${ink}`}>{who}</span>
      <div className="body">{children}</div>
    </div>
  );
}

function Body({ row }: { row: Row }): ReactNode {
  const { event } = row;
  switch (event.kind) {
    case "created":
      return (
        <>
          <span>Started: {event.payload.title}</span>
          <span className="detail">"{event.payload.goal}"</span>
        </>
      );
    case "state":
      return <>{`${event.payload.reason}; ${stateName(event.payload.to)}`}</>;
    case "brief":
      return (
        <>
          <span>{event.payload.task}</span>
          <span className="detail">
            brief {event.payload.brief_id.split("/").slice(1).join("/")} · attempt {event.payload.attempt}
          </span>
        </>
      );
    case "result": {
      const p = event.payload;
      const marked = p.awarded !== null && p.max_marks !== null && p.status === "done";
      return (
        <>
          {marked ? (
            <span className="marks">
              {p.script_id} {p.agent === "moderator" ? "re-marked blind" : "marked"}{" "}
              <span className={`ink ${p.agent === "moderator" ? "green" : "red"}`}>{p.awarded}</span>
              <span className="of">of {p.max_marks}</span>
            </span>
          ) : (
            <span>{p.summary}</span>
          )}
          {p.status !== "done" && <span className="detail">{p.reason}</span>}
          {row.passed.length > 0 && (
            <span className="tags">
              {row.passed.map((c) => (
                <span key={c.check} className="tag pass">
                  {c.check.replace("_", " ")}
                </span>
              ))}
            </span>
          )}
        </>
      );
    }
    case "check": {
      const all = [event.payload, ...row.group];
      return (
        <>
          <span>
            <span className={`tag ${event.payload.passed ? "pass" : "fail"}`}>{event.payload.passed ? "PASS" : "FAIL"}</span>{" "}
            {event.payload.check.replace("_", " ")} check
            {all.length > 1 ? ` on ${all.length}` : ` on ${event.payload.about}`}
          </span>
          <span className="detail">
            {all.length > 1 ? all.map((c) => c.about.split("/").slice(-3, -2)[0] ?? c.about).join(" · ") + `: ${event.payload.detail}` : event.payload.detail}
          </span>
        </>
      );
    }
    case "decision":
      return (
        <>
          <span>{sentence(event.payload.action)}</span>
          <span className="detail">{event.payload.reason}</span>
        </>
      );
    case "disagreement": {
      const d = event.payload;
      return (
        <>
          <span>
            {d.script_id}, question {d.question_id}: the marks differ
          </span>
          <div className="marks">
            <span>
              <span className="ink red">{d.first.awarded}</span>
              <span className="of"> of {d.max_marks} Marker</span>
            </span>
            <span>
              <span className="ink green">{d.second.awarded}</span>
              <span className="of"> of {d.max_marks} Moderator</span>
            </span>
          </div>
          <EvidencePair d={d} />
        </>
      );
    }
    case "usage":
      return <span>{event.payload.input_tokens + event.payload.output_tokens} tokens processed</span>;
    case "teacher":
      return <span>{event.summary}</span>;
    case "delivered":
      return (
        <>
          <span>
            Wrote {event.payload.written.length} files and sent {event.payload.sent.length}{" "}
            {event.payload.sent.length === 1 ? "message" : "messages"}
          </span>
          <span className="detail">the Executor's brief carried your approval; it is never briefed without one</span>
        </>
      );
    default:
      return <span>{event.summary}</span>;
  }
}

const sentence = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

function rowClass(row: Row): string {
  const e = row.event;
  if (e.kind === "state") return "state";
  if (e.kind === "check" && !e.payload.passed) return "check-fail";
  if (e.kind === "result" && e.payload.status !== "done") return "check-fail";
  if (e.kind === "decision") return e.payload.action === "intake" ? "" : "decision";
  if (e.kind === "disagreement") return "disagree";
  if (e.kind === "teacher") return "teacher";
  return "";
}

function whoOf(row: Row): { who: string; ink: string } {
  const e = row.event;
  if (e.kind === "teacher") return { who: "You", ink: "teacher" };
  if (e.kind === "check") return { who: "Code", ink: "code" };
  if (e.kind === "decision" && e.payload.action === "intake") return { who: "Intake", ink: "code" };
  if (e.kind === "state" || e.kind === "decision" || e.kind === "created") return { who: "Planner", ink: "planner" };
  return { who: agentLabel(e.agent), ink: inkOf(e.agent) };
}

export function EventLog({
  events,
  start,
  title = "What Mothusi is doing",
  note = "newest at the bottom · follows live",
  footer,
}: {
  events: MothusiEvent[];
  start: string | null;
  title?: string;
  note?: string;
  footer?: ReactNode;
}) {
  // Rows that arrive after the first paint rise in (motion table); a reload's replay does not.
  const lastSeq = events[events.length - 1]?.seq ?? -1;
  const [settled, setSettled] = useState<number | null>(null);
  useEffect(() => {
    if (settled !== null) return;
    const timer = setTimeout(() => setSettled(lastSeq), 400);
    return () => clearTimeout(timer);
  }, [lastSeq, settled]);
  // The body scrolls inside the panel and follows the newest row while the reader is at the
  // bottom; scrolling up to read stops the following until they scroll back down.
  const body = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  useEffect(() => {
    const el = body.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [lastSeq]);
  return (
    <main className="panel log" aria-live="polite">
      <div className="panel-head">
        <h2>{title}</h2>
        <span className="small muted">{note}</span>
      </div>
      <div
        className="log-body"
        ref={body}
        tabIndex={0}
        aria-label="The log, newest at the bottom"
        onScroll={(e) => {
          const el = e.currentTarget;
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
        }}
      >
      {rows(events).map((row) => {
        const { who, ink } = whoOf(row);
        const fresh = settled !== null && row.event.seq > settled;
        return (
          <Ev key={row.event.seq} event={row.event} start={start} className={`${rowClass(row)} ${fresh ? "arrive" : ""}`} who={who} ink={ink}>
            <Body row={row} />
          </Ev>
        );
      })}
      </div>
      {footer}
    </main>
  );
}
