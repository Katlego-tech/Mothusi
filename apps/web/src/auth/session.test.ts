// Sign-in with PKCE (console-ui.md §12.1, §12.3) and the 401 path through the API client.
import { act, renderHook } from "@testing-library/react";

import { api } from "../api/client";
import { authHeader, challengeFor, completeSignIn, navigation, resetSession, signIn, signOut, teacherId, useSession } from "./session";

const ENV = {
  VITE_COGNITO_DOMAIN: "https://mothusi-1.auth.af-south-1.amazoncognito.com",
  VITE_COGNITO_CLIENT_ID: "spa-client",
  VITE_AUTH_REDIRECT: "https://d1.cloudfront.net/auth/callback",
};

function signInOn() {
  for (const [k, v] of Object.entries(ENV)) vi.stubEnv(k, v);
}

afterEach(() => {
  resetSession();
  sessionStorage.clear();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function startSignIn(returnTo = "/runs/a1"): Promise<URL> {
  let target = "";
  vi.spyOn(navigation, "assign").mockImplementation((u) => {
    target = u;
  });
  await signIn(returnTo);
  return new URL(target);
}

function tokenEndpoint(reply: object, status = 200) {
  const calls: { url: string; body: URLSearchParams }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, body: new URLSearchParams(String(init?.body ?? "")) });
      return Response.json(reply, { status });
    }),
  );
  return calls;
}

describe("PKCE sign-in", () => {
  it("computes the S256 challenge of RFC 7636's own example", async () => {
    expect(await challengeFor("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")).toBe("E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM");
  });

  it("redirects to the hosted UI with an S256 challenge of the verifier it keeps for the callback", async () => {
    signInOn();
    const url = await startSignIn();
    expect(url.origin + url.pathname).toBe(`${ENV.VITE_COGNITO_DOMAIN}/oauth2/authorize`);
    const p = url.searchParams;
    expect([p.get("response_type"), p.get("client_id"), p.get("redirect_uri"), p.get("code_challenge_method")]).toEqual([
      "code",
      "spa-client",
      ENV.VITE_AUTH_REDIRECT,
      "S256",
    ]);
    const kept = JSON.parse(sessionStorage.getItem("mothusi.signin") ?? "{}") as { verifier: string; state: string };
    expect(kept.verifier).toMatch(/^[A-Za-z0-9_-]{43,128}$/);
    expect(p.get("state")).toBe(kept.state);
    expect(p.get("code_challenge")).toBe(await challengeFor(kept.verifier));
  });

  it("exchanges the code with the verifier, keeps the token in memory, and returns to the page", async () => {
    signInOn();
    const url = await startSignIn("/runs/a1");
    const { verifier } = JSON.parse(sessionStorage.getItem("mothusi.signin") ?? "{}") as { verifier: string };
    const calls = tokenEndpoint({ id_token: "id-1", refresh_token: "r-1", expires_in: 3600 });
    const back = await completeSignIn(new URL(`${ENV.VITE_AUTH_REDIRECT}?code=c-1&state=${url.searchParams.get("state")}`));
    expect(back).toBe("/runs/a1");
    expect(calls[0]?.url).toBe(`${ENV.VITE_COGNITO_DOMAIN}/oauth2/token`);
    expect(Object.fromEntries(calls[0]?.body ?? [])).toMatchObject({ grant_type: "authorization_code", code: "c-1", code_verifier: verifier });
    expect(authHeader()).toEqual({ Authorization: "Bearer id-1" });
    expect(sessionStorage.getItem("mothusi.signin")).toBeNull(); // the round trip's state is gone
    expect(JSON.stringify({ ...localStorage })).not.toContain("id-1"); // never stored
  });

  it("refuses a callback whose state does not match, and exchanges nothing", async () => {
    signInOn();
    await startSignIn();
    const calls = tokenEndpoint({ id_token: "id-x", expires_in: 3600 });
    await expect(completeSignIn(new URL(`${ENV.VITE_AUTH_REDIRECT}?code=c-1&state=forged`))).rejects.toThrow(/did not start here/);
    expect(calls).toHaveLength(0);
    expect(authHeader()).toEqual({});
  });

  it("never sends the teacher back to another site after sign-in", async () => {
    signInOn();
    const url = await startSignIn("//evil.example/x");
    tokenEndpoint({ id_token: "id-1", expires_in: 3600 });
    const back = await completeSignIn(new URL(`${ENV.VITE_AUTH_REDIRECT}?code=c&state=${url.searchParams.get("state")}`));
    expect(back).toBe("/");
  });
});

describe("the session in the console", () => {
  it("is off without a client id: no header, no Sign in", () => {
    const { result } = renderHook(() => useSession());
    expect(result.current.state).toBe("off");
    expect(authHeader()).toEqual({});
  });

  it("shows Sign in after a 401 from any call", async () => {
    signInOn();
    const url = await startSignIn();
    tokenEndpoint({ id_token: "id-1", refresh_token: "r", expires_in: 3600 });
    await completeSignIn(new URL(`${ENV.VITE_AUTH_REDIRECT}?code=c&state=${url.searchParams.get("state")}`));
    const { result } = renderHook(() => useSession());
    expect(result.current.state).toBe("signed-in");
    const sent: Headers[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) => {
        sent.push(new Headers(init?.headers));
        return Response.json({ message: "sign in" }, { status: 401 });
      }),
    );
    await act(async () => {
      await expect(api.packs()).rejects.toMatchObject({ status: 401 });
    });
    expect(sent[0]?.get("Authorization")).toBe("Bearer id-1");
    expect(result.current).toMatchObject({ state: "signed-out" });
    expect(authHeader()).toEqual({});
  });
});

describe("sign out", () => {
  it("clears tokens and pending sign-in, and uses Cognito logout", async () => {
    signInOn();
    const url = await startSignIn();
    tokenEndpoint({ id_token: "id-1", refresh_token: "r", expires_in: 3600 });
    await completeSignIn(new URL(`${ENV.VITE_AUTH_REDIRECT}?code=c&state=${url.searchParams.get("state")}`));
    sessionStorage.setItem("mothusi.signin", "pending");
    signOut();
    expect(authHeader()).toEqual({});
    expect(sessionStorage.getItem("mothusi.signin")).toBeNull();
    const target = new URL(vi.mocked(navigation.assign).mock.calls.at(-1)?.[0] ?? "");
    expect(target.origin + target.pathname).toBe(`${ENV.VITE_COGNITO_DOMAIN}/logout`);
    expect(target.searchParams.get("client_id")).toBe("spa-client");
    expect(target.searchParams.get("logout_uri")).toBe("https://d1.cloudfront.net/");
  });
  it("returns to the desk when sign-in is off", () => {
    const assign = vi.spyOn(navigation, "assign").mockImplementation(() => {});
    signOut();
    expect(assign).toHaveBeenCalledWith("/");
  });
});

describe("teacher identity for voice preferences", () => {
  it("uses the Cognito subject and forgets it on sign out", async () => {
    signInOn();
    expect(teacherId()).toBeNull();
    const url = await startSignIn();
    const jwt = `header.${btoa(JSON.stringify({ sub: "teacher-a" }))}.signature`;
    tokenEndpoint({ id_token: jwt, expires_in: 3600 });
    await completeSignIn(new URL(`${ENV.VITE_AUTH_REDIRECT}?code=c&state=${url.searchParams.get("state")}`));
    expect(teacherId()).toBe("spa-client:teacher-a");
    signOut();
    expect(teacherId()).toBeNull();
  });
  it("has a local preference scope when sign-in is off", () => {
    expect(teacherId()).toBe("local");
  });
});
