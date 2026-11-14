// The goal band and the loop strip (console-ui.md §14): the teacher's goal verbatim, then the
// pipeline as eleven stations on one rule, a brief (a teal slip) at the live station, and a
// correction drawn in red when a Brief has been sent back. Everything here is read from the
// RunView and the event list; the component decides nothing.
import type { AgentRole, AssessmentState, MothusiEvent } from "../api/types";
import { clock } from "../lib/utils";
import type { CardRole, RunView } from "../run/reducer";

export type Station = CardRole | "teacher";

export const STATIONS: { id: Station; label: string; initials: string; ink: string }[] = [
  { id: "planner", label: "Planner", initials: "PL", ink: "planner" },
  { id: "setter", label: "Setter", initials: "ST", ink: "" },
  { id: "marker", label: "Marker", initials: "MK", ink: "marker" },
  { id: "code", label: "Code", initials: "CD", ink: "code" },
  { id: "moderator", label: "Moderator", initials: "MD", ink: "moderator" },
  { id: "analyst", label: "Analyst", initials: "AN", ink: "" },
  { id: "support", label: "Support", initials: "SU", ink: "" },
  { id: "writer", label: "Writer", initials: "WR", ink: "" },
  { id: "reviewer", label: "Reviewer", initials: "RV", ink: "" },
  { id: "teacher", label: "You", initials: "YOU", ink: "teacher" },
  { id: "executor", label: "Executor", initials: "EX", ink: "" },
];

const BY_STATE: Partial<Record<AssessmentState, Station>> = {
  draft: "planner",
  reading_check: "teacher",
  memo_check: "setter",
  marking: "marker",
  moderating: "moderator",
  re_marking: "marker",
  analysing: "analyst",
  writing: "writer",
  reviewing: "reviewer",
  rejected: "writer",
  awaiting_approval: "teacher",
  approved: "executor",
  delivered: "executor",
};

/** Who sends work back to whom: the rules engine to the markers, the Reviewer to the Writer,
 *  the Setter's re-read to itself (the memo changed under it). */
const SENT_BACK_BY: Partial<Record<AgentRole, Station>> = {
  marker: "code",
  moderator: "code",
  writer: "reviewer",
  setter: "planner",
};

export interface Correction {
  from: Station;
  to: AgentRole;
  attempt: number;
  scriptId: string | null;
}

/** The latest Brief that is a retry (attempt > 1) and has no result yet: work sent back, in flight. */
export function correctionInFlight(events: MothusiEvent[]): Correction | null {
  const answered = new Set(events.flatMap((e) => (e.kind === "result" ? [e.payload.brief_id] : [])));
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (e?.kind !== "brief") continue;
    if (answered.has(e.payload.brief_id)) return null;
    if (e.payload.attempt > 1)
      return { from: SENT_BACK_BY[e.payload.agent] ?? "planner", to: e.payload.agent, attempt: e.payload.attempt, scriptId: e.payload.script_id };
    return null;
  }
  return null;
}

/** Briefs that were retries: the corrections the colleague made on its own. */
export const corrections = (events: MothusiEvent[]) =>
  events.filter((e) => e.kind === "brief" && e.payload.attempt > 1).length;

/** Intake is code the Planner runs (reading uploaded pages, console-ui.md §11): it works under the
 *  Planner's tile rather than having one of its own. */
const UNDER: Partial<Record<CardRole, Station>> = { intake: "planner" };

/** The card whose Brief is open at a station: the Planner's tile answers for intake too. */
export function liveCard(run: RunView, station: Station) {
  if (station === "planner") {
    const intake = run.agents.get("intake");
    if (intake?.live) return intake;
  }
  return station === "teacher" || station === "code" ? undefined : run.agents.get(station);
}

/** Where the brief is: with the teacher when a question is open, else the agent with an open Brief
 *  (the most recently briefed if several), else the station the state implies. */
export function liveStation(run: RunView): Station {
  if (run.pending) return "teacher";
  if (run.halted) return "planner";
  for (let i = run.events.length - 1; i >= 0; i--) {
    const e = run.events[i];
    if (e?.kind === "brief" && run.agents.get(e.payload.agent)?.live) return UNDER[e.payload.agent] ?? e.payload.agent;
  }
  return BY_STATE[run.state] ?? "planner";
}

