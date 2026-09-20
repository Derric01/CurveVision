/**
 * Running inside the desktop application.
 *
 * The desktop shell spawns the local server, then opens this page at that server's address
 * with an initialization script that runs before any of our own code:
 *
 *     window.__CURVEVISION__ = { url, token, data_dir, version, desktop: true }
 *
 * That is the entire integration surface. The same bundle is served to browsers against a
 * shared server, where the global is simply absent and everything behaves as it always has
 * — a real sign-in screen, because there really are other people.
 *
 * The value is read **once, at module load**, deliberately: it is injected before the page
 * scripts run, so it is available before React mounts. That is what lets the application
 * skip rendering a signed-out state it would immediately have to replace.
 */

export interface DesktopConnection {
  /** Where the local server is listening. Informational: the page is same-origin with it. */
  readonly url: string;
  /** An API token minted for this launch. Never persisted — see `parseConnection`. */
  readonly token: string;
  /** Where this installation keeps its database and media. Shown, never used as a path. */
  readonly dataDir: string;
  readonly version: string;
}

declare global {
  interface Window {
    __CURVEVISION__?: unknown;
  }
}

/**
 * Validate whatever the shell injected.
 *
 * Only two fields are load-bearing: the `desktop` flag, and a token. The rest is
 * informational, so a shell that grows a field or drops a cosmetic one does not break the
 * application. Anything that fails this returns `null` and the app falls back to the
 * ordinary browser path, which is the safe direction to fail in.
 */
export function parseConnection(source: unknown): DesktopConnection | null {
  if (typeof source !== 'object' || source === null) return null;
  const value = source as Record<string, unknown>;

  // Not a truthiness check: a stray global must not be able to put the app into a mode
  // where it stops asking who you are.
  if (value.desktop !== true) return null;

  const token = typeof value.token === 'string' ? value.token.trim() : '';
  if (!token) return null;

  return {
    url: typeof value.url === 'string' ? value.url : '',
    token,
    dataDir: typeof value.data_dir === 'string' ? value.data_dir : '',
    version: typeof value.version === 'string' ? value.version : '',
  };
}

function read(): DesktopConnection | null {
  // `typeof window` guards the test environment, which runs in Node with no DOM.
  if (typeof window === 'undefined') return null;
  return parseConnection(window.__CURVEVISION__);
}

/** The desktop connection, or `null` when running in an ordinary browser. */
export const desktop: DesktopConnection | null = read();

export function isDesktop(): boolean {
  return desktop !== null;
}

/**
 * The credential to authenticate with, when there is one.
 *
 * Never written to `localStorage`. The shell mints a fresh token on every launch and
 * revokes the previous one, so a stored copy would be a stale credential that outlives the
 * session that owned it — exactly the failure the per-launch rotation exists to prevent.
 */
export function desktopToken(): string | null {
  return desktop?.token ?? null;
}

// ---------------------------------------------------------------- asking the shell

/**
 * Everything below talks to the Tauri shell, and it is deliberately the *only* place that
 * does. Two consequences fall out of keeping it here:
 *
 * 1. Every caller can be tested without a shell, because in a browser these are a `null`
 *    and a no-op rather than a thrown error.
 * 2. Playwright drives Chromium, not the Tauri webview, so an actual `invoke()` across the
 *    IPC bridge cannot be exercised in CI. Confining it to three short functions keeps the
 *    untestable surface as small as it can be, and everything either side of it testable.
 *
 * `@tauri-apps/api` is imported **dynamically**, so a browser never fetches the chunk, and
 * `withGlobalTauri` stays `false` — the shell's API is not attached to `window`. See
 * ADR 0008.
 */

/** The event the shell emits for File ▸ Open Folder… (and Cmd/Ctrl+O). */
export const OPEN_FOLDER_EVENT = 'menu:open-folder';

/**
 * Ask the shell for a folder of images, via the operating system's own dialog.
 *
 * Returns the chosen path, or `null` when the user cancelled — and also when there is no
 * shell, so a browser build simply does nothing rather than breaking. The shell returns a
 * path and nothing else: the page has no filesystem access, and the *server* reads the
 * folder.
 */
export async function chooseFolder(title?: string): Promise<string | null> {
  if (!isDesktop()) return null;
  const { invoke } = await import('@tauri-apps/api/core');
  const chosen = await invoke<string | null>('choose_folder', { title: title ?? null });
  return typeof chosen === 'string' && chosen.length > 0 ? chosen : null;
}

/**
 * Ask the shell for one or more individual image or video files, via the operating
 * system's own dialog.
 *
 * Returns the chosen paths, or an empty array when the user cancelled — and also when
 * there is no shell. The shell's own filter already limits the dialog to media
 * extensions, so what comes back is not re-validated here; a file it cannot actually read
 * is the server's `local-import` endpoint's problem to report, the same as it already is
 * for one that turns up inside a chosen folder.
 */
export async function chooseFiles(): Promise<string[]> {
  if (!isDesktop()) return [];
  const { invoke } = await import('@tauri-apps/api/core');
  const chosen = await invoke<string[]>('choose_files', {});
  return Array.isArray(chosen) ? chosen.filter((path) => typeof path === 'string' && path.length > 0) : [];
}

/**
 * Run `handler` when the shell's Open Folder menu item is used. Returns an unsubscribe.
 *
 * Subscribing is asynchronous and unmounting is not, so a component that unmounts before
 * the listener is registered would otherwise leak one — hence `cancelled`.
 */
export function onOpenFolder(handler: () => void): () => void {
  let stop: (() => void) | null = null;
  let cancelled = false;

  void (async () => {
    if (!isDesktop()) return;
    try {
      const { listen } = await import('@tauri-apps/api/event');
      const unlisten = await listen(OPEN_FOLDER_EVENT, () => handler());
      if (cancelled) unlisten();
      else stop = unlisten;
    } catch (error) {
      // A menu that does not reach the page is a degraded shell, not a broken app: the
      // button does the same thing. Say so once rather than taking the page down.
      console.warn('CurveVision: the shell menu is not available', error);
    }
  })();

  return () => {
    cancelled = true;
    stop?.();
    stop = null;
  };
}
