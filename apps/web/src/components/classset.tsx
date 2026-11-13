// The class set (console-ui.md §2 signature): one cell per learner that fills with the Marker's
// red total and gets the Moderator's green tick; a click opens the script in the inspector.
import type { Question } from "../api/types";
import type { RunView, ScriptCell } from "../run/reducer";

function Cell({ cell, selected, onSelect }: { cell: ScriptCell; selected: boolean; onSelect: () => void }) {
  const status = cell.disputed && cell.status === "marked" ? "working" : cell.status;
  const cls = ["cell", status === "marked" ? "" : status, selected ? "selected" : ""].join(" ");
  const label =
    cell.total === null ? `${cell.scriptId}, not marked yet` : `${cell.scriptId}, ${cell.total} of ${cell.maxMarks}`;
  return (
    <button type="button" className={`${cls} text-left`} onClick={onSelect} aria-pressed={selected} aria-label={label}>
      <span className="id">{cell.scriptId}</span>
      {cell.moderated && !cell.disputed && <span className="ink green tick">✓</span>}
      {status === "working" || status === "retry" ? (
        <span className="ink red">?</span>
      ) : cell.total !== null ? (
        <span className={`ink ${cell.status === "teacher" ? "teal" : "red"}`}>{cell.total}</span>
      ) : null}
    </button>
  );
}

export function ClassSet({
  run,
  selected,
  onSelect,
}: {
  run: RunView;
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const cells = [...run.cells.values()];
  return (
    <aside className="panel set">
      <div className="panel-head">
        <h2>The class set</h2>
        <span className="small muted">
          {run.settled} of {cells.length} settled
        </span>
      </div>
      <div className="grid">
        {cells.map((cell) => (
          <Cell key={cell.scriptId} cell={cell} selected={cell.scriptId === selected} onSelect={() => onSelect(cell.scriptId)} />
        ))}
      </div>
      <div className="key">
        <span>
          <span className="ink red">27</span> Marker's total
        </span>
        <span>
          <span className="ink green">✓</span> moderated, agreed
        </span>
        <span>
          <span className="swatch working" /> being marked
        </span>
        <span>
          <span className="swatch teacher" /> with you
        </span>
      </div>
      {selected && <ScriptInspector run={run} scriptId={selected} />}
    </aside>
  );
}

export function ScriptInspector({ run, scriptId }: { run: RunView; scriptId: string }) {
  const marks = run.scripts.get(scriptId);
  const cell = run.cells.get(scriptId);
  const questions: Question[] = run.created?.questions ?? [];
  const sampled = cell?.moderated ? "in the moderation sample" : "not in the moderation sample";
  return (
    <div className="inspector" aria-labelledby="insp-h">
      <div className="panel-head" style={{ borderTop: "1px solid var(--border)" }}>
        <h2 id="insp-h">{scriptId}</h2>
        <span className="small muted">{marks ? sampled : "not marked yet"}</span>
      </div>
      {marks ? (
        <>
          <div className="marks">
            <span className="ink red" style={{ fontSize: 28 }}>
              {marks.reduce((s, m) => s + m.awarded, 0)}
            </span>
            <span className="of">of {run.totalMarks} · every mark quotes the learner's own words</span>
          </div>
          {questions.map((q) => {
            const m = marks.find((x) => x.question_id === q.question_id);
            return (
              <div className="qrow" key={q.question_id}>
                <span className="qid">{q.question_id}</span>
                <span className="mk">
                  {m?.awarded ?? 0}
                  <span className="of"> /{q.max_marks}</span>
                </span>
                <span className="quote" title={m?.evidence_quote || m?.principle}>
                  {m?.evidence_quote ? <mark>{m.evidence_quote}</mark> : (m?.principle ?? "")}
                </span>
              </div>
            );
          })}
        </>
      ) : (
        <p className="small muted" style={{ padding: "0 var(--card-pad) var(--card-pad)" }}>
          This script has not been marked yet.
        </p>
      )}
    </div>
  );
}
