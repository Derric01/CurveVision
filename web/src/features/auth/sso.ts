/**
 * Single sign-on in the browser: going to the provider, and checking what comes back.
 *
 * The server signs the `state` it hands out and checks that signature when the sign-in is
 * finished, so nobody can forge one. What the server cannot know is *which browser* started
 * the sign-in — and a sign-in started on someone else's browser and finished in yours
 * would quietly sign you into their account (login CSRF). So the state is also kept here,
 * in this tab's session storage, and a callback whose state is not the one this tab sent
 * is refused before anything is sent to the server. No cookie is involved.
 */

const STATE_KEY = "curvevision.sso.state";

/** Where the provider returns the browser; registered with the provider exactly so. */
export const CALLBACK_PATH = "/auth/callback";

export type CallbackOutcome =
  | { kind: "finish"; code: string; state: string }
  | { kind: "refused"; message: string };

/**
 * What to do with the query string the provider sent back, given the state this tab kept.
 * Pure, so every way it can go wrong is tested without a browser.
 */
export function readCallback(
  search: string,
  expected: string | null,
): CallbackOutcome {
  const params = new URLSearchParams(search);
  const error = params.get("error");
  if (error) {
    const description = params.get("error_description");
    return {
      kind: "refused",
      message:
        error === "access_denied"
          ? "The sign-in was cancelled at the provider."
          : `The sign-in provider refused: ${description ?? error}`,
    };
  }
  const code = params.get("code");
  const state = params.get("state");
  if (!code || !state) {
    return {
      kind: "refused",
      message: "The sign-in provider sent back an incomplete answer.",
    };
  }
  if (!expected || state !== expected) {
    return {
      kind: "refused",
      message:
        "This sign-in was not started in this browser tab, so it was not finished here. " +
        "Start it again.",
    };
  }
  return { kind: "finish", code, state };
}

function storage(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

export function keepState(state: string): void {
  storage()?.setItem(STATE_KEY, state);
}

/** The state this tab is expecting back — once: reading it forgets it. */
export function takeState(): string | null {
  const store = storage();
  const state = store?.getItem(STATE_KEY) ?? null;
  store?.removeItem(STATE_KEY);
  return state;
}
