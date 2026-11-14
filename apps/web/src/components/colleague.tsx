// The colleague on every page (conversation.md §11): the page on the left, the communication box on
// the right. On a run's pages the box is that run's thread; elsewhere it is this session's.
import type { ReactNode } from "react";
import { useNavigate } from "react-router";

import { useOpenOnScreen } from "../lib/ui-actions";

import type { RunView } from "../run/reducer";
import { TalkBox } from "./talk";

export function WithColleague({ run, children }: { run?: RunView; children: ReactNode }) {
  const navigate = useNavigate();
  // §14: moving between pages; the screens themselves answer script, note and stats.
  useOpenOnScreen((a) => {
    if (a.open === "desk") void navigate("/");
    if (a.open === "new") void navigate("/new");
    if (a.open === "approval" && run) void navigate(`/runs/${run.assessmentId}`);
  });
  return (
    <div className="withtalk">
      <div className="withtalk-main">{children}</div>
      <TalkBox run={run} />
    </div>
  );
}
