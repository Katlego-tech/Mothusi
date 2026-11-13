// The Sign in gate and the callback screen (console-ui.md §12.4).
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";

import { completeSignIn, navigation, resetSession } from "./session";
import { SignInCallback, SignInGate } from "./SignIn";

function app(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/auth/callback" element={<SignInCallback />} />
        <Route element={<SignInGate />}>
          <Route path="/new" element={<p>Set the task</p>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

function signInOn() {
  vi.stubEnv("VITE_COGNITO_CLIENT_ID", "spa");
  vi.stubEnv("VITE_COGNITO_DOMAIN", "https://c.example");
  vi.stubEnv("VITE_AUTH_REDIRECT", "https://d.example/auth/callback");
}

afterEach(() => {
  resetSession();
  sessionStorage.clear();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

it("lets the console straight through when sign-in is off (the laptop)", () => {
  app("/new");
  expect(screen.getByText("Set the task")).toBeInTheDocument();
});

it("asks for sign-in first on the AWS URL, and the button goes to the hosted UI", async () => {
  signInOn();
  const assign = vi.spyOn(navigation, "assign").mockImplementation(() => undefined);
  app("/new");
  expect(screen.queryByText("Set the task")).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
  await waitFor(() => expect(assign).toHaveBeenCalledTimes(1));
  expect(assign.mock.calls[0]?.[0]).toMatch(/^https:\/\/c\.example\/oauth2\/authorize\?/);
});

it("shows why a callback that did not start here was refused", async () => {
  signInOn();
  window.history.replaceState(null, "", "/auth/callback?code=c&state=forged");
  app("/auth/callback");
  expect(await screen.findByRole("alert")).toHaveTextContent("did not start here");
  expect(screen.getByRole("button", { name: "Sign in" })).toBeEnabled();
});

it("a retry from a refused callback returns to the console, not to the dead callback", async () => {
  signInOn();
  window.history.replaceState(null, "", "/auth/callback?code=c&state=forged");
  let authorize = "";
  vi.spyOn(navigation, "assign").mockImplementation((u) => {
    authorize = u;
  });
  app("/auth/callback");
  await screen.findByRole("alert");
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
  await waitFor(() => expect(authorize).not.toBe(""));
  const kept = JSON.parse(sessionStorage.getItem("mothusi.signin") ?? "{}") as { returnTo: string };
  expect(kept.returnTo).toBe("/new");
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ id_token: "id-2", expires_in: 3600 })));
  const state = new URL(authorize).searchParams.get("state") ?? "";
  expect(await completeSignIn(new URL(`https://d.example/auth/callback?code=c2&state=${state}`))).toBe("/new");
  vi.unstubAllGlobals();
});
