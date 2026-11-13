// The fetch-based SSE reader (console-ui.md §12.2).
import { authHeader, completeSignIn, navigation, resetSession, signIn } from "../auth/session";
import { type Frame, frameParser, openEvents, type Connection } from "./events";
import type { MothusiEvent } from "./types";

afterEach(() => {
  resetSession();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  vi.restoreAllMocks();
  sessionStorage.clear();
});

function parse(chunks: string[]): Frame[] {
  const frames: Frame[] = [];
  const feed = frameParser((f) => frames.push(f));
  chunks.forEach(feed);
  return frames;
}

const ev = (seq: number, kind = "state"): string =>
  `id: ${seq}\nevent: ${kind}\ndata: ${JSON.stringify({ seq, kind, assessment_id: "a1", summary: "", payload: {} })}\n\n`;

describe("the SSE frame parser", () => {
  it("reassembles frames split anywhere, including between \\r and \\n", () => {
    const text = "id: 4\r\nevent: state\r\ndata: {\"a\":1}\r\n\r\n";
    const whole = parse([text]);
    for (let cut = 1; cut < text.length; cut++) expect(parse([text.slice(0, cut), text.slice(cut)])).toEqual(whole);
    expect(whole).toEqual([{ id: "4", event: "state", data: '{"a":1}' }]);
  });

  it("ignores : ping comments and joins multi-line data", () => {
    expect(parse([": ping\n\n", "event: decision\ndata: one\ndata: two\n\n", ": ping\n\n"])).toEqual([
      { id: undefined, event: "decision", data: "one\ntwo" },
    ]);
  });

  it("dispatches nothing for a frame with no data, and keeps one leading space only", () => {
    expect(parse(["event: x\n\n", "data:  two spaces\n\n"])).toEqual([{ id: undefined, event: "message", data: " two spaces" }]);
  });
});

function streamOf(text: string, close = true): ReadableStream<Uint8Array> {
  const bytes = new TextEncoder().encode(text);
  return new ReadableStream({
    start(controller) {
      controller.enqueue(bytes.slice(0, 7)); // split mid-frame on purpose
      controller.enqueue(bytes.slice(7));
      if (close) controller.close();
    },
  });
}

describe("openEvents", () => {
  it("reconnects after a dropped stream with Last-Event-ID and stops after delivery", async () => {
    vi.useFakeTimers();
    const sent: Headers[] = [];
    const replies = [ev(0, "created") + ev(1), ev(2, "delivered")];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) => {
        sent.push(new Headers(init?.headers));
        return new Response(streamOf(replies.shift() ?? ""), { status: 200 });
      }),
    );
    const seen: number[] = [];
    const states: Connection[] = [];
    openEvents("a1", (e: MothusiEvent) => seen.push(e.seq), undefined, (c) => states.push(c));
    await vi.runAllTimersAsync();
    expect(seen).toEqual([0, 1, 2]);
    expect(sent.map((h) => h.get("Last-Event-ID"))).toEqual([null, "1"]);
    expect(states).toEqual(["connecting", "live", "reconnecting", "live", "closed"]);
  });

  it("sends the teacher's token, and a 401 closes the stream and shows Sign in", async () => {
    vi.stubEnv("VITE_COGNITO_CLIENT_ID", "spa");
    vi.stubEnv("VITE_COGNITO_DOMAIN", "https://c.example");
    vi.stubEnv("VITE_AUTH_REDIRECT", "https://d.example/auth/callback");
    let authorize = "";
    vi.spyOn(navigation, "assign").mockImplementation((u) => {
      authorize = u;
    });
    await signIn("/runs/a1");
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ id_token: "id-9", expires_in: 3600 })));
    const state = new URL(authorize).searchParams.get("state") ?? "";
    await completeSignIn(new URL(`https://d.example/auth/callback?code=c&state=${state}`));

    const sent: Headers[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) => {
        sent.push(new Headers(init?.headers));
        return new Response(null, { status: 401 });
      }),
    );
    const states: Connection[] = [];
    openEvents("a1", () => undefined, "7", (c) => states.push(c));
    await vi.waitFor(() => expect(states.at(-1)).toBe("closed"));
    expect(sent).toHaveLength(1); // no reconnect loop after a 401
    expect(sent[0]?.get("Authorization")).toBe("Bearer id-9");
    expect(sent[0]?.get("Last-Event-ID")).toBe("7");
    expect(authHeader()).toEqual({}); // signed out: the gate shows Sign in
  });

  it("puts no token in the events URL", async () => {
    const urls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        urls.push(url);
        return new Response(streamOf(ev(0, "delivered")), { status: 200 });
      }),
    );
    const close = openEvents("a1", () => undefined);
    await vi.waitFor(() => expect(urls).toHaveLength(1));
    close();
    expect(urls[0]).toBe("/api/assessments/a1/events");
  });
});
