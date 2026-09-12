import { useEffect } from 'react';
import { Link, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { LogOut, Layers, ListChecks } from 'lucide-react';
import { useSession } from '@/store/session';
import { desktop } from '@/desktop';
import { Button, Spinner } from '@/ui/primitives';
import { LoginPage } from '@/features/auth/LoginPage';
import { ProjectsPage } from '@/features/projects/ProjectsPage';
import { ProjectPage } from '@/features/projects/ProjectPage';
import { TaskPage } from '@/features/tasks/TaskPage';
import { EditorPage } from '@/features/editor/EditorPage';
import { MyWorkPage } from '@/features/tasks/MyWorkPage';

function Logo() {
  return (
    <Link to="/projects" className="flex items-center gap-2">
      <svg viewBox="0 0 32 32" className="h-6 w-6" aria-hidden>
        <path
          d="M6 22C6 13 12 8 20 8"
          stroke="#2dd4bf"
          strokeWidth="3"
          fill="none"
          strokeLinecap="round"
        />
        <rect x="14" y="14" width="12" height="10" rx="1.5" stroke="#5eead4" strokeWidth="2" fill="none" />
      </svg>
      <span className="text-sm font-semibold tracking-tight text-ink-100">CurveVision</span>
    </Link>
  );
}

function TopBar() {
  const { user, logout } = useSession();
  const location = useLocation();

  // The editor takes the whole window: chrome there costs annotation area, which is the
  // scarcest resource in the product.
  if (location.pathname.includes('/jobs/')) return null;

  return (
    <header className="flex h-12 shrink-0 items-center justify-between border-b border-ink-800 px-4">
      <div className="flex items-center gap-6">
        <Logo />
        <nav className="flex items-center gap-1 text-sm">
          <NavLink to="/projects" icon={<Layers size={14} />}>
            Projects
          </NavLink>
          <NavLink to="/my-work" icon={<ListChecks size={14} />}>
            My work
          </NavLink>
        </nav>
      </div>
      <div className="flex items-center gap-3">
        {/* On a shared server, who you are matters and you can stop being them. In the
            desktop application there is one person and no session to end, so a name badge
            and a sign-out button would both be furniture. */}
        {desktop ? (
          <span className="text-xs text-ink-500" title={desktop.dataDir || undefined}>
            {desktop.version ? `v${desktop.version}` : 'Desktop'}
          </span>
        ) : (
          <>
            <span className="text-xs text-ink-400">{user?.username}</span>
            <Button size="sm" variant="ghost" onClick={() => void logout()} title="Sign out">
              <LogOut size={14} />
            </Button>
          </>
        )}
      </div>
    </header>
  );
}

function NavLink({
  to,
  icon,
  children,
}: {
  to: string;
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  const location = useLocation();
  const active = location.pathname.startsWith(to);
  return (
    <Link
      to={to}
      className={
        active
          ? 'flex items-center gap-1.5 rounded-md bg-ink-800 px-2.5 py-1.5 text-ink-100'
          : 'flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-ink-400 hover:text-ink-100'
      }
    >
      {icon}
      {children}
    </Link>
  );
}

function LocalServerUnavailable({
  error,
  onRetry,
}: {
  error: string | null;
  onRetry: () => void;
}) {
  return (
    <div className="flex h-full items-center justify-center p-8">
      <div className="max-w-md space-y-4 text-center">
        <h1 className="text-base font-semibold text-ink-100">
          CurveVision could not start this session
        </h1>
        <p className="text-sm text-ink-400">
          The application runs a server on this machine and talks to it privately. This
          window could not establish a session with it — usually because CurveVision was
          relaunched while this window was still open, which replaces the credential this
          window was given.
        </p>
        {error ? (
          <p className="rounded-md bg-ink-800 px-3 py-2 text-left font-mono text-xs text-ink-400">
            {error}
          </p>
        ) : null}
        <div className="space-y-2">
          <Button size="sm" onClick={onRetry}>
            Try again
          </Button>
          <p className="text-xs text-ink-500">If it keeps failing, restart CurveVision.</p>
        </div>
      </div>
    </div>
  );
}

export default function App() {
  const { status, error, restore } = useSession();

  useEffect(() => {
    void restore();
  }, [restore]);

  if (status === 'unknown') {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner className="h-6 w-6" />
      </div>
    );
  }

  // Desktop only. A sign-in form here would be a dead end: there is no password to type,
  // so the useful thing is to say what actually went wrong.
  if (status === 'unavailable') {
    return <LocalServerUnavailable error={error} onRetry={() => void restore()} />;
  }

  if (status === 'anonymous') {
    return (
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <TopBar />
      <main className="min-h-0 flex-1 overflow-auto">
        <Routes>
          <Route path="/projects" element={<ProjectsPage />} />
          <Route path="/projects/:projectId" element={<ProjectPage />} />
          <Route path="/tasks/:taskId" element={<TaskPage />} />
          <Route path="/jobs/:jobId" element={<EditorPage />} />
          <Route path="/my-work" element={<MyWorkPage />} />
          <Route path="/login" element={<Navigate to="/projects" replace />} />
          <Route path="*" element={<Navigate to="/projects" replace />} />
        </Routes>
      </main>
    </div>
  );
}
