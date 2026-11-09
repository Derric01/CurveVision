/**
 * An export too large for the page to hold, in a browser.
 *
 * `api.exportProject` answers with the archive itself, which has to sit in the page's
 * memory before the save prompt can appear: fine for annotations, impossible for an export
 * carrying tens of gigabytes of images. This runs the same export as a background task,
 * waits for it, and hands the browser a short-lived signed link, so its own download
 * manager streams the archive to disk.
 */

import type { BackgroundTaskBrief } from '@/api/types';

export interface ExportSteps {
  start: () => Promise<BackgroundTaskBrief>;
  poll: (id: string) => Promise<BackgroundTaskBrief>;
  link: (id: string) => Promise<{ url: string }>;
  /** Hand the URL to the browser's download manager. */
  download: (url: string) => void;
  /** Between polls; injectable so a test does not wait. */
  wait?: (ms: number) => Promise<void>;
  onProgress?: (task: BackgroundTaskBrief) => void;
}

const DONE = new Set(['succeeded', 'failed', 'cancelled']);

/** Run an export in the background and download it. Throws with the server's reason. */
export async function runBackgroundExport(steps: ExportSteps): Promise<void> {
  const wait = steps.wait ?? ((ms: number) => new Promise((resolve) => setTimeout(resolve, ms)));
  let task = await steps.start();
  steps.onProgress?.(task);
  while (!DONE.has(task.state)) {
    await wait(1000);
    task = await steps.poll(task.id);
    steps.onProgress?.(task);
  }
  if (task.state !== 'succeeded') {
    throw new Error(task.error || `The export ${task.state}`);
  }
  const { url } = await steps.link(task.id);
  steps.download(url);
}

/** Follow a URL as a download, the way a link the person clicked would be. */
export function followDownload(url: string): void {
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.rel = 'noopener';
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
}
