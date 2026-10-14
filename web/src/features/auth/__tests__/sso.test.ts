import { describe, expect, it } from "vitest";
import { newVerifier, readCallback } from "../sso";

describe("readCallback", () => {
  it("finishes a sign-in this tab started", () => {
    expect(readCallback("?code=abc&state=s1", "s1")).toEqual({
      kind: "finish",
      code: "abc",
      state: "s1",
    });
  });

  it("refuses one another browser started: login CSRF", () => {
    const outcome = readCallback("?code=abc&state=theirs", "mine");
    expect(outcome.kind).toBe("refused");
    expect(outcome.kind === "refused" && outcome.message).toContain(
      "not started in this browser",
    );
  });

  it("refuses one when this tab started none", () => {
    expect(readCallback("?code=abc&state=s1", null).kind).toBe("refused");
  });

  it("says so when the person cancelled at the provider", () => {
    expect(readCallback("?error=access_denied&state=s1", "s1")).toEqual({
      kind: "refused",
      message: "The sign-in was cancelled at the provider.",
    });
  });

  it("passes on the provider's reason for anything else", () => {
    const outcome = readCallback(
      "?error=invalid_scope&error_description=no+email",
      "s1",
    );
    expect(outcome.kind === "refused" && outcome.message).toContain("no email");
  });

  it("refuses an answer with no code", () => {
    expect(readCallback("?state=s1", "s1").kind).toBe("refused");
  });
});

describe("readCallback, for SAML", () => {
  it("finishes with the login code and the verifier this tab kept", () => {
    expect(readCallback("?saml=code-1", null, "v-1")).toEqual({
      kind: "saml",
      code: "code-1",
      verifier: "v-1",
    });
  });

  it("refuses a login code when this tab holds no verifier", () => {
    const outcome = readCallback("?saml=code-1", null, null);
    expect(outcome.kind === "refused" && outcome.message).toContain(
      "not started in this browser tab",
    );
  });

  it("shows the server's reason when it refused the provider's answer", () => {
    expect(readCallback("?saml_error=Sign-ins+must+start+here.", null, "v")).toEqual({
      kind: "refused",
      message: "Sign-ins must start here.",
    });
  });
});

describe("newVerifier", () => {
  it("is random, and its hash is the base64url SHA-256 the server recomputes", async () => {
    const one = await newVerifier();
    const two = await newVerifier();
    expect(one.verifier).not.toBe(two.verifier);
    expect(one.verifier).toMatch(/^[A-Za-z0-9_-]{64}$/);
    expect(one.hash).toMatch(/^[A-Za-z0-9_-]{43}$/);
    const digest = await crypto.subtle.digest(
      "SHA-256",
      new TextEncoder().encode(one.verifier),
    );
    const expected = btoa(String.fromCharCode(...new Uint8Array(digest)))
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "");
    expect(one.hash).toBe(expected);
  });
});
