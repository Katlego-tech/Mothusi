// The live run as a colleague at work (console-ui.md §14): the goal, the loop with the brief
// moving between stations, then who is working now, the feed, and the class set once marking has
// begun. The screen follows the stream and is assembled from the run's state; it decides nothing.
import { useQuery } from "@tanstack/react-query";
import { NeedsYouQueue } from "../components/needs-you";
import { lazy, Suspense, useState } from "react";

import { api } from "../api/client";
import type { Connection } from "../api/events";
import { TopBar } from "../components/chrome";
import { EventLog } from "../components/log";
import { GoalBand, LoopStrip } from "../components/loop";
import { NowPanel } from "../components/now";
import { stateName } from "../lib/utils";
import type { RunView } from "../run/reducer";

// The class set is fetched the first time a run reaches marking (§14.2).
const ClassSet = lazy(() => import("../components/classset").then((m) => ({ default: m.ClassSet })));

export function runSubtitle(run: RunView, weightPercent?: string): string {
  const c = run.created;
  if (!c) return "";
  const weight = weightPercent ? `, ${Number(weightPercent)}% of the SBA mark` : "";
  return `${c.script_ids.length} scripts · DBE CAPS ${c.subject} Grade ${c.grade} · ${c.total_marks} marks${weight}`;
}

/** The task's SBA weight, from the pack the run was created with (GET /packs). */
function useTaskWeight(run: RunView): string | undefined {
  const packs = useQuery({ queryKey: ["packs"], queryFn: api.packs, staleTime: Infinity });
  const c = run.created;
  return packs.data?.find((p) => p.pack_id === c?.pack_id)?.tasks.find((t) => t.name === c?.poa_task)?.weight_percent;
}

const BEFORE_MARKING = new Set(["draft", "reading_check", "memo_check"]);

/** Marking has begun: a state past memo_check was entered, or a script has been briefed. */
export function markingBegun(run: RunView): boolean {
  if (run.cells.size === 0) return false;
  if (!BEFORE_MARKING.has(run.state)) return true;
  return run.events.some((e) => e.kind === "state" && !BEFORE_MARKING.has(e.payload.to));
}

export function RunScreen({ run, connection }: { run: RunView; connection: Connection }) {
  const [selected, setSelected] = useState<string | null>(null);
  const weight = useTaskWeight(run);
  const waiting = run.pending !== null;
  const withSet = markingBegun(run);
  return (
    <>
      <TopBar
        title={run.title || "Loading the run…"}
        subtitle={runSubtitle(run, weight)}
        step={1}
        chip={{ text: waiting ? "waiting for you" : stateName(run.state), kind: waiting ? "wait" : undefined }}
      />
      <div className="canvas">
        {connection === "reconnecting" && (
          <p className="notice warn" role="status">
            The live log lost its connection; reconnecting. Nothing is lost: the run continues and the log catches up.
          </p>
        )}
        {run.halted && (
          <p className="notice warn" role="alert">
            The run stopped: {run.halted}
          </p>
        )}
        <GoalBand run={run} />
        <LoopStrip run={run} />
        <div className={`agentrun ${withSet ? "" : "no-set"} ${waiting ? "asking" : ""}`}>
          <NowPanel run={run} questionKey={questionKey(run)} />
          <div className="feed">
            <EventLog events={run.events} start={run.startedAt} title="The feed" />
          </div>
          {withSet && (
            <div className="sidecol">
              <NeedsYouQueue run={run} />
              <Suspense fallback={<aside className="panel set compose muted">Opening the class set…</aside>}>
                <ClassSet run={run} selected={selected} onSelect={(id) => setSelected(id === selected ? null : id)} />
              </Suspense>
            </div>
          )}
        </div>
      </div>
    </>
  );
}

/** One card per question: a new question remounts the form, a new log event does not. */
function questionKey(run: RunView): string {
  const q = run.pending;
  if (!q) return "";
  if (q.topic === "memo") return `memo/${q.question_id}/${q.memo_answer}`;
  if (q.topic === "script") return `script/${q.stage}/${q.script_id}`;
  if (q.topic === "reading") return `reading/${q.items.length}`;
  return `budget/${q.limit_zar}`;
}
