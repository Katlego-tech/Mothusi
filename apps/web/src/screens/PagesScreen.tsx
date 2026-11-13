// Answer sheets and page upload (console-ui.md §11; 10-print-sheets.png, 11-upload-pages.png).
// Pages mode: the class list is in; print the sheets, upload the photographed or scanned pages,
// then start reading. Before any page is read, the core checks every page's corners and code: a
// refused file or a page that never arrived is named here, and the teacher retakes, removes or adds
// files, or starts without them (intake.md §11.4).
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { ApiError, api, pageGaps } from "../api/client";
import type { PageGaps } from "../api/types";
import { TopBar } from "../components/chrome";
import type { RunView } from "../run/reducer";

type Status = "uploading" | "uploaded" | "refused";

interface Row {
  file: File;
  status: Status;
  note: string;
  /** On the core: removing it there takes a DELETE, not only this list. */
  stored: boolean;
}

const ACCEPT = "image/png,image/jpeg,application/pdf";
const kind = (f: File) => (f.type === "application/pdf" ? "PDF" : "photo");

/** "page 2", "pages 2 and 3", "pages 1, 2 and 3". */
export function pageList(pages: number[]): string {
  if (pages.length === 1) return `page ${pages[0]}`;
  return `pages ${pages.slice(0, -1).join(", ")} and ${pages[pages.length - 1]}`;
}

/** The refusal that names this file ("x.png: …" or "x.pdf page 2: …"), without the name. */
function refusalFor(name: string, refused: string[]): string | null {
  const hit = refused.find((m) => m.startsWith(`${name}: `) || m.startsWith(`${name} page `));
  return hit ? hit.slice(name.length).replace(/^: /, "").trim() : null;
}

