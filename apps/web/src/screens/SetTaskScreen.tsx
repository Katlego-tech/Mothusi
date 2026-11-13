// Set the task (01-set-task.png): the instruction, the memo and scripts, the pack and task, the
// spending limit, then "Start marking". POST /assessments; a 422 names the file and its sentence.
import { useMutation, useQuery } from "@tanstack/react-query";
import { useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router";

import { ApiError, api } from "../api/client";
import type { Pack } from "../api/types";
import { TopBar } from "../components/chrome";
import { MemoDraftPanel } from "../components/memo-draft";
import { Button } from "../components/ui/button";
import { Input, Textarea } from "../components/ui/input";

const DEMO_GOAL = "Mark my Grade 10 Term 1 Maths test and get it ready for my HOD.";

/** What the console can tell from the memo before sending it: the total and the item count. */
export function memoFacts(text: string): string | null {
  const total = /^\s*total_marks\s*=\s*(\d+)/m.exec(text)?.[1];
  const items = (text.match(/^\s*\[\[item\]\]/gm) ?? []).length;
  return total && items ? `${total} marks · ${items} questions` : null;
}

/** Rows of a learner,question_id,answer CSV, quotes and embedded newlines respected. */
export function csvRows(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') {
        cell += '"';
        i++;
      } else if (ch === '"') quoted = false;
      else cell += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ",") {
      row.push(cell);
      cell = "";
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i++;
      row.push(cell);
      rows.push(row);
      row = [];
      cell = "";
    } else cell += ch;
  }
  if (cell || row.length) {
    row.push(cell);
    rows.push(row);
  }
  return rows.filter((r) => r.some((c) => c.trim()));
}

export function scriptFacts(text: string): string | null {
  const [head, ...rows] = csvRows(text);
  if (!head || head[0]?.trim() !== "learner") return null;
  const learners = new Set(rows.map((r) => r[0]?.trim()).filter(Boolean)).size;
  const n = String(learners).padStart(3, "0");
  return `${learners} learners · ${rows.length} answers · names become L-001 to L-${n} here, before any agent reads a script; they come back only on the mark sheet you approve`;
}

/** A class list: one column, `learner`; the names become L-001… in this order. */
export function classListFacts(text: string): string | null {
  const [head, ...rows] = csvRows(text);
  if (!head || head.length !== 1 || head[0]?.trim() !== "learner") return null;
  const n = rows.filter((r) => r[0]?.trim()).length;
  return `${n} learners · one answer sheet each, named here and nowhere else; the sheets carry L-001 to L-${String(n).padStart(3, "0")}`;
}

type Mode = "csv" | "pages";

interface Slot {
  file: File | null;
  facts: string | null;
}

const kb = (f: File) => `${Math.max(1, Math.round(f.size / 1024))} KB`;

function FileSlot({
  label,
  accept,
  slot,
  error,
  onFile,
  hint,
}: {
  label: string;
  accept: string;
  slot: Slot;
  error: string | undefined;
  onFile: (f: File | null) => void;
  hint: string;
}) {
  const id = `file-${label.toLowerCase().replace(/\W+/g, "-")}`;
  return (
    <div className={`file ${slot.file ? "" : "empty"}`}>
      <span className="name">
        {label} {slot.file && <span>{`${slot.file.name} · ${kb(slot.file)}`}</span>}
      </span>
      <span className="facts">{slot.file ? (slot.facts ?? "Read when you start; the service checks it.") : hint}</span>
      <span>
        <label htmlFor={id} className="btn small" style={{ cursor: "pointer" }}>
          {slot.file ? "Choose another file" : "Choose a file"}
        </label>
        <input
          id={id}
          className="visually-hidden"
          type="file"
          accept={accept}
          aria-label={label}
          onChange={(e) => onFile(e.target.files?.[0] ?? null)}
        />
      </span>
      {error && (
        <span className="error" role="alert">
          {error}
        </span>
      )}
    </div>
  );
}

