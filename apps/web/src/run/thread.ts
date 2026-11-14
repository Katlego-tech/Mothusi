// The communication box's thread (conversation.md §1, §7): the teacher's messages, Mothusi's replies
// and its updates, folded from the run's own events so a reload rebuilds the same conversation.
// The feed stays the full log; this is the conversation about it.
import type { MothusiEvent, NoteCard, Skill } from "../api/types";

export type ThreadItem =
  | {
      kind: "update";
      seq: number;
      at: string;
      text: string;
      scriptId?: string;
      flagged?: string[];
    }
  | {
      kind: "message";
      seq: number;
      at: string;
      messageId: string;
      text: string;
    }
  | {
      kind: "reply";
      seq: number;
      at: string;
      messageId: string | null;
      skill: Skill;
      say: string;
      card: NoteCard | null;
      promise: string | null;
    };

const STAGE: Partial<Record<string, string>> = {
  reading_check: "Reading the scanned pages.",
  memo_check: "Checking your memo by sitting the test.",
  moderating: "Moderating: re-marking a blind sample, the way a HOD would.",
  re_marking: "Re-marking the scripts the moderation sent back.",
  analysing: "Marks are final. Analysing the items.",
  writing: "Writing the feedback notes and the catch-up plan.",
  reviewing: "Checking every note against the learner's own work.",
  awaiting_approval: "The set is ready for your approval.",
  approved: "Approved. Sending.",
  rejected: "Sent back with your note. Redrafting.",
};

/** One short sentence for an event worth telling the teacher about, or null. Wording lives here only. */
export function updateFor(event: MothusiEvent, scripts: number): { text: string; scriptId?: string } | null {
  switch (event.kind) {
    case "state": {
      const to = event.payload.to;
      if (to === "marking")
        return {
          text: scripts ? `Marking ${scripts} scripts against your memo.` : "Marking against your memo.",
        };
      const text = STAGE[to];
      return text ? { text } : null;
    }
    case "needs_teacher": {
      const q = event.payload;
      if (q.topic === "script")
        return {
          text: `${q.script_id} needs you. Marking carries on meanwhile.`,
          scriptId: q.script_id,
        };
      if (q.topic === "memo")
        return {
          text: `Memo item ${q.question_id} may be wrong. It needs your decision before marking starts.`,
        };
      if (q.topic === "budget") return { text: "The run is taking longer than planned. Carry on? Your decision is waiting." };
      return { text: "Some pages need you to check how they were read." };
    }
    case "disagreement": {
      const d = event.payload;
      return {
        text: `The Moderator gave ${d.script_id} ${d.second.awarded} on ${d.question_id}; the Marker gave ${d.first.awarded}.`,
        scriptId: d.script_id,
      };
    }
    case "decision":
      if (event.payload.action === "moderation sample" || event.payload.action === "halt")
        return {
          text: event.payload.reason.charAt(0).toUpperCase() + event.payload.reason.slice(1) + ".",
        };
      return null;
    case "delivered": {
      const { written, sent } = event.payload;
      return {
        text: `Delivered: ${written.length} files written, ${sent.length} ${sent.length === 1 ? "message" : "messages"} sent.`,
      };
    }
    case "note_sent":
      return {
        text: `${event.payload.script_id}'s note was sent.`,
        scriptId: event.payload.script_id,
      };
    default:
      return null;
  }
}

/** Scripts flagged back to back are one line, so a burst of flags doesn't bury the conversation. */
export function flaggedText(ids: readonly string[]): string {
  if (ids.length === 1) return `${ids[0]} needs you. Marking carries on meanwhile.`;
  const named = ids.slice(0, 3).join(", ");
  const more = ids.length > 3 ? ` and ${ids.length - 3} more` : "";
  return `${ids.length} scripts need you: ${named}${more}. Marking carries on meanwhile.`;
}

export function threadOf(events: readonly MothusiEvent[]): ThreadItem[] {
  const created = events.find((e) => e.kind === "created");
  const scripts = created?.kind === "created" ? created.payload.script_ids.length : 0;
  const items: ThreadItem[] = [];
  for (const e of events) {
    if (e.kind === "message") {
      items.push({
        kind: "message",
        seq: e.seq,
        at: e.at,
        messageId: e.payload.message_id,
        text: e.payload.text,
      });
    } else if (e.kind === "reply") {
      const p = e.payload;
      items.push({
        kind: "reply",
        seq: e.seq,
        at: e.at,
        messageId: p.message_id,
        skill: p.skill,
        say: p.say,
        card: p.card,
        promise: p.promise,
      });
    } else if (e.kind === "needs_teacher" && e.payload.topic === "script") {
      const id = e.payload.script_id;
      const last = items[items.length - 1];
      if (last?.kind === "update" && last.flagged) {
        const flagged = last.flagged.includes(id) ? last.flagged : [...last.flagged, id];
        items[items.length - 1] = {
          ...last,
          flagged,
          text: flaggedText(flagged),
          scriptId: undefined,
        };
      } else {
        items.push({
          kind: "update",
          seq: e.seq,
          at: e.at,
          text: flaggedText([id]),
          scriptId: id,
          flagged: [id],
        });
      }
    } else {
      const u = updateFor(e, scripts);
      if (u) items.push({ kind: "update", seq: e.seq, at: e.at, ...u });
    }
  }
  return items;
}

/** The scripts whose note has been sent early, from `note_sent` events. */
export function sentNotes(events: readonly MothusiEvent[]): Map<string, string> {
  const sent = new Map<string, string>();
  for (const e of events) if (e.kind === "note_sent") sent.set(e.payload.script_id, e.at);
  return sent;
}
