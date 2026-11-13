// Teacher sign-in on the AWS URL (console-ui.md §12): the authorization code grant with PKCE
// against the Cognito hosted UI. Tokens live in memory only, so a closed tab means signing in again.
// The PKCE verifier, the state and the page to return to must survive the redirect to Cognito, so
// they sit in sessionStorage for that one round trip and are removed at the callback.
// With VITE_COGNITO_CLIENT_ID unset (the laptop, tests) sign-in is off and authHeader() is {}.
import { useSyncExternalStore } from "react";

export interface AuthConfig {
  domain: string; // https://mothusi-<acct>.auth.af-south-1.amazoncognito.com
  clientId: string;
  redirect: string; // https://<distribution-domain>/auth/callback
}

/** Where signIn() sends the browser; tests replace assign (jsdom cannot navigate). */
export const navigation = { assign: (url: string) => window.location.assign(url) };

const PENDING = "mothusi.signin"; // sessionStorage key for the round trip only
const REFRESH_EARLY_MS = 60_000;

interface Tokens {
  idToken: string;
  refreshToken: string;
  expiresAt: number; // epoch ms
}

let tokens: Tokens | null = null;
let refreshTimer: ReturnType<typeof setTimeout> | undefined;
let notice = ""; // why the teacher is looking at Sign in again, if anything went wrong
const listeners = new Set<() => void>();
const changed = () => listeners.forEach((l) => l());

export function authConfig(): AuthConfig | null {
  const env = import.meta.env as Record<string, string | undefined>;
  const clientId = env.VITE_COGNITO_CLIENT_ID;
  const domain = env.VITE_COGNITO_DOMAIN;
  const redirect = env.VITE_AUTH_REDIRECT;
  if (!clientId) return null;
  if (!domain || !redirect) throw new Error("VITE_COGNITO_DOMAIN and VITE_AUTH_REDIRECT must be set with VITE_COGNITO_CLIENT_ID");
  return { domain: domain.replace(/\/$/, ""), clientId, redirect };
}

const b64url = (bytes: Uint8Array) =>
  btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");

const randomString = (n: number) => b64url(crypto.getRandomValues(new Uint8Array(n)));

/** RFC 7636 S256: base64url(SHA-256(verifier)), no padding. */
export async function challengeFor(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return b64url(new Uint8Array(digest));
}

/** A console page to come back to: same-site, and never the callback itself (a retry from a failed
 * callback would otherwise return to that dead callback). Anything else becomes /new. */
function consolePath(path: string): string {
  return path.startsWith("/") && !path.startsWith("//") && !path.startsWith("/auth/") ? path : "/new";
}

/** Redirect to the hosted UI. `returnTo` is where the console goes after the callback. */
export async function signIn(returnTo = `${location.pathname}${location.search}`): Promise<void> {
  const config = authConfig();
  if (!config) return;
  returnTo = consolePath(returnTo);
  const verifier = randomString(48); // 64 characters, within RFC 7636's 43-128
  const state = randomString(24);
  sessionStorage.setItem(PENDING, JSON.stringify({ verifier, state, returnTo }));
  const url = new URL(`${config.domain}/oauth2/authorize`);
  url.search = new URLSearchParams({
    response_type: "code",
    client_id: config.clientId,
    redirect_uri: config.redirect,
    scope: "openid email profile",
    state,
    code_challenge: await challengeFor(verifier),
    code_challenge_method: "S256",
  }).toString();
  navigation.assign(url.toString());
}

async function token(config: AuthConfig, body: Record<string, string>): Promise<Tokens> {
  const response = await fetch(`${config.domain}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ client_id: config.clientId, ...body }).toString(),
  });
  if (!response.ok) throw new Error("Cognito did not accept the sign-in. Sign in again.");
  const reply = (await response.json()) as { id_token?: string; refresh_token?: string; expires_in?: number };
  if (!reply.id_token || !reply.expires_in) throw new Error("Cognito's reply had no ID token. Sign in again.");
  return {
    idToken: reply.id_token,
    refreshToken: reply.refresh_token ?? body.refresh_token ?? "",
    expiresAt: Date.now() + reply.expires_in * 1000,
  };
}

function keep(next: Tokens): void {
  tokens = next;
  notice = "";
  clearTimeout(refreshTimer);
  const config = authConfig();
  if (config && next.refreshToken)
    refreshTimer = setTimeout(() => void refresh(config), Math.max(0, next.expiresAt - Date.now() - REFRESH_EARLY_MS));
  changed();
}

async function refresh(config: AuthConfig): Promise<void> {
  if (!tokens) return;
  try {
    keep(await token(config, { grant_type: "refresh_token", refresh_token: tokens.refreshToken }));
  } catch {
    onUnauthorized("Your session ended. Sign in again.");
  }
}

/** Finish the hosted UI's redirect: check state, exchange the code. Returns the page to go back to. */
export async function completeSignIn(url: URL): Promise<string> {
  const config = authConfig();
  if (!config) return "/new";
  const raw = sessionStorage.getItem(PENDING);
  sessionStorage.removeItem(PENDING);
  const pending = raw ? (JSON.parse(raw) as { verifier: string; state: string; returnTo: string }) : null;
  const error = url.searchParams.get("error_description") ?? url.searchParams.get("error");
  if (error) throw new Error(`Cognito refused the sign-in: ${error}`);
  const code = url.searchParams.get("code");
  if (!pending || !code || url.searchParams.get("state") !== pending.state)
    throw new Error("This sign-in did not start here, so it was refused. Sign in again.");
  keep(
    await token(config, {
      grant_type: "authorization_code",
      code,
      redirect_uri: config.redirect,
      code_verifier: pending.verifier,
    }),
  );
  return consolePath(pending.returnTo);
}

export function authHeader(): Record<string, string> {
  return authConfig() && tokens ? { Authorization: `Bearer ${tokens.idToken}` } : {};
}

/** A 401 from any call, or a refresh that failed: forget the tokens and show Sign in. */
export function onUnauthorized(message = "Sign in to carry on."): void {
  if (!authConfig()) return;
  tokens = null;
  notice = message;
  clearTimeout(refreshTimer);
  changed();
}

export type Session = { state: "off" } | { state: "signed-out"; notice: string } | { state: "signed-in" };

let snapshot: Session = { state: "off" };
function current(): Session {
  const next: Session = !authConfig() ? { state: "off" } : tokens ? { state: "signed-in" } : { state: "signed-out", notice };
  if (JSON.stringify(next) !== JSON.stringify(snapshot)) snapshot = next;
  return snapshot;
}

export function useSession(): Session {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    current,
  );
}

/** Tests only: forget everything between cases. */
export function resetSession(): void {
  tokens = null;
  notice = "";
  clearTimeout(refreshTimer);
  changed();
}
