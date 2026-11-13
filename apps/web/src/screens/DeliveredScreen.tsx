// Delivered (08-delivered.png): the three numbers and your steps, what was written, what was sent
// and what stays with the teacher, and the end of the log. No action is offered.
import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import { TopBar } from "../components/chrome";
import { EventLog } from "../components/log";
import { clock, rand } from "../lib/utils";
import type { RunView } from "../run/reducer";

const WHAT: Record<string, string> = {
  "mark-sheet.csv": "Mark sheet, PoA weighting (CSV)",
  "mark-sheet.xlsx": "Mark sheet, PoA weighting (spreadsheet)",
  "class-feedback.md": "Item analysis, misconceptions and worked corrections",
  "catch-up-plan.md": "Catch-up plan in ATP weeks",
  "moderation-report.md": "Moderation report, with signature lines",
  "README.md": "What is in the bundle",
};

export function DeliveredScreen({ run }: { run: RunView }) {
  const bundle = useQuery({ queryKey: ["bundle", run.assessmentId, "delivered"], queryFn: () => api.bundle(run.assessmentId) });
  const s = bundle.data?.summary;
  const written = run.delivered?.written ?? [];
  const notes = written.filter((p) => p.includes("/notes/"));
  const files = written.filter((p) => !p.includes("/notes/"));
  const root = files[0]?.slice(0, files[0].lastIndexOf("/") + 1) ?? "";
  const sent = run.delivered?.sent ?? [];
  const approvedAt = run.events.find((e) => e.kind === "state" && e.payload.to === "approved")?.at;
  const n = run.cells.size;
  return (
    <>
      <TopBar
        title={run.title}
        subtitle={`${n} scripts · approved by you${approvedAt ? ` at ${new Date(approvedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : ""} · delivered`}
        step={3}
        chip={{ text: "delivered", kind: "done" }}
      />
      <div className="canvas">
        <div className="panel strip" aria-label="The run in numbers">
          <div>
            <span className="kpi-label">Time, upload to bundle</span>
            <span className="kpi lead">{clock(s?.elapsed_s ?? run.elapsedS)}</span>
            <span className="kpi-note">
              {n} scripts · {n ? Math.round((s?.elapsed_s ?? run.elapsedS) / n) : 0} s a script
            </span>
          </div>
          <div>
            <span className="kpi-label">Moderator agreed</span>
            <span className="kpi lead">
              {s?.questions_agreed ?? "—"} <span className="unit">of {s?.questions_compared ?? "—"}</span>
            </span>
            <span className="kpi-note">
              questions on the {s?.moderated ?? 0} blind scripts
              {s && s.questions_compared ? ` (${Math.round((s.questions_agreed / s.questions_compared) * 100)}%)` : ""}
            </span>
          </div>
          <div>
            <span className="kpi-label">Cost a script</span>
            <span className="kpi lead">{s ? (s.priced ? rand(s.per_script_zar) : "not metered") : "—"}</span>
            <span className="kpi-note">{s?.priced ? `${rand(s.cost_zar)} in all` : "no sourced prices yet"}</span>
          </div>
          <div>
            <span className="kpi-label">Your steps</span>
            <span className="kpi lead">{run.teacherSteps}</span>
            <span className="kpi-note">answers you gave, the approval included</span>
          </div>
        </div>

        <div className="cols">
          <section className="panel" aria-labelledby="written-h">
            <div className="panel-head">
              <h2 id="written-h">Written to disk</h2>
              <span className="small muted" title={root}>
                {root.split("/").filter(Boolean).slice(-2).join("/")}/
              </span>
            </div>
            <ul className="files-list" style={{ marginTop: 10 }}>
              {files.map((p) => {
                const name = p.slice(p.lastIndexOf("/") + 1);
                return (
                  <li key={p}>
                    <span>{WHAT[name] ?? name}</span>
                    <span className="path">{name}</span>
                  </li>
                );
              })}
              {notes.length > 0 && (
                <li>
                  <span>Learner notes, one file each, by pseudonym</span>
                  <span className="path">
                    notes/ · {notes.length} {notes.length === 1 ? "file" : "files"}
                  </span>
                </li>
              )}
            </ul>
          </section>
          <section className="panel" aria-labelledby="sent-h">
            <div className="panel-head">
              <h2 id="sent-h">Sent</h2>
              <span className="small muted">after your approval, not before</span>
            </div>
            <ul className="files-list" style={{ marginTop: 10 }}>
              {sent.length > 0 ? (
                <li>
                  <span>The bundle notice to you</span>
                  <span className="path">{sent.length} {sent.length === 1 ? "message" : "messages"} (Maildir mailer)</span>
                </li>
              ) : (
                <li>
                  <span>No message was sent</span>
                  <span className="path">no teacher address set (MOTHUSI_TEACHER_EMAIL)</span>
                </li>
              )}
              <li>
                <span>Learner notes: yours to hand out</span>
                <span className="path">not sent</span>
              </li>
              <li>
                <span>Moderation report: for your HOD's signature</span>
                <span className="path">not sent</span>
              </li>
              <li>
                <span>Mark sheet: yours to hand in</span>
                <span className="path">not sent</span>
              </li>
              <li>
                <span>Nothing else left Mothusi</span>
                <span className="path">
                  {written.length} files · {sent.length} {sent.length === 1 ? "message" : "messages"}
                </span>
              </li>
            </ul>
          </section>
        </div>

        <EventLog
          events={run.events.slice(-10)}
          start={run.startedAt}
          priced={run.priced}
          title="What Mothusi did"
          note="the last ten events · the full log stays in events.jsonl"
        />
      </div>
    </>
  );
}
