// The teacher's desk (desk.md): the page after sign-in. One action, the teacher's runs in their state,
// and this term read straight from the sources of truth: the ATP's teaching weeks and the PoA's tasks.
// The colleague box sits beside it, as on every page. No cost appears here or anywhere.
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";

import { api } from "../api/client";
import type { AtpRow, Pack, RunSummary } from "../api/types";
import { TopBar } from "../components/chrome";
import "../desk.css";

/** Consecutive weeks on one topic read as one row: "Weeks 1–4 · Algebraic expressions". */
export interface WeekRange {
  from: number;
  to: number;
  topic: string;
  sources: string[];
}

export function mergeWeeks(rows: readonly AtpRow[]): WeekRange[] {
  const out: WeekRange[] = [];
  for (const r of [...rows].sort((a, b) => a.week - b.week)) {
    const last = out[out.length - 1];
    if (last && last.topic === r.topic && last.to === r.term_week - 1) {
      last.to = r.term_week;
      last.sources.push(r.source);
    } else {
      out.push({ from: r.term_week, to: r.term_week, topic: r.topic, sources: [r.source] });
    }
  }
  return out;
}

/** A run's state in the teacher's words, and whether it needs them. */
export function runWords(run: RunSummary): { text: string; kind: "wait" | "done" | "stop" | "work" } {
  if (run.halted) return { text: "Stopped", kind: "stop" };
  if (run.waiting) return { text: "Waiting for you", kind: "wait" };
  switch (run.state) {
    case "awaiting_approval":
      return { text: "Ready for your approval", kind: "wait" };
    case "approved":
      return { text: "Approved, delivering", kind: "work" };
    case "delivered":
      return { text: "Delivered", kind: "done" };
    case "rejected":
      return { text: "Redrafting your notes", kind: "work" };
    case "draft":
    case "reading_check":
    case "memo_check":
      return { text: "Checking the memo", kind: "work" };
    case "marking":
      return { text: "Marking", kind: "work" };
    case "moderating":
    case "re_marking":
      return { text: "Moderating", kind: "work" };
    default:
      return { text: "Writing up", kind: "work" };
  }
}

function greeting(now: Date): string {
  const h = now.getHours();
  return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}