export function PagesScreen({ run }: { run: RunView }) {
  const id = run.assessmentId;
  const learners = run.created?.script_ids.length ?? 0;
  const [rows, setRows] = useState<Row[]>([]);
  const [printed, setPrinted] = useState(false);
  const [gaps, setGaps] = useState<PageGaps | null>(null);

  const print = useMutation({
    mutationFn: () => api.answerSheets(id),
    onSuccess: (pdf) => {
      const url = URL.createObjectURL(pdf);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${id}-answer-sheets.pdf`;
      a.click();
      URL.revokeObjectURL(url);
      setPrinted(true);
    },
  });

  const update = (name: string, patch: Partial<Row>) =>
    setRows((all) => all.map((r) => (r.file.name === name ? { ...r, ...patch } : r)));

  const upload = useMutation({
    mutationFn: async (files: File[]) => {
      setGaps(null); // the last check no longer describes what is uploaded
      setRows((all) => [
        ...all.filter((r) => !files.some((f) => f.name === r.file.name)),
        ...files.map((file) => ({ file, status: "uploading" as const, note: "", stored: false })),
      ]);
      const { uploads } = await api.uploads(id, files);
      await Promise.all(
        uploads.map(async (target, n) => {
          const file = files[n];
          if (!file) return;
          try {
            await api.putPage(target, file);
            update(file.name, { status: "uploaded", note: "", stored: true });
          } catch (error) {
            update(file.name, { status: "refused", note: error instanceof ApiError ? error.message : "Did not upload." });
          }
        }),
      );
    },
    onError: (error) => {
      const note = error instanceof ApiError ? error.message : "The files did not upload. Try again.";
      setRows((all) => all.map((r) => (r.status === "uploading" ? { ...r, status: "refused", note } : r)));
    },
  });

  const remove = useMutation({
    mutationFn: (name: string) => api.removeUpload(id, name),
    onSuccess: (_none, name) => {
      setGaps(null);
      setRows((all) => all.filter((r) => r.file.name !== name));
    },
  });

  const start = useMutation({
    mutationFn: (acceptGaps: boolean) => api.pagesReady(id, acceptGaps),
    onMutate: () => setGaps(null),
    onError: (error) => {
      const found = pageGaps(error);
      if (!found) return;
      setGaps(found);
      setRows((all) =>
        all.map((r) => {
          if (!r.stored) return r;
          const why = refusalFor(r.file.name, found.refused);
          return why ? { ...r, status: "refused" as const, note: why } : { ...r, status: "uploaded" as const, note: "" };
        }),
      );
    },
  });
  // Files on the core, whatever the last check said about them: a refused PDF can still hold
  // readable pages, so it counts until the teacher removes it.
  const uploaded = rows.filter((r) => r.stored).length;
  const named = new Set(rows.map((r) => r.file.name));
  const unplaced = gaps?.refused.filter((m) => ![...named].some((n) => m.startsWith(`${n}: `) || m.startsWith(`${n} page `))) ?? [];
  const busy = upload.isPending || remove.isPending || start.isPending;

  return (
    <>
      <TopBar title={run.title || "New assessment"} subtitle={`${learners} learners · answer sheets`} step={0} />
      <main className="compose pages">
        <h1>Print the sheets, then bring back the pages</h1>
        <p className="muted">
          Each learner writes on their own sheet. The code in its corner tells Mothusi whose page it is, so no name is read from
          the page.
        </p>

        <section className="pagestep" aria-labelledby="print-h">
          <h2 id="print-h">Print the answer sheets</h2>
          <p className="small">
            One sheet per learner, with their name printed at the top and a box for every question on the memo. The name band
            is cut off before anything reads the page.
          </p>
          <button type="button" className="btn" onClick={() => print.mutate()} disabled={print.isPending}>
            {print.isPending ? "Making the sheets…" : printed ? "Download the sheets again" : `Download ${learners} answer sheets (PDF)`}
          </button>
          {print.isError && <p className="error">The sheets could not be made. Try again.</p>}
        </section>

        <section className="pagestep" aria-labelledby="upload-h">
          <h2 id="upload-h">Upload the written pages</h2>
          <p className="small">Phone photos (PNG or JPEG) or scanned PDFs, in any order and any number of files. Keep all four corner squares in the photo.</p>
          <label className="btn" htmlFor="pages-input" style={{ cursor: "pointer" }}>
            Choose photos or PDFs
          </label>
          <input
            id="pages-input"
            className="visually-hidden"
            type="file"
            multiple
            accept={ACCEPT}
            onChange={(e) => {
              const files = Array.from(e.target.files ?? []);
              if (files.length) upload.mutate(files);
              e.target.value = "";
            }}
          />
          {rows.length > 0 && (
            <ul className="uploads" aria-label="Uploaded pages">
              {rows.map((r) => (
                <li key={r.file.name} className={r.status}>
                  <span className="name">{r.file.name}</span>
                  <span className="small muted">{kind(r.file)}</span>
                  <span className="small status">
                    {r.status === "uploading" ? "uploading…" : r.status === "uploaded" ? "uploaded" : r.note}
                  </span>
                  {r.status !== "uploading" && !start.isSuccess ? (
                    <button
                      type="button"
                      className="btn quiet small"
                      disabled={busy}
                      aria-label={`Remove ${r.file.name}`}
                      onClick={() => (r.stored ? remove.mutate(r.file.name) : setRows((all) => all.filter((x) => x !== r)))}
                    >
                      Remove
                    </button>
                  ) : (
                    <span />
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="pagestep" aria-labelledby="read-h">
          <h2 id="read-h">Read the pages</h2>
          <p className="small">
            Mothusi straightens each page, reads every answer box on the model host in Cape Town, and asks you about any line it is
            unsure of before marking starts.
          </p>
          {gaps && (
            <div className="gaps" role="alert" aria-labelledby="gaps-h">
              <h3 id="gaps-h">{gaps.can_accept ? "Some pages are not ready" : "No page could be read"}</h3>
              {gaps.refused.length > 0 && (
                <p className="small">
                  {gaps.refused.length} page{gaps.refused.length === 1 ? " was" : "s were"} refused (marked in the list above).
                  Retake the photo with all four corner squares and the code in frame, then upload it, or remove the file.
                </p>
              )}
              {unplaced.length > 0 && (
                <ul className="small">
                  {unplaced.map((m) => (
                    <li key={m}>{m}</li>
                  ))}
                </ul>
              )}
              {gaps.missing.length > 0 && (
                <>
                  <p className="small">Not uploaded yet:</p>
                  <ul className="small missing">
                    {gaps.missing.map((m) => (
                      <li key={m.script_id}>
                        <b>{m.script_id}</b> · {pageList(m.pages)}
                      </li>
                    ))}
                  </ul>
                </>
              )}
              {gaps.can_accept && (
                <>
                  <p className="small muted">
                    You can read without them. Every answer box on a missing page comes to you at the reading check, to type
                    in or confirm blank; nothing is marked zero on its own.
                  </p>
                  <button type="button" className="btn" disabled={busy} onClick={() => start.mutate(true)}>
                    Read without them
                  </button>
                </>
              )}
            </div>
          )}
          <button
            type="button"
            className="btn primary"
            disabled={!uploaded || busy || start.isSuccess}
            onClick={() => start.mutate(false)}
          >
            {start.isPending
              ? "Checking the pages…"
              : !uploaded
                ? "Upload the pages first"
                : gaps
                  ? "Check the pages again"
                  : `Read ${uploaded} file${uploaded === 1 ? "" : "s"} and start`}
          </button>
          {start.error instanceof ApiError && !gaps && <p className="error">{start.error.message}</p>}
          {remove.error instanceof ApiError && <p className="error">{remove.error.message}</p>}
        </section>
      </main>
    </>
  );
}
