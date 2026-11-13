// Approve the bundle (04, 05, 09): the stat strip, the five documents in tabs, and the approval
// bar with exactly two actions, approve and reject-with-note (SPEC US8). No motion here.
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { ApiError, api } from "../api/client";
import type { Bundle } from "../api/types";
import { TopBar } from "../components/chrome";
import { Button } from "../components/ui/button";
import { Input, Textarea } from "../components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../components/ui/tabs";
import { clock, rand } from "../lib/utils";
import type { RunView } from "../run/reducer";

/** From "## Worked corrections" on: the part of the class sheet the item table does not show. */
export const correctionsOf = (md: string) => {
  const at = md.indexOf("## Worked corrections");
  return at >= 0 ? md.slice(at) : md;
};

const pct = (n: number, of: number) => (of ? `${Math.round((n / of) * 100)}%` : "—");

export function StatStrip({ b, run }: { b: Bundle; run: RunView }) {
  const s = b.summary;
  const total = b.mark_sheet.reduce((sum, r) => sum + r.raw, 0);
  const outOf = b.mark_sheet[0]?.out_of ?? run.totalMarks;
  return (
    <div className="panel strip" aria-label="Summary of the run">
      <div>
        <span className="kpi-label">Class average</span>
        <span className="kpi lead">{Math.round(Number(s.class_average_percent))}%</span>
        <span className="kpi-note">
          {s.scripts ? (total / s.scripts).toFixed(2) : "0"} of {outOf}
        </span>
      </div>
      <div>
        <span className="kpi-label">Moderated blind</span>
        <span className="kpi">
          {s.moderated} <span className="unit">of {s.scripts}</span>
        </span>
        <span className="kpi-note">the pack's moderation sample</span>
      </div>
      <div>
        <span className="kpi-label">Moderator agreed</span>
        <span className="kpi">
          {s.questions_agreed} <span className="unit">of {s.questions_compared}</span>
        </span>
        <span className="kpi-note">questions compared</span>
      </div>
      <div>
        <span className="kpi-label">Re-marked after moderation</span>
        <span className="kpi">{s.re_marked}</span>
        <span className="kpi-note">{run.disagreements.map((d) => `${d.script_id}, question ${d.question_id}`).join("; ") || "none"}</span>
      </div>
      <div>
        <span className="kpi-label">Time</span>
        <span className="kpi">{clock(s.elapsed_s)}</span>
        <span className="kpi-note">upload to bundle</span>
      </div>
      <div>
        <span className="kpi-label">Cost</span>
        <span className="kpi">{s.priced ? rand(s.cost_zar) : "not metered"}</span>
        <span className="kpi-note">{s.priced ? `${rand(s.per_script_zar)} a script` : "no sourced prices yet"}</span>
      </div>
      <div>
        <span className="kpi-label">Your steps</span>
        <span className="kpi">{s.teacher_steps}</span>
        <span className="kpi-note">answers you gave</span>
      </div>
    </div>
  );
}

