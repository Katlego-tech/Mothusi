// Typed calls for the HTTP endpoints of docs/architecture/design/console-ui.md §6.
// The base is /api (Vite proxies it to the core service in development); VITE_API_URL overrides.
import { authHeader, onUnauthorized } from "../auth/session";
import type { AtpRow, Bundle, FieldError, MemoDraftView, PageGaps, Pack, ReadingAnswer, ReplyPayload, ReadingItem, RunSummary, UploadTarget } from "./types";

export const API = (import.meta.env.VITE_API_URL as string | undefined) ?? "/api";

/** A 409 or 422 from the core service, with its own sentence(s) for the teacher. */
export class ApiError extends Error {
  readonly status: number;
  readonly errors: FieldError[];
  /** The whole response body, for the replies that carry more than a sentence. */
  readonly body: unknown;

  constructor(status: number, message: string, errors: FieldError[] = [], body: unknown = null) {
    super(message);
    this.status = status;
    this.errors = errors;
    this.body = body;
  }
}

/** The gaps a 409 from POST /pages/ready names, or null for any other error. */
export function pageGaps(error: unknown): PageGaps | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  const body = error.body as Partial<PageGaps> | null;
  if (!body || !Array.isArray(body.refused) || !Array.isArray(body.missing)) return null;
  return { refused: body.refused, missing: body.missing, can_accept: body.can_accept === true };
}

/** Every call to the core carries the teacher's token (console-ui.md §12); none when sign-in is off. */
const withAuth = (init?: RequestInit): RequestInit => ({
  ...init,
  headers: { ...(init?.headers as Record<string, string> | undefined), ...authHeader() },
});

