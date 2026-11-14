import { render, screen, within } from "@testing-library/react";
import type { MothusiEvent } from "../api/types";
import { apply, emptyRun } from "../run/reducer";
import plantedLog from "../run/__fixtures__/planted-events.jsonl?raw";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import type { Bundle } from "../api/types";
import bundleJson from "../run/__fixtures__/planted-bundle.json";
import { DeliveredScreen } from "./DeliveredScreen";
import { clock, rand } from "../lib/utils";

function required<T>(value: T | null | undefined, description: string): T {
  if (value === null || value === undefined) throw new Error(description);
  return value;
}

const events = plantedLog.trim().split("\n").map((line) => JSON.parse(line) as MothusiEvent);
const id = "gr10-maths-t1-planted";
const finished = events.reduce(apply, emptyRun(id));

const bundle = bundleJson as unknown as Bundle;
function wrap(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return { ...render(<QueryClientProvider client={client}>{node}</QueryClientProvider>), client };
}
function respond(status = 200, body: unknown = bundle) {
  const fetch = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(async () => new Response(JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });


describe("the delivered run", () => {
  it("fetches the bundle and shows the real run summary with no actions", async () => {
    const fetch = respond();
    wrap(<DeliveredScreen run={finished} />);
    const numbers = screen.getByLabelText("The run in numbers");
    await waitFor(() => expect(numbers).toHaveTextContent(`${bundle.summary.questions_agreed} of ${bundle.summary.questions_compared}`));
    expect(numbers).toHaveTextContent(clock(bundle.summary.elapsed_s));
    expect(numbers).toHaveTextContent(rand(bundle.summary.per_script_zar));
    expect(numbers).toHaveTextContent(String(finished.teacherSteps));
    expect(screen.getByRole("banner")).toHaveTextContent(finished.title);
    expect(screen.getByRole("banner")).toHaveTextContent(/approved by you at .* · delivered/);
    expect(required(fetch.mock.calls[0], "Missing fetch.mock.calls[0]")[0]).toBe(`/api/assessments/${id}/bundle`);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("lists written documents and groups the learner notes", async () => {
    respond();
    wrap(<DeliveredScreen run={finished} />);
    const written = screen.getByRole("region", { name: "Written to disk" });
    for (const path of required(finished.delivered, "Missing finished.delivered").written.filter((p) => !p.includes("/notes/"))) expect(written).toHaveTextContent(required(path.split("/").at(-1), "Missing path.split(\"/\").at(-1)"));
    const notes = required(finished.delivered, "Missing finished.delivered").written.filter((p) => p.includes("/notes/")).length;
    expect(written).toHaveTextContent(`notes/ · ${notes} files`);
    expect(written).toHaveTextContent("Learner notes, one file each, by pseudonym");
    await waitFor(() => expect(screen.getByLabelText("The run in numbers")).toHaveTextContent(`${bundle.summary.questions_agreed} of`));
  });

  it("explains no email was sent and which files stay with the teacher", async () => {
    respond();
    wrap(<DeliveredScreen run={finished} />);
    const sent = screen.getByRole("region", { name: "Sent" });
    expect(sent).toHaveTextContent("No message was sent");
    expect(sent).toHaveTextContent("Learner notes: yours to hand out");
    expect(sent).toHaveTextContent("Moderation report: for your HOD's signature");
    expect(sent).toHaveTextContent("Mark sheet: yours to hand in");
    expect(within(sent).getAllByText("not sent")).toHaveLength(3);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
  });

  it.each([1, 2])("reports %s sent messages, a single note and unknown filenames", async (count) => {
    respond();
    const payload = { written: ["/bundle/extra.txt", "/bundle/notes/L-001.md"], sent: Array.from({ length: count }, (_, n) => `/mail/${n}`) };
    const run = apply(finished, { seq: finished.lastSeq + 1, at: required(finished.lastAt, "Missing finished.lastAt"), assessment_id: id, kind: "delivered", agent: "executor", summary: "Written", payload });
    wrap(<DeliveredScreen run={run} />);
    expect(screen.getByRole("region", { name: "Sent" })).toHaveTextContent(`${count} ${count === 1 ? "message" : "messages"} (Maildir mailer)`);
    const written = screen.getByRole("region", { name: "Written to disk" });
    expect(written).toHaveTextContent("extra.txt");
    expect(written).toHaveTextContent("notes/ · 1 file");
    await waitFor(() => expect(fetch).toHaveBeenCalled());
  });

  it("shows only the last ten events in the final log", async () => {
    respond();
    wrap(<DeliveredScreen run={finished} />);
    const log = screen.getByRole("main");
    expect(within(log).getByRole("heading", { name: "What Mothusi did" })).toBeInTheDocument();
    expect(log).toHaveTextContent("the last ten events");
    expect(log).toHaveTextContent(`Wrote ${required(finished.delivered, "Missing finished.delivered").written.length} files`);
    expect(log).not.toHaveTextContent(`Started: ${finished.title}`);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
  });

  it("uses elapsed run time and placeholder comparisons while the bundle loads", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
    wrap(<DeliveredScreen run={finished} />);
    const numbers = screen.getByLabelText("The run in numbers");
    expect(numbers).toHaveTextContent(clock(finished.elapsedS));
    expect(numbers).toHaveTextContent("— of —");
    expect(numbers).toHaveTextContent("no sourced prices yet");
  });

  it("keeps the delivery evidence visible when the bundle request fails", async () => {
    respond(500, { message: "Cannot load summary" });
    const { client } = wrap(<DeliveredScreen run={finished} />);
    await waitFor(() => expect(client.getQueryState(["bundle", id, "delivered"])?.status).toBe("error"));
    expect(screen.getByRole("region", { name: "Written to disk" })).toHaveTextContent("mark-sheet.csv");
    expect(screen.getByLabelText("The run in numbers")).toHaveTextContent("— of —");
  });

  it("says when costs are not metered", async () => {
    respond(200, { ...bundle, summary: { ...bundle.summary, priced: false } });
    wrap(<DeliveredScreen run={finished} />);
    expect(await screen.findByText("not metered")).toBeInTheDocument();
    expect(screen.getByText("no sourced prices yet")).toBeInTheDocument();
  });

  it("handles no delivery payload or scripts without dividing by zero", async () => {
    respond(200, { ...bundle, summary: { ...bundle.summary, questions_compared: 0 } });
    wrap(<DeliveredScreen run={emptyRun(id)} />);
    expect(within(screen.getByRole("region", { name: "Written to disk" })).queryAllByRole("listitem")).toHaveLength(0);
    expect(screen.getByLabelText("The run in numbers")).toHaveTextContent("0 scripts · 0 s a script");
    expect(screen.queryByText(/NaN|Infinity/)).not.toBeInTheDocument();
    expect(screen.getByRole("banner")).toHaveTextContent("approved by you · delivered");
    await waitFor(() => expect(screen.getByLabelText("The run in numbers")).toHaveTextContent(`${bundle.summary.questions_agreed} of 0`));
  });
});