function MarkSheet({ b }: { b: Bundle }) {
  const [find, setFind] = useState("");
  const rows = b.mark_sheet.filter((r) => r.learner.toLowerCase().includes(find.trim().toLowerCase()));
  const raw = b.mark_sheet.reduce((s, r) => s + r.raw, 0);
  const outOf = b.mark_sheet.reduce((s, r) => s + r.out_of, 0);
  const sum = (k: "sba_points" | "year_points") => b.mark_sheet.reduce((s, r) => s + Number(r[k]), 0).toFixed(2);
  const download = () => {
    const url = URL.createObjectURL(new Blob([b.mark_sheet_csv], { type: "text/csv" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "mark-sheet.csv";
    a.click();
    URL.revokeObjectURL(url);
  };
  return (
    <>
      <div className="doc-head" style={{ padding: "16px var(--card-pad) 0" }}>
        <div>
          <h1>Mark sheet</h1>
          <p>SBA and year points are the pack's programme-of-assessment weighting, computed in code. Names appear here and nowhere else.</p>
        </div>
        <div className="row">
          <label className="visually-hidden" htmlFor="find">
            Find a learner
          </label>
          <Input id="find" className="search" placeholder="Find a learner" value={find} onChange={(e) => setFind(e.target.value)} />
          <Button size="small" onClick={download}>
            Download CSV
          </Button>
        </div>
      </div>
      {b.memo_unverified.length > 0 && (
        <p className="notice warn" style={{ margin: "0 var(--card-pad)" }}>
          Code could not check memo {b.memo_unverified.map((u) => u.question_id).join(", ")} against the Setter's answer
          {b.memo_unverified.map((u) => ` (memo "${u.memo_answer}", Setter "${u.setter_answer}")`).join(";")}. Check
          {b.memo_unverified.length === 1 ? " it" : " them"} before you approve.
        </p>
      )}
      <div className="table-wrap">
        <table className="rows">
          <thead>
            <tr>
              <th>Learner</th>
              <th className="num">Raw</th>
              <th className="num">Percent</th>
              <th className="num">SBA points</th>
              <th className="num">Year points</th>
              <th>Moderated</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.learner}>
                <td className="learner">{r.learner}</td>
                <td className="num lead">
                  {r.raw}
                  <span className="of"> /{r.out_of}</span>
                </td>
                <td className="num sub">{pct(r.raw, r.out_of)}</td>
                <td className="num sub">{r.sba_points}</td>
                <td className="num sub">{r.year_points}</td>
                <td className="sub">
                  {r.moderated ? <span className="ink green">✓</span> : <span className="muted">—</span>}
                  {r.re_marked && <span className="muted small"> re-marked</span>}
                </td>
              </tr>
            ))}
            <tr className="total">
              <td>Class total · {b.mark_sheet.length} learners</td>
              <td className="num lead">
                {raw}
                <span className="of"> /{outOf}</span>
              </td>
              <td className="num sub">{outOf ? ((raw / outOf) * 100).toFixed(2) : "0"}%</td>
              <td className="num sub">{sum("sba_points")}</td>
              <td className="num sub">{sum("year_points")}</td>
              <td className="sub" />
            </tr>
          </tbody>
        </table>
      </div>
    </>
  );
}

