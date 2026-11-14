// RunView and apply (docs/architecture/design/console-ui.md §3): the whole screen folded from the event stream.
// Pure: apply(view, event) returns a new view, or the same one for an event already seen, so a
// reload or a reconnect replay rebuilds exactly the same screen.
import type {
  AgentRole,
  AssessmentState,
  CreatedPayload,
  DeliveredPayload,
  DisagreementPayload,
  Mark,
  NeedsTeacher,
  Question,
  ScriptQuestion,
  MothusiEvent,
} from "../api/types";

export type CellStatus = "pending" | "working" | "retry" | "marked" | "teacher";

export interface ScriptCell {
  scriptId: string;
  status: CellStatus;
  total: number | null;
  maxMarks: number;
  moderated: boolean;
  disputed: boolean;
}

/** One card in the agents strip. `code` is the rules engine; `intake` is code too, shown by role. */
export type CardRole = AgentRole | "code";

export interface AgentCard {
  role: CardRole;
  briefs: number;
  live: boolean;
  task: string;
  lastSummary: string;
}

export interface NeedsYouItem {
  scriptId: string;
  question: NeedsTeacher;
  flaggedSeq: number;
  status: "open" | "sending" | "error";
  error?: string;
}

export interface RunView {
  assessmentId: string;
  lastSeq: number;
  created: CreatedPayload | null;
  title: string;
  state: AssessmentState;
  events: MothusiEvent[];
  cells: Map<string, ScriptCell>;
  scripts: Map<string, Mark[]>;
  agents: Map<CardRole, AgentCard>;
  checks: { run: number; failed: number; open: number };
  costZar: string;
  priced: boolean;
  elapsedS: number;
  teacherSteps: number;
  totalMarks: number;
  settled: number;
  finding: string;
  pending: NeedsTeacher | null;
  needsYou: NeedsYouItem[];
  disagreements: DisagreementPayload[];
  delivered: DeliveredPayload | null;
  halted: string | null;
  startedAt: string | null;
  lastAt: string | null;
}

export const emptyRun = (assessmentId: string): RunView => ({
  assessmentId,
  lastSeq: -1,
  created: null,
  title: "",
  state: "draft",
  events: [],
  cells: new Map(),
  scripts: new Map(),
  agents: new Map(),
  checks: { run: 0, failed: 0, open: 0 },
  costZar: "0.00",
  priced: true,
  elapsedS: 0,
  teacherSteps: 0,
  totalMarks: 0,
  settled: 0,
  finding: "",
  pending: null,
  needsYou: [],
  disagreements: [],
  delivered: null,
  halted: null,
  startedAt: null,
  lastAt: null,
});

export function needsYouSending(run: RunView, scriptId: string): RunView {
  return {
    ...run,
    needsYou: run.needsYou.map((item) =>
      item.scriptId === scriptId
        ? {
            scriptId: item.scriptId,
            question: item.question,
            flaggedSeq: item.flaggedSeq,
            status: "sending",
          }
        : item,
    ),
  };
}

export function needsYouAnswered(run: RunView, scriptId: string): RunView {
  return {
    ...run,
    needsYou: run.needsYou.filter((item) => item.scriptId !== scriptId),
  };
}

export function needsYouFailed(
  run: RunView,
  scriptId: string,
  message: string,
): RunView {
  return {
    ...run,
    needsYou: run.needsYou.map((item) =>
      item.scriptId === scriptId
        ? { ...item, status: "error", error: message }
        : item,
    ),
  };
}

const MARKING_AGENTS = new Set<AgentRole>(["marker", "moderator"]);

function card(view: RunView, role: CardRole): AgentCard {
  return (
    view.agents.get(role) ?? {
      role,
      briefs: 0,
      live: false,
      task: "",
      lastSummary: "",
    }
  );
}

function withCell(
  view: RunView,
  id: string,
  change: Partial<ScriptCell>,
): Map<string, ScriptCell> {
  const cells = new Map(view.cells);
  const cell = cells.get(id);
  if (cell) cells.set(id, { ...cell, ...change });
  return cells;
}

