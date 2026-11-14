// The colleague on every page (conversation.md §11): the page on the left, the communication box on
// the right. On a run's pages the box is that run's thread; elsewhere it is this session's.
import type { ReactNode } from "react";

import type { RunView } from "../run/reducer";
import { TalkBox } from "./talk";

export function WithColleague({ run, children }: { run?: RunView; children: ReactNode }) {
  return (
    <div className="withtalk">
      <div className="withtalk-main">{children}</div>
      <TalkBox run={run} />
    </div>
  );
}
