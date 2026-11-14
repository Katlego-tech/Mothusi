// The colleague's hands on the screen (conversation.md §14): one window event that the open screen
// answers. The box sends it for a fresh reply's action; RunScreen and ApprovalScreen listen.
import { useEffect, useRef } from "react";

import type { UiAction } from "../api/types";

const EVENT = "mothusi:open";

export function openOnScreen(action: UiAction): void {
  window.dispatchEvent(new CustomEvent<UiAction>(EVENT, { detail: action }));
}

/** Call `handler` for every action the colleague opens while this screen is mounted. */
export function useOpenOnScreen(handler: (action: UiAction) => void): void {
  const latest = useRef(handler);
  useEffect(() => {
    latest.current = handler;
  });
  useEffect(() => {
    const listen = (e: Event) => latest.current((e as CustomEvent<UiAction>).detail);
    window.addEventListener(EVENT, listen);
    return () => window.removeEventListener(EVENT, listen);
  }, []);
}

/** The words on a reply's button for an action, so the teacher can open it again. */
export function actionLabel(a: UiAction): string {
  const who = a.script_id ?? "";
  switch (a.open) {
    case "script":
      return `Open ${who}'s script`;
    case "note":
      return `Open ${who}'s note`;
    case "stats":
      return "Open the item analysis";
    case "approval":
      return "Open the approval";
    case "desk":
      return "Go to your desk";
    case "new":
      return "Mark a new set";
  }
}
