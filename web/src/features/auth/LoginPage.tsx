import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/api/client";
import type { SignInOptions } from "@/api/types";
import { isDesktop } from "@/desktop";
import { useSession } from "@/store/session";
import { Button, ErrorNotice, Input } from "@/ui/primitives";
import { ThemeToggle } from "@/ui/ThemeToggle";
import { keepState, keepVerifier, newVerifier } from "./sso";

/** What a server too old to say offers: the password form, as it always did. */
const PASSWORDS_ONLY: SignInOptions = {
  password: true,
  registration: true,
  sso: false,
};

export function LoginPage() {
  const { login, register, error } = useSession();
  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    identifier: "",
    password: "",
    email: "",
    username: "",
    fullName: "",
  });

  const set =
    (key: keyof typeof form) => (event: React.ChangeEvent<HTMLInputElement>) =>
      setForm((current) => ({ ...current, [key]: event.target.value }));
  const options =
    useQuery({
      queryKey: ["sign-in-options"],
      queryFn: () => api.signInOptions().catch(() => PASSWORDS_ONLY),
      staleTime: Infinity,
    }).data ?? PASSWORDS_ONLY;
  const [ssoProblem, setSsoProblem] = useState<unknown>(null);

  async function signInWithSso() {
    setSsoProblem(null);
    setBusy(true);
    try {
      const started = await api.ssoStart("/");
      // Checked when the provider sends the browser back (`SsoCallback`).
      keepState(started.state);
      window.location.assign(started.authorization_url);
    } catch (error) {
      setSsoProblem(error);
      setBusy(false);
    }
  }

  async function signInWithSaml() {
    setSsoProblem(null);
    setBusy(true);
    try {
      const { verifier, hash } = await newVerifier();
      const started = await api.samlStart(hash, "/");
      // Only this tab can finish it: the callback needs the verifier (`SsoCallback`).
      keepVerifier(verifier);
      window.location.assign(started.url);
    } catch (error) {
      setSsoProblem(error);
      setBusy(false);
    }
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      if (mode === "signin") {
        await login(form.identifier, form.password);
      } else {
        await register({
          email: form.email,
          username: form.username,
          password: form.password,
          full_name: form.fullName || undefined,
        });
      }
    } catch {
      // The store holds the message; nothing to add here.
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="relative flex min-h-full items-center justify-center px-4 py-12">
      <ThemeToggle className="absolute right-4 top-4" />
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <svg viewBox="0 0 32 32" className="mx-auto h-10 w-10 text-ink-100" aria-hidden>
            <path
              d="M6 22C6 13 12 8 20 8"
              stroke="currentColor"
              strokeWidth="3"
              fill="none"
              strokeLinecap="round"
            />
            <rect
              x="14"
              y="14"
              width="12"
              height="10"
              rx="1.5"
              stroke="currentColor"
              strokeWidth="2"
              fill="none"
            />
          </svg>
          <h1 className="mt-4 text-xl font-semibold tracking-tight">
            CurveVision
          </h1>
          <p className="mt-1 text-sm text-ink-400">
            Build better vision datasets.
          </p>
        </div>

        {(options.sso || options.saml) && (
          <div className="panel mb-4 space-y-3 p-6">
            {options.sso && (
              <Button
                variant="primary"
                className="w-full"
                disabled={busy}
                onClick={() => void signInWithSso()}
                data-sso-start=""
              >
                Sign in with {options.sso_name ?? "single sign-on"}
              </Button>
            )}
            {options.saml && (
              <Button
                variant={options.sso ? "secondary" : "primary"}
                className="w-full"
                disabled={busy}
                onClick={() => void signInWithSaml()}
                data-saml-start=""
              >
                Sign in with {options.saml_name ?? "SAML single sign-on"}
              </Button>
            )}
            {ssoProblem != null && <ErrorNotice error={ssoProblem} />}
          </div>
        )}

        {options.password && (
          <form onSubmit={submit} className="panel space-y-4 p-6">
            {mode === "signup" && (
              <>
                <Input
                  label="Email"
                  name="email"
                  type="email"
                  autoComplete="email"
                  required
                  value={form.email}
                  onChange={set("email")}
                />
                <Input
                  label="Username"
                  name="username"
                  autoComplete="username"
                  required
                  value={form.username}
                  onChange={set("username")}
                />
                <Input
                  label="Full name"
                  name="fullName"
                  autoComplete="name"
                  value={form.fullName}
                  onChange={set("fullName")}
                />
              </>
            )}

            {mode === "signin" && (
              <Input
                label="Username or email"
                name="identifier"
                autoComplete="username"
                required
                value={form.identifier}
                onChange={set("identifier")}
              />
            )}

            <Input
              label="Password"
              name="password"
              type="password"
              autoComplete={
                mode === "signin" ? "current-password" : "new-password"
              }
              required
              value={form.password}
              onChange={set("password")}
              hint={mode === "signup" ? "At least 10 characters." : undefined}
            />

            {error && <ErrorNotice error={error} />}

            <Button
              type="submit"
              variant="primary"
              className="w-full"
              disabled={busy}
            >
              {busy
                ? "Working…"
                : mode === "signin"
                  ? "Sign in"
                  : "Create account"}
            </Button>

            {options.registration && (
              <p className="text-center text-xs text-ink-500">
                {mode === "signin"
                  ? "No account yet?"
                  : "Already have an account?"}{" "}
                <button
                  type="button"
                  className="text-curve-400 hover:underline"
                  onClick={() =>
                    setMode(mode === "signin" ? "signup" : "signin")
                  }
                >
                  {mode === "signin" ? "Create one" : "Sign in"}
                </button>
              </p>
            )}
          </form>
        )}

        <p className="mt-6 text-center text-xs text-ink-600">
          The first account created on a fresh instance becomes its
          administrator.
        </p>
        {!isDesktop() && (
          <p className="mt-2 text-center text-xs text-ink-600">
            Rather work on your own machine?{" "}
            <a
              href="https://github.com/Derric01/CurveVision/releases/latest"
              target="_blank"
              rel="noopener noreferrer"
              className="text-curve-400 hover:underline"
              data-download-desktop=""
            >
              Download the desktop application
            </a>
            .
          </p>
        )}
      </div>
    </div>
  );
}
