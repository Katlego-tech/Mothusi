// A memo drafted from a typed PDF or Word file (intake.md §12; console-ui.md §13; 14-memo-draft.png).
// The Setter copied the memo's rows; the teacher checks and edits them here, and only a table that
// passes the Memo contract's own checks can be used. Nothing is marked from a draft.
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { ApiError, api } from "../api/client";
import type { DraftItem } from "../api/types";

/** The Memo contract's checks (packages/contracts Memo, MemoItem), as sentences, in table order. */
export function rowProblems(items: DraftItem[], total: number | null, levels: string[]): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  items.forEach((item, n) => {
    const name = item.question_id.trim() || `row ${n + 1}`;
    if (!item.question_id.trim()) out.push(`${name}: give the question number`);
    else if (seen.has(item.question_id.trim())) out.push(`${name} appears twice; keep one row`);
    seen.add(item.question_id.trim());
    if (!(item.max_marks > 0)) out.push(`${name}: give its marks`);
    else if (!Number.isInteger(item.max_marks)) out.push(`${name}: marks must be a whole number`);
    if (!item.answer.trim()) out.push(`${name}: type the memo's answer`);
    if (!item.level.trim() || (levels.length > 0 && !levels.includes(item.level))) out.push(`${name}: choose its level`);
  });
  if (!items.length) out.push("the memo needs at least one row");
  const sum = items.reduce((a, i) => a + (i.max_marks > 0 ? i.max_marks : 0), 0);
  if (total === null || !(total > 0)) out.push("give the paper's total");
  else if (!Number.isInteger(total)) out.push("the paper's total must be a whole number");
  else if (sum !== total) out.push(`the rows add up to ${sum}, not ${total}`);
  return out;
}

