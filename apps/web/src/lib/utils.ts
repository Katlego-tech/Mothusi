import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** shadcn/ui's class joiner: later classes win over earlier ones. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** "13.02" -> "R 13.02", as the mockups set rand. */
export const rand = (zar: string | number) => `R ${Number(zar).toFixed(2)}`;

/** Seconds -> "06:42" (the run's clock). */
export const clock = (s: number) =>
  `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

/** The state's name as the rail and chip write it: "re_marking" -> "re-marking". */
export const stateName = (state: string) =>
  state === "memo_check" ? "memo check" : state === "reading_check" ? "reading check" : state === "awaiting_approval" ? "awaiting approval" : state.replace("_", "-");