const SIGN_IN_AGAIN = "Your sign-in has ended. Sign in again to carry on.";

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API}${path}`, withAuth(init));
  } catch {
    throw new ApiError(0, "Mothusi's service is not answering. Check that it is running, then try again.");
  }
  if (response.status === 401) {
    onUnauthorized();
    throw new ApiError(401, SIGN_IN_AGAIN);
  }
  const text = await response.text();
  const body: unknown = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const shaped = (body ?? {}) as { message?: string; errors?: FieldError[] };
    const errors = shaped.errors ?? [];
    const message = shaped.message ?? errors.map((e) => e.message).join("; ") ?? response.statusText;
    throw new ApiError(response.status, message || response.statusText, errors, body);
  }
  return body as T;
}

/** A lesson plan's details gathered so far (conversation.md §11.1). */
export interface LessonSlots {
  subject?: string;
  weeks?: number[];
  topic?: string;
  lessons?: number;
  minutes?: number;
  notes?: string;
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  packs: () => call<Pack[]>("/packs"),

  /** desk.md §6: every run, newest first. */
  runs: () => call<RunSummary[]>("/assessments"),

  /** desk.md §6: the subject's ATP rows, each with its DBE source. */
  atp: (packId: string, subject: string) =>
    call<AtpRow[]>(`/packs/${packId}/atp?subject=${encodeURIComponent(subject)}`),

  /** conversation.md §11.2: the colleague off a run; answered in the response. */
  colleague: (
    text: string,
    context: { lesson?: LessonSlots; pack_id?: string; subject?: string; assessment_id?: string },
  ) => call<{ reply: ReplyPayload; ask: string | null; lesson: LessonSlots | null }>("/colleague/messages", json({ text, context })),

  /** conversation.md §6.2: accepted at once (202); the reply arrives on the event stream. */
  postMessage: (assessmentId: string, text: string) =>
    call<{ message_id: string }>(`/assessments/${assessmentId}/messages`, json({ text })),

  /** The teacher's click on a note card is the approval for that one learner (conversation.md §5). */
  sendNote: (assessmentId: string, scriptId: string) =>
    call<{ state: string }>(`/assessments/${assessmentId}/notes/${scriptId}/send`, { method: "POST" }),

  create: (form: {
    goal: string;
    memo: File;
    scripts?: File | null;
    class_list?: File | null;
    atp?: File | null;
    pack_id: string;
    poa_task: string;
    budget_zar: string;
  }) => {
    const data = new FormData();
    data.set("goal", form.goal);
    data.set("memo", form.memo);
    if (form.scripts) data.set("scripts", form.scripts);
    if (form.class_list) data.set("class_list", form.class_list);
    if (form.atp) data.set("atp", form.atp);
    data.set("pack_id", form.pack_id);
    data.set("poa_task", form.poa_task);
    data.set("budget_zar", form.budget_zar);
    return call<{ assessment_id: string; state: string; mode?: "pages" }>("/assessments", { method: "POST", body: data });
  },

  /** The class's answer sheets as one PDF (pages mode, console-ui.md §11.5). */
  answerSheets: async (id: string): Promise<Blob> => {
    const response = await fetch(`${API}/assessments/${id}/answer-sheets.pdf`, withAuth());
    if (response.status === 401) {
      onUnauthorized();
      throw new ApiError(401, SIGN_IN_AGAIN);
    }
    if (!response.ok) throw new ApiError(response.status, "The answer sheets could not be made. Try again.");
    return response.blob();
  },

  uploads: (id: string, files: File[]) =>
    call<{ uploads: UploadTarget[] }>(
      `/assessments/${id}/uploads`,
      json({ files: files.map((f) => ({ name: f.name, content_type: f.type, size: f.size })) }),
    ),

  /** One page file to the URL the core gave for it (its own route here; S3 later). */
  putPage: async (target: UploadTarget, file: File): Promise<void> => {
    // The core's own route takes the token; a presigned S3 URL is already signed and takes none.
    const local = target.url.startsWith("/");
    const url = local ? `${API}${target.url}` : target.url;
    const init: RequestInit = { method: "PUT", headers: target.headers, body: file };
    let response: Response;
    try {
      response = await fetch(url, local ? withAuth(init) : init);
    } catch {
      throw new ApiError(0, `${file.name} did not upload: the service is not answering.`);
    }
    if (local && response.status === 401) {
      onUnauthorized();
      throw new ApiError(401, SIGN_IN_AGAIN);
    }
    if (!response.ok) {
      const body = (await response.json().catch(() => ({}))) as { errors?: FieldError[]; message?: string };
      throw new ApiError(response.status, body.errors?.[0]?.message ?? body.message ?? `${file.name} did not upload.`);
    }
  },

  /** A typed memo PDF or .docx, drafted into rows in the background (intake.md §12.3). */
  memoDraft: (file: File, packId: string) => {
    const data = new FormData();
    data.append("memo", file);
    data.append("pack_id", packId);
    return call<{ draft_id: string; state: string }>("/memo-drafts", { method: "POST", body: data });
  },

  memoDraftView: (draftId: string) => call<MemoDraftView>(`/memo-drafts/${encodeURIComponent(draftId)}`),

  /** 409 with PageGaps in its body when pages were refused or are missing; acceptGaps starts anyway. */
  pagesReady: (id: string, acceptGaps = false) =>
    call<{ state: string }>(`/assessments/${id}/pages/ready`, json({ accept_gaps: acceptGaps })),

  removeUpload: (id: string, name: string) =>
    call<null>(`/assessments/${id}/uploads/${encodeURIComponent(name)}`, { method: "DELETE" }),

  /** The pending reading check with freshly signed crop links (they last 15 minutes). */
  readingItems: (id: string) => call<{ items: ReadingItem[] }>(`/assessments/${id}/reading`),

  reading: (id: string, answers: ReadingAnswer[]) => call<{ state: string }>(`/assessments/${id}/reading`, json({ answers })),

  memo: (id: string, body: { decision: "use_proposed" | "keep_memo" | "amend"; question_id: string; answer?: string }) =>
    call<{ state: string }>(`/assessments/${id}/memo`, json(body)),

  budget: (id: string, raise_to_zar: string) =>
    call<{ state: string }>(`/assessments/${id}/budget`, json({ raise_to_zar })),

  script: (
    id: string,
    scriptId: string,
    body:
      | { decision: "use_attempt"; attempt: 1 | 2 | 3 }
      | { decision: "set_marks"; marks: { question_id: string; awarded: number }[] },
  ) => call<{ state: string }>(`/assessments/${id}/scripts/${scriptId}`, json(body)),

  bundle: (id: string) => call<Bundle>(`/assessments/${id}/bundle`),

  approval: (id: string, body: { decision: "approve" } | { decision: "reject"; note: string }) =>
    call<{ state: string }>(`/assessments/${id}/approval`, json(body)),
};

export const eventsUrl = (id: string) => `${API}/assessments/${id}/events`;

/** A crop image: the core signs the path, relative to the API base. */
export const cropSrc = (path: string) => `${API}${path}`;
