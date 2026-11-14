// The top bar, the run header (ring + finding + meters), the agents strip and the state rail
// (console-ui.md §7; 02-live-run.png).
import type { AgentRole, AssessmentState } from "../api/types";
import { clock, stateName } from "../lib/utils";
import type { CardRole, RunView } from "../run/reducer";

const STEPS = ["Set the task", "Live run", "Approval"];

export function TopBar({
  title,
  subtitle,
  step,
  chip,
}: {
  title: string;
  subtitle: string;
  step?: 0 | 1 | 2 | 3; // left out on pages outside a run's steps (the desk)
  chip?: { text: string; kind?: "wait" | "done" } | undefined;
}) {
  return (
    <header className="topbar">
      <div className="wordmark">
        Mothusi<span>.</span>
      </div>
      <div className="run-title">
        <strong>{title}</strong>
        <span className="muted">{subtitle}</span>
      </div>
      {step !== undefined && (
        <ol className="steps">
          {STEPS.map((name, i) => (
            <li key={name} className={i < step ? "done" : i === step ? "now" : undefined}>
              {name}
            </li>
          ))}
        </ol>
      )}
      {chip && <span className={`chip ${chip.kind ?? ""}`}>{chip.text}</span>}
    </header>
  );
}

export function Ring({ share, green = false }: { share: number; green?: boolean }) {
  const c = 2 * Math.PI * 24;
  return (
    <svg className="progress-ring" viewBox="0 0 56 56" aria-hidden="true">
      <circle className="track" cx="28" cy="28" r="24" />
      <circle
        className={`fill ${green ? "green" : ""}`}
        cx="28"
        cy="28"
        r="24"
        strokeDasharray={`${(c * Math.min(1, share)).toFixed(1)} ${c.toFixed(1)}`}
      />
    </svg>
  );
}

export function RunHeader({ run }: { run: RunView }) {
  const n = run.cells.size;
  return (
    <div className="runhead">
      <div className="panel progress">
        <Ring share={n ? run.settled / n : 0} green={run.state === "delivered"} />
        <div>
          <h1>
            {run.settled} of {n} scripts settled
          </h1>
          <p>{run.finding ? <Finding text={run.finding} /> : "Mothusi is working through the class set."}</p>
        </div>
      </div>
      <div className="panel strip">
        <div>
          <span className="kpi-label">Time</span>
          <span className="kpi">{clock(run.elapsedS)}</span>
        </div>
        <div>
          <span className="kpi-label">Your steps</span>
          <span className="kpi">{run.teacherSteps}</span>
        </div>
      </div>
    </div>
  );
}

/** "L-021, question 4: the Marker gave …": the subject in bold, as the mockup sets it. */
function Finding({ text }: { text: string }) {
  const cut = text.indexOf(": ");
  if (cut < 0 || cut > 40) return <>{text}</>;
  return (
    <>
      <b>{text.slice(0, cut + 1)}</b>
      {text.slice(cut + 1)}
    </>
  );
}

const LABEL: Record<CardRole, string> = {
  planner: "Planner",
  intake: "Intake",
  setter: "Setter",
  marker: "Marker",
  moderator: "Moderator",
  analyst: "Analyst",
  support: "Support",
  writer: "Writer",
  reviewer: "Reviewer",
  executor: "Executor",
  code: "Code",
};

export const agentLabel = (role: CardRole | null) => (role ? LABEL[role] : "Planner");

/** The ink class of a role's dot: red Marker, green Moderator, teal Planner, grey code. */
export const inkOf = (role: CardRole | null): string =>
  role === "marker" ? "marker" : role === "moderator" ? "moderator" : role === "code" ? "code" : role === "planner" || role === null ? "planner" : "";

const ORDER: AgentRole[] = ["setter", "marker", "moderator", "analyst", "support", "writer", "reviewer", "executor"];

export function AgentStrip({ run }: { run: RunView }) {
  const seen = [...run.agents.values()];
  const waiting = ORDER.filter((r) => !run.agents.has(r)).map((r) => LABEL[r]);
  return (
    <div className="panel agents" aria-label="Who is doing what">
      {seen.map((a) => (
        <div key={a.role}>
          <span className={`who ${inkOf(a.role)} ${a.live ? "live" : ""}`}>{LABEL[a.role]}</span>
          <span className="what">
            {a.role === "code" ? (
              <>
                <b>{run.checks.run} checks</b> · {run.checks.failed} failed · {run.checks.open} open
              </>
            ) : a.live ? (
              <>
                <b className="busy">{a.task}</b> · {a.briefs} {a.briefs === 1 ? "brief" : "briefs"} so far
              </>
            ) : (
              <>
                <b>Done.</b> {a.lastSummary}
              </>
            )}
          </span>
        </div>
      ))}
      {waiting.length > 0 && (
        <div className="idle">
          <span className="who">Waiting</span>
          <span className="what">{waiting.join(" · ")}</span>
        </div>
      )}
    </div>
  );
}

const RAIL: { state: AssessmentState; branch?: boolean }[] = [
  { state: "draft" },
  { state: "reading_check", branch: true },
  { state: "memo_check" },
  { state: "marking" },
  { state: "moderating" },
  { state: "re_marking", branch: true },
  { state: "analysing" },
  { state: "writing" },
  { state: "reviewing" },
  { state: "rejected", branch: true },
  { state: "awaiting_approval" },
  { state: "approved" },
  { state: "delivered" },
];

export function StateRail({ run }: { run: RunView }) {
  const visited = new Set<AssessmentState>(["draft"]);
  for (const e of run.events) if (e.kind === "state") visited.add(e.payload.to);
  return (
    <aside className="rail">
      <h2>Where it is</h2>
      <ol className="states">
        {RAIL.filter((r) => r.state !== "rejected" || visited.has("rejected")).map((r) => {
          const now = r.state === run.state;
          const cls = [now ? "now" : visited.has(r.state) ? "done" : "", r.branch ? "branch" : ""].join(" ").trim();
          return (
            <li key={r.state} className={cls || undefined} aria-current={now ? "step" : undefined}>
              {stateName(r.state)}
            </li>
          );
        })}
      </ol>
    </aside>
  );
}
