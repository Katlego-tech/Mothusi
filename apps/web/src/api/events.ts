// useEventStream(id): the live log over SSE (console-ui.md §6, §7, §12.2).
// EventSource cannot send an Authorization header, so this is a fetch-based reader: it parses
// `id:`, `event:` and `data:` frames from the response stream, ignores `: ping` comments, and on a
// reconnect sends Last-Event-ID so the server replays only seq > id. The first connection sends
// none, so the server replays from seq 0 and a reload rebuilds the whole screen. No token is ever
// put in a URL.
import { useEffect, useReducer, useState } from "react";

import { authHeader, onUnauthorized } from "../auth/session";
import { eventsUrl } from "./client";
import type { MothusiEvent } from "./types";
import { apply, emptyRun, type RunView } from "../run/reducer";

export type Connection = "connecting" | "live" | "reconnecting" | "closed";

export interface Frame {
  id?: string;
  event: string;
  data: string;
}

/** An SSE frame parser fed arbitrary chunks (a frame, a line, even a "\r\n" pair may be split). */
export function frameParser(onFrame: (frame: Frame) => void): (chunk: string) => void {
  let buffer = "";
  let id: string | undefined;
  let event = "";
  let data: string[] = [];
  const dispatch = () => {
    if (data.length) onFrame({ id, event: event || "message", data: data.join("\n") });
    event = "";
    data = [];
    id = undefined;
  };
  return (chunk) => {
    buffer += chunk;
    for (;;) {
      const end = buffer.search(/\r\n|\r|\n/);
      if (end < 0) return;
      // A "\r" at the very end might be the first half of "\r\n": wait for the next chunk.
      if (buffer[end] === "\r" && end === buffer.length - 1) return;
      const line = buffer.slice(0, end);
      buffer = buffer.slice(end + (buffer.startsWith("\r\n", end) ? 2 : 1));
      if (line === "") {
        dispatch();
        continue;
      }
      if (line.startsWith(":")) continue; // a comment: the server's ": ping"
      const colon = line.indexOf(":");
      const field = colon < 0 ? line : line.slice(0, colon);
      const value = colon < 0 ? "" : line.slice(colon + 1).replace(/^ /, "");
      if (field === "data") data.push(value);
      else if (field === "event") event = value;
      else if (field === "id" && !value.includes("\0")) id = value;
    }
  };
}

const RETRY_MS = [1000, 2000, 4000, 8000, 10000];

/**
 * Follow one assessment's events. `onStatus` reports the connection; the reader reconnects after a
 * dropped stream (with Last-Event-ID) until delivery, close(), or a 401 (which shows Sign in).
 */
export function openEvents(
  id: string,
  onEvent: (event: MothusiEvent) => void,
  lastEventId?: string,
  onStatus: (connection: Connection) => void = () => undefined,
): () => void {
  const abort = new AbortController();
  let last = lastEventId;
  let done = false;
  let attempt = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;

  const finish = (status: Connection) => {
    done = true;
    onStatus(status);
  };

  const connect = async () => {
    try {
      const headers: Record<string, string> = { Accept: "text/event-stream", ...authHeader() };
      if (last !== undefined) headers["Last-Event-ID"] = last;
      const response = await fetch(eventsUrl(id), { headers, signal: abort.signal, cache: "no-store" });
      if (response.status === 401) {
        onUnauthorized();
        return finish("closed");
      }
      if (!response.ok || !response.body) throw new Error(`events: ${response.status}`);
      onStatus("live");
      attempt = 0;
      const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
      const feed = frameParser((frame) => {
        if (frame.id !== undefined) last = frame.id;
        const event = JSON.parse(frame.data) as MothusiEvent;
        onEvent(event);
        if (event.kind === "delivered") done = true; // nothing follows delivery; the server ends here too
      });
      for (;;) {
        const { value, done: ended } = await reader.read();
        if (ended) break;
        feed(value);
      }
      if (done) return finish("closed");
    } catch (error) {
      if (abort.signal.aborted) return;
      if (error instanceof SyntaxError) return finish("closed"); // a frame that is not an event: never loop on it
    }
    if (done || abort.signal.aborted) return;
    onStatus("reconnecting");
    timer = setTimeout(() => void connect(), RETRY_MS[Math.min(attempt++, RETRY_MS.length - 1)]);
  };

  onStatus("connecting");
  void connect();
  return () => {
    done = true;
    clearTimeout(timer);
    abort.abort();
  };
}

export function useEventStream(id: string): { run: RunView; connection: Connection } {
  const [run, dispatch] = useReducer(apply, id, emptyRun);
  const [connection, setConnection] = useState<Connection>("connecting");
  useEffect(() => openEvents(id, dispatch, undefined, setConnection), [id]);
  return { run, connection };
}
