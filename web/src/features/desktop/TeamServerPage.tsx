/**
 * File ▸ Connect to a Team Server…: switch the desktop window to a shared server.
 *
 * Desktop only. This computer's server checks the address (`POST /local/team-server-check`,
 * which says in a sentence what is wrong with one); the switch itself is the shell's
 * (`connectToTeamServer`), which opens the team server's pages in a window of their own
 * with none of this installation's credentials.
 */

import { useState } from "react";
import { Globe, HardDrive } from "lucide-react";

import { api } from "@/api/client";
import { connectToTeamServer, desktop } from "@/desktop";
import { Button, Input, Panel } from "@/ui/primitives";

type Step =
  | { kind: "editing" }
  | { kind: "checking" }
  | { kind: "refused"; reason: string }
  | { kind: "switching"; url: string; version: string };

export function TeamServerPage() {
  const [address, setAddress] = useState("");
  const [step, setStep] = useState<Step>({ kind: "editing" });
  const busy = step.kind === "checking" || step.kind === "switching";

  async function connect() {
    setStep({ kind: "checking" });
    try {
      const checked = await api.checkTeamServer(address);
      if (!checked.ok || !checked.url) {
        setStep({
          kind: "refused",
          reason: checked.reason ?? "That is not a team server.",
        });
        return;
      }
      setStep({
        kind: "switching",
        url: checked.url,
        version: checked.version ?? "",
      });
      // On success the shell closes this window, so nothing after this line runs.
      await connectToTeamServer(checked.url);
    } catch (error) {
      setStep({ kind: "refused", reason: messageOf(error) });
    }
  }

  return (
    <div className="mx-auto max-w-2xl space-y-4 p-6" data-team-server="">
      <div>
        <h1 className="text-lg font-semibold text-ink-100">
          Connect to a team server
        </h1>
        <p className="mt-1 text-sm text-ink-400">
          If your team runs a shared CurveVision server, this window can show it
          instead of this computer’s. You sign in there as you would in a
          browser, and your work is saved on that server.
        </p>
      </div>

      <Panel
        title={
          <span className="flex items-center gap-2">
            <Globe size={14} /> Server address
          </span>
        }
      >
        <form
          className="space-y-3"
          onSubmit={(event) => {
            event.preventDefault();
            if (!busy && address.trim()) void connect();
          }}
        >
          <Input
            name="team-server-address"
            placeholder="https://cv.example.com"
            value={address}
            autoFocus
            spellCheck={false}
            autoComplete="url"
            disabled={busy}
            onChange={(event) => {
              setAddress(event.target.value);
              if (step.kind === "refused") setStep({ kind: "editing" });
            }}
            hint="The address you would open in a browser. Ask whoever runs the server."
          />
          {step.kind === "refused" && (
            <p
              className="text-sm text-red-300"
              role="alert"
              data-team-server-refused=""
            >
              {step.reason}
            </p>
          )}
          {step.kind === "switching" && (
            <p
              className="text-sm text-curve-200"
              role="status"
              data-team-server-switching=""
            >
              CurveVision {step.version} at {step.url}. Opening it…
            </p>
          )}
          <Button
            type="submit"
            variant="primary"
            disabled={busy || !address.trim()}
          >
            {step.kind === "checking" ? "Checking…" : "Connect"}
          </Button>
        </form>
      </Panel>

      <Panel
        title={
          <span className="flex items-center gap-2">
            <HardDrive size={14} /> This computer
          </span>
        }
      >
        <div className="space-y-2 text-sm text-ink-400">
          <p>
            Nothing here is moved or deleted. Your projects on this computer
            stay in{" "}
            <code className="text-ink-300">
              {desktop?.dataDir || "the application data folder"}
            </code>
            , and File ▸ Use This Computer switches back to them at any time.
          </p>
          <p>
            CurveVision remembers the server you choose and opens it the next
            time it starts. To move this computer’s projects onto the team
            server, use{" "}
            <code className="text-ink-300">curvevision-server backup</code> and{" "}
            <code className="text-ink-300">restore</code> (see the operations
            guide).
          </p>
        </div>
      </Panel>
    </div>
  );
}

function messageOf(error: unknown): string {
  if (error instanceof Error) return error.message;
  // The shell rejects with its own sentence, as a string.
  return typeof error === "string" ? error : "Something went wrong. Try again.";
}