const when = (iso: string) =>
  new Date(iso).toLocaleString("en-ZA", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

const TERMS = [1, 2, 3, 4] as const;

export function DeskScreen() {
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.runs, refetchInterval: 15_000 });
  const packs = useQuery({ queryKey: ["packs"], queryFn: api.packs, staleTime: Infinity });
  const [subject, setSubject] = useState<string | null>(null);
  const pack: Pack | undefined = packs.data?.find((p) => p.subject === subject) ?? packs.data?.[0];
  const waiting = runs.data?.filter((r) => runWords(r).kind === "wait").length ?? 0;

  return (
    <>
      <TopBar title="Your desk" subtitle="Grade 10 · DBE CAPS" />
      <main className="desk">
        <section className="desk-hero">
          <div>
            <h1>{greeting(new Date())}</h1>
            <p className="lede">
              {waiting > 0
                ? `${waiting} ${waiting === 1 ? "run is" : "runs are"} waiting for you.`
                : "Give Mothusi a set to mark. It checks your memo first, marks every script against it, moderates a blind sample and hands everything back for your approval."}
            </p>
          </div>
          <Link to="/new" className="btn primary desk-cta">
            Mark a set
          </Link>
        </section>

        <section className="panel desk-runs" aria-labelledby="runs-h">
          <div className="panel-head">
            <h2 id="runs-h">Your runs</h2>
            {runs.data && runs.data.length > 0 && <span className="muted">newest first</span>}
          </div>
          {runs.isPending && <p className="desk-note">Finding your runs…</p>}
          {runs.isError && (
            <p className="desk-note" role="alert">
              Your runs could not be loaded. Mothusi's service is not answering; reload to try again.
            </p>
          )}
          {runs.data?.length === 0 && (
            <p className="desk-note">No sets yet. Mark your first one: Mothusi starts by sitting the test against your memo.</p>
          )}
          {runs.data && runs.data.length > 0 && (
            <ul className="runlist">
              {runs.data.map((r) => {
                const w = runWords(r);
                return (
                  <li key={r.assessment_id}>
                    <Link to={`/runs/${r.assessment_id}`}>
                      <span className="run-name">{r.title}</span>
                      <span className="run-meta">
                        {r.scripts} scripts · {r.subject} Grade {r.grade} · started {when(r.started_at)}
                      </span>
                      <span className={`run-state ${w.kind}`}>{w.text}</span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        <div className="desk-sources">
          <AtpPanel pack={pack} packs={packs.data ?? []} onSubject={setSubject} />
          <PoaPanel pack={pack} />
        </div>
      </main>
    </>
  );
}

function AtpPanel({ pack, packs, onSubject }: { pack: Pack | undefined; packs: Pack[]; onSubject: (s: string) => void }) {
  const [term, setTerm] = useState<number>(1);
  const atp = useQuery({
    queryKey: ["atp", pack?.pack_id, pack?.subject],
    queryFn: () => api.atp(pack?.pack_id ?? "", pack?.subject ?? ""),
    enabled: Boolean(pack),
    staleTime: Infinity,
  });
  const ranges = mergeWeeks(atp.data?.filter((r) => r.term === term) ?? []);
  return (
    <section className="panel desk-atp" aria-labelledby="atp-h">
      <div className="panel-head">
        <h2 id="atp-h">Term {term} in the ATP</h2>
        {packs.length > 1 && (
          <label className="desk-subject">
            <span className="sr-only">Subject</span>
            <select value={pack?.subject ?? ""} onChange={(e) => onSubject(e.target.value)}>
              {packs.map((p) => (
                <option key={p.subject} value={p.subject}>
                  {p.subject}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      <div className="terms" role="group" aria-label="Term">
        {TERMS.map((t) => (
          <button key={t} type="button" aria-pressed={t === term} onClick={() => setTerm(t)}>
            Term {t}
          </button>
        ))}
      </div>
      {atp.isPending && pack && <p className="desk-note">Reading the ATP…</p>}
      {atp.isError && <p className="desk-note">This subject's ATP is not installed.</p>}
      {ranges.length > 0 && (
        <ol className="weeks">
          {ranges.map((r) => (
            <li key={`${r.from}-${r.topic}`} title={r.sources[0]}>
              <span className="wk">{r.from === r.to ? `Week ${r.from}` : `Weeks ${r.from}–${r.to}`}</span>
              <span className="topic">{r.topic}</span>
            </li>
          ))}
        </ol>
      )}
      {atp.data && atp.data[0] && (
        <p className="desk-source">From {atp.data[0].source.split(", PDF")[0]}. Hover a week for its page.</p>
      )}
    </section>
  );
}

function PoaPanel({ pack }: { pack: Pack | undefined }) {
  return (
    <section className="panel desk-poa" aria-labelledby="poa-h">
      <div className="panel-head">
        <h2 id="poa-h">The PoA this year</h2>
        {pack && <span className="muted">{pack.subject}</span>}
      </div>
      {!pack && <p className="desk-note">Reading the programme of assessment…</p>}
      {pack && (
        <table className="poa">
          <thead>
            <tr>
              <th scope="col">Term</th>
              <th scope="col">Task</th>
              <th scope="col" className="num">
                Marks
              </th>
              <th scope="col" className="num">
                Weight
              </th>
            </tr>
          </thead>
          <tbody>
            {pack.tasks.map((t) => (
              <tr key={`${t.term}-${t.name}`}>
                <td>{t.term}</td>
                <td>{t.name}</td>
                <td className="num">{t.max_marks}</td>
                <td className="num">{Number(t.weight_percent)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {pack && <p className="desk-source">Weights from DBE Circular S8 of 2023, Annexure §26, as the pack holds them.</p>}
    </section>
  );
}
