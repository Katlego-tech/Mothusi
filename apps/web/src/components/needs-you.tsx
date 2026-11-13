// The "Needs you" queue (console-ui.md: the queue, drawn to 09-needs-you-queue.png). While marking
// runs, every flagged script waits here; the teacher answers it with the existing script form and
// the agent keeps marking. The list itself is the reducer's (run.needsYou); this component only adds
// what one browser knows and the event stream doesn't yet: an answer being sent, accepted, or refused.
import { lazy, Suspense, useState } from "react";
import { needsYouAnswered, needsYouFailed, needsYouSending, type RunView } from "../run/reducer";

const ScriptDecisionForm = lazy(() => import("./ask").then((m) => ({ default: m.ScriptDecisionForm })));

type Local = { kind: "sending" } | { kind: "answered" } | { kind: "error"; message: string };

/** The run as this browser sees it: the reducer's list with local sends, answers and refusals applied. */
export function withLocal(run: RunView, local: Record<string, Local>): RunView {
  let view = run;
  for (const [scriptId, l] of Object.entries(local)) {
    if (l.kind === "sending") view = needsYouSending(view, scriptId);
    else if (l.kind === "answered") view = needsYouAnswered(view, scriptId);
    else view = needsYouFailed(view, scriptId, l.message);
  }
  return view;
}

export function NeedsYouQueue({ run }: { run: RunView }) {
  const [local, setLocal] = useState<Record<string, Local>>({});
  const [open, setOpen] = useState<string | null>(null);
  const items = withLocal(run, local).needsYou;
  if (run.state !== "marking" || items.length === 0) return null;
  const mark = (scriptId: string, l: Local) => setLocal((prev) => ({ ...prev, [scriptId]: l }));
  return (
    <aside className="panel needs-you" aria-label="Needs you">
      <div className="panel-head">
        <h2>Needs you</h2>
        <span className="count" aria-label={`${items.length} scripts need you`}>
          {items.length}
        </span>
      </div>
      <p className="compose muted needs-you-hint">Answer while marking runs. Each answer is used at the next batch.</p>
      <ul className="needs-you-list">
        {items.map((item) => {
          const isOpen = open === item.scriptId;
          return (
            <li key={item.scriptId} className={`needs-you-item ${isOpen ? "open" : ""}`}>
              <button
                type="button"
                className="needs-you-row"
                aria-expanded={isOpen}
                onClick={() => setOpen(isOpen ? null : item.scriptId)}
              >
                <span className="sid">{item.scriptId}</span>
                <span className="why">{item.question.title}</span>
              </button>
              {item.status === "error" && !isOpen && (
                <p className="needs-you-error" role="alert">
                  {item.error}
                </p>
              )}
              {isOpen && item.question.topic === "script" && (
                <Suspense fallback={<p className="compose muted">Opening the script…</p>}>
                  <ScriptDecisionForm
                    id={run.assessmentId}
                    q={item.question}
                    questions={run.created?.questions ?? []}
                    onSend={() => mark(item.scriptId, { kind: "sending" })}
                    onResult={(result) => {
                      if (result.ok) {
                        mark(item.scriptId, { kind: "answered" });
                        setOpen(null);
                      } else {
                        mark(item.scriptId, { kind: "error", message: result.message });
                      }
                    }}
                  />
                </Suspense>
              )}
            </li>
          );
        })}
      </ul>
      <p className="compose muted needs-you-foot">When marking ends, anything still here is asked one at a time.</p>
    </aside>
  );
}