function ItemAnalysis({ b, run }: { b: Bundle; run: RunView }) {
  const questions = new Map((run.created?.questions ?? []).map((q) => [q.question_id, q]));
  const reteach = b.item_analysis.filter((s) => s.flag === "re-teach").length;
  return (
    <>
      <div className="doc-head" style={{ padding: "16px var(--card-pad) 0" }}>
        <div>
          <h1>Item analysis</h1>
          <p>
            Facility is the class mean as a share of the marks available. {reteach} {reteach === 1 ? "item falls" : "items fall"} under 40% and
            go to the catch-up plan; each misconception quotes the learners' own work, by pseudonym.
          </p>
        </div>
      </div>
      <div className="cols" style={{ padding: "0 0 var(--card-pad)" }}>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Question</th>
                <th>What it asked</th>
                <th className="num">Marks</th>
                <th className="num">Class mean</th>
                <th className="num">Facility</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {b.item_analysis.map((s) => {
                const weak = s.flag === "re-teach";
                return (
                  <tr key={s.question_id}>
                    <td className="qcol">{s.question_id}</td>
                    <td style={{ whiteSpace: "normal", maxWidth: 320 }}>{questions.get(s.question_id)?.question ?? ""}</td>
                    <td className="num">{s.max_marks}</td>
                    <td className="num">{s.mean_awarded}</td>
                    <td className={`num ${weak ? "neg" : ""}`}>{Number(s.facility_percent).toFixed(1)}%</td>
                    <td>
                      <span className={`bar ${weak ? "weak" : ""}`}>
                        <i style={{ width: `${s.facility_percent}%` }} />
                      </span>{" "}
                      {weak && <span className="tag fail">re-teach</span>}
                      {s.flag === "review-item" && <span className="tag">review item</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <aside className="panel raised" aria-labelledby="mis-h" style={{ marginRight: "var(--card-pad)" }}>
          <div className="panel-head">
            <h2 id="mis-h">What the Analyst found</h2>
            <span className="small muted">
              {b.misconceptions.length} {b.misconceptions.length === 1 ? "misconception" : "misconceptions"}
            </span>
          </div>
          {b.misconceptions.length === 0 && (
            <p className="small muted" style={{ padding: "8px var(--card-pad) 14px" }}>
              No item needs re-teaching, so there is no misconception to name.
            </p>
          )}
          {b.misconceptions.map((m) => (
            <dl className="note-card" key={m.question_id}>
              <dt>Question {m.question_id}</dt>
              <dd>
                <b>{m.pattern}.</b> {m.scripts} {m.scripts === 1 ? "script shows" : "scripts show"} it:{" "}
                {m.evidence_quotes.map((q) => `"${q}"`).join(", ")}.
              </dd>
            </dl>
          ))}
        </aside>
      </div>
      <div className="doc-md" style={{ padding: "0 var(--card-pad) var(--card-pad)" }}>
        {/* The class sheet's tables repeat the item table above; its worked corrections do not. */}
        <Markdown remarkPlugins={[remarkGfm]}>{correctionsOf(b.class_sheet_md)}</Markdown>
        {!b.class_sheet_review.passed && (
          <p className="notice warn">The Reviewer did not pass this class sheet: {b.class_sheet_review.reason}</p>
        )}
      </div>
    </>
  );
}

function Doc({ title, lead, md }: { title: string; lead: string; md: string }) {
  return (
    <>
      <div className="doc-head" style={{ padding: "16px var(--card-pad) 0" }}>
        <div>
          <h1>{title}</h1>
          <p>{lead}</p>
        </div>
      </div>
      <div className="doc-md table-wrap" style={{ padding: "0 var(--card-pad) var(--card-pad)" }}>
        <Markdown remarkPlugins={[remarkGfm]}>{md}</Markdown>
      </div>
    </>
  );
}

function LearnerNotes({ b }: { b: Bundle }) {
  const flagged = b.learner_notes.filter((n) => !n.review.passed).length;
  return (
    <>
      <div className="doc-head" style={{ padding: "16px var(--card-pad) 0" }}>
        <div>
          <h1>Learner notes</h1>
          <p>
            One note per learner: what the test assessed, a mark with the learner's own work quoted, the next task and a question
            to ask themselves. {flagged ? `${flagged} still rejected by the Reviewer: read them before you approve.` : "Every note passed the Reviewer."}
          </p>
        </div>
      </div>
      <div style={{ paddingBottom: "var(--card-pad)" }}>
        {b.learner_notes.map((n) => (
          <dl className="note-card" key={n.learner} style={n.review.passed ? undefined : { background: "var(--marker-soft)" }}>
            <dt>
              <b>{n.learner}</b>
            </dt>
            <dd>{n.review.passed ? "" : `Rejected by the Reviewer: ${n.review.reason}`}</dd>
            <dt>What it tested</dt>
            <dd>{n.outcome}</dd>
            <dt>The mark and the work</dt>
            <dd>{n.mark_with_quote}</dd>
            <dt>Next task</dt>
            <dd>{n.next_task}</dd>
            <dt>Ask yourself</dt>
            <dd>{n.self_assessment_prompt}</dd>
          </dl>
        ))}
      </div>
    </>
  );
}

export function ApprovalBar({ assessmentId, disabled }: { assessmentId: string; disabled: boolean }) {
  const [rejecting, setRejecting] = useState(false);
  const [note, setNote] = useState("");
  const send = useMutation({ mutationFn: (body: Parameters<typeof api.approval>[1]) => api.approval(assessmentId, body) });
  const error = send.error instanceof ApiError ? send.error.message : send.error ? "That did not go through. Try again." : null;
  if (send.isSuccess) {
    return (
      <footer className="approvebar" role="status">
        <div className="note">
          <strong>{send.variables?.decision === "approve" ? "Approved." : "Sent back to the Writer."}</strong>
          <span>
            {send.variables?.decision === "approve"
              ? "The Executor is writing the files now."
              : "The notes are being redrafted with your note; the marks stay as they are."}
          </span>
        </div>
      </footer>
    );
  }
  return (
    <footer className="approvebar">
      {rejecting ? (
        <form
          className="note"
          style={{ flex: 1 }}
          onSubmit={(e) => {
            e.preventDefault();
            send.mutate({ decision: "reject", note });
          }}
        >
          <label htmlFor="reject-note">
            <strong>What should change?</strong>
          </label>
          <Textarea
            id="reject-note"
            style={{ minHeight: 64, fontSize: 14 }}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Name the learner or document and what to change. Names are replaced with pseudonyms before the Writer reads it."
            autoFocus
          />
          <div className="row" style={{ marginTop: 8 }}>
            <Button type="submit" variant="primary" disabled={!note.trim() || send.isPending}>
              Send it back to the Writer
            </Button>
            <Button type="button" variant="quiet" onClick={() => setRejecting(false)}>
              Cancel
            </Button>
            {error && <span className="error">{error}</span>}
          </div>
        </form>
      ) : (
        <>
          <div className="note">
            <strong>Nothing has been sent.</strong>
            <span>
              Approving writes the five documents to disk; the learner notes stay with you to hand out. Rejecting with a note
              sends the bundle back to the Writer; the marks stay.
            </span>
            {error && <span className="error">{error}</span>}
          </div>
          <Button disabled={disabled || send.isPending} onClick={() => setRejecting(true)}>
            Reject with a note
          </Button>
          <Button variant="primary" disabled={disabled || send.isPending} onClick={() => send.mutate({ decision: "approve" })}>
            Approve the bundle
          </Button>
        </>
      )}
    </footer>
  );
}

export function ApprovalScreen({ run }: { run: RunView }) {
  // One cache entry per approval round: after a rejection the rewritten bundle never shows the
  // previous round's documents, even for a moment.
  const round = run.events.filter((e) => e.kind === "state" && e.payload.to === "awaiting_approval").length;
  const bundle = useQuery({
    queryKey: ["bundle", run.assessmentId, round],
    queryFn: () => api.bundle(run.assessmentId),
  });
  const b = bundle.data;
  const plan = (b?.catch_up_plan_md.match(/^\| \d+ \|/gm) ?? []).length;
  return (
    <>
      <TopBar
        title={run.title}
        subtitle={`${run.cells.size} scripts · ready for your approval`}
        step={2}
        chip={{ text: "waiting for you", kind: "wait" }}
      />
      <div className="canvas">
        {bundle.isPending && <p className="muted">Opening the bundle…</p>}
        {bundle.isError && (
          <p className="notice warn" role="alert">
            The bundle could not be opened: {bundle.error.message}
          </p>
        )}
        {b && (
          <>
            <StatStrip b={b} run={run} />
            <Tabs defaultValue="sheet" className="panel">
              <TabsList aria-label="Documents in the bundle">
                <TabsTrigger value="sheet">
                  Mark sheet <span className="small">{b.mark_sheet.length} learners</span>
                </TabsTrigger>
                <TabsTrigger value="items">
                  Item analysis <span className="small">{b.item_analysis.filter((s) => s.flag === "re-teach").length} to re-teach</span>
                </TabsTrigger>
                <TabsTrigger value="plan">
                  Catch-up plan <span className="small">{plan} {plan === 1 ? "activity" : "activities"}</span>
                </TabsTrigger>
                <TabsTrigger value="notes">
                  Learner notes <span className="small">{b.learner_notes.length}</span>
                </TabsTrigger>
                <TabsTrigger value="moderation">
                  Moderation report <span className="small">{b.summary.moderated} scripts</span>
                </TabsTrigger>
              </TabsList>
              <TabsContent value="sheet">
                <MarkSheet b={b} />
              </TabsContent>
              <TabsContent value="items">
                <ItemAnalysis b={b} run={run} />
              </TabsContent>
              <TabsContent value="plan">
                <Doc
                  title="Catch-up plan"
                  lead="Re-teaching placed only in the ATP weeks still to come, each with the ATP row it came from."
                  md={b.catch_up_plan_md}
                />
              </TabsContent>
              <TabsContent value="notes">
                <LearnerNotes b={b} />
              </TabsContent>
              <TabsContent value="moderation">
                <Doc
                  title="Moderation report"
                  lead="The blind sample, every disagreement and how it was resolved, the agreement rate, and the signature lines."
                  md={b.moderation_report_md}
                />
              </TabsContent>
            </Tabs>
          </>
        )}
      </div>
      {run.state === "approved" ? (
        <footer className="approvebar" role="status">
          <div className="note">
            {run.halted ? (
              <>
                <strong className="error">Delivery stopped.</strong>
                <span>{run.halted} Nothing more was written or sent; the log says what was.</span>
              </>
            ) : (
              <>
                <strong>Approved.</strong>
                <span>The Executor is writing the files now.</span>
              </>
            )}
          </div>
        </footer>
      ) : (
        <ApprovalBar assessmentId={run.assessmentId} disabled={!b || bundle.isFetching} />
      )}
    </>
  );
}
