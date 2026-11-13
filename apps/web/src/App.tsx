// The console's routes (console-ui.md §7): /new sets the task; /runs/:id follows one run, and the
// screen is chosen by the run's state alone (§5): the console never decides a transition.
import { lazy, Suspense } from "react";
import { createBrowserRouter, Navigate, RouterProvider, useParams } from "react-router";

import { useEventStream } from "./api/events";
import { SignInCallback, SignInGate } from "./auth/SignIn";
import { RunScreen } from "./screens/RunScreen";
import { PagesScreen } from "./screens/PagesScreen";
import { SetTaskScreen } from "./screens/SetTaskScreen";

// The bundle's documents (Markdown) are only read at approval: load those screens when needed.
const ApprovalScreen = lazy(() => import("./screens/ApprovalScreen").then((m) => ({ default: m.ApprovalScreen })));
const DeliveredScreen = lazy(() => import("./screens/DeliveredScreen").then((m) => ({ default: m.DeliveredScreen })));

function Run({ id }: { id: string }) {
  const { run, connection } = useEventStream(id);
  const loading = <p className="compose muted">Opening the bundle…</p>;
  // Until the first event (`created`) arrives the screen cannot be chosen: pages mode and a typed
  // run look different from the start. A stream that fails shows RunScreen's connection state.
  if (run.events.length === 0 && (connection === "connecting" || connection === "live"))
    return (
      <p className="compose muted" role="status">
        Opening the run…
      </p>
    );
  // Pages mode before reading starts: only the created event exists (console-ui.md §11.4).
  if (run.created?.mode === "pages" && run.state === "draft" && run.events.length <= 1 && !run.halted)
    return <PagesScreen run={run} />;
  if (run.state === "delivered" && run.delivered)
    return (
      <Suspense fallback={loading}>
        <DeliveredScreen run={run} />
      </Suspense>
    );
  if (run.state === "awaiting_approval" || run.state === "approved")
    return (
      <Suspense fallback={loading}>
        <ApprovalScreen run={run} />
      </Suspense>
    );
  return <RunScreen run={run} connection={connection} />;
}

function RunRoute() {
  const { id = "" } = useParams();
  return <Run key={id} id={id} />;
}

const router = createBrowserRouter([
  { path: "/auth/callback", element: <SignInCallback /> },
  {
    element: <SignInGate />, // Sign in first on the AWS URL (§12); a pass-through on the laptop
    children: [
      { path: "/", element: <Navigate to="/new" replace /> },
      { path: "/new", element: <SetTaskScreen /> },
      { path: "/runs/:id", element: <RunRoute /> },
      { path: "*", element: <Navigate to="/new" replace /> },
    ],
  },
]);

export function App() {
  return <RouterProvider router={router} />;
}