function statusOf(run: RunView, id: Station, live: Station): string {
  if (id === "code") {
    const c = run.checks;
    return c.run ? `${c.run} checks · ${c.failed} failed` : "checks every mark";
  }
  if (id === "teacher") {
    if (run.pending) return "needs you";
    return run.teacherSteps ? `${run.teacherSteps} ${run.teacherSteps === 1 ? "decision" : "decisions"}` : "approves the bundle";
  }
  if (id === "executor") return run.delivered ? "delivered" : run.state === "approved" ? "writing the bundle" : "after your approval";
  const card = liveCard(run, id) ?? run.agents.get(id);
  if (!card) return id === "planner" ? "plans the work" : "waiting";
  if (card.live || id === live) return card.task || "working";
  if (id === "planner") return card.lastSummary || "deciding what's next";
  return card.lastSummary ? `done · ${card.lastSummary}` : "done";
}

const visitedAll = (run: RunView): Set<Station> => {
  const seen = new Set<Station>(["planner"]);
  for (const [role, card] of run.agents) if (card.briefs > 0 || card.lastSummary) seen.add(role);
  if (run.teacherSteps > 0) seen.add("teacher");
  if (run.delivered) seen.add("executor");
  return seen;
};

export function GoalBand({ run }: { run: RunView }) {
  return (
    <section className="panel goal" aria-label="The goal">
      <div>
        <div className="label">Goal</div>
        <q>{run.created?.goal ?? "Loading the run…"}</q>
      </div>
      <div className="meters">
        <div>
          <span className="kpi-label">Time</span>
          <span className="kpi">{clock(run.elapsedS)}</span>
        </div>
        <div>
          <span className="kpi-label">Your decisions</span>
          <span className="kpi">{run.teacherSteps}</span>
        </div>
      </div>
    </section>
  );
}

export function LoopStrip({ run }: { run: RunView }) {
  const live = liveStation(run);
  const liveIdx = STATIONS.findIndex((s) => s.id === live);
  const seen = visitedAll(run);
  const fix = correctionInFlight(run.events);
  const n = STATIONS.length;
  // The slip sits on the rule just before the live tile: it has arrived there, not vanished under it.
  const left = `${(Math.max(liveIdx, 0.5) * 100) / n}%`;
  return (
    <section className="panel loop" aria-label="The loop">
      <div className="stations">
        <div className="rule" aria-hidden="true" />
        <div className={`packet ${fix ? "red" : ""}`} style={{ left }} aria-hidden="true" />
        {STATIONS.map((s) => {
          const isLive = s.id === live;
          const cls = ["station", s.ink, isLive ? "live" : seen.has(s.id) ? "done" : "idle"].join(" ");
          return (
            <div key={s.id} className={cls} aria-current={isLive ? "step" : undefined}>
              <span className="tile" aria-hidden="true">
                {s.initials}
              </span>
              <span className="name">{s.label}</span>
              <span className="status">{statusOf(run, s.id, live)}</span>
            </div>
          );
        })}
        {fix && <CorrectionLine fix={fix} />}
      </div>
      <div className="counters">
        <span>
          <b>{run.settled}</b> of {run.cells.size} scripts settled
        </span>
        <span>
          <b>{corrections(run.events)}</b> {corrections(run.events) === 1 ? "correction" : "corrections"}
        </span>
        <span>
          <b>{run.checks.run}</b> checks
        </span>
        <span>
          <b>{run.teacherSteps}</b> {run.teacherSteps === 1 ? "decision" : "decisions"} of yours
        </span>
      </div>
    </section>
  );
}

function CorrectionLine({ fix }: { fix: Correction }) {
  const to = STATIONS.findIndex((s) => s.id === fix.to) + 1;
  const from = STATIONS.findIndex((s) => s.id === fix.from) + 1;
  const [a, b] = to <= from ? [to, from] : [from, to];
  return (
    <div className="correction" style={{ gridColumn: `${a} / ${b + 1}` }} role="status">
      sent back{fix.scriptId ? ` · ${fix.scriptId}` : ""} · attempt {fix.attempt} of 3
    </div>
  );
}
