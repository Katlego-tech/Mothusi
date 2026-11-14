import { useParams } from "react-router";
import { useEventStream } from "../api/events";
import type { MothusiEvent } from "../api/types";
import { agentLabel, inkOf, TopBar } from "../components/chrome";
import { WithColleague } from "../components/colleague";
import type { CardRole } from "../run/reducer";

const LANES: CardRole[] = ["planner", "setter", "marker", "moderator", "code", "analyst", "support", "writer", "reviewer", "executor"];

function outputRole(event: MothusiEvent): CardRole | null {
  if (["teacher", "message", "reply", "note_sent", "usage"].includes(event.kind)) return null;
  if (event.kind === "check") return "code";
  if (event.kind === "brief" || event.kind === "result") return event.payload.agent;
  return event.agent ?? "planner";
}

function status(event: MothusiEvent): string {
  if (event.kind === "result") return event.payload.status;
  if (event.kind === "check") return event.payload.passed ? "passed" : "failed";
  if (event.kind === "brief") return "started";
  if (event.kind === "needs_teacher") return "waiting for teacher";
  if (event.kind === "state") return event.payload.to;
  if (event.kind === "disagreement") return "disagreement";
  return "recorded";
}

export function StreamScreen() {
  const { id = "" } = useParams();
  return <RunStream key={id} id={id} />;
}

function RunStream({ id }: { id: string }) {
  const { run, connection } = useEventStream(id);
  const lanes = new Map<CardRole, MothusiEvent[]>(LANES.map((role) => [role, []]));
  for (const event of run.events) {
    const role = outputRole(event);
    if (role) {
      const entries = lanes.get(role) ?? [];
      entries.push(event);
      lanes.set(role, entries);
    }
  }
  return <WithColleague run={run}>
    <TopBar title={run.title || "Agent output"} subtitle="Agent output · newest first" />
    <main className="canvas min-h-0 min-w-0 flex-1 overflow-hidden!">
      <p role="status" className="small muted">{connection === "live" ? "Live" : connection === "connecting" ? "Connecting…" : connection === "reconnecting" ? "Reconnecting… The run continues; output will catch up." : "Closed · showing recorded output"}</p>
      {run.halted && <p className="notice warn" role="alert">The run stopped: {run.halted}</p>}
      <div className="flex min-h-0 flex-1 gap-4 overflow-x-auto pb-2" tabIndex={0} aria-label="Agent output lanes">
        {[...lanes].map(([role, events]) => <section key={role} aria-label={`${agentLabel(role)} output`} className="panel flex min-h-0 w-[min(20rem,80vw)] shrink-0 flex-col">
          <div className="panel-head shrink-0 pb-3"><h2 className={`who ${inkOf(role)}`}>{agentLabel(role)}</h2><span className="small muted">{events.length}</span></div>
          <ol tabIndex={0} aria-label={`${agentLabel(role)} entries, newest first`} aria-live="polite" className="min-h-0 flex-1 list-none overflow-y-auto overscroll-contain p-0">
            {events.length === 0 && <li className="p-4 small muted">No output yet</li>}
            {[...events].sort((a, b) => b.seq - a.seq).map((event) => <li key={event.seq} data-seq={event.seq} className="border-t border-rule p-4 break-words">
              <time dateTime={event.at} className="small muted">{new Date(event.at).toLocaleTimeString("en-ZA", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</time>
              <p><span className={`tag ${event.kind === "check" ? event.payload.passed ? "pass" : "fail" : ""}`}>{event.kind} · {status(event)}</span></p>
              <p className="mt-2 whitespace-pre-wrap">{event.kind === "brief" ? event.payload.task : event.kind === "result" ? event.payload.summary : event.summary}</p>
              {event.kind === "result" && event.payload.reason && <p className="small muted mt-2">{event.payload.reason}</p>}
              {event.kind === "check" && <p className="small muted mt-2">{event.payload.detail}</p>}
            </li>)}
          </ol>
        </section>)}
      </div>
    </main>
  </WithColleague>;
}