export function SetTaskScreen() {
  const navigate = useNavigate();
  const packs = useQuery({ queryKey: ["packs"], queryFn: api.packs });
  const [goal, setGoal] = useState("");
  const [memo, setMemo] = useState<Slot>({ file: null, facts: null });
  const [scripts, setScripts] = useState<Slot>({ file: null, facts: null });
  const [mode, setMode] = useState<Mode>("csv");
  const [classList, setClassList] = useState<Slot>({ file: null, facts: null });
  const [atp, setAtp] = useState<Slot>({ file: null, facts: null });
  const [choice, setChoice] = useState("");
  const [budget, setBudget] = useState("40.00");
  const [draftId, setDraftId] = useState<string | null>(null);

  const options = useMemo(
    () =>
      (packs.data ?? []).flatMap((p: Pack) =>
        p.tasks.map((t) => ({ value: `${p.pack_id}::${t.name}`, pack: p, task: t })),
      ),
    [packs.data],
  );
  const selected = options.find((o) => o.value === choice) ?? options.find((o) => o.task.name === "Test") ?? options[0];

  const read = async (f: File | null, facts: (t: string) => string | null, set: (s: Slot) => void) => {
    set({ file: f, facts: f ? facts(await f.text()) : null });
  };

  const loadDemo = async () => {
    // The demo class set is the repo's own fixture (services/core/fixtures/gr10-maths-t1), loaded
    // only when asked for, so it is never in the console's main bundle.
    const [memoText, scriptsText, atpText] = await Promise.all([
      import("../../../../services/core/fixtures/gr10-maths-t1/memo.toml?raw").then((m) => m.default),
      import("../../../../services/core/fixtures/gr10-maths-t1/scripts.csv?raw").then((m) => m.default),
      import("../../../../services/core/fixtures/gr10-maths-t1/atp-weeks.toml?raw").then((m) => m.default),
    ]);
    memoChoice.current++; // the demo's memo replaces any draft in flight
    setDraftId(null);
    drafting.reset();
    setGoal(DEMO_GOAL);
    const file = (text: string, name: string) => new File([text], name, { type: "text/plain" });
    setMemo({ file: file(memoText, "memo.toml"), facts: memoFacts(memoText) });
    setScripts({ file: file(scriptsText, "scripts.csv"), facts: scriptFacts(scriptsText) });
    setAtp({ file: file(atpText, "atp-weeks.toml"), facts: "ATP weeks 10 to 17 (RATP Mathematics Grade 10, 2024)" });
    const maths = options.find((o) => o.pack.pack_id === "dbe-caps/mathematics-gr10" && o.task.name === "Test");
    if (maths) setChoice(maths.value);
  };

  // A typed memo PDF or .docx is drafted into rows for the teacher to check (intake.md §12).
  const drafting = useMutation({
    mutationFn: (f: File) => {
      if (!selected) throw new ApiError(0, "Choose the pack and task first, so the levels are the pack's.");
      return api.memoDraft(f, selected.pack.pack_id);
    },
  });
  // Each memo choice gets a number; a draft that answers after a newer choice is ignored, so a
  // slow PDF upload can never reopen an obsolete draft over the memo chosen since.
  const memoChoice = useRef(0);
  const pickMemo = (f: File | null) => {
    const mine = ++memoChoice.current;
    setDraftId(null);
    drafting.reset();
    if (f && /\.(pdf|docx)$/i.test(f.name)) {
      setMemo({ file: null, facts: null });
      drafting.mutateAsync(f).then(
        (r) => {
          if (memoChoice.current === mine) setDraftId(r.draft_id);
        },
        () => undefined, // shown from drafting.error
      );
      return;
    }
    void read(f, memoFacts, setMemo);
  };
  const draftError = drafting.error instanceof ApiError ? (drafting.error.errors[0]?.message ?? drafting.error.message) : undefined;

  const start = useMutation({
    mutationFn: () => {
      const learners = mode === "pages" ? classList.file : scripts.file;
      if (!memo.file || !learners || !selected)
        throw new ApiError(0, mode === "pages" ? "Choose the memo, the class list and the task first." : "Choose the memo, the scripts and the task first.");
      return api.create({
        goal,
        memo: memo.file,
        scripts: mode === "csv" ? scripts.file : null,
        class_list: mode === "pages" ? classList.file : null,
        atp: atp.file,
        pack_id: selected.pack.pack_id,
        poa_task: selected.task.name,
        budget_zar: budget,
      });
    },
    onSuccess: (r) => navigate(`/runs/${r.assessment_id}`),
  });
  const fieldError = (field: string) =>
    start.error instanceof ApiError ? start.error.errors.find((e) => e.field === field)?.message : undefined;
  const ready = goal.trim() && memo.file && (mode === "pages" ? classList.file : scripts.file) && selected && Number(budget) > 0;
  const rule = selected?.pack.moderation;

  return (
    <>
      <TopBar title="New assessment" subtitle="A colleague for the assessment cycle" step={0} />
      <main className="compose">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h1>What should Mothusi do?</h1>
          <Button size="small" variant="quiet" onClick={() => void loadDemo()} disabled={!options.length}>
            Load the demo class set
          </Button>
        </div>

        <div className="field">
          <label htmlFor="goal">Your instruction</label>
          <Textarea id="goal" value={goal} onChange={(e) => setGoal(e.target.value)} placeholder={DEMO_GOAL} />
        </div>

        <div className="files">
          <FileSlot
            label="Memo"
            accept=".toml,.pdf,.docx"
            slot={memo}
            error={fieldError("memo") ?? draftError}
            onFile={pickMemo}
            hint={drafting.isPending ? "Reading the memo…" : "memo.toml, or your typed memo as a PDF or Word file: Mothusi copies its rows for you to check"}
          />
          {mode === "csv" ? (
            <FileSlot
              label="Scripts"
              accept=".csv"
              slot={scripts}
              error={fieldError("scripts")}
              onFile={(f) => void read(f, scriptFacts, setScripts)}
              hint="scripts.csv: learner, question_id, answer, one row per answer"
            />
          ) : (
            <FileSlot
              label="Class list"
              accept=".csv"
              slot={classList}
              error={fieldError("class_list")}
              onFile={(f) => void read(f, classListFacts, setClassList)}
              hint="class-list.csv: one column, learner, one name per row"
            />
          )}
        </div>
        {draftId && (
          <MemoDraftPanel
            key={draftId}
            draftId={draftId}
            onUse={(file, facts) => {
              setMemo({ file, facts });
              setDraftId(null);
            }}
            onDiscard={() => setDraftId(null)}
          />
        )}
        <div className="row" role="group" aria-label="How the scripts come in">
          <span className="small muted">The scripts come in as</span>
          <span className="mode-toggle">
            <button type="button" aria-pressed={mode === "csv"} onClick={() => setMode("csv")}>
              typed answers (CSV)
            </button>
            <button type="button" aria-pressed={mode === "pages"} onClick={() => setMode("pages")}>
              answer sheets (photos or PDF)
            </button>
          </span>
        </div>
        <FileSlot
          label="ATP weeks still to come (optional)"
          accept=".toml"
          slot={atp}
          error={fieldError("atp")}
          onFile={(f) => void read(f, () => null, setAtp)}
          hint="Without it the catch-up plan is not placed in ATP weeks, and the log says so."
        />

        <div className="field">
          <label htmlFor="task">Curriculum pack and task</label>
          <select id="task" className="select" value={selected?.value ?? ""} onChange={(e) => setChoice(e.target.value)}>
            {packs.isPending && <option>Loading the installed packs…</option>}
            {options.map((o) => (
              <option key={o.value} value={o.value}>
                {`DBE CAPS · ${o.pack.subject} · Grade ${o.pack.grade} · Term ${o.task.term} ${o.task.name} (${o.task.max_marks} marks, ${Number(o.task.weight_percent)}% of the SBA mark)`}
              </option>
            ))}
          </select>
          {packs.isError ? (
            <span className="error">Mothusi's service is not answering, so the packs cannot be listed. Start it, then reload.</span>
          ) : (
            <span className="hint">
              Packs installed: {(packs.data ?? []).map((p) => `${p.subject} Grade ${p.grade}`).join(", ") || "…"}. The task list is the
              pack's programme of assessment.
            </span>
          )}
          {fieldError("pack_id") && <span className="error">{fieldError("pack_id")}</span>}
          {fieldError("poa_task") && <span className="error">{fieldError("poa_task")}</span>}
        </div>

        <div className="field">
          <label htmlFor="budget">Spending limit</label>
          <div className="row">
            <span>Stop and ask me if this costs more than R</span>
            <Input id="budget" className="num" value={budget} onChange={(e) => setBudget(e.target.value)} inputMode="decimal" />
          </div>
          {fieldError("budget_zar") && <span className="error">{fieldError("budget_zar")}</span>}
        </div>

        <section className="next" aria-labelledby="next-h">
          <h2 id="next-h">What happens next</h2>
          <ol>
            <li>
              <b>The Setter sits the test</b> and compares its answers with your memo.{" "}
              <span>A line it doubts stops the run and asks you before a single script is marked.</span>
            </li>
            <li>
              <b>The Marker marks every script</b> against the memo,{" "}
              <span>quoting the words that earned each mark and the memo line it applied.</span>
            </li>
            <li>
              <b>Code checks every total, maximum and quote</b>, the PoA weighting and the cognitive grid.{" "}
              <span>A failed check sends the script back with the reason; after three attempts it comes to you.</span>
            </li>
            <li>
              <b>The Moderator re-marks a blind sample</b>
              {rule ? `: ${Number(rule.sample_percent)}% of the class, at least ${rule.min_scripts} scripts, ${rule.spread.replaceAll("-", ", ")}, ` : " "}
              <span>without seeing the first marks. Disagreements go back with the reason written down.</span>
            </li>
            <li>
              <b>You approve the paperwork</b>: mark sheet, item analysis, catch-up plan, a note per learner, the moderation report.{" "}
              <span>Only then are the files written.</span>
            </li>
          </ol>
        </section>

        <div className="promise">
          <p className="muted">Nothing goes to a learner or a parent until you approve it.</p>
          <div className="row">
            {start.error && !(start.error instanceof ApiError && start.error.errors.length) && (
              <span className="error" role="alert">
                {start.error.message}
              </span>
            )}
            <Button variant="primary" disabled={!ready || start.isPending} onClick={() => start.mutate()}>
              {start.isPending ? "Starting…" : "Start marking"}
            </Button>
          </div>
        </div>
      </main>
    </>
  );
}