/** The confirmed table as a Memo (memo.json for POST /assessments). */
export function memoFile(items: DraftItem[], total: number, source: string): File {
  const id = source.replace(/\.[^.]+$/, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "memo";
  const memo = {
    assessment_id: id,
    total_marks: total,
    items: items.map((i) => ({ ...i, question_id: i.question_id.trim() })),
  };
  return new File([JSON.stringify(memo)], "memo.json", { type: "application/json" });
}

const blank: DraftItem = { question_id: "", question: "", max_marks: 0, answer: "", marking_notes: "", level: "" };

export function MemoDraftPanel({
  draftId,
  onUse,
  onDiscard,
}: {
  draftId: string;
  onUse: (file: File, facts: string) => void;
  onDiscard: () => void;
}) {
  const view = useQuery({
    queryKey: ["memo-draft", draftId],
    queryFn: () => api.memoDraftView(draftId),
    refetchInterval: (q) => (q.state.data?.state === "drafting" ? 2000 : false),
  });
  // The teacher's edits; until the first one, the rows and total are the draft's own.
  const [items, setItems] = useState<DraftItem[] | null>(null);
  const [totalEdit, setTotal] = useState<number | null | undefined>(undefined);
  const draft = view.data;

  if (view.error)
    return (
      <section className="memo-draft" aria-label="Memo draft">
        <p className="error" role="alert">
          {view.error instanceof ApiError ? view.error.message : "The draft could not be fetched."}
        </p>
        <button type="button" className="btn small" onClick={onDiscard}>
          Choose another memo
        </button>
      </section>
    );
  if (!draft || draft.state === "drafting")
    return (
      <section className="memo-draft" aria-label="Memo draft">
        <p role="status" className="muted">
          The Setter is copying {draft ? draft.source_name : "the memo"} into rows. A long memo takes a minute.
        </p>
      </section>
    );
  if (draft.state === "failed")
    return (
      <section className="memo-draft" aria-label="Memo draft">
        <p className="error" role="alert">
          {draft.source_name}: {draft.message}
        </p>
        <button type="button" className="btn small" onClick={onDiscard}>
          Choose another memo
        </button>
      </section>
    );

  const rows = items ?? draft.items;
  const total = totalEdit === undefined ? (draft.total_marks ?? draft.items.reduce((a, i) => a + Math.max(0, i.max_marks), 0)) : totalEdit;
  const problems = rowProblems(rows, total, draft.levels);
  const sum = rows.reduce((a, i) => a + (i.max_marks > 0 ? i.max_marks : 0), 0);
  const set = (n: number, patch: Partial<DraftItem>) => setItems(rows.map((r, i) => (i === n ? { ...r, ...patch } : r)));

  return (
    <section className="memo-draft" aria-labelledby="draft-h">
      <div className="draft-head">
        <h2 id="draft-h">Check the memo Mothusi copied from {draft.source_name}</h2>
        <p className="small muted">
          Every row was copied from your file, not worked out. Fix anything that is wrong; marking uses exactly this table.
        </p>
      </div>
      <div className="draft-scroll">
        <table className="draft-table">
          <colgroup>
            <col className="c-q" />
            <col className="c-marks" />
            <col />
            <col className="c-level" />
            <col className="c-remove" />
          </colgroup>
          <thead>
            <tr>
              <th scope="col">Question</th>
              <th scope="col">Marks</th>
              <th scope="col">Memo answer · where the marks go</th>
              <th scope="col">Level</th>
              <th scope="col">
                <span className="visually-hidden">Remove</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, n) => (
              <tr key={n}>
                <td>
                  <input
                    className="input qid"
                    aria-label={`Row ${n + 1} question number`}
                    value={r.question_id}
                    onChange={(e) => set(n, { question_id: e.target.value })}
                  />
                  {r.question && <span className="small muted q">{r.question}</span>}
                </td>
                <td>
                  <input
                    className="input num"
                    type="number"
                    min={1}
                    aria-label={`Row ${n + 1} marks`}
                    value={r.max_marks || ""}
                    onChange={(e) => set(n, { max_marks: Number(e.target.value) })}
                  />
                </td>
                <td>
                  <textarea
                    className="input"
                    rows={2}
                    aria-label={`Row ${n + 1} answer`}
                    value={r.answer}
                    onChange={(e) => set(n, { answer: e.target.value })}
                  />
                  <textarea
                    className="input small"
                    rows={2}
                    aria-label={`Row ${n + 1} marking notes`}
                    placeholder="Where each mark is earned (optional)"
                    value={r.marking_notes}
                    onChange={(e) => set(n, { marking_notes: e.target.value })}
                  />
                </td>
                <td>
                  {draft.levels.length ? (
                    <select
                      className="input"
                      aria-label={`Row ${n + 1} level`}
                      value={r.level}
                      onChange={(e) => set(n, { level: e.target.value })}
                    >
                      <option value="">Choose…</option>
                      {draft.levels.map((l) => (
                        <option key={l} value={l}>
                          {l.replace(/_/g, " ")}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input className="input" aria-label={`Row ${n + 1} level`} value={r.level} onChange={(e) => set(n, { level: e.target.value })} />
                  )}
                </td>
                <td>
                  <button
                    type="button"
                    className="btn quiet small"
                    aria-label={`Remove row ${n + 1}`}
                    onClick={() => setItems(rows.filter((_, i) => i !== n))}
                  >
                    Remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="draft-foot">
        <button type="button" className="btn quiet small" onClick={() => setItems([...rows, { ...blank }])}>
          Add a row
        </button>
        <label className="small">
          Paper total{" "}
          <input
            className="input num"
            type="number"
            min={1}
            aria-label="Paper total"
            value={total ?? ""}
            onChange={(e) => setTotal(e.target.value ? Number(e.target.value) : null)}
          />
        </label>
        <span className="small muted">rows add up to {sum}</span>
      </div>
      {problems.length > 0 && (
        <ul className="small draft-problems" aria-label="Still to fix">
          {problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
      <div className="row">
        <button
          type="button"
          className="btn primary"
          disabled={problems.length > 0}
          onClick={() => onUse(memoFile(rows, total ?? sum, draft.source_name), `${rows.length} items · ${total} marks, checked by you from ${draft.source_name}`)}
        >
          Use this memo
        </button>
        <button type="button" className="btn quiet" onClick={onDiscard}>
          Discard the draft
        </button>
      </div>
    </section>
  );
}
