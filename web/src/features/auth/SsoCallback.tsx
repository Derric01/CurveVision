import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useSession } from "@/store/session";
import { ErrorNotice, Spinner } from "@/ui/primitives";
import { readCallback, takeState } from "./sso";

/** Where the sign-in provider sends the browser back (`CALLBACK_PATH`). */
export function SsoCallback() {
  const completeSso = useSession((state) => state.completeSso);
  const navigate = useNavigate();
  const [problem, setProblem] = useState<string | null>(null);
  // Once: a code is spent the first time it is exchanged, and React may run an effect twice.
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const outcome = readCallback(window.location.search, takeState());
    if (outcome.kind === "refused") {
      setProblem(outcome.message);
      return;
    }
    completeSso(outcome.code, outcome.state)
      .then((returnTo) =>
        navigate(returnTo === "/" ? "/projects" : returnTo, { replace: true }),
      )
      .catch((error: unknown) =>
        setProblem(error instanceof Error ? error.message : "Sign-in failed"),
      );
  }, [completeSso, navigate]);

  return (
    <div className="flex min-h-full items-center justify-center px-4 py-12">
      <div
        className="w-full max-w-sm space-y-4 text-center"
        data-sso-callback=""
      >
        {problem ? (
          <>
            <ErrorNotice error={problem} />
            <Link
              to="/login"
              className="text-sm text-curve-400 hover:underline"
            >
              Back to sign-in
            </Link>
          </>
        ) : (
          <>
            <Spinner className="mx-auto h-6 w-6" />
            <p className="text-sm text-ink-400">Signing you in…</p>
          </>
        )}
      </div>
    </div>
  );
}
