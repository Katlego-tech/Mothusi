// The colleague on every page (conversation.md §11): the page on the left, the communication box on
// the right. On a run's pages the box is that run's thread; elsewhere it is this session's.
import type { ReactNode } from "react";
import { useLocation, useNavigate } from "react-router";

import { useOpenOnScreen } from "../lib/ui-actions";

import type { RunView } from "../run/reducer";
import { TalkBox } from "./talk";

export function WithColleague({ run, children }: { run?: RunView; children: ReactNode }) {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  // §14: moving between pages; the screens themselves answer script, note and stats.
  useOpenOnScreen((a) => {
    if (a.open === "desk") void navigate("/");
    if (a.open === "new") void navigate("/new");
    if (a.open === "approval" && run) void navigate(`/runs/${run.assessmentId}`);
  });
  return (
    <div className="withtalk h-dvh! min-h-0! overflow-hidden">
      <div className="withtalk-main flex! h-full min-h-0 flex-col overflow-hidden [&>.topbar]:shrink-0 [&>.canvas]:min-h-0 [&>.canvas]:flex-1 [&>.canvas]:overflow-y-auto [&>.canvas>.panel]:shrink-0 [&>.canvas>.cols]:shrink-0 [&>main]:min-h-0 [&>main]:flex-1 [&>main]:overflow-y-auto [&>.desk]:w-full [&>.desk>*]:shrink-0 [&>.compose]:w-full [&>.compose>*]:shrink-0 [&>.approvebar]:shrink-0">{children}</div>
      <TalkBox run={run} page={pathname} />
    </div>
  );
}
