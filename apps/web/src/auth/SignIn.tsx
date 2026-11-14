// Sign in (console-ui.md §12; 13-sign-in.png): one button to the Cognito hosted UI, and the
// callback that finishes it. Shown only when sign-in is on (VITE_COGNITO_CLIENT_ID set).
import { useEffect, useState } from "react";
import { Navigate, Outlet, useNavigate } from "react-router";

import signInImage from "../assets/sign-in.jpg";
import { completeSignIn, signIn, useSession } from "./session";

function SignInScreen({ notice }: { notice: string }) {
  const [going, setGoing] = useState(false);
  return (
    <main className="signin">
      <img className="signin-image" src={signInImage} alt="" />
      <section className="signin-panel" aria-labelledby="signin-title">
        <div className="signin-content">
          <div className="wordmark">
            Mothusi<span>.</span>
          </div>
          <p className="signin-description muted">
            A colleague for the assessment cycle: it marks against your memo, moderates like your HOD, and hands everything back for your approval.
          </p>
          <div className="signin-form">
            <h1 id="signin-title">Sign in to Mothusi</h1>
            <p className="muted">Mothusi is open to invited teachers. Sign in with the account your invitation set up.</p>
            {notice && (
              <p className="error" role="alert">
                {notice}
              </p>
            )}
            <div>
              <button
                type="button"
                className="btn primary"
                disabled={going}
                onClick={() => {
                  setGoing(true);
                  void signIn();
                }}
              >
                {going ? "Opening sign-in…" : "Sign in"}
              </button>
            </div>
            <p className="small muted">Sign-in runs on Amazon Cognito in Cape Town, then brings you back to this page.</p>
          </div>
        </div>
      </section>
    </main>
  );
}

/** Every console route sits behind this: Sign in when sign-in is on and there is no session. */
export function SignInGate() {
  const session = useSession();
  if (session.state === "signed-out") return <SignInScreen notice={session.notice} />;
  return <Outlet />;
}

// StrictMode runs effects twice; the first completeSignIn removes the PKCE entry, so both runs
// must share one exchange per callback URL.
const exchanges = new Map<string, Promise<string>>();

/** /auth/callback: exchange the code, then go back to the page that asked for sign-in. */
export function SignInCallback() {
  const navigate = useNavigate();
  const session = useSession();
  const [error, setError] = useState("");
  useEffect(() => {
    const href = window.location.href;
    let exchange = exchanges.get(href);
    if (!exchange) {
      exchange = completeSignIn(new URL(href));
      exchanges.set(href, exchange);
    }
    let live = true;
    exchange.then(
      (returnTo) => live && navigate(returnTo, { replace: true }),
      (e: unknown) => live && setError(e instanceof Error ? e.message : "The sign-in did not finish. Sign in again."),
    );
    return () => {
      live = false;
    };
  }, [navigate]);
  if (session.state === "off") return <Navigate to="/" replace />;
  if (error) return <SignInScreen notice={error} />;
  return (
    <p className="compose muted" role="status">
      Signing you in…
    </p>
  );
}
