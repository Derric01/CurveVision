import { useState } from 'react';
import { useSession } from '@/store/session';
import { Button, ErrorNotice, Input } from '@/ui/primitives';

export function LoginPage() {
  const { login, register, error } = useSession();
  const [mode, setMode] = useState<'signin' | 'signup'>('signin');
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    identifier: '',
    password: '',
    email: '',
    username: '',
    fullName: '',
  });

  const set = (key: keyof typeof form) => (event: React.ChangeEvent<HTMLInputElement>) =>
    setForm((current) => ({ ...current, [key]: event.target.value }));

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      if (mode === 'signin') {
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
    <div className="flex min-h-full items-center justify-center px-4 py-12">
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <svg viewBox="0 0 32 32" className="mx-auto h-10 w-10" aria-hidden>
            <path
              d="M6 22C6 13 12 8 20 8"
              stroke="#2dd4bf"
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
              stroke="#5eead4"
              strokeWidth="2"
              fill="none"
            />
          </svg>
          <h1 className="mt-4 text-xl font-semibold tracking-tight">CurveVision</h1>
          <p className="mt-1 text-sm text-ink-400">Build better vision datasets.</p>
        </div>

        <form onSubmit={submit} className="panel space-y-4 p-6">
          {mode === 'signup' && (
            <>
              <Input
                label="Email"
                name="email"
                type="email"
                autoComplete="email"
                required
                value={form.email}
                onChange={set('email')}
              />
              <Input
                label="Username"
                name="username"
                autoComplete="username"
                required
                value={form.username}
                onChange={set('username')}
              />
              <Input
                label="Full name"
                name="fullName"
                autoComplete="name"
                value={form.fullName}
                onChange={set('fullName')}
              />
            </>
          )}

          {mode === 'signin' && (
            <Input
              label="Username or email"
              name="identifier"
              autoComplete="username"
              required
              value={form.identifier}
              onChange={set('identifier')}
            />
          )}

          <Input
            label="Password"
            name="password"
            type="password"
            autoComplete={mode === 'signin' ? 'current-password' : 'new-password'}
            required
            value={form.password}
            onChange={set('password')}
            hint={mode === 'signup' ? 'At least 10 characters.' : undefined}
          />

          {error && <ErrorNotice error={error} />}

          <Button type="submit" variant="primary" className="w-full" disabled={busy}>
            {busy ? 'Working…' : mode === 'signin' ? 'Sign in' : 'Create account'}
          </Button>

          <p className="text-center text-xs text-ink-500">
            {mode === 'signin' ? 'No account yet?' : 'Already have an account?'}{' '}
            <button
              type="button"
              className="text-curve-400 hover:underline"
              onClick={() => setMode(mode === 'signin' ? 'signup' : 'signin')}
            >
              {mode === 'signin' ? 'Create one' : 'Sign in'}
            </button>
          </p>
        </form>

        <p className="mt-6 text-center text-xs text-ink-600">
          The first account created on a fresh instance becomes its administrator.
        </p>
      </div>
    </div>
  );
}
