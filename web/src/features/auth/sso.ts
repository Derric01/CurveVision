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
  | { kind: "saml"; code: string; verifier: string }
  | { kind: "refused"; message: string };

/**
 * What to do with the query string the provider sent back, given the state this tab kept.
 * Pure, so every way it can go wrong is tested without a browser.
 */
export function readCallback(
  search: string,
  expected: string | null,
  verifier: string | null = null,
): CallbackOutcome {
  const params = new URLSearchParams(search);
  // SAML: the server read the provider's answer and sent back a login code, or why not.
  const samlError = params.get("saml_error");
  if (samlError) return { kind: "refused", message: samlError };
  const samlCode = params.get("saml");
  if (samlCode) {
    if (!verifier) {
      return {
        kind: "refused",
        message:
          "This sign-in was not started in this browser tab, so it was not finished here. " +
          "Start it again.",
      };
    }
    return { kind: "saml", code: samlCode, verifier };
  }
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

const VERIFIER_KEY = "curvevision.saml.verifier";

function base64url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/**
 * A SAML sign-in's secret, kept by this tab: the server mints the request from its hash,
 * and only whoever holds the verifier itself can finish what comes back. Returns the
 * verifier and its SHA-256, base64url without padding — what `samlStart` sends.
 */
export async function newVerifier(): Promise<{ verifier: string; hash: string }> {
  const random = new Uint8Array(48);
  crypto.getRandomValues(random);
  const verifier = base64url(random);
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return { verifier, hash: base64url(new Uint8Array(digest)) };
}

export function keepVerifier(verifier: string): void {
  storage()?.setItem(VERIFIER_KEY, verifier);
}

/** The verifier this tab kept — once: reading it forgets it. */
export function takeVerifier(): string | null {
  const store = storage();
  const verifier = store?.getItem(VERIFIER_KEY) ?? null;
  store?.removeItem(VERIFIER_KEY);
  return verifier;
}
