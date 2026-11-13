// The now panel (console-ui.md §14): the live station, large; or the teacher's form when the run
// needs them, loaded on the first question.
import { lazy, Suspense } from "react";

import type { RunView } from "../run/reducer";
import { STATIONS, correctionInFlight, liveCard, liveStation } from "./loop";

const NeedsTeacherCard = lazy(() => import("./ask").then((m) => ({ default: m.NeedsTeacherCard })));

export function NowPanel({ run, questionKey }: { run: RunView; questionKey: string }) {
  if (run.pending) {
    return (
      <section className="panel now ask wide" aria-label="Mothusi needs you">
        <Suspense fallback={<p className="compose muted">Opening your question…</p>}>
          <NeedsTeacherCard
            key={questionKey}
            assessmentId={run.assessmentId}
            question={run.pending}
            questions={run.created?.questions ?? []}
          />
        </Suspense>
      </section>
    );
  }
  const live = liveStation(run);
  const station = STATIONS.find((s) => s.id === live) ?? { id: "planner", label: "Planner", initials: "PL", ink: "planner" };
  const card = liveCard(run, live);
  const fix = correctionInFlight(run.events);
  const briefed = card?.live ?? false;
  const task = run.halted
    ? `The run stopped: ${run.halted}`
    : run.delivered
      ? "The bundle is written and the notes are with you."
      : briefed
        ? card?.task
        : live === "planner"
          ? card?.lastSummary || "Deciding what to do next."
          : live === "executor"
            ? "Waiting for your approval before anything is written."
            : card?.lastSummary || "Waiting for a Brief.";
  const lastBrief = [...run.events].reverse().find((e) => e.kind === "brief" && e.payload.agent === (card?.role ?? live));
  return (
    <section className={`panel now ${station.ink} ${briefed ? "live" : ""}`} aria-label="Who is working now">
      <div className="head">
        <span className="tile" aria-hidden="true">
          {station.initials}
        </span>
        <div>
          <div className="label">{briefed ? "Working now" : "Now"}</div>
          <h2>{station.label}</h2>
        </div>
      </div>
      <p className="task">{task}</p>
      {card && (
        <div className="meta">
          {lastBrief?.kind === "brief" && briefed && (
            <span>
              brief <b>{lastBrief.payload.brief_id.split("/").slice(1).join("/")}</b>
            </span>
          )}
          {fix && fix.to === live && (
            <span>
              attempt <b>{fix.attempt} of 3</b>, sent back by {STATIONS.find((s) => s.id === fix.from)?.label}
            </span>
          )}
          <span>
            <b>{card.briefs}</b> {card.briefs === 1 ? "brief" : "briefs"} so far
          </span>
        </div>
      )}
    </section>
  );
}
