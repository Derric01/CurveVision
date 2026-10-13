import { describe, expect, it } from "vitest";
import { readCallback } from "../sso";

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