const openRetries = (cells: Map<string, ScriptCell>) =>
  [...cells.values()].filter((c) => c.status === "retry").length;

const settledCount = (cells: Map<string, ScriptCell>) =>
  [...cells.values()].filter(
    (c) => c.status === "marked" || c.status === "teacher",
  ).length;

/** The marks a teacher's script answer settles on: the chosen attempt's, or the ones they set. */
function teacherMarks(
  answer: {
    decision?: string;
    attempt?: number;
    marks?: { question_id: string; awarded: number }[];
  },
  asked: ScriptQuestion,
  questions: Question[],
): Mark[] {
  if (answer.decision === "use_attempt")
    return asked.attempts[(answer.attempt ?? 1) - 1]?.marks ?? [];
  return (answer.marks ?? []).map((m) => ({
    question_id: m.question_id,
    awarded: m.awarded,
    max_marks:
      questions.find((q) => q.question_id === m.question_id)?.max_marks ??
      m.awarded,
    evidence_quote: "",
    memo_line: "",
    principle: "teacher's decision",
  }));
}

export function apply(view: RunView, event: MothusiEvent): RunView {
  if (event.seq <= view.lastSeq) return view;
  const startedAt = view.startedAt ?? event.at;
  const base: RunView = {
    ...view,
    lastSeq: event.seq,
    events: event.kind === "usage" ? view.events : [...view.events, event],
    startedAt,
    lastAt: event.at,
    elapsedS: Math.max(
      0,
      Math.round((Date.parse(event.at) - Date.parse(startedAt)) / 1000),
    ),
  };

  switch (event.kind) {
    case "created": {
      const p = event.payload;
      const cells = new Map<string, ScriptCell>();
      for (const id of p.script_ids) {
        cells.set(id, {
          scriptId: id,
          status: "pending",
          total: null,
          maxMarks: p.total_marks,
          moderated: false,
          disputed: false,
        });
      }
      return {
        ...base,
        created: p,
        title: p.title,
        totalMarks: p.total_marks,
        cells,
      };
    }

    case "state": {
      const to = event.payload.to;
      return {
        ...base,
        state: to,
        pending: to === "awaiting_approval" ? null : base.pending,
        needsYou: to === "marking" ? base.needsYou : [],
      };
    }

    case "brief": {
      const p = event.payload;
      const agents = new Map(base.agents);
      const c = card(base, p.agent);
      agents.set(p.agent, {
        ...c,
        briefs: c.briefs + 1,
        live: true,
        task: p.task,
      });
      const cells =
        p.script_id && MARKING_AGENTS.has(p.agent)
          ? withCell(base, p.script_id, { status: "working" })
          : base.cells;
      return { ...base, agents, cells };
    }

    case "result": {
      const p = event.payload;
      const agents = new Map(base.agents);
      agents.set(p.agent, {
        ...card(base, p.agent),
        live: false,
        lastSummary: p.summary,
      });
      let { cells, scripts } = base;
      if (p.script_id && p.status === "done" && p.marks) {
        if (p.agent === "marker") {
          cells = withCell(base, p.script_id, {
            status: "marked",
            total: p.awarded,
            disputed: false,
          });
          scripts = new Map(scripts).set(p.script_id, p.marks);
        } else if (p.agent === "moderator") {
          const before = base.cells.get(p.script_id);
          cells = withCell(base, p.script_id, {
            moderated: true,
            status: before?.total == null ? "pending" : "marked",
          });
        }
      } else if (p.script_id && p.status !== "done") {
        cells = withCell(base, p.script_id, { status: "retry" });
      }
      return {
        ...base,
        agents,
        cells,
        scripts,
        settled: settledCount(cells),
        checks: { ...base.checks, open: openRetries(cells) },
      };
    }

    case "check": {
      const p = event.payload;
      const agents = new Map(base.agents);
      const c = card(base, "code");
      agents.set("code", {
        ...c,
        briefs: c.briefs + 1,
        lastSummary: `${p.check}: ${p.detail}`,
      });
      const cells =
        !p.passed && base.cells.has(p.about)
          ? withCell(base, p.about, { status: "retry" })
          : base.cells;
      return {
        ...base,
        agents,
        cells,
        settled: settledCount(cells),
        checks: {
          run: base.checks.run + 1,
          failed: base.checks.failed + (p.passed ? 0 : 1),
          open: openRetries(cells),
        },
        finding: p.passed
          ? base.finding
          : `${p.about}: ${p.check} check failed. ${p.detail}`,
      };
    }

    case "decision": {
      if (event.payload.action === "intake") {
        const agents = new Map(base.agents);
        agents.set("intake", {
          ...card(base, "intake"),
          lastSummary: event.payload.reason,
        });
        return { ...base, agents };
      }
      if (event.payload.action === "halt")
        return { ...base, halted: event.payload.reason };
      return base;
    }

    case "disagreement": {
      const p = event.payload;
      return {
        ...base,
        disagreements: [...base.disagreements, p],
        cells: withCell(base, p.script_id, { disputed: true }),
        finding: `${p.script_id}, question ${p.question_id}: the Marker gave ${p.first.awarded} of ${p.max_marks} and the blind Moderator ${p.second.awarded} of ${p.max_marks}.`,
      };
    }

    case "needs_teacher": {
      const p = event.payload;
      let needsYou = base.needsYou;
      if (p.topic === "script" && p.stage === "marking") {
        const cell = base.cells.get(p.script_id);
        const settled =
          cell?.status === "marked" ||
          (cell?.status === "teacher" && cell.total !== null);
        if (
          !settled &&
          !needsYou.some((item) => item.scriptId === p.script_id)
        ) {
          needsYou = [
            ...needsYou,
            {
              scriptId: p.script_id,
              question: p,
              flaggedSeq: event.seq,
              status: "open",
            },
          ];
        }
      }
      // A marking question puts the cell with the teacher; a moderation question leaves the first
      // marking's cell as it is (the teacher is settling the second marking).
      const cells =
        p.topic === "script" && p.stage !== "moderation"
          ? withCell(base, p.script_id, { status: "teacher" })
          : base.cells;
      return { ...base, pending: p, cells, finding: p.title, needsYou };
    }

    case "teacher": {
      const p = event.payload as {
        topic?: string;
        script_id?: string;
        decision?: string;
        attempt?: number;
        marks?: { question_id: string; awarded: number }[];
      };
      let { cells, scripts } = base;
      const asked = base.pending;
      if (p.topic === "script" && p.script_id && asked?.topic === "script") {
        const marks = teacherMarks(p, asked, base.created?.questions ?? []);
        if (asked.stage === "moderation") {
          // The teacher's marks stand in for the blind second marking: the first marking stays.
          const first = base.cells.get(p.script_id);
          cells = withCell(base, p.script_id, {
            moderated: true,
            status:
              first?.total == null ? (first?.status ?? "pending") : "marked",
          });
        } else {
          const total = marks.reduce((sum, m) => sum + m.awarded, 0);
          cells = withCell(base, p.script_id, {
            status: "teacher",
            total,
            disputed: false,
          });
          scripts = new Map(scripts).set(p.script_id, marks);
        }
      }
      return {
        ...base,
        scripts,
        pending: null,
        needsYou:
          p.topic === "script" && p.script_id
            ? base.needsYou.filter((item) => item.scriptId !== p.script_id)
            : base.needsYou,
        cells,
        settled: settledCount(cells),
        checks: { ...base.checks, open: openRetries(cells) },
        teacherSteps: base.teacherSteps + 1,
      };
    }

    case "usage": {
      const p = event.payload;
      return {
        ...base,
        costZar: p.cost_zar_total,
        priced: p.priced,
        teacherSteps: Math.max(base.teacherSteps, p.teacher_steps),
      };
    }

    case "delivered": {
      const agents = new Map(base.agents);
      for (const [role, c] of agents) agents.set(role, { ...c, live: false });
      return { ...base, delivered: event.payload, agents };
    }
    // The conversation (conversation.md §6.1) lives in the events; the box folds it (run/thread.ts).
    case "message":
    case "reply":
    case "note_sent":
      return base;
  }
}
