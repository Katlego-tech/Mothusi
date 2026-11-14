// The voice (conversation.md §12): a mic in the box's input row. The ElevenLabs agent has one client
// tool, ask_mothusi, which sends the teacher's words down the same path as typing and hands back
// Mothusi's answer to be read aloud verbatim. The agent's own model never states a mark or a fact.
import { ConversationProvider, useConversation } from "@elevenlabs/react";
import { useEffect, useRef, useState } from "react";

import { ApiError, api } from "../api/client";

type Ask = (text: string) => Promise<string>;

const NOTICE_KEY = "mothusi.voice.noticed";

export function VoiceControl({ ask }: { ask: Ask }) {
  return (
    <ConversationProvider>
      <Mic ask={ask} />
    </ConversationProvider>
  );
}

function Mic({ ask }: { ask: Ask }) {
  const latest = useRef(ask);
  useEffect(() => {
    latest.current = ask;
  });
  const [problem, setProblem] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const conversation = useConversation({
    clientTools: {
      ask_mothusi: async (parameters: Record<string, unknown>) => {
        const question = typeof parameters.question === "string" ? parameters.question : "";
        return question.trim() ? latest.current(question.trim()) : "Say that again, please?";
      },
    },
    onError: (message: unknown) => setProblem(typeof message === "string" ? message : "The voice connection failed."),
  });
  const live = conversation.status === "connected" || conversation.status === "connecting";

  const start = async () => {
    setProblem(null);
    setStarting(true);
    try {
      const noticed = (() => {
        try {
          return window.localStorage.getItem(NOTICE_KEY) === "1";
        } catch {
          return false;
        }
      })();
      if (!noticed) {
        const ok = window.confirm("Voice goes to ElevenLabs. Say learner numbers like L-014, not names. Start talking?");
        if (!ok) return;
        try {
          window.localStorage.setItem(NOTICE_KEY, "1");
        } catch {
          /* private window: ask again next time */
        }
      }
      const { signed_url } = await api.voiceSession();
      conversation.startSession({ signedUrl: signed_url });
    } catch (e) {
      setProblem(e instanceof ApiError ? e.message : "Voice is unavailable right now.");
    } finally {
      setStarting(false);
    }
  };

  const label = starting || conversation.status === "connecting"
    ? "Connecting…"
    : conversation.status === "connected"
      ? conversation.isSpeaking
        ? "Mothusi is speaking. Stop"
        : "Listening. Stop"
      : "Talk";

  return (
    <span className="voice">
      <button
        type="button"
        className={`mic ${live ? "on" : ""} ${conversation.isSpeaking ? "speaking" : ""}`}
        onClick={() => (live ? conversation.endSession() : void start())}
        aria-pressed={live}
        aria-label={live ? "Stop talking to Mothusi" : "Talk to Mothusi by voice"}
        title={problem ?? (live ? "Stop" : "Talk by voice")}
        disabled={starting}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true" width="18" height="18">
          <path
            d="M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3Zm7 9a7 7 0 0 1-14 0M12 19v3"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
          />
        </svg>
        <span className="mic-label">{label}</span>
      </button>
      {problem && (
        <span className="voice-problem" role="alert">
          {problem}
        </span>
      )}
    </span>
  );
}
