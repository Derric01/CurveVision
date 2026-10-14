import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useLocation } from 'react-router-dom';
import { checkForUpdate, installUpdate, isDesktop, type UpdateStatus } from '@/desktop';
import { Button, ErrorNotice } from '@/ui/primitives';

/** What the notice says about a status, or `null` for nothing to say. Pure, for tests. */
export function noticeFor(status: UpdateStatus | null | undefined): string | null {
  if (!status?.configured || !status.available || status.available === status.current) {
    return null;
  }
  return `CurveVision ${status.available} is available — this is ${status.current}.`;
}

/**
 * The desktop application's update notice: once per launch it asks the shell whether the
 * release feed offers a newer version, and offers to install it. Nothing in a browser, and
 * nothing for a build without the release key (Help ▸ Check for Updates… says why).
 */
export function UpdateNotice() {
  const desktop = isDesktop();
  const status = useQuery({
    queryKey: ['desktop-update'],
    queryFn: checkForUpdate,
    enabled: desktop,
    staleTime: Infinity,
    retry: false,
  }).data;
  const [dismissed, setDismissed] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [problem, setProblem] = useState<unknown>(null);
  const message = noticeFor(status);
  // Not over the editor, which takes the whole window as the top bar does.
  const inEditor = useLocation().pathname.includes('/jobs/');
  if (!desktop || !message || dismissed || inEditor) return null;

  async function install() {
    setProblem(null);
    setInstalling(true);
    try {
      await installUpdate(); // restarts the application when it succeeds
    } catch (error) {
      setProblem(error);
    } finally {
      setInstalling(false);
    }
  }

  return (
    <div
      className="flex shrink-0 flex-wrap items-center gap-3 border-b border-curve-800 bg-curve-950/60 px-4 py-2 text-sm text-ink-100"
      data-update-notice=""
    >
      <span>{message}</span>
      <Button variant="primary" disabled={installing} onClick={() => void install()} data-update-install="">
        {installing ? 'Installing…' : 'Install and restart'}
      </Button>
      <Button disabled={installing} onClick={() => setDismissed(true)}>
        Later
      </Button>
      {problem != null && <ErrorNotice error={problem} />}
    </div>
  );
}
